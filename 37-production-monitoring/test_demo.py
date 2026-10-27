import json
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import demo
import ui


class MonitoringTests(unittest.TestCase):
    def record(self, expected=True):
        return {"agent": "agent-a", "response_id": "resp_123", "expected_pass": expected, "configured_version": "1"}

    def evaluated(self, passed=True, response_id="resp_123"):
        return [{
            "agent": "agent-a", "run_id": "cloud-eval",
            "item": {"datasource_item": {"response_id": response_id, "agent_name": "agent-a", "agent_version": "1"},
                     "results": [{"name": "price_correct", "passed": passed, "status": "completed"}]},
        }]

    def test_pending_is_not_pass(self):
        self.assertIsNone(demo.match_evaluation(self.record(), []))

    def test_completed_cloud_pass(self):
        self.assertIsNotNone(demo.match_evaluation(self.record(), self.evaluated()))

    def test_actual_regression_must_fail_cloud_grader(self):
        self.assertIsNotNone(demo.match_evaluation(self.record(False), self.evaluated(False)))
        with self.assertRaises(RuntimeError):
            demo.match_evaluation(self.record(False), self.evaluated(True))

    def test_wrong_response_does_not_count(self):
        self.assertIsNone(demo.match_evaluation(self.record(), self.evaluated(response_id="resp_other")))
        self.assertIsNone(demo.match_evaluation(self.record(), self.evaluated(response_id="resp_1234")))

    def test_wrong_agent_does_not_count(self):
        values = self.evaluated()
        values[0]["agent"] = "agent-b"
        self.assertIsNone(demo.match_evaluation(self.record(), values))

    def test_missing_grader_is_error(self):
        values = self.evaluated()
        values[0]["item"]["results"] = []
        with self.assertRaises(RuntimeError):
            demo.match_evaluation(self.record(), values)

    def test_actual_served_version_must_match_deployed_route(self):
        values = self.evaluated()
        values[0]["item"]["datasource_item"]["agent_version"] = "2"
        with self.assertRaises(RuntimeError):
            demo.match_evaluation(self.record(), values)

    def test_grader_pass_must_be_boolean(self):
        values = self.evaluated()
        values[0]["item"]["results"][0]["passed"] = "true"
        with self.assertRaises(RuntimeError):
            demo.match_evaluation(self.record(), values)

    def test_failed_grader_is_not_a_valid_regression_verdict(self):
        values = self.evaluated(False)
        values[0]["item"]["results"][0]["status"] = "failed"
        with self.assertRaises(RuntimeError):
            demo.match_evaluation(self.record(False), values)

    def test_invalid_mode_fails_before_cloud_call(self):
        for mode in ("unknown", None, False):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                demo.set_mode(mode)

    def test_manual_runs_cannot_satisfy_native_monitoring(self):
        with (
            patch.object(demo, "configuration", return_value={
                "agents": {"agent-a": {"eval_id": "cloud-eval"}},
            }),
            patch.object(demo, "AzureCliCredential"),
            patch.object(demo, "client") as client,
        ):
            project = client.return_value.__enter__.return_value
            ai = project.get_openai_client.return_value.__enter__.return_value
            ai.evals.runs.list.return_value.data = [
                SimpleNamespace(metadata={"trigger_type": "oneoff"}, status="completed"),
            ]
            self.assertEqual(demo.evaluation_results(), [])
            ai.evals.runs.output_items.list.assert_not_called()


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ui.ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_html(self):
        with urlopen(self.base) as response:
            self.assertIn(b"Native continuous evaluation", response.read())

    def test_cross_origin_denied(self):
        request = Request(self.base + "/api/traffic", data=b"{}", headers={"Origin": "https://example.com"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_invalid_mode_is_explicit_error(self):
        request = Request(self.base + "/api/traffic", data=b'{"mode":"invalid"}')
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("error", json.loads(caught.exception.read()))


if __name__ == "__main__":
    unittest.main()
