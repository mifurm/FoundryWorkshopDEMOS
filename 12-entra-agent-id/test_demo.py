import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import parse_qs
from unittest.mock import AsyncMock, Mock, patch

import httpx

import demo


TENANT = "11111111-1111-1111-1111-111111111111"
BLUEPRINT = "22222222-2222-2222-2222-222222222222"
AGENT = "33333333-3333-3333-3333-333333333333"


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_stage_flow_impersonates_child_not_blueprint(self):
        requests = []

        def handle(request):
            requests.append(parse_qs(request.content.decode()))
            self.assertNotIn("test-secret", str(request.url))
            return httpx.Response(200, json={"access_token": "exchange" if len(requests) == 1 else "agent-token"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            token = await demo.agent_token(client, TENANT, BLUEPRINT, AGENT, "test-secret")
        self.assertEqual(token, "agent-token")
        self.assertEqual(requests[0]["client_id"], [BLUEPRINT])
        self.assertEqual(requests[0]["fmi_path"], [AGENT])
        self.assertEqual(requests[0]["scope"], ["api://AzureADTokenExchange/.default"])
        self.assertEqual(requests[1]["client_id"], [AGENT])
        self.assertEqual(requests[1]["client_assertion"], ["exchange"])
        self.assertEqual(requests[1]["scope"], ["https://cognitiveservices.azure.com/.default"])
        self.assertNotIn("client_secret", requests[1])

    async def test_empty_tokens_and_authentication_failures_are_errors(self):
        for status, payload in ((200, {}), (403, {"error": "denied"})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda _: httpx.Response(status, json=payload))) as client:
                with self.subTest(status=status), self.assertRaises((RuntimeError, httpx.HTTPStatusError)):
                    await demo.agent_token(client, TENANT, BLUEPRINT, AGENT, "test-secret")

    def test_invalid_ids_and_identity_reuse_are_rejected(self):
        for values in (("bad", BLUEPRINT, AGENT, "secret"),
                       (TENANT, BLUEPRINT, BLUEPRINT, "secret"), (TENANT, BLUEPRINT, AGENT, "")):
            with self.subTest(values=values[:3]), self.assertRaises(ValueError):
                demo.validate_identity(*values)

    async def test_framework_uses_agent_token_and_never_logs_it(self):
        tutor = Mock(run=AsyncMock(return_value=Mock(text="A helpful agent.")))
        framework = Mock(as_agent=Mock(return_value=tutor))
        with (
            patch.dict(os.environ, {
                "ENTRA_TENANT_ID": TENANT, "ENTRA_BLUEPRINT_CLIENT_ID": BLUEPRINT,
                "ENTRA_AGENT_ID": AGENT, "ENTRA_BLUEPRINT_SECRET": "test-secret",
            }),
            patch("demo.agent_token", new=AsyncMock(return_value="agent-token")),
            patch("demo.AsyncOpenAI") as openai,
            patch("demo.OpenAIChatClient", return_value=framework),
            patch("builtins.print") as output,
        ):
            await demo.run_agent("https://model.openai.azure.com/", "chat")
        self.assertEqual(openai.call_args.kwargs["api_key"], "agent-token")
        tutor.run.assert_awaited_once()
        self.assertNotIn("agent-token", str(output.call_args_list))
        self.assertNotIn("test-secret", str(output.call_args_list))

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is needed for the tenant setup test.")
    def test_setup_creates_agent_graph_types_and_never_persists_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "setup_identity.ps1"
            shutil.copyfile(Path(demo.__file__).with_name("setup_identity.ps1"), script)
            command = r'''
                $ErrorActionPreference = "Stop"
                $global:graphCalls = [System.Collections.Generic.List[object]]::new()
                function Import-Module { param($Name, $ErrorAction) }
                function Connect-MgGraph {
                    param($TenantId, $ContextScope, [switch] $NoWelcome, $Scopes)
                    if ($Scopes -contains "Directory.AccessAsUser.All") { throw "Overbroad scope" }
                }
                function Invoke-MgGraphRequest {
                    param($Method, $Uri, $Headers, $ContentType, $Body)
                    if ($Method -eq "GET") { return @{ id = "sponsor" } }
                    if ($Headers["OData-Version"] -ne "4.0") { throw "Missing OData header" }
                    $global:graphCalls.Add(@{ Method = $Method; Uri = $Uri; Body = ($Body | ConvertFrom-Json) })
                    if ($Uri -match "agentIdentityBlueprint$") {
                        return @{ id = "blueprint-object"; appId = "blueprint-client" }
                    }
                    if ($Uri -match "agentIdentityBlueprintPrincipal$") { return @{ id = "blueprint-principal" } }
                    if ($Uri -match "Microsoft.Graph.AgentIdentity$") { return @{ id = "agent-id" } }
                    if ($Uri -match "/addPassword$") { return @{ secretText = "unit-test-secret" } }
                    if ($Method -ne "PATCH") { throw "Unexpected Graph call: $Uri" }
                }
                & $env:TEST_SETUP_SCRIPT -TenantId "11111111-1111-1111-1111-111111111111"
                $state = Get-Content (Join-Path (Split-Path $env:TEST_SETUP_SCRIPT) ".azure/identity.json") -Raw
                if ($state -match "unit-test-secret") { throw "Secret persisted" }
                if (($state | ConvertFrom-Json).agentIdentityId -ne "agent-id") { throw "Missing agent ID" }
                if ($env:ENTRA_BLUEPRINT_SECRET -ne "unit-test-secret") { throw "Secret missing from environment" }
                $agentCall = $global:graphCalls | Where-Object { $_.Uri -match "Microsoft.Graph.AgentIdentity$" }
                if ($agentCall.Body.agentIdentityBlueprintId -ne "blueprint-client") { throw "Wrong parent blueprint" }
                if ($global:graphCalls.Count -ne 5) { throw "Unexpected provisioning step count" }
            '''
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
                env={**os.environ, "TEST_SETUP_SCRIPT": str(script)},
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("unit-test-secret", result.stdout)


if __name__ == "__main__":
    unittest.main()
