import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openai.types.responses import Response

import demo


def response(*, search: bool = True, citation: bool = True, status: str = "completed") -> Response:
    output = []
    if search:
        output.append({
            "id": "search", "type": "web_search_call", "status": "completed",
            "action": {"type": "search", "query": "Foundry model router"},
        })
    output.append({
        "id": "message", "type": "message", "role": "assistant", "status": "completed",
        "content": [{
            "type": "output_text", "text": "Foundry routes prompts to models.",
            "annotations": [{
                "type": "url_citation", "start_index": 0, "end_index": 6,
                "title": "Microsoft Learn", "url": "https://learn.microsoft.com/azure/foundry/",
            }] if citation else [],
        }],
    })
    return Response.model_validate({
        "id": "response", "created_at": 0, "model": "gpt-4.1-mini", "object": "response",
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

    def test_rejects_ungrounded_or_incomplete_answers(self) -> None:
        for item in (response(search=False), response(citation=False), response(status="incomplete")):
            with self.subTest(item=item), self.assertRaises(RuntimeError):
                demo.show_response(item)

    def test_temporary_agent_deleted_on_success_and_failure(self) -> None:
        for failure in (False, True):
            project = MagicMock()
            project.agents.create_version.return_value = SimpleNamespace(name="test-agent", version="1")
            client = project.get_openai_client.return_value.__enter__.return_value
            if failure:
                client.responses.create.side_effect = RuntimeError("service failed")
            else:
                client.responses.create.return_value = response()
            with (
                patch("demo.AzureCliCredential"),
                patch("demo.AIProjectClient") as factory,
                redirect_stdout(io.StringIO()),
            ):
                factory.return_value.__enter__.return_value = project
                args = ["--project-endpoint", "https://example/api/projects/demo"]
                if failure:
                    with self.assertRaisesRegex(RuntimeError, "service failed"):
                        demo.main(args)
                else:
                    demo.main(args)
            project.agents.delete_version.assert_called_once_with(
                agent_name="test-agent", agent_version="1",
            )
            self.assertEqual(client.responses.create.call_args.kwargs["tool_choice"], "required")
            definition = project.agents.create_version.call_args.kwargs["definition"]
            self.assertEqual(definition.tools[0].type, "web_search")

    def test_missing_endpoint_fails_before_authentication(self) -> None:
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(SystemExit):
            demo.main([])


if __name__ == "__main__":
    unittest.main()
