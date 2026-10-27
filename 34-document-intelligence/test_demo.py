import json
from hashlib import sha256
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import RateLimitError

import demo


def extraction(document_id="clear"):
    expected = demo.EXPECTED[document_id]
    return {
        "source": demo.SOURCE, "document_id": document_id, "model_id": "prebuilt-invoice",
        "operation_id": "real-operation-id", "status": "succeeded", "page_count": 1,
        "document_sha256": sha256((demo.HERE / "samples" / f"{document_id}.pdf").read_bytes()).hexdigest(),
        "fields": {
            name: {"value": expected[name], "confidence": 0.95 if expected[name] is not None else 0,
                   "evidence": str(expected[name]) if expected[name] is not None else None,
                   "page": 1 if expected[name] is not None else None}
            for name in ("vendor", "invoice_id", "total")
        },
        "currency": expected["currency"], "needs_review": expected["total"] is None,
    }


class DocumentTests(unittest.TestCase):
    def test_exact_extraction_and_missing_total(self):
        for document_id in demo.EXPECTED:
            self.assertEqual(demo.validate_extraction(extraction(document_id), document_id), extraction(document_id))

    def test_bad_values_and_missing_evidence_are_rejected(self):
        for field, key, value in (
            ("total", "value", 999), ("total", "value", True), ("total", "confidence", -1),
            ("vendor", "confidence", 0.79), ("invoice_id", "evidence", None),
            ("invoice_id", "page", 2),
        ):
            result = extraction()
            result["fields"][field][key] = value
            with self.subTest(field=field, key=key), self.assertRaises(RuntimeError):
                demo.validate_extraction(result, "clear")

    def test_review_and_operation_evidence_required(self):
        for key, value in (("needs_review", 0), ("source", "fake"), ("operation_id", ""),
                           ("status", "running"), ("page_count", 2), ("currency", "EUR")):
            result = extraction()
            result[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                demo.validate_extraction(result, "clear")

    def test_missing_total_cannot_be_guessed(self):
        result = extraction("missing-total")
        result["fields"]["total"]["value"] = 125.5
        with self.assertRaisesRegex(RuntimeError, "accuracy"):
            demo.validate_extraction(result, "missing-total")

    def test_submitted_pdf_must_match_repository_bytes(self):
        result = extraction()
        result["document_sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "differs from the repository"):
            demo.validate_extraction(result, "clear")

    def test_corpus_and_all_tool_surfaces_include_twelve_pdfs(self):
        samples = demo.HERE / "samples"
        ids = set(demo.EXPECTED)
        self.assertEqual(len(ids), 12)
        self.assertEqual({file.stem for file in samples.glob("*.pdf")}, ids)
        self.assertEqual(len({value["invoice_id"] for value in demo.EXPECTED.values()}), 12)
        self.assertEqual(sum(value["total"] is None for value in demo.EXPECTED.values()), 3)
        self.assertEqual(demo.EXPECTED["zero-total"]["total"], 0)
        spec = json.loads((demo.HERE / "openapi.json").read_text())
        self.assertEqual(set(spec["paths"]["/invoices/{document_id}"]["get"]["parameters"][0]["schema"]["enum"]), ids)
        source = (demo.HERE / "infra" / "main.bicep").read_text()
        deployed = re.findall(r"loadFileAsBase64\('\.\./samples/([^']+)\.pdf'\)", source)
        self.assertEqual(len(deployed), 12)
        self.assertEqual(set(deployed), ids)
        for document_id, sample in demo.EXPECTED.items():
            with self.subTest(document_id=document_id):
                pdf = (samples / f"{document_id}.pdf").read_bytes()
                self.assertIn(sample["invoice_id"].encode(), pdf)
                self.assertIn(sample["vendor"].encode(), pdf)
                self.assertIn(b"SYNTHETIC TRAINING DOCUMENT - NOT PAYABLE", pdf)

    def test_grounded_answer_and_strict_shape(self):
        for document_id in demo.EXPECTED:
            result = extraction(document_id)
            answer = {"document_id": document_id,
                      **{name: field["value"] for name, field in result["fields"].items()},
                      "currency": result["currency"], "needs_review": result["needs_review"],
                      "operation_id": result["operation_id"]}
            demo.validate_answer(json.dumps(answer), result)
            for key, value in (("total", 3), ("operation_id", "invented"),
                               ("needs_review", int(result["needs_review"])), ("extra", 1)):
                bad = {**answer, key: value}
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    demo.validate_answer(json.dumps(bad), result)

    def test_actual_cloud_tool_evidence_required(self):
        call = {"type": "openapi_call", "status": "completed", "call_id": "call-real",
                "name": "invoice_documents_extract_invoice", "arguments": '{"document_id":"clear"}'}
        result = {**call, "type": "openapi_call_output",
                  "output": json.dumps({"response": json.dumps(extraction())})}
        raw = {"output": [call, result]}
        demo.tool_extraction(raw, "clear")
        for output in ([], raw["output"] * 2,
                       [{**call, "status": "failed"}, result],
                       [call, {**result, "call_id": "unrelated"}],
                       [{**call, "arguments": '{"document_id":"missing-total"}'}, result],
                       [call, {**result, "output": '{"response":"[]"}'}],
                       [{"type": "message", "text": "I called Document Intelligence."}]):
            with self.assertRaises(RuntimeError):
                demo.tool_extraction({"output": output}, "clear")

    def test_exactly_one_final_text_message_is_required(self):
        item = {"type": "message", "status": "completed",
                "content": [{"type": "output_text", "text": "answer"}]}
        self.assertEqual(demo.response_text({"output": [item]}), "answer")
        for output in ([], [item, item], [{**item, "status": "incomplete"}],
                       [{**item, "content": [{"type": "refusal", "refusal": "no"}]}]):
            with self.assertRaises(RuntimeError):
                demo.response_text({"output": output})

    def test_persistent_sdk_definition_uses_secure_cloud_tool(self):
        config = {"deployment": "gpt-4.1-mini", "toolEndpoint": "https://test.azurewebsites.net/api",
                  "projectResourceId": "/subscriptions/test/resourceGroups/test/providers/Microsoft.CognitiveServices/accounts/a/projects/p"}
        value = demo.agent_definition(config).as_dict()
        self.assertEqual(value["kind"], "prompt")
        tool = value["tools"][0]
        self.assertEqual(tool["type"], "openapi")
        self.assertEqual(tool["openapi"]["auth"]["type"], "project_connection")
        spec = tool["openapi"]["spec"]
        self.assertEqual(spec["components"]["securitySchemes"]["functionKey"]["name"], "x-functions-key")
        self.assertNotIn("credentials", json.dumps(value))
        original = json.loads((demo.HERE / "openapi.json").read_text())
        self.assertNotEqual(original["servers"], spec["servers"])

    def test_only_runtime_startup_400_is_retried(self):
        credential = Mock()
        credential.get_token.return_value.token = "test-token"
        request = httpx.Request("POST", "https://management.azure.com/test")
        for error, retries in (("Encountered an error (InternalServerError) from host runtime.", True),
                               ("Invalid resource", False)):
            client = Mock()
            client.request.side_effect = [
                httpx.Response(400, json={"Message": error}, request=request),
                httpx.Response(200, json={"ready": True}, request=request),
            ]
            with patch("demo.sleep") as wait:
                if retries:
                    self.assertEqual(demo.arm_request(
                        client, credential, "POST", "/test/host/default/listkeys?api-version=test",
                    ), {"ready": True})
                    wait.assert_called_once_with(30)
                else:
                    with self.assertRaises(httpx.HTTPStatusError):
                        demo.arm_request(client, credential, "POST",
                                         "/test/host/default/listkeys?api-version=test")
                    wait.assert_not_called()

    def test_model_throttling_honors_service_delay_and_bounds_retries(self):
        request = httpx.Request("POST", "https://example.test/responses")
        for headers, delay in (({"retry-after": "2"}, 2),
                               ({"retry-after-ms": "1500"}, 1.5), ({}, 30)):
            error = RateLimitError("Throttled", response=httpx.Response(
                429, headers=headers, request=request), body=None)
            client = Mock()
            client.responses.with_raw_response.create.side_effect = [
                error, Mock(http_response=Mock(json=lambda: {"status": "completed"})),
            ]
            with patch("demo.sleep") as wait:
                self.assertEqual(demo.request_invoice(client, "clear"), {"status": "completed"})
                wait.assert_called_once_with(delay)
            client.responses.with_raw_response.create.side_effect = error
            with patch("demo.sleep") as wait, self.assertRaises(RateLimitError):
                demo.request_invoice(client, "clear")
            self.assertEqual(wait.call_count, 5)

    def test_node_tool_tests(self):
        subprocess.run(["node", "--test", "tools/test-extract-invoice.js"], cwd=Path(__file__).parent, check=True)


if __name__ == "__main__":
    unittest.main()
