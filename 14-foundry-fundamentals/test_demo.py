import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace as NS
from unittest.mock import Mock

import demo


class FundamentalsTests(unittest.TestCase):
    def test_denied_endpoint_matches_template(self):
        self.assertEqual(demo.denied_endpoint("https://abc.openai.azure.com/"),
                         "https://abc-deny.openai.azure.com/")

    def test_nonstandard_endpoint_requires_explicit_target(self):
        for endpoint in ("http://abc.openai.azure.com", "https://example.org"):
            with self.assertRaises(ValueError):
                demo.denied_endpoint(endpoint)

    def test_stream_handles_usage_only_chunk_and_empty_delta(self):
        usage = NS(model_dump=lambda: {"total_tokens": 7})
        chunks = [NS(choices=[NS(delta=NS(content="Hello"))], usage=None),
                  NS(choices=[NS(delta=NS(content=None))], usage=None),
                  NS(choices=[], usage=usage)]
        written = []
        self.assertEqual(demo.stream_response(chunks, written.append), {"total_tokens": 7})
        self.assertEqual("".join(written), "Hello")

    def test_empty_stream_does_not_fabricate_usage(self):
        self.assertIsNone(demo.stream_response([], lambda _: None))

    def test_real_sdk_errors_are_not_converted_to_fake_success(self):
        import httpx
        from openai import NotFoundError, PermissionDeniedError
        response = httpx.Response(403, request=httpx.Request("POST", "https://example.org"))
        client = Mock()
        client.chat.completions.create.side_effect = PermissionDeniedError(
            "denied", response=response, body={"error": "missing role"})
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(demo.probe(client, "alias", 403))
            self.assertFalse(demo.probe(client, "alias", 404))
            client.chat.completions.create.side_effect = NotFoundError(
                "missing", response=httpx.Response(404, request=response.request), body={})
            self.assertTrue(demo.probe(client, "missing", 404))
        self.assertIn("missing role", output.getvalue())

    def test_missing_configuration_is_noninteractive(self):
        with self.assertRaises(SystemExit):
            demo.main(["--endpoint", ""])


if __name__ == "__main__":
    unittest.main()
