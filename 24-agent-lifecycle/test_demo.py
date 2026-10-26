import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import demo


class LifecycleTests(unittest.TestCase):
    def test_real_managed_application_deployment_contract(self):
        deployment = {"properties": {
            "state": "Running", "agents": [{"agentName": "agent", "agentVersion": "2"}],
        }}
        with patch("demo.arm", side_effect=[{}, deployment]) as arm:
            self.assertEqual(demo.select_version(Mock(), Mock(), "/deployment", "agent", "2"), deployment)
        body = arm.call_args_list[0].args[-1]["properties"]
        self.assertEqual(body["deploymentType"], "Managed")
        self.assertEqual(body["agents"], [{"agentName": "agent", "agentVersion": "2"}])
        self.assertEqual(body["protocols"], [{"protocol": "Responses", "version": "1.0"}])

    def test_rollout_and_rollback_use_actual_application_endpoint(self):
        project = Mock()
        project.agents.create_version.side_effect = [
            SimpleNamespace(version="1"), SimpleNamespace(version="2"),
        ]
        args = SimpleNamespace(project_resource_id="/subscriptions/sub/resourceGroups/rg/projects/p",
                               project_endpoint="https://project", deployment="model")
        report = {"started_at": "2026-10-25T00:00:00Z"}
        with patch("demo.publish_application") as publish, patch("demo.select_version") as select, \
                patch("demo.invoke") as invoke, patch("demo.arm", return_value={"value": []}):
            demo.lifecycle(project, Mock(), Mock(), args, report)
        self.assertTrue(report["rollback_verified"])
        self.assertEqual([call.args[4] for call in select.call_args_list], ["1", "2", "1"])
        self.assertEqual([call.args[4] for call in invoke.call_args_list], ["USD 25", "USD 40", "USD 25"])
        self.assertIn("/applications/workshop-app-", invoke.call_args.args[2])
        self.assertTrue(invoke.call_args.args[2].endswith("/protocols/openai/responses"))
        self.assertIn("/applications/", publish.call_args.args[2])

    def test_failed_rollout_still_restores_good_version(self):
        project = Mock()
        project.agents.create_version.side_effect = [SimpleNamespace(version="1"), SimpleNamespace(version="2")]
        args = SimpleNamespace(project_resource_id="/subscriptions/sub/projects/p",
                               project_endpoint="https://project", deployment="model")
        report = {}
        with patch("demo.publish_application"), patch("demo.select_version") as select, \
                patch("demo.invoke", side_effect=[None, RuntimeError("bad rollout"), None]):
            with self.assertRaisesRegex(RuntimeError, "bad rollout"):
                demo.lifecycle(project, Mock(), Mock(), args, report)
        self.assertEqual(select.call_args.args[-1], "1")
        self.assertTrue(report["rollback_verified"])

    def test_invocation_uses_entra_and_validates_real_output(self):
        session, credential = Mock(), Mock()
        credential.get_token.return_value.token = "test-token"
        response = session.post.return_value
        response.status_code, response.headers = 200, {"x-request-id": "server-id"}
        response.json.return_value = {
            "id": "resp1", "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": "USD 25"}]}],
        }
        report = {"requests": []}
        demo.invoke(session, credential, "https://app/responses", "1", "USD 25", report)
        credential.get_token.assert_called_with("https://ai.azure.com/.default")
        self.assertEqual(session.post.call_args.kwargs["params"]["api-version"], demo.DATA_VERSION)
        self.assertEqual(len(report["requests"]), len(demo.QUESTIONS))
        response.json.return_value["output"][0]["content"][0]["text"] = "USD 40"
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            demo.invoke(session, credential, "https://app/responses", "1", "USD 25", report)

    def test_failed_or_stale_deployment_does_not_pass(self):
        with patch("demo.arm", side_effect=[{}, {"properties": {"state": "Failed"}}]):
            with self.assertRaises(RuntimeError):
                demo.select_version(Mock(), Mock(), "/deployment", "agent", "1")
        with patch("demo.arm", return_value={"properties": {"state": "Running", "agents": []}}), \
                patch("demo.monotonic", side_effect=[0, 301]):
            with self.assertRaises(TimeoutError):
                demo.select_version(Mock(), Mock(), "/deployment", "agent", "1")


if __name__ == "__main__":
    unittest.main()
