import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import httpx

import demo


class KnowledgeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("az"), "Azure CLI/Bicep is required for the infrastructure contract.")
    def test_search_region_is_independent_of_foundry_region(self):
        compiled = subprocess.run(
            ["az", "bicep", "build", "--file", "infra/main.bicep", "--stdout"],
            cwd=Path(__file__).parent, check=True, capture_output=True, text=True,
        )
        template = json.loads(compiled.stdout)
        resources = {item["type"]: item for item in template["resources"]}
        search = resources["Microsoft.Search/searchServices"]
        self.assertEqual(template["parameters"]["searchLocation"]["defaultValue"], "westus2")
        self.assertEqual(search["location"], "[parameters('searchLocation')]")
        self.assertEqual(
            resources["Microsoft.CognitiveServices/accounts"]["location"], "[parameters('location')]",
        )
        self.assertEqual(search["sku"]["name"], "basic")
        self.assertTrue(search["properties"]["disableLocalAuth"])
        self.assertEqual(search["identity"]["type"], "SystemAssigned")
        account = resources["Microsoft.CognitiveServices/accounts"]
        project = resources["Microsoft.CognitiveServices/accounts/projects"]
        model = resources["Microsoft.CognitiveServices/accounts/deployments"]
        self.assertTrue(account["properties"]["allowProjectManagement"])
        self.assertEqual(account["identity"]["type"], "SystemAssigned")
        self.assertEqual(template["parameters"]["projectName"]["defaultValue"], "foundry-iq-demo")
        self.assertEqual(project["location"], account["location"])
        self.assertEqual(project["identity"]["type"], "SystemAssigned")
        self.assertTrue(any("Microsoft.CognitiveServices/accounts'" in dependency
                            for dependency in project["dependsOn"]))
        self.assertTrue(any("Microsoft.CognitiveServices/accounts/projects" in dependency
                            for dependency in model["dependsOn"]))
        self.assertIn("projectEndpoint", template["outputs"])
        self.assertIn("projectResourceId", template["outputs"])
        self.assertIn("services.ai.azure.com/api/projects/", template["outputs"]["projectEndpoint"]["value"])
        self.assertIn("parameters('projectName')", template["outputs"]["projectEndpoint"]["value"])
        project_role = next(
            item for item in template["resources"]
            if item["type"] == "Microsoft.Authorization/roleAssignments"
            and "Microsoft.CognitiveServices/accounts/projects" in item.get("scope", "")
        )
        self.assertIn("Microsoft.CognitiveServices/accounts/projects", project_role["scope"])
        self.assertIn("53ca6127-db72-4b80-b1b0-d745d6d5456d", template["variables"]["foundryUserRole"])

    def client(self, handle):
        return httpx.Client(
            base_url="https://search.example/",
            params={"api-version": demo.API_VERSION}, transport=httpx.MockTransport(handle),
        )

    def test_configures_real_index_source_and_synthesizing_knowledge_base(self):
        requests = []

        def handle(request):
            requests.append(request)
            self.assertEqual(request.url.params["api-version"], "2026-08-01-preview")
            if request.url.path.endswith("/docs/index"):
                return httpx.Response(200, json={"value": [{"status": True}] * 3})
            if request.url.path.endswith("/$count"):
                return httpx.Response(200, text="3")
            return httpx.Response(200, json={})

        with self.client(handle) as client:
            demo.configure(client, "https://model.openai.azure.com/", "chat")
        self.assertEqual(len(requests), 5)
        index = json.loads(requests[0].content)
        self.assertEqual(index["semantic"]["defaultConfiguration"], "workshop-semantic")
        source = json.loads(requests[-2].content)
        self.assertEqual(source["kind"], "searchIndex")
        self.assertEqual(source["searchIndexParameters"]["searchIndexName"], demo.INDEX)
        base = json.loads(requests[-1].content)
        self.assertEqual(base["outputMode"], "answerSynthesis")
        self.assertEqual(base["models"][0]["azureOpenAIParameters"]["deploymentId"], "chat")

    def test_failed_document_upload_is_not_success(self):
        def handle(request):
            return httpx.Response(200, json={"value": [{"status": False, "errorMessage": "bad field"}]})
        with self.client(handle) as client, self.assertRaisesRegex(RuntimeError, "indexing failed"):
            demo.configure(client, "https://model/", "chat")

    def test_index_visibility_wait_has_a_deadline(self):
        def handle(request):
            if request.url.path.endswith("/docs/index"):
                return httpx.Response(200, json={"value": [{"status": True}] * 3})
            if request.url.path.endswith("/$count"):
                return httpx.Response(200, text="0")
            return httpx.Response(200, json={})
        with self.client(handle) as client, patch("demo.monotonic", side_effect=[0, 61]):
            with self.assertRaises(TimeoutError):
                demo.configure(client, "https://model/", "chat")

    def test_retrieval_requests_and_displays_grounding_evidence(self):
        payload = {
            "response": [{"content": [{"type": "text", "text": "Friday at 10:00. [ref_id:0]"}]}],
            "activity": [{"type": "searchIndex", "knowledgeSourceName": demo.SOURCE}],
            "references": [{"id": "0", "docKey": "schedule"}],
        }

        def handle(request):
            body = json.loads(request.content)
            self.assertTrue(body["knowledgeSourceParams"][0]["alwaysQuerySource"])
            self.assertTrue(body["includeActivity"])
            self.assertEqual(body["retrievalReasoningEffort"], {"kind": "low"})
            return httpx.Response(200, json=payload)

        with self.client(handle) as client:
            self.assertEqual(demo.retrieve(client, demo.QUESTION), payload)

    def test_missing_evidence_activity_errors_and_http_errors_fail(self):
        for payload in ({}, {
            "response": [{"content": [{"type": "text", "text": "Answer"}]}],
            "activity": [{"error": {"message": "failed"}}], "references": [{"id": "0"}],
        }):
            with self.subTest(payload=payload):
                with self.client(lambda _: httpx.Response(200, json=payload)) as client:
                    with self.assertRaises(RuntimeError):
                        demo.retrieve(client, demo.QUESTION)
        with self.client(lambda _: httpx.Response(403)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                demo.retrieve(client, demo.QUESTION)

    def test_main_displays_project_but_retrieves_from_search(self):
        project = "https://model.services.ai.azure.com/api/projects/foundry-iq-demo"
        environment = {
            "AZURE_SEARCH_ENDPOINT": "https://search.example/",
            "AZURE_OPENAI_ENDPOINT": "https://model.openai.azure.com/",
            "AZURE_OPENAI_DEPLOYMENT": "chat",
        }
        for project_endpoint in (None, project):
            with self.subTest(project_endpoint=project_endpoint):
                settings = dict(environment)
                if project_endpoint:
                    settings["FOUNDRY_PROJECT_ENDPOINT"] = project_endpoint
                with (
                    patch.dict(os.environ, settings, clear=True),
                    patch("sys.argv", ["demo.py"]),
                    patch("demo.AzureCliCredential") as credential,
                    patch("demo.httpx.Client") as client,
                    patch("demo.configure") as configure,
                    patch("demo.retrieve") as retrieve,
                    redirect_stdout(io.StringIO()) as output,
                ):
                    demo.main()
                credential.return_value.__enter__.return_value.get_token.assert_called_once_with(
                    "https://search.azure.com/.default",
                )
                self.assertEqual(client.call_args.kwargs["base_url"], "https://search.example/")
                configure.assert_called_once_with(
                    client.return_value.__enter__.return_value, environment["AZURE_OPENAI_ENDPOINT"], "chat",
                )
                retrieve.assert_called_once_with(client.return_value.__enter__.return_value, demo.QUESTION)
                if project_endpoint:
                    self.assertIn(project_endpoint, output.getvalue())
                else:
                    self.assertNotIn("Foundry project:", output.getvalue())

    def test_rejects_nonproject_endpoint_before_authentication(self):
        for endpoint in ("", "http://model.services.ai.azure.com/api/projects/demo",
                         "https://model.openai.azure.com/", "https://search.search.windows.net/"):
            with (
                self.subTest(endpoint=endpoint),
                patch.dict(os.environ, {}, clear=True),
                patch("sys.argv", [
                    "demo.py", "--search-endpoint", "https://search.example/",
                    "--endpoint", "https://model.openai.azure.com/",
                    "--project-endpoint", endpoint,
                ]),
                patch("demo.AzureCliCredential") as credential,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                demo.main()
            credential.assert_not_called()


if __name__ == "__main__":
    unittest.main()
