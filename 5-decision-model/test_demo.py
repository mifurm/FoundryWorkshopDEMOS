import unittest
from unittest.mock import patch

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

    def test_subdomain_routing_error_retries_then_succeeds(self):
        responses = iter([
            httpx.Response(404, json={"error": {
                "code": "ResourceNotFound", "message": "Subdomain does not map to a resource.",
            }}, headers={"apim-request-id": "routing-request"}),
            httpx.Response(200, json={"answers": {"team": {"type": "choice", "choice": "billing"}}}),
        ])
        with httpx.Client(
            base_url="https://example/",
            transport=httpx.MockTransport(lambda request: next(responses)),
        ) as client, patch("demo.sleep") as sleep, patch("builtins.print") as output:
            self.assertEqual(demo.classify(client, "decision", "Refund please"), "billing")
            sleep.assert_called_once_with(1)
            self.assertIn("routing-request", output.call_args.args[0])

    def test_subdomain_routing_retries_are_bounded_and_report_service_error(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(404, json={"error": {
                "code": "ResourceNotFound", "message": "Subdomain does not map to a resource.",
            }}, headers={"apim-request-id": "routing-request"})
        with httpx.Client(
            base_url="https://example/", transport=httpx.MockTransport(handle),
        ) as client, patch("demo.sleep") as sleep:
            with self.assertRaises(httpx.HTTPStatusError) as raised:
                demo.classify(client, "decision", "Refund please")
            self.assertEqual(len(calls), 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
            self.assertIn("Subdomain does not map to a resource.", str(raised.exception))
            self.assertIn("routing-request", str(raised.exception))
            self.assertIn("Deployment: decision", str(raised.exception))
            self.assertEqual(raised.exception.response.status_code, 404)

    def test_other_not_found_responses_are_not_retried(self):
        for payload in ({"error": {"code": "DeploymentNotFound", "message": "Missing deployment"}},
                        {"error": {"code": "ResourceNotFound", "message": "Wrong API path"}},
                        {"error": {"code": "ResourceNotFound", "message": None}},
                        {"error": "not found"}, [], {}):
            with self.subTest(payload=payload), self.client(payload, 404) as client, \
                    patch("demo.sleep") as sleep:
                with self.assertRaises(httpx.HTTPStatusError):
                    demo.classify(client, "decision", "Refund please")
                sleep.assert_not_called()

    def test_non_json_not_found_is_not_retried(self):
        with httpx.Client(
            base_url="https://example/",
            transport=httpx.MockTransport(lambda request: httpx.Response(404, text="Not found")),
        ) as client, patch("demo.sleep") as sleep:
            with self.assertRaises(httpx.HTTPStatusError):
                demo.classify(client, "decision", "Refund please")
            sleep.assert_not_called()

    def test_throttling_respects_retry_header(self):
        for headers, delay in (({"retry-after-ms": "2500"}, 2.5),
                               ({"Retry-After": "3"}, 3)):
            responses = iter([
                httpx.Response(429, headers=headers),
                httpx.Response(200, json={"answers": {"team": {"type": "choice", "choice": "billing"}}}),
            ])
            with self.subTest(headers=headers), httpx.Client(
                base_url="https://example/",
                transport=httpx.MockTransport(lambda request: next(responses)),
            ) as client, patch("demo.sleep") as sleep:
                self.assertEqual(demo.classify(client, "decision", "Refund please"), "billing")
                sleep.assert_called_once_with(delay)

    def test_throttling_is_bounded_and_missing_or_excessive_delay_fails(self):
        for headers, expected_calls in (({"retry-after-ms": "1"}, 3), ({}, 1),
                                        ({"Retry-After": "120"}, 1)):
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(429, headers=headers)
            with self.subTest(headers=headers), httpx.Client(
                base_url="https://example/", transport=httpx.MockTransport(handle),
            ) as client, patch("demo.sleep"), self.assertRaises(httpx.HTTPStatusError):
                demo.classify(client, "decision", "Refund please")
            self.assertEqual(len(calls), expected_calls)


if __name__ == "__main__":
    unittest.main()
