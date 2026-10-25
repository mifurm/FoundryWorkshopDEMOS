import unittest

import httpx

import demo


class DecisionTests(unittest.TestCase):
    def client(self, payload, status=200):
        def handle(request):
            self.assertEqual(request.url.path, "/providers/microsoft/v1/systemone")
            self.assertIn(b'"model":"decision"', request.content)
            self.assertIn(b'"type":"choice"', request.content)
            return httpx.Response(status, json=payload)
        return httpx.Client(base_url="https://example/", transport=httpx.MockTransport(handle))

    def test_uses_typed_decision_endpoint(self):
        with self.client({"answers": {"team": {"type": "choice", "choice": "billing"}}}) as client:
            self.assertEqual(demo.classify(client, "decision", "Refund please"), "billing")

    def test_rejects_missing_unknown_and_wrong_type_answers(self):
        for payload in ({}, {"answers": {"team": {"type": "choice", "choice": "sales"}}},
                        {"answers": {"team": {"type": "score", "choice": "billing"}}}):
            with self.subTest(payload=payload), self.client(payload) as client:
                with self.assertRaises(RuntimeError):
                    demo.classify(client, "decision", "Refund please")

    def test_http_errors_are_not_answers(self):
        with self.client({"error": "denied"}, 403) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                demo.classify(client, "decision", "Refund please")


if __name__ == "__main__":
    unittest.main()
