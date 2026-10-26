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

    def test_no_tool_answer_does_not_require_grounding(self) -> None:
        for answer in (
            "I cannot browse the web.",
            "See https://learn.microsoft.com/azure/foundry/",
        ):
            with self.subTest(answer=answer), redirect_stdout(io.StringIO()) as output:
                item = response(search=False, citation=False, text=answer)
                demo.show_response(item, expect_web_search=False, debug_response=True)
                self.assertIn(answer, output.getvalue())
                self.assertIn("Search calls: 0", output.getvalue())
                self.assertIn("URL citations: 0", output.getvalue())
                self.assertIn("not verified search citations", output.getvalue())
                self.assertIn(item.model_dump_json(indent=2), output.getvalue())

    def test_no_tool_response_still_rejects_errors_and_unexpected_grounding(self) -> None:
        for item in (
            response(search=False, citation=False, status="incomplete"),
            response(search=False, citation=False, text=""),
            response(search=True, citation=False),
            response(search=False, citation=True),
        ):
            with self.subTest(item=item), redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                demo.show_response(item, expect_web_search=False)

    def test_temporary_agent_deleted_on_success_and_failure(self) -> None:
        for failure in ("", "search-service", "search-validation", "no-tool-service", "no-tool-validation"):
            project = MagicMock()
            project.agents.create_version.side_effect = [
                SimpleNamespace(name="test-agent", version="1"),
                SimpleNamespace(name="test-agent", version="2"),
            ]
            client = project.get_openai_client.return_value.__enter__.return_value
            grounded: Response | RuntimeError = response()
            no_tool: Response | RuntimeError = response(search=False, citation=False)
            if failure == "search-service":
                grounded = RuntimeError("service failed")
            elif failure == "search-validation":
                grounded = response(search=False)
            elif failure == "no-tool-service":
                no_tool = RuntimeError("service failed")
            elif failure == "no-tool-validation":
                no_tool = response(search=False, citation=False, status="incomplete")
            client.responses.create.side_effect = [grounded, no_tool]
            with (
                patch("demo.AzureCliCredential"),
                patch("demo.AIProjectClient") as factory,
                redirect_stdout(io.StringIO()),
            ):
                factory.return_value.__enter__.return_value = project
                args = ["--project-endpoint", "https://example/api/projects/demo", "--debug-response"]
                if failure:
                    if failure.endswith("service"):
                        message = "service failed"
                    elif failure == "search-validation":
                        message = "Grounding evidence missing"
                    else:
                        message = "Agent did not complete"
                    with self.assertRaisesRegex(RuntimeError, message):
                        demo.main(args)
                else:
                    with redirect_stdout(io.StringIO()) as output:
                        demo.main(args)
                    self.assertIn("=== WITH WebSearchTool() ===", output.getvalue())
                    self.assertIn("=== WITHOUT WebSearchTool() ===", output.getvalue())
                    self.assertIn("Comparison:", output.getvalue())
            expected_runs = 1 if failure.startswith("search-") else 2
            self.assertEqual(project.agents.delete_version.call_count, expected_runs)
            for index, call in enumerate(project.agents.delete_version.call_args_list, start=1):
                self.assertEqual(call.kwargs, {"agent_name": "test-agent", "agent_version": str(index)})
            requests = client.responses.create.call_args_list
            self.assertEqual(requests[0].kwargs["tool_choice"], "required")
            definitions = project.agents.create_version.call_args_list
            self.assertEqual(definitions[0].kwargs["definition"].tools[0].type, "web_search")
            if expected_runs == 2:
                self.assertEqual(requests[1].kwargs["tool_choice"], "none")
                self.assertEqual(requests[0].kwargs["input"], requests[1].kwargs["input"])
                self.assertNotIn("previous_response_id", requests[1].kwargs)
                self.assertEqual(definitions[1].kwargs["definition"].tools, [])
                self.assertEqual(
                    definitions[0].kwargs["agent_name"], definitions[1].kwargs["agent_name"],
                )
                self.assertEqual(
                    definitions[0].kwargs["definition"].instructions,
                    definitions[1].kwargs["definition"].instructions,
                )
                self.assertEqual(
                    definitions[0].kwargs["definition"].model,
                    definitions[1].kwargs["definition"].model,
                )

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
                client.responses.create.side_effect = [response(), response(search=False, citation=False)]
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
