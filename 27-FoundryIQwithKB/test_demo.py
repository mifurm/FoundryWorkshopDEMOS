import copy
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

import demo


def config():
    return demo.Config(
        endpoint="https://foundry.openai.azure.com",
        deployment="gpt-4.1-mini", embeddingDeployment="text-embedding-3-small",
        projectEndpoint="https://foundry.services.ai.azure.com/api/projects/surface-support",
        projectResourceId="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry/projects/surface-support",
        searchEndpoint="https://search.search.windows.net",
        searchResourceId="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Search/searchServices/search",
        storageEndpoint="https://storage.blob.core.windows.net",
        storageResourceId="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/storage",
    )


class DemoTests(unittest.TestCase):
    def test_managed_blob_sources_use_identity_embeddings_and_hourly_refresh(self):
        for container, name in demo.SOURCES.items():
            source = demo.source_definition(config(), container, name)
            self.assertEqual(source["kind"], "azureBlob")
            blob = source["azureBlobParameters"]
            self.assertEqual(blob["connectionString"], f"ResourceId={config().storageResourceId}")
            self.assertEqual(blob["containerName"], container)
            ingestion = blob["ingestionParameters"]
            self.assertEqual(ingestion["ingestionSchedule"], {"interval": "PT1H"})
            self.assertEqual(ingestion["contentExtractionMode"], "minimal")
            self.assertEqual(ingestion["embeddingModel"]["azureOpenAIParameters"]["modelName"],
                             "text-embedding-3-small")
            self.assertNotIn("ingestionPermissionOptions", ingestion)

    def test_seed_uploads_and_reads_back_all_eight_synthetic_documents(self):
        api = Mock(config=config())
        blobs = {}

        def request(method, url, scope, **kwargs):
            self.assertEqual(scope, demo.STORAGE_SCOPE)
            if method == "PUT":
                self.assertEqual(kwargs["headers"]["x-ms-blob-type"], "BlockBlob")
                blobs[url] = kwargs["content"]
            if url not in blobs:
                self.assertTrue(kwargs["allow_missing"])
                return SimpleNamespace(status_code=404, content=b"")
            return SimpleNamespace(status_code=200, content=blobs[url])

        api.request.side_effect = request
        with redirect_stdout(io.StringIO()):
            demo.seed_storage(api)
        self.assertEqual(len(blobs), 8)
        self.assertTrue(all(b"SYNTHETIC WORKSHOP DATA" in text for text in blobs.values()))
        api.request.reset_mock()
        with redirect_stdout(io.StringIO()):
            demo.seed_storage(api)
        self.assertEqual(api.request.call_count, 8)
        self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))

    def test_blob_readback_mismatch_is_an_error(self):
        api = Mock(config=config())
        api.request.return_value.content = b"wrong"
        with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
            demo.seed_storage(api)

    def test_agent_uses_real_kb_mcp_with_project_connection_not_search_index_tool(self):
        definition = demo.agent_definition(config()).as_dict()
        tool = definition["tools"][0]
        self.assertEqual(tool["type"], "mcp")
        self.assertEqual(tool["server_url"], config().mcp_endpoint)
        self.assertEqual(tool["project_connection_id"], demo.CONNECTION)
        self.assertEqual(tool["allowed_tools"], ["knowledge_base_retrieve"])
        self.assertEqual(tool["require_approval"], "never")
        self.assertIn("I don't know based on this knowledge base", definition["instructions"])

    def test_portal_search_and_identity_mcp_connections_are_both_created(self):
        api = Mock(config=config())
        api.connection.side_effect = lambda name, properties: {"properties": properties}
        result = demo.connect_project(api)
        self.assertEqual(result["search"]["properties"]["category"], "CognitiveSearch")
        self.assertEqual(result["mcp"]["properties"]["authType"], "ProjectManagedIdentity")
        self.assertEqual(result["mcp"]["properties"]["audience"], "https://search.azure.com/")

    def test_ingestion_waits_for_success_and_index_visibility(self):
        api = Mock()
        source = {"azureBlobParameters": {"createdResources": {"indexer": "managed", "index": "chunks"}}}
        api.search.side_effect = [
            {"lastResult": {"status": "inProgress"}},
            {"lastResult": {"status": "inProgress"}},
            {"lastResult": {"status": "success", "itemsFailed": 0, "warnings": []}},
        ]
        api.count.return_value = 4
        with patch("demo.sleep"), redirect_stdout(io.StringIO()):
            result = demo.wait_for_ingestion(api, source, 4, 30)
        self.assertEqual(result["chunks"], 4)
        self.assertTrue(all(call.args[0] == "GET" for call in api.search.call_args_list))

    def test_ingestion_errors_warnings_and_timeout_fail_explicitly(self):
        source = {"azureBlobParameters": {"createdResources": {"indexer": "i", "index": "x"}}}
        for last in (
            {"status": "persistentFailure"},
            {"status": "transientFailure"},
            {"status": "success", "errors": [{"message": "bad"}]},
            {"status": "success", "itemsFailed": 1},
            {"status": "success", "warnings": [{"message": "embedding failed"}]},
        ):
            api = Mock()
            api.search.side_effect = [
                {"lastResult": {"status": "inProgress"}}, {"lastResult": last},
            ]
            with self.subTest(last=last), self.assertRaises(RuntimeError):
                demo.wait_for_ingestion(api, source, 4, 30)
        api = Mock()
        api.search.return_value = {"lastResult": {"status": "inProgress"}}
        with patch("demo.monotonic", side_effect=[0, 31]), self.assertRaises(TimeoutError):
            demo.wait_for_ingestion(api, source, 4, 30)

    def test_refresh_does_not_accept_a_stale_successful_indexer_run(self):
        api = Mock()
        source = {"azureBlobParameters": {"createdResources": {"indexer": "i", "index": "x"}}}
        old = {"status": "success", "startTime": "old"}
        new = {"status": "success", "startTime": "new"}
        api.search.side_effect = [
            {"lastResult": old}, {}, {"lastResult": old}, {"lastResult": new},
        ]
        api.count.return_value = 4
        with patch("demo.sleep") as sleep, redirect_stdout(io.StringIO()):
            result = demo.wait_for_ingestion(api, source, 4, 30)
        sleep.assert_called_once_with(5)
        self.assertEqual(result["lastResult"]["startTime"], "new")

    def test_new_indexer_with_no_last_result_is_started_and_polled(self):
        api = Mock()
        source = {"azureBlobParameters": {"createdResources": {"indexer": "i", "index": "x"}}}
        api.search.side_effect = [
            {"lastResult": None}, {},
            {"lastResult": {"status": "success", "startTime": "new"}},
        ]
        api.count.return_value = 4
        with redirect_stdout(io.StringIO()):
            demo.wait_for_ingestion(api, source, 4, 30)
        self.assertIn(unittest.mock.call("POST", "indexers/i/run"), api.search.call_args_list)

    def test_configure_knowledge_keeps_two_managed_sources_and_extractive_retrieval(self):
        api = Mock(config=config())

        def search(method, path, **kwargs):
            if path.endswith("/docs/search"):
                container = "support-cases" if "support-cases" in path else "surface-kb"
                return {"value": [
                    {"blob_url": f"{config().storageEndpoint}/{container}/{document.name}",
                     "snippet": document.read_text()}
                    for document in (demo.HERE / "data" / container).glob("*.txt")
                ]}
            return {"kind": "azureBlob"}

        api.search.side_effect = search
        with patch("demo.wait_for_ingestion", side_effect=[
            {"chunks": 4, "resources": {"index": name + "-index"}} for name in demo.SOURCES.values()
        ]):
            demo.configure_knowledge(api, 30)
        body = next(
            call.kwargs["json"] for call in api.search.call_args_list
            if call.args == ("PUT", f"knowledgebases/{demo.BASE}")
        )
        self.assertEqual(len(body["knowledgeSources"]), 2)
        self.assertEqual(body["outputMode"], "extractiveData")
        self.assertEqual(body["retrievalReasoningEffort"], {"kind": "low"})
        self.assertEqual(body["models"][0]["azureOpenAIParameters"]["deploymentId"], config().deployment)

    def test_matching_chunk_count_does_not_hide_a_missing_source_document(self):
        api = Mock(config=config())
        api.search.side_effect = lambda method, path, **kwargs: (
            {"value": [{"blob_url": "https://wrong-blob", "snippet": "content"}] * 4}
            if path.endswith("/docs/search") else {"kind": "azureBlob"}
        )
        with patch("demo.wait_for_ingestion", return_value={"resources": {"index": "i"}, "chunks": 4}):
            with self.assertRaisesRegex(RuntimeError, "coverage mismatch"):
                demo.configure_knowledge(api, 30)

    def test_retrieve_requests_real_activity_and_source_references(self):
        api = Mock()
        api.search.return_value = {"response": [{"content": []}], "activity": [{"type": "search"}],
                                   "references": [{"id": "0"}]}
        with redirect_stdout(io.StringIO()):
            demo.retrieve(api, "question")
        params = api.search.call_args.kwargs["json"]
        self.assertEqual(len(params["knowledgeSourceParams"]), 2)
        self.assertTrue(all(source["kind"] == "azureBlob" and source["includeReferenceSourceData"]
                            for source in params["knowledgeSourceParams"]))
        api.search.return_value["activity"] = [{"error": {"message": "denied"}}]
        with self.assertRaisesRegex(RuntimeError, "contains errors"):
            demo.retrieve(api, "question")

    def test_response_requires_actual_successful_mcp_call_and_supported_source_ids(self):
        response = {
            "status": "completed", "output": [
                {"type": "mcp_call", "name": "knowledge_base_retrieve",
                 "output": '{"source": "CASE-1001.txt"}', "error": None},
                {"type": "message", "content": [
                    {"type": "output_text", "text": "Resolved with dock firmware (CASE-1001)."}]},
            ],
        }
        with redirect_stdout(io.StringIO()):
            demo.validate_agent_response(response)
        for variant in ("no-call", "error", "no-text", "invented-id", "incomplete"):
            changed = copy.deepcopy(response)
            if variant == "no-call":
                changed["output"].pop(0)
            elif variant == "error":
                changed["output"][0]["error"] = {"message": "403"}
            elif variant == "no-text":
                changed["output"][1]["content"] = []
            elif variant == "invented-id":
                changed["output"][1]["content"][0]["text"] = "CASE-9999"
            else:
                changed["status"] = "incomplete"
            with self.subTest(variant=variant), self.assertRaises(RuntimeError):
                demo.validate_agent_response(changed)
        response["output"][1]["content"][0]["text"] = "I don't know based on this knowledge base"
        with redirect_stdout(io.StringIO()):
            demo.validate_agent_response(response)

    def test_http_failure_is_explicit_and_does_not_retry_bad_requests(self):
        credential = Mock()
        credential.get_token.return_value.token = "test-token"
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(400, json={"error": "bad schema"})

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            api = demo.AzureAPI(client, credential, config())
            with self.assertRaisesRegex(RuntimeError, "400.*bad schema"):
                api.search("PUT", "knowledgesources/source", json={})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.params["api-version"], demo.API_VERSION)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer test-token")

    def test_missing_configuration_fails_without_azure_changes(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "Deploy lab 27"):
            demo.load_config(Path("/nonexistent/lab27-deployment.json"))

    def test_rbac_retry_is_visible_bounded_and_still_fails(self):
        credential = Mock()
        credential.get_token.return_value.token = "test-token"
        transport = httpx.MockTransport(lambda request: httpx.Response(403, text="denied"))
        with httpx.Client(transport=transport) as client, patch("demo.sleep") as sleep:
            api = demo.AzureAPI(client, credential, config())
            with redirect_stdout(io.StringIO()) as output, self.assertRaisesRegex(RuntimeError, "403"):
                api.search("GET", "knowledgebases/base")
        self.assertEqual(sleep.call_count, 10)
        self.assertEqual(credential.get_token.call_count, 11)
        self.assertIn("waiting 30s for RBAC", output.getvalue())


if __name__ == "__main__":
    unittest.main()
