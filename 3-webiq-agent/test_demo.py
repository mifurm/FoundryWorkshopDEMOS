import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openai.types.responses import Response

import demo


def response(
    *, search: bool = True, citation: bool = True, status: str = "completed",
    search_status: str = "completed", text: str = "Foundry routes prompts to models.",
) -> Response:
    output = []
    if search:
        output.append({
            "id": "search", "type": "web_search_call", "status": search_status,
            "action": {"type": "search", "query": "Foundry model router"},
        })
    output.append({
        "id": "message", "type": "message", "role": "assistant", "status": "completed",
        "content": [{
            "type": "output_text", "text": text,
            "annotations": [{
                "type": "url_citation", "start_index": 0, "end_index": 6,
                "title": "Microsoft Learn", "url": "https://learn.microsoft.com/azure/foundry/",
            }] if citation else [],
        }],
    })
    return Response.model_validate({
        "id": "response", "created_at": 0, "model": "gpt-5-mini", "object": "response",
        "status": status, "output": output, "parallel_tool_calls": True,
        "tool_choice": "required", "tools": [],
    })


class WebSearchTests(unittest.TestCase):
    def test_prints_real_search_count_answer_and_citations(self) -> None:
        with redirect_stdout(io.StringIO()) as text:
            demo.show_response(response())
        self.assertIn("Web searches: 1", text.getvalue())
        self.assertIn("Foundry routes prompts", text.getvalue())
        self.assertIn("https://learn.microsoft.com", text.getvalue())
        self.assertIn("Output types: ['web_search_call', 'message']", text.getvalue())
        self.assertIn("Search calls: 1", text.getvalue())
        self.assertIn("Search statuses: ['completed']", text.getvalue())
        self.assertIn("URL citations: 1", text.getvalue())
        self.assertNotIn("Raw response JSON:", text.getvalue())

    def test_rejects_ungrounded_or_incomplete_answers(self) -> None:
        for item in (response(search=False), response(citation=False), response(status="incomplete"), response(text="")):
            with self.subTest(item=item), redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                demo.show_response(item)

    def test_missing_evidence_is_distinguished_and_reported_before_failure(self) -> None:
        for search, citation in ((False, True), (True, False), (False, False)):
            with self.subTest(search=search, citation=citation), redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(RuntimeError, "Grounding evidence missing") as error:
                    demo.show_response(response(search=search, citation=citation))
                self.assertEqual("web_search_call output" in str(error.exception), not search)
                self.assertEqual("url_citation annotations" in str(error.exception), not citation)
                self.assertIn(f"Search calls: {int(search)}", output.getvalue())
                self.assertIn(f"URL citations: {int(citation)}", output.getvalue())

    def test_plain_text_urls_do_not_substitute_for_citation_annotations(self) -> None:
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "url_citation annotations"):
            demo.show_response(response(citation=False, text="See https://learn.microsoft.com/azure/foundry/"))

    def test_failed_or_unfinished_searches_are_rejected_with_status(self) -> None:
        for status in ("failed", "in_progress", "searching", "incomplete"):
            with self.subTest(status=status), redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(RuntimeError, status):
                    demo.show_response(response(search_status=status))
                self.assertIn(f"Search statuses: ['{status}']", output.getvalue())

    def test_debug_json_is_available_on_success_and_validation_failure(self) -> None:
        for valid in (True, False):
            with self.subTest(valid=valid), redirect_stdout(io.StringIO()) as output:
                item = response(search=valid, citation=valid)
                if valid:
                    demo.show_response(item, debug_response=True)
                else:
                    with self.assertRaisesRegex(RuntimeError, "Grounding evidence missing"):
                        demo.show_response(item, debug_response=True)
                self.assertIn("Raw response JSON:", output.getvalue())
                self.assertIn(item.model_dump_json(indent=2), output.getvalue())

    def test_temporary_agent_deleted_on_success_and_failure(self) -> None:
        for failure in ("", "service", "validation"):
            project = MagicMock()
            project.agents.create_version.return_value = SimpleNamespace(name="test-agent", version="1")
            client = project.get_openai_client.return_value.__enter__.return_value
            if failure == "service":
                client.responses.create.side_effect = RuntimeError("service failed")
            else:
                client.responses.create.return_value = response(search=failure != "validation")
            with (
                patch("demo.AzureCliCredential"),
                patch("demo.AIProjectClient") as factory,
                redirect_stdout(io.StringIO()),
            ):
                factory.return_value.__enter__.return_value = project
                args = ["--project-endpoint", "https://example/api/projects/demo", "--debug-response"]
                if failure:
                    message = "service failed" if failure == "service" else "Grounding evidence missing"
                    with self.assertRaisesRegex(RuntimeError, message):
                        demo.main(args)
                else:
                    demo.main(args)
            project.agents.delete_version.assert_called_once_with(
                agent_name="test-agent", agent_version="1",
            )
            self.assertEqual(client.responses.create.call_args.kwargs["tool_choice"], "required")
            definition = project.agents.create_version.call_args.kwargs["definition"]
            self.assertEqual(definition.tools[0].type, "web_search")

    def test_default_deployment_and_explicit_overrides(self) -> None:
        for environment, arguments, expected in (
            ({}, [], "gpt-5-mini"),
            ({"AZURE_OPENAI_DEPLOYMENT": "environment-model"}, [], "environment-model"),
            ({"AZURE_OPENAI_DEPLOYMENT": "environment-model"}, ["--deployment", "custom-model"], "custom-model"),
        ):
            with self.subTest(expected=expected):
                project = MagicMock()
                project.agents.create_version.return_value = SimpleNamespace(name="test-agent", version="1")
                client = project.get_openai_client.return_value.__enter__.return_value
                client.responses.create.return_value = response()
                with (
                    patch.dict("os.environ", environment, clear=True),
                    patch("demo.AzureCliCredential"),
                    patch("demo.AIProjectClient") as factory,
                    redirect_stdout(io.StringIO()),
                ):
                    factory.return_value.__enter__.return_value = project
                    demo.main(["--project-endpoint", "https://example/api/projects/demo", *arguments])
                self.assertEqual(project.agents.create_version.call_args.kwargs["definition"].model, expected)

    def test_missing_endpoint_fails_before_authentication(self) -> None:
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(SystemExit):
            demo.main([])


if __name__ == "__main__":
    unittest.main()
