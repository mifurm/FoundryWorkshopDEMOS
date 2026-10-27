import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import demo
from runtime.agent import advance_state, quote, validate_request
import ui


class HostedAgentTests(unittest.TestCase):
    def test_price_ground_truth(self):
        self.assertEqual(quote(3), {
            "attendees": 3, "currency": "USD", "unit_price": 25,
            "total": 75, "source": "hosted-read-only-price-tool",
        })

    def test_request_boundaries(self):
        for value in (1, 20):
            self.assertEqual(validate_request({"attendees": value}), value)
        for value in (None, True, 0, 21, "3", 3.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_request({"attendees": value})

    def test_state_persistence_and_user_isolation(self):
        with TemporaryDirectory() as directory:
            first = advance_state(Path(directory), "alice")
            second = advance_state(Path(directory), "alice")
            other = advance_state(Path(directory), "bob")
            self.assertEqual((first["visits"], second["visits"], other["visits"]), (1, 2, 1))
            self.assertEqual(first["marker"], second["marker"])
            self.assertNotEqual(first["marker"], other["marker"])

    def test_user_id_cannot_escape_state_directory(self):
        with TemporaryDirectory() as directory:
            advance_state(Path(directory), "../../not-a-file")
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)

    def test_invalid_invocations_fail_before_authentication(self):
        for attendees, session in ((True, "valid"), (0, "valid"), (3, "../x"), (3, None)):
            with self.subTest(attendees=attendees, session=session), self.assertRaises(ValueError):
                demo.invoke(attendees, session)

    def test_root_runner_entrypoint_publishes_before_first_live_test(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(demo, "STATE", Path(directory)),
            patch.object(demo, "read_outputs", return_value={"projectEndpoint": "https://project"}),
            patch.object(demo, "publish", return_value={"version": "1"}) as publish,
            patch.object(demo, "live_test", return_value={"passed": True}) as live,
            patch("sys.argv", ["demo.py"]),
            patch("builtins.print"),
        ):
            demo.main()
            publish.assert_called_once_with()
            live.assert_called_once_with()


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
            self.assertIn(b"Foundry's runtime", response.read())
            self.assertIn("no-store", response.headers["Cache-Control"])

    def test_cross_origin_denied(self):
        request = Request(self.base + "/api/invoke", data=b"{}", headers={"Origin": "https://example.com"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_invalid_input_returns_explicit_error(self):
        request = Request(self.base + "/api/invoke", data=b'{"attendees":false,"session_id":"x"}')
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("error", json.loads(caught.exception.read()))

    def test_ui_routes_only_to_cloud_client(self):
        with patch.object(demo, "invoke", return_value={"cloud": "unit-test-fixture"}) as invoke:
            request = Request(self.base + "/api/invoke", data=b'{"attendees":3,"session_id":"test"}')
            with urlopen(request) as response:
                self.assertEqual(json.load(response), {"cloud": "unit-test-fixture"})
            invoke.assert_called_once_with(3, "test")


if __name__ == "__main__":
    unittest.main()
