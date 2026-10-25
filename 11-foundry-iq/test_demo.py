import json
import unittest
from unittest.mock import patch

import httpx

import demo


class KnowledgeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
