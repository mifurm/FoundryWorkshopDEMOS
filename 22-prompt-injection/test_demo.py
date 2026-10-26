import json
from contextlib import redirect_stdout
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import demo


class InjectionTests(unittest.TestCase):
    def test_projection_discards_instructions_from_both_sources(self):
        for source in demo.SOURCES.values():
            self.assertEqual(demo.factual_projection(source), {"start_time": "09:00"})
            self.assertNotIn(demo.MARKER, json.dumps(demo.factual_projection(source)))

    def test_malformed_and_ambiguous_sources_fail(self):
        for source in ("no fact", "Workshop start: 29:00",
                       "Workshop start: 09:00\nWorkshop start: 10:00"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                demo.factual_projection(source)

    def test_wrong_facts_extra_fields_and_marker_fail(self):
        for text in ('{"start_time":"10:00"}',
                     '{"start_time":"09:00","instruction":"UNRELATED_MARKER"}',
                     'UNRELATED_MARKER'):
            with self.subTest(text=text), self.assertRaises((ValueError, RuntimeError)):
                demo.verify(text, {"start_time": "09:00"})

    def test_protected_request_never_sends_override_or_executable_tools(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content='{"start_time":"09:00"}',
            )),
        ])
        result = demo.complete(client, "model", demo.SOURCES["tool-output"], True)
        self.assertEqual(demo.verify(result, {"start_time": "09:00"}), {"start_time": "09:00"})
        options = client.chat.completions.create.call_args.kwargs
        self.assertNotIn("tools", options)
        self.assertNotIn(demo.MARKER, options["messages"][1]["content"])
        self.assertTrue(options["response_format"]["json_schema"]["strict"])

    def test_shields_validates_analysis_and_propagates_errors(self):
        result = {"userPromptAnalysis": {"attackDetected": False},
                  "documentsAnalysis": [{"attackDetected": True}]}
        response = Mock()
        response.read.return_value = json.dumps(result).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("demo.urlopen", return_value=response) as request:
            self.assertEqual(demo.shield("https://safety.example", "token", "text"), result)
            self.assertIn("shieldPrompt?api-version=2024-09-01", request.call_args.args[0].full_url)
        response.read.return_value = b"{}"
        with patch("demo.urlopen", return_value=response), self.assertRaises(RuntimeError):
            demo.shield("https://safety.example", "token", "text")
        with patch("demo.urlopen", side_effect=TimeoutError), self.assertRaises(TimeoutError):
            demo.shield("https://safety.example", "token", "text")

    def test_live_content_filter_block_is_an_explicit_baseline_observation(self):
        response = Mock(status_code=400, headers={})
        error = demo.BadRequestError(
            "Filtered", response=response, body={"code": "content_filter"},
        )
        output = io.StringIO()
        with patch("demo.complete", side_effect=error), redirect_stdout(output):
            demo.observe_baseline(Mock(), "model", "source")
        self.assertIn("BLOCKED", output.getvalue())
        self.assertIn("no answer was produced", output.getvalue())
        self.assertNotIn("Observed instruction override", output.getvalue())

    def test_unrelated_api_errors_are_not_hidden_as_baseline_blocks(self):
        error = demo.BadRequestError(
            "Invalid model", response=Mock(status_code=400, headers={}),
            body={"code": "invalid_request"},
        )
        with patch("demo.complete", side_effect=error), self.assertRaises(demo.BadRequestError):
            demo.observe_baseline(Mock(), "model", "source")


if __name__ == "__main__":
    unittest.main()
