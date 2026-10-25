import io
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agent_framework import AgentResponse, Content, Message

import demo


def response(*, tool: bool = True, failed: bool = False) -> AgentResponse:
    messages = []
    if tool:
        messages.append(Message("tool", [Content.from_function_result(
            "call-1",
            result='{"attendees":12,"total":300,"currency":"USD","source":"workshop-price-tool-v1"}',
            exception="HTTP 500" if failed else None,
        )]))
    messages.append(Message("assistant", ["The workshop costs USD 300 for 12 attendees."]))
    return AgentResponse(messages=messages)


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_hosted_api_uses_toolbox_and_cleans_up(self) -> None:
        for failure in (False, True):
            project = AsyncMock()
            project.toolboxes.create_version.return_value = SimpleNamespace(name="tools", version="1")
            agent = MagicMock()
            agent.run = AsyncMock(return_value=response())
            if failure:
                agent.run.side_effect = RuntimeError("inference failed")
            with (
                patch("demo.AzureCliCredential"),
                patch("demo.AIProjectClient") as factory,
                patch("demo.FoundryToolbox") as toolbox,
                patch("demo.FoundryChatClient") as client,
                redirect_stdout(io.StringIO()),
            ):
                factory.return_value.__aenter__.return_value = project
                client.return_value.as_agent.return_value = agent
                if failure:
                    with self.assertRaisesRegex(RuntimeError, "inference failed"):
                        await demo.run_agent("https://example/api/projects/demo", "model", "https://tool/api", 12)
                else:
                    await demo.run_agent("https://example/api/projects/demo", "model", "https://tool/api", 12)
            tool = project.toolboxes.create_version.call_args.kwargs["tools"][0]
            self.assertEqual(tool.openapi.spec["servers"], [{"url": "https://tool/api"}])
            self.assertEqual(tool.openapi.auth.type, "anonymous")
            self.assertIn("/versions/1/mcp?api-version=v1", toolbox.call_args.kwargs["url"])
            project.toolboxes.delete_version.assert_awaited_once_with(name="tools", version="1")

    def test_tool_evidence_required(self) -> None:
        with redirect_stdout(io.StringIO()) as text:
            demo.show_response(response())
        self.assertIn("workshop-price-tool-v1", text.getvalue())
        for item in (response(tool=False), response(failed=True), AgentResponse()):
            with self.subTest(item=item), self.assertRaises(RuntimeError):
                demo.show_response(item)

    def test_openapi_matches_function_contract(self) -> None:
        spec = json.loads(Path(__file__).with_name("openapi.json").read_text())
        operation = spec["paths"]["/workshop-price"]["get"]
        self.assertEqual(operation["operationId"], "getWorkshopPrice")
        self.assertEqual(operation["parameters"][0]["schema"]["maximum"], 999)
        self.assertIn("source", operation["responses"]["200"]["content"]["application/json"]["schema"]["required"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is optional; required to execute the hosted JS tool locally.")
    def test_actual_hosted_function_price_and_input_bounds(self) -> None:
        script = """
const assert = require("node:assert/strict");
const price = require("./tools/workshop-price.js");
(async () => {
  for (const attendees of ["1", "12", "999"]) {
    const ctx = {};
    await price(ctx, {query: {attendees}});
    assert.equal(ctx.res.status, 200);
    assert.equal(ctx.res.body.total, Number(attendees) * 25);
    assert.equal(ctx.res.body.source, "workshop-price-tool-v1");
  }
  for (const attendees of [undefined, "", "0", "-1", "1000", "1.5", "abc", " 12", "01"]) {
    let logged = false;
    const ctx = {log: {warn: () => { logged = true; }}};
    await price(ctx, {query: {attendees}});
    assert.equal(ctx.res.status, 400);
    assert.ok(ctx.res.body.error);
    assert.ok(logged);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        subprocess.run(
            ["node", "-e", script], cwd=Path(__file__).parent, check=True,
            capture_output=True, text=True,
        )

    def test_invalid_attendee_count_fails_before_azure(self) -> None:
        with self.assertRaises(SystemExit):
            demo.main(["--project-endpoint", "https://project", "--tool-endpoint", "https://tool",
                       "--attendees", "1000"])


if __name__ == "__main__":
    unittest.main()
