import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

import demo


class StructuredTests(unittest.TestCase):
    def test_expected_fields_and_nulls(self):
        for case in demo.CASES:
            result = demo.evaluate(json.dumps(case["expected"]), case["expected"])
            self.assertTrue(result["schema_ok"])
            self.assertTrue(result["correct"])

    def test_valid_schema_does_not_imply_correct_labels(self):
        result = demo.evaluate('{"category":"outage","priority":"high","summary":"Service unavailable"}',
                               demo.CASES[0]["expected"])
        self.assertTrue(result["schema_ok"])
        self.assertFalse(result["correct"])
        self.assertFalse(result["field_matches"]["category"])

    def test_missing_fields_are_not_silently_filled(self):
        for content in ('{"category":null}', "not json", "[]",
                        '{"category":null,"priority":null,"summary":null,"invented":true}'):
            self.assertEqual(demo.evaluate(content)["status"], "invalid")

    def test_refusal_has_precedence_over_parse_and_truncation(self):
        result = demo.evaluate("", refusal="I cannot reveal credentials.", finish_reason="length")
        self.assertEqual(result["status"], "refusal")

    def test_incomplete_valid_looking_json_is_never_accepted(self):
        content = json.dumps(demo.CASES[0]["expected"])
        for finish_reason in ("length", "content_filter", None):
            self.assertEqual(demo.evaluate(content, finish_reason=finish_reason)["status"], "incomplete")

    def test_few_shot_examples_do_not_change_rules(self):
        basic = demo.messages("instructions", "ticket")
        few = demo.messages("few-shot", "ticket")
        self.assertEqual(basic[0], few[0])
        self.assertEqual(basic[-1], few[-1])
        self.assertEqual(len(few), len(basic) + 4)
        self.assertTrue(demo.FORMAT["json_schema"]["strict"])
        self.assertEqual(set(demo.SCHEMA["required"]), set(demo.SCHEMA["properties"]))

    def test_request_errors_are_explicit(self):
        client = Mock()
        client.chat.completions.create.side_effect = TimeoutError("offline fixture")
        result = demo.call(client, "alias", "structured", "ticket")
        self.assertEqual(result["status"], "request_error")
        self.assertIn("TimeoutError", result["reason"])

    def test_service_prompt_filter_is_distinct_from_refusal_and_transport_failure(self):
        import httpx
        from openai import BadRequestError

        response = httpx.Response(400, request=httpx.Request("POST", "https://example.org"))
        for body in ({"error": {"code": "content_filter"}}, {"code": "content_filter"}):
            client = Mock()
            client.chat.completions.create.side_effect = BadRequestError(
                "blocked by service", response=response, body=body)
            result = demo.call(client, "alias", "structured", "ticket")
            self.assertEqual(result["status"], "content_filter")
            self.assertEqual(result["http_status"], 400)
            self.assertEqual(result["service_error"], body)
            self.assertFalse(result["schema_ok"])
            self.assertFalse(result["correct"])
        client.chat.completions.create.side_effect = BadRequestError(
            "bad schema", response=response, body={"code": "invalid_json_schema"})
        self.assertEqual(demo.call(client, "alias", "structured", "ticket")["status"], "request_error")

    def test_offline_error_examples_are_labeled_and_need_no_configuration(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(demo.main(["--offline-errors"]), 0)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r["status"] for r in rows], ["refusal", "incomplete", "incomplete"])
        self.assertTrue(all("NOT AZURE OUTPUT" in row["source"] for row in rows))

    def test_invalid_request_interval_is_rejected(self):
        for interval in ("-1", "nan", "inf"):
            with self.subTest(interval=interval), self.assertRaises(SystemExit):
                demo.main(["--offline-errors", "--request-interval", interval])

    def test_live_probe_request_error_is_not_success(self):
        success = {"status": "complete", "schema_ok": True, "correct": True}
        error = {"status": "request_error", "schema_ok": False, "correct": False}
        for probe_index in (12, 13):
            results = [success.copy() for _ in range(14)]
            results[probe_index] = error
            with self.subTest(probe_index=probe_index), \
                    patch("azure.identity.DefaultAzureCredential"), \
                    patch("azure.identity.get_bearer_token_provider"), \
                    patch("openai.OpenAI"), \
                    patch("demo.call", side_effect=results), \
                    redirect_stdout(StringIO()):
                self.assertEqual(demo.main(["--endpoint", "https://example.org",
                                            "--request-interval", "0"]), 1)

    def test_filtered_safety_probe_is_observed_but_not_a_request_failure(self):
        success = {"status": "complete", "schema_ok": True, "correct": True}
        filtered = {"status": "content_filter", "schema_ok": False, "correct": False}
        with patch("azure.identity.DefaultAzureCredential"), \
                patch("azure.identity.get_bearer_token_provider"), \
                patch("openai.OpenAI"), \
                patch("demo.call", side_effect=[success.copy() for _ in range(13)] + [filtered]), \
                redirect_stdout(StringIO()):
            self.assertEqual(demo.main(["--endpoint", "https://example.org",
                                        "--request-interval", "0"]), 0)


if __name__ == "__main__":
    unittest.main()
