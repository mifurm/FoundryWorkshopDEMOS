import base64
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import threading
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import ui


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ui.ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_ui_and_valid_question(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Foundry workshop | Learn by doing", page)
        with patch("ui.ask", return_value="CASE-1001") as ask:
            status, body = self.request("POST", "/api/ask", json.dumps({"question": " question "}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"answer": "CASE-1001"})
        ask.assert_called_once_with("question")

    def test_ui_has_unique_ids_and_accessible_media_labels(self):
        class Structure(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = {}
                self.duplicates = []
                self.labels = []
                self.audio_names = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                element_id = attrs.get("id")
                if element_id:
                    if element_id in self.elements:
                        self.duplicates.append(element_id)
                    self.elements[element_id] = tag
                if tag == "label" and attrs.get("for"):
                    self.labels.append(attrs["for"])
                if tag == "audio":
                    self.audio_names.append(attrs.get("aria-labelledby", ""))

        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        structure = Structure()
        structure.feed(page.decode("utf-8"))
        self.assertEqual(structure.duplicates, [])
        for target in structure.labels:
            with self.subTest(label=target):
                self.assertIn(structure.elements.get(target), {"button", "input", "select", "textarea"})
        for name in structure.audio_names:
            with self.subTest(audio_name=name):
                self.assertTrue(name)
                self.assertTrue(all(target in structure.elements for target in name.split()))

    def test_invalid_and_cross_origin_requests_are_rejected(self):
        for body in ('{"question": ""}', '{"question": 1}', '[]', 'not json'):
            with self.subTest(body=body):
                status, _ = self.request("POST", "/api/ask", body)
                self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/ask", '{"question":"hi"}',
                                 {"Origin": "https://example.com"})
        self.assertEqual(status, 403)

    def test_lab_guides_serve_only_approved_readmes(self):
        status, guide = self.request("GET", "/guides/31")
        self.assertEqual(status, 200)
        self.assertIn(b"Native Foundry agent memory", guide)
        self.assertEqual(self.request("GET", "/guides/10")[0], 200)
        self.assertEqual(self.request("GET", "/guides/31/../demo.py")[0], 404)
        self.assertEqual(self.request("GET", "/guides/0")[0], 404)

    def test_agent_failure_is_not_reported_as_success(self):
        with patch("ui.ask", side_effect=RuntimeError("retrieval failed")):
            status, body = self.request("POST", "/api/ask", '{"question":"hi"}')
        self.assertEqual(status, 502)
        self.assertIn("retrieval failed", json.loads(body)["error"])

    def test_hosted_mode_rejects_other_hosts_and_origins(self):
        with patch.dict(os.environ, {"UI_AZURE_HOSTED": "1", "WEBSITE_HOSTNAME": "lab.azurewebsites.net"}):
            headers = {"Host": "lab.azurewebsites.net", "Origin": "https://lab.azurewebsites.net"}
            self.assertEqual(self.request("GET", "/", headers=headers)[0], 200)
            headers["Origin"] = "https://evil.example"
            self.assertEqual(self.request("GET", "/", headers=headers)[0], 403)
            self.assertEqual(self.request("GET", "/")[0], 403)

    def test_only_one_question_runs_at_a_time(self):
        ui.operation.acquire()
        try:
            status, _ = self.request("POST", "/api/ask", '{"question":"hi"}')
            self.assertEqual(status, 409)
        finally:
            ui.operation.release()

    def test_skill_comparison_uses_title_and_returns_real_results(self):
        result = {"skill_version": "7", "comparisons": [{"with_skill": True}]}
        with patch("ui.compare_skills", return_value=result) as compare:
            status, body = self.request("POST", "/api/skills", '{"title":" Workshop "}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), result)
        compare.assert_called_once_with("Workshop")
        for value in ("", " " * 201, "x" * 201):
            status, _ = self.request("POST", "/api/skills", json.dumps({"title": value}))
            self.assertEqual(status, 400)

    def test_basics_concepts_are_real_local_calculations(self):
        body = json.dumps({"mode": "concepts", "temperature": 1, "top_p": 0.8})
        status, response = self.request("POST", "/api/basics", body)
        self.assertEqual(status, 200)
        result = json.loads(response)
        self.assertEqual(result["nucleus_indexes"], [0, 1])
        self.assertAlmostEqual(result["temperature_distribution"][0], 0.5)
        status, _ = self.request("POST", "/api/basics", '{"mode":"concepts","top_p":true}')
        self.assertEqual(status, 400)

    def test_basics_rejects_unknown_mode(self):
        status, _ = self.request("POST", "/api/basics", '{"mode":"delete"}')
        self.assertEqual(status, 400)

    def test_router_preset_is_routed_and_unknown_preset_is_rejected(self):
        with patch("ui.route_model", return_value={"serving_model": "actual"}) as route:
            status, body = self.request("POST", "/api/router", '{"preset":"Simple"}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["serving_model"], "actual")
        route.assert_called_once_with({"preset": "Simple"})
        status, _ = self.request("POST", "/api/router", '{"preset":"delete"}')
        self.assertEqual(status, 400)

    def test_workflow_uses_real_lab_adapter_and_returns_both_stages(self):
        lab = MagicMock()
        lab.run_workflow = AsyncMock(return_value={"writer": "Draft", "editor": "Edited"})
        with patch.dict(os.environ, {"UI_WORKFLOW_PROJECT_ENDPOINT": "https://example.test/project",
                                    "UI_AZURE_HOSTED": "1", "WEBSITE_HOSTNAME": "lab.azurewebsites.net"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AsyncManagedIdentityCredential") as credential:
            headers = {"Host": "lab.azurewebsites.net", "Origin": "https://lab.azurewebsites.net"}
            status, body = self.request("POST", "/api/workflow", '{"prompt":" Invite people "}', headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"writer": "Draft", "editor": "Edited"})
            lab.run_workflow.assert_awaited_once_with(
                "https://example.test/project", "gpt-4.1-mini", "Invite people", credential.return_value)
            lab.run_workflow.side_effect = ui.AgentFrameworkException("workflow failed")
            status, body = self.request("POST", "/api/workflow", '{"prompt":"Invite people"}', headers)
            self.assertEqual(status, 502)
            self.assertIn("workflow failed", json.loads(body)["error"])
        self.assertEqual(self.request("POST", "/api/workflow", '{"prompt":" " }')[0], 400)

    def test_web_search_uses_only_fixed_public_prompt(self):
        lab = MagicMock()
        lab.DEFAULT_PROMPT = "Fixed public question"
        lab.compare.return_value = {"with_search": {"citations": [{"url": "https://example.test"}]},
                                    "without_search": {"citations": []}}
        with patch.dict(os.environ, {"UI_WEB_SEARCH_PROJECT_ENDPOINT": "https://example.test/project"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.AIProjectClient") as factory:
            status, body = self.request("POST", "/api/web-search", '{"prompt":"ignored"}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["without_search"]["citations"], [])
            lab.compare.assert_called_once_with(factory.return_value.__enter__.return_value,
                                                "gpt-5-mini", "Fixed public question")

    def test_orchestration_routes_pattern_and_rejects_all_or_unknown(self):
        lab = MagicMock()
        lab.run_samples = AsyncMock(return_value=[{"pattern": "concurrent", "outputs": []}])
        with patch.dict(os.environ, {"UI_ORCHESTRATION_PROJECT_ENDPOINT": "https://example.test/project"}), \
                patch("ui.load_demo", return_value=lab) as loader, patch("ui.AsyncAzureCliCredential") as credential:
            status, body = self.request("POST", "/api/orchestration", '{"pattern":"concurrent"}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)[0]["pattern"], "concurrent")
            loader.assert_called_once_with("13-agent-orchestrations", package=True)
            lab.run_samples.assert_awaited_once_with(
                "https://example.test/project", "gpt-4.1-mini", "concurrent", credential.return_value, timeout=180)
        for pattern in ("all", "unknown"):
            self.assertEqual(self.request("POST", "/api/orchestration",
                                          json.dumps({"pattern": pattern}))[0], 400)

    def test_injection_uses_configured_shield_and_validates_source(self):
        lab = MagicMock()
        lab.evaluate_source.return_value = {"baseline": {"status": "content_filter"},
                                            "protected": {"start_time": "09:00"}}
        credential = MagicMock()
        credential.__enter__.return_value = credential
        credential.get_token.return_value.token = "test-token"
        with patch.dict(os.environ, {"UI_INJECTION_ENDPOINT": "https://model.test",
                                    "UI_INJECTION_SAFETY_ENDPOINT": "https://shield.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential", return_value=credential), \
                patch("ui.get_bearer_token_provider"), patch("ui.OpenAI") as factory:
            status, body = self.request("POST", "/api/injection", '{"source":"tool-output"}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["baseline"]["status"], "content_filter")
            lab.evaluate_source.assert_called_once_with(factory.return_value.__enter__.return_value,
                                                        "gpt-4.1-mini", "tool-output",
                                                        "https://shield.test", "test-token")
        self.assertEqual(self.request("POST", "/api/injection", '{"source":"other"}')[0], 400)

    def test_resilience_uses_lab_runner_without_retries(self):
        lab = MagicMock()
        lab.demonstrate.return_value = {"booking_count": 1, "model_attempts": 5}
        with patch.dict(os.environ, {"UI_RESILIENCE_ENDPOINT": "https://example.test"}), \
                patch("ui.load_demo", return_value=lab) as loader, patch("ui.AzureCliCredential"), \
                patch("ui.get_bearer_token_provider"), patch("ui.OpenAI") as factory:
            status, body = self.request("POST", "/api/resilience", "{}")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["booking_count"], 1)
            self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
            loader.assert_called_once_with("25-resilience-cost", package=True)
            lab.demonstrate.assert_called_once_with(factory.return_value.__enter__.return_value, "gpt-4.1-mini")

    def test_benchmark_reports_each_slot_and_total_failure(self):
        lab = MagicMock()
        lab.CASES = [{"id": "test", "text": "ticket", "expected": {}}]
        lab.DEFAULT_RATES = {"mini": [.4, 1.6], "full": [2, 8]}
        lab.run_one.side_effect = [{"correct": True}, {"transport_error": "timeout"}, {"correct": False}]
        with patch.dict(os.environ, {"UI_BENCHMARK_ENDPOINT": "https://example.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.get_bearer_token_provider"), patch("ui.OpenAI"):
            status, body = self.request("POST", "/api/benchmark", '{"case":"test"}')
            self.assertEqual(status, 200)
            results = json.loads(body)["results"]
            self.assertEqual([item["slot"] for item in results], ["mini", "full", "router"])
            self.assertEqual(results[1]["transport_error"], "timeout")
            self.assertIsNone(lab.run_one.call_args_list[2].args[3])
            lab.run_one.side_effect = [{"transport_error": "timeout"}] * 3
            status, body = self.request("POST", "/api/benchmark", '{"case":"test"}')
            self.assertEqual(status, 502)
            self.assertIn("All benchmark requests failed", json.loads(body)["error"])
            self.assertEqual(self.request("POST", "/api/benchmark", '{"case":"unknown"}')[0], 400)

    def test_release_job_runs_in_background_and_persists_result(self):
        started = threading.Event()
        finish = threading.Event()

        def release(regression):
            started.set()
            self.assertTrue(finish.wait(5))
            return {"rollback_verified": True}

        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_JOB_STATE_DIR": directory}), \
                patch("ui.release_governance", side_effect=release):
            status, body = self.request("POST", "/api/release", '{"regression":false}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["status"], "running")
            self.assertTrue(started.wait(5))
            self.assertEqual(json.loads(self.request("GET", "/api/job")[1])["status"], "running")
            self.assertEqual(self.request("POST", "/api/release", "{}")[0], 409)
            finish.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertTrue(job["result"]["rollback_verified"])
            self.assertEqual(ui.read_job(), job)

    def test_interrupted_release_requires_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"UI_JOB_STATE_DIR": directory}):
            ui.write_job({"id": "previous", "status": "running"})
            ui.recover_interrupted_job()
            self.assertEqual(ui.read_job()["status"], "interrupted")
            status, body = self.request("POST", "/api/release", "{}")
            self.assertEqual(status, 400)
            self.assertIn("acknowledge", json.loads(body)["error"])

    def test_evaluation_job_returns_actual_report(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_JOB_STATE_DIR": directory}), \
                patch("ui.agent_evaluation", return_value={"evaluation_id": "actual"}) as evaluate:
            status, body = self.request("POST", "/api/evaluation", "{}")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["kind"], "agent-evaluation")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["evaluation_id"], "actual")
            evaluate.assert_called_once_with()

    def test_release_gate_job_reports_blocked_as_completed(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_JOB_STATE_DIR": directory}), \
                patch("ui.evaluation_gate", return_value={"passed": False}) as gate:
            status, body = self.request("POST", "/api/cicd", '{"variant":"regression"}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["variant"], "regression")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertFalse(job["result"]["passed"])
            gate.assert_called_once_with("regression")
        self.assertEqual(self.request("POST", "/api/cicd", '{"variant":"other"}')[0], 400)

    def test_foundry_iq_uses_configured_search_and_returns_real_evidence(self):
        lab = MagicMock()
        lab.API_VERSION = "preview"
        lab.retrieve.return_value = {"references": [{"docKey": "schedule"}], "activity": [{}]}
        credential = MagicMock()
        credential.get_token.return_value.token = "test-token"
        with patch.dict(os.environ, {"UI_IQ_SEARCH_ENDPOINT": "https://search.test",
                                    "UI_IQ_MODEL_ENDPOINT": "https://model.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential", return_value=credential), \
                patch("ui.httpx.Client") as factory:
            status, body = self.request("POST", "/api/iq", '{"question":" When? "}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["references"][0]["docKey"], "schedule")
            client = factory.return_value.__enter__.return_value
            lab.configure.assert_called_once_with(client, "https://model.test", "gpt-4.1-mini")
            lab.retrieve.assert_called_once_with(client, "When?")
        self.assertEqual(self.request("POST", "/api/iq", '{"question":" "}')[0], 400)

    def test_discovery_uses_selected_fixed_case_and_project_identity(self):
        lab = MagicMock()
        lab.demonstrate = AsyncMock(return_value={"catalog": [], "results": [{"answer": {"kind": "brief"}}]})
        credential = MagicMock()
        with patch.dict(os.environ, {"UI_DISCOVERY_PROJECT_ENDPOINT": "https://project.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential", return_value=credential), \
                patch("ui.AIProjectClient") as factory:
            status, body = self.request("POST", "/api/discovery", '{"case":"workshop-brief"}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["results"][0]["answer"]["kind"], "brief")
            lab.demonstrate.assert_awaited_once_with(factory.return_value.__enter__.return_value,
                                                    credential, "https://project.test",
                                                    "gpt-4.1-mini", "workshop-brief")
        self.assertEqual(self.request("POST", "/api/discovery", '{"case":"unknown"}')[0], 400)

    def test_tracing_uses_selected_scenario_and_explicit_identity(self):
        lab = MagicMock()
        lab.run_scenarios.return_value = [{"scenario": "failed", "trace_id": "abc"}]
        credential = MagicMock()
        with patch.dict(os.environ, {"UI_TRACING_ENDPOINT": "https://model.test",
                                    "UI_TRACING_CONNECTION_STRING": "synthetic"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential", return_value=credential):
            status, body = self.request("POST", "/api/tracing", '{"scenario":"failed"}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["traces"][0]["trace_id"], "abc")
            lab.run_scenarios.assert_called_once_with("https://model.test", "gpt-4.1-mini",
                                                     "synthetic", credential, ("failed",))
        self.assertEqual(self.request("POST", "/api/tracing", '{"scenario":"other"}')[0], 400)

    def test_tool_agent_checks_attendees_and_uses_identity(self):
        lab = MagicMock()
        lab.run_agent = AsyncMock(return_value={"tool_results": ["real tool"], "answer": "price"})
        with patch.dict(os.environ, {"UI_TOOLS_PROJECT_ENDPOINT": "https://project.test",
                                    "UI_TOOLS_FUNCTION_ENDPOINT": "https://function.test/api"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AsyncAzureCliCredential") as credential:
            status, body = self.request("POST", "/api/tools", '{"attendees":12}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["tool_results"], ["real tool"])
            lab.run_agent.assert_awaited_once_with("https://project.test", "gpt-4.1-mini",
                                                  "https://function.test/api", 12, credential.return_value)
        for value in (0, 1000, True, 1.5):
            self.assertEqual(self.request("POST", "/api/tools", json.dumps({"attendees": value}))[0], 400)

    def test_decision_selection_and_custom_ticket_validation(self):
        with patch("ui.decision_model", return_value={"team": "billing"}) as classify:
            status, body = self.request("POST", "/api/decision", '{"selection":"billing"}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["team"], "billing")
        classify.assert_called_once_with({"selection": "billing"})
        for data in ({"selection": "invalid"}, {"selection": "custom", "ticket": ""}):
            status, _ = self.request("POST", "/api/decision", json.dumps(data))
            self.assertEqual(status, 400)

    def test_safety_request_and_threshold_validation(self):
        with patch("ui.content_safety", return_value={"blocked": False}) as analyze:
            status, body = self.request("POST", "/api/safety", '{"text":"Hello","threshold":2}')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["blocked"])
        analyze.assert_called_once_with({"text": "Hello", "threshold": 2})
        for data in ({"text": "", "threshold": 2}, {"text": "Hello", "threshold": True},
                     {"text": "Hello", "threshold": 3}):
            status, _ = self.request("POST", "/api/safety", json.dumps(data))
            self.assertEqual(status, 400)

    def test_structured_output_compares_real_lab_result_and_reports_request_failure(self):
        response = MagicMock()
        response.choices[0].message.content = json.dumps(
            {"category": "billing", "priority": None, "summary": "Billing problem"})
        response.choices[0].message.refusal = None
        response.choices[0].finish_reason = "stop"
        response.model = "actual-model"
        response.usage = None
        with patch.dict(os.environ, {"UI_STRUCTURED_ENDPOINT": "https://example.test"}), \
                patch("ui.AzureCliCredential"), patch("ui.get_bearer_token_provider"), \
                patch("ui.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.chat.completions.create.return_value = response
            status, body = self.request("POST", "/api/structured",
                                        '{"mode":"structured","case":"missing-impact"}')
            self.assertEqual(status, 200)
            result = json.loads(body)
            self.assertTrue(result["correct"])
            self.assertEqual(result["serving_model"], "actual-model")
            self.assertTrue(client.chat.completions.create.call_args.kwargs["response_format"]["json_schema"]["strict"])
            client.chat.completions.create.side_effect = TimeoutError("service timeout")
            status, body = self.request("POST", "/api/structured",
                                        '{"mode":"structured","case":"missing-impact"}')
            self.assertEqual(status, 502)
            self.assertIn("service timeout", json.loads(body)["error"])
        for data in ({"mode": "bad", "case": "clear"}, {"mode": "few-shot", "case": "../"}):
            self.assertEqual(self.request("POST", "/api/structured", json.dumps(data))[0], 400)

    def test_receipt_uses_selected_image_and_ground_truth(self):
        status, image = self.request("GET", "/images/ambiguous.png")
        self.assertEqual(status, 200)
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(self.request("GET", "/images/../demo.py")[0], 404)
        response = MagicMock()
        response.choices[0].message.content = json.dumps(
            {"merchant": "WORKSHOP CAFE", "currency": "USD", "total": 12.5, "uncertainty": []})
        response.choices[0].message.refusal = None
        response.choices[0].finish_reason = "stop"
        response.model = "actual-model"
        response.usage = None
        with patch.dict(os.environ, {"UI_VISION_ENDPOINT": "https://example.test"}), \
                patch("ui.AzureCliCredential"), patch("ui.get_bearer_token_provider"), \
                patch("ui.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.chat.completions.create.return_value = response
            status, body = self.request("POST", "/api/receipt", '{"image":"ambiguous.png"}')
            self.assertEqual(status, 200)
            result = json.loads(body)
            self.assertTrue(result["schema_ok"])
            self.assertFalse(result["correct"])
            self.assertIsNone(result["truth"]["total"])
            self.assertIsNone(result["estimated_usd"])
            payload = client.chat.completions.create.call_args.kwargs["messages"][1]["content"][1]["image_url"]["url"]
            self.assertEqual(base64.b64decode(payload.split(",", 1)[1]), image)
            client.chat.completions.create.side_effect = TimeoutError("vision timeout")
            status, body = self.request("POST", "/api/receipt", '{"image":"clear.png"}')
            self.assertEqual(status, 502)
            self.assertIn("vision timeout", json.loads(body)["error"])
        self.assertEqual(self.request("POST", "/api/receipt", '{"image":"../demo.py"}')[0], 400)

    def test_conversation_persists_separate_scopes_and_failed_turn_does_not_save(self):
        response = MagicMock()
        response.choices[0].message.content = "Remembered MAPLE-742."
        response.choices[0].finish_reason = "stop"
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_CONVERSATION_STATE_PATH": directory + "/state.sqlite3",
                                        "UI_CONVERSATION_ENDPOINT": "https://example.test"}), \
                patch("ui.AzureCliCredential"), patch("ui.get_bearer_token_provider"), \
                patch("ui.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.chat.completions.create.return_value = response
            status, body = self.request("POST", "/api/conversation",
                                        '{"action":"turn","user":"alice","prompt":"Remember MAPLE-742."}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["answer"], "Remembered MAPLE-742.")
            saved = json.loads(body)["history"]
            status, body = self.request("POST", "/api/conversation", '{"action":"show","user":"bob"}')
            self.assertEqual(json.loads(body)["history"], [])
            response.choices[0].finish_reason = "length"
            status, _ = self.request("POST", "/api/conversation",
                                     '{"action":"turn","user":"alice","prompt":"Again"}')
            self.assertEqual(status, 502)
            status, body = self.request("POST", "/api/conversation", '{"action":"show","user":"alice"}')
            self.assertEqual(json.loads(body)["history"], saved)
            status, body = self.request("POST", "/api/conversation", '{"action":"delete","user":"alice"}')
            self.assertEqual(json.loads(body)["history"], [])
            self.assertEqual(self.request("POST", "/api/conversation",
                                          '{"action":"delete","user":"mallory"}')[0], 400)

    def test_fundamentals_returns_actual_stream_and_negative_probe_results(self):
        import httpx
        from openai import APIStatusError, NotFoundError

        normal = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="A project organizes assets.", refusal=None),
        )], model="actual-model", usage=None)
        chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Streamed answer"), finish_reason=None)],
                                  usage=None),
                  SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")],
                                  usage=None),
                  SimpleNamespace(choices=[], usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 7}))]
        stream = MagicMock()
        stream.__enter__.return_value = chunks
        request = httpx.Request("POST", "https://example.test")
        missing = NotFoundError("missing", response=httpx.Response(404, request=request), body={"code": "missing"})
        denied = APIStatusError("denied", response=httpx.Response(401, request=request), body={
            "code": "PermissionDenied", "message": "Principal does not have access to API/Operation.",
        })
        primary, restricted = MagicMock(), MagicMock()
        primary.__enter__.return_value.chat.completions.create.side_effect = [normal, stream, missing]
        restricted.__enter__.return_value.chat.completions.create.side_effect = denied
        with patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_FUNDAMENTALS_ENDPOINT": "https://primary.test",
                                     "UI_FUNDAMENTALS_DENIED_ENDPOINT": "https://denied.test"}), \
                patch("ui.AzureCliCredential"), patch("ui.get_bearer_token_provider"), \
                patch("ui.OpenAI", side_effect=[primary, restricted]):
            status, body = self.request("POST", "/api/fundamentals", "{}")
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["stream"], {"text": "Streamed answer", "usage": {"total_tokens": 7}})
        self.assertEqual(result["missing_deployment"]["status"], 404)
        self.assertEqual(result["permission_denial"]["status"], 401)
        self.assertTrue(result["negative_probes_verified"])

    def test_approval_requires_explicit_exact_decision_and_prevents_replay_duplicates(self):
        lab = ui.load_demo("20-human-approval")
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_APPROVAL_ENDPOINT": "https://model.test",
                                        "UI_APPROVAL_STATE_PATH": directory + "/bookings.db"}), \
                patch("ui.load_demo", return_value=lab), patch.object(lab, "model_action", return_value={
                    "workshop": "foundry-intro", "attendees": 4,
                }), patch("ui.AzureCliCredential"), patch("ui.approval_actor", return_value="tenant/presenter"), \
                patch("ui.get_bearer_token_provider"), patch("ui.OpenAI"):
            status, body = self.request("POST", "/api/approval", '{"action":"propose","attendees":4}')
            proposal = json.loads(body)
            self.assertEqual(status, 200)
            self.assertFalse(proposal["booking_created"])
            db = lab.connect(ui.Path(directory) / "bookings.db")
            try:
                self.assertEqual(db.execute("SELECT count(*) FROM bookings").fetchone()[0], 0)
                decision = {"action": "approve", "proposal_id": proposal["proposal_id"], "digest": "0" * 64}
                self.assertEqual(self.request("POST", "/api/approval", json.dumps(decision))[0], 403)
                decision["digest"] = proposal["digest"]
                status, body = self.request("POST", "/api/approval", json.dumps(decision))
                self.assertEqual(status, 200)
                self.assertIn("APPROVED", json.loads(body)["result"])
                self.assertIn("REPLAY", json.loads(self.request("POST", "/api/approval", json.dumps(decision))[1])["result"])
                self.assertEqual(db.execute("SELECT count(*) FROM bookings").fetchone()[0], 1)
            finally:
                db.close()
            self.assertEqual(self.request("POST", "/api/approval", '{"action":"propose","attendees":true}')[0], 400)

    def test_hosted_approval_requires_the_easyauth_presenter(self):
        lab = MagicMock()
        credential = MagicMock()
        def headers(oid, tid):
            payload = {"claims": [
                {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": oid},
                {"typ": "http://schemas.microsoft.com/identity/claims/tenantid", "val": tid},
            ]}
            return {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(payload).encode()).decode()}
        with patch.dict(os.environ, {"UI_AZURE_HOSTED": "1", "UI_PRESENTER_TENANT_ID": "tenant",
                                     "UI_PRESENTER_OBJECT_ID": "presenter"}):
            self.assertEqual(ui.approval_actor(headers("presenter", "tenant"), credential, lab),
                             "tenant/presenter")
            for invalid in ({}, headers("another", "tenant"), headers("presenter", "other"),
                            {"X-MS-CLIENT-PRINCIPAL": "invalid"}):
                with self.assertRaises(ui.ApprovalPermissionError):
                    ui.approval_actor(invalid, credential, lab)
        credential.get_token.assert_not_called()

    def test_csv_analyst_persists_verified_report_and_chart_in_background(self):
        lab = MagicMock()
        lab.TIMEOUT = 180
        lab.demonstrate.return_value = {"response_id": "actual", "report": {"row_count": 6}, "chart": b"chart"}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_ANALYST_PROJECT_ENDPOINT": "https://project.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.AIProjectClient"):
            status, body = self.request("POST", "/api/analyst", "{}")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["kind"], "csv-analyst")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["response_id"], "actual")
            self.assertEqual(base64.b64decode(job["result"]["chart_base64"]), b"chart")
            lab.demonstrate.assert_called_once()

    def test_hosted_agent_job_uses_requested_session_and_checks_inputs(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_JOB_STATE_DIR": directory}), \
                patch("ui.hosted_agent", return_value={"quote": {"total": 75}}) as invoke:
            for value in ({"attendees": True, "session_id": "good"},
                          {"attendees": 3, "session_id": "../bad"}):
                self.assertEqual(self.request("POST", "/api/hosted-agent", json.dumps(value))[0], 400)
            status, body = self.request("POST", "/api/hosted-agent",
                                        '{"attendees":3,"session_id":"ui-test"}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["kind"], "hosted-agent")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["quote"]["total"], 75)
            invoke.assert_called_once_with(3, "ui-test")

    def test_rag_job_validates_choices_and_runs_selected_case(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_JOB_STATE_DIR": directory}), \
                patch("ui.rag_case", return_value={"recall_at_3": 1}) as run:
            for value in ({"chunk_words": True, "mode": "hybrid", "question_index": 0},
                          {"chunk_words": 12, "mode": "other", "question_index": 0},
                          {"chunk_words": 12, "mode": "hybrid", "question_index": 4}):
                self.assertEqual(self.request("POST", "/api/rag", json.dumps(value))[0], 400)
            status, body = self.request("POST", "/api/rag",
                                        '{"chunk_words":12,"mode":"hybrid","question_index":0}')
            self.assertEqual(status, 200)
            started = json.loads(body)
            self.assertEqual(started["kind"], "rag")
            self.assertTrue(started["index_name"].startswith("ui-rag-"))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["recall_at_3"], 1)
            run.assert_called_once_with(12, "hybrid", 0, started["index_name"])

    def test_rag_question_labels_match_the_actual_labeled_cases(self):
        lab = ui.load_demo("17-rag-fundamentals")
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        for index, (question, _) in enumerate(lab.QUESTIONS):
            self.assertIn(f'<option value="{index}">{question}'.encode(), page)

    def test_rag_uses_retrieved_evidence_and_cleans_up_on_failure(self):
        lab = MagicMock()
        lab.QUESTIONS = [("Question?", "schedule")]
        lab.DOCUMENTS = {"schedule": "Evidence"}
        lab.chunks.return_value = [{"id": "schedule-0", "source": "schedule", "content": "Evidence"}]
        lab.retrieve.return_value = [{"id": "schedule-0", "source": "schedule", "content": "Evidence"}]
        lab.grounded_answer.return_value = {"answer": "Evidence", "citations": ["schedule-0"], "quotes": []}
        lab.metrics.return_value = (1, 1)
        vector = SimpleNamespace(index=0, embedding=[0.1])
        with patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_RAG_ENDPOINT": "https://model.test",
                                    "UI_RAG_SEARCH_ENDPOINT": "https://search.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.get_bearer_token_provider"), patch("ui.AzureOpenAI") as openai, \
                patch("azure.search.documents.SearchClient") as search_type, \
                patch("azure.search.documents.indexes.SearchIndexClient") as indexes_type:
            client = openai.return_value.__enter__.return_value
            client.embeddings.create.side_effect = [SimpleNamespace(data=[vector])] * 4
            search = search_type.return_value.__enter__.return_value
            search.upload_documents.return_value = [SimpleNamespace(succeeded=True)]
            indexes = indexes_type.return_value.__enter__.return_value
            result = ui.rag_case(12, "hybrid", 0, "ui-rag-test")
            self.assertEqual(result["hits"][0]["id"], "schedule-0")
            self.assertEqual(result["recall_at_3"], 1)
            self.assertEqual(search.upload_documents.call_args.args[0][0]["vector"], [0.1])
            lab.retrieve.assert_called_once_with(search, "hybrid", "Question?", [0.1])
            indexes.delete_index.assert_called_once_with("ui-rag-test")

            lab.grounded_answer.side_effect = RuntimeError("model failed")
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                ui.rag_case(12, "hybrid", 0, "ui-rag-test2")
            indexes.delete_index.assert_called_with("ui-rag-test2")

    def test_content_understanding_job_passes_credential_and_persists_evidence_path(self):
        lab = MagicMock()
        lab.demonstrate.return_value = {"status": "PASS", "modalities": {"document": {}, "audio": {}}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_CU_ENDPOINT": "https://cu.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential") as credentials:
            status, body = self.request("POST", "/api/content-understanding", "{}")
            self.assertEqual(status, 200)
            started = json.loads(body)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(set(job["result"]["modalities"]), {"document", "audio"})
            lab.demonstrate.assert_called_once_with(
                "https://cu.test", "cu-ga-completion", "cu-embedding",
                credentials.return_value, Path(directory) / "content-understanding" / started["id"])

    def test_monitoring_restores_regression_even_when_traffic_fails(self):
        lab = MagicMock()
        lab.NAMES = ("monitored", "stable")
        lab.project_traffic.side_effect = RuntimeError("model failed")
        config = {"agents": {
            "monitored": {"good_version": "1", "bad_version": "2", "eval_id": "eval-a"},
            "stable": {"good_version": "1", "bad_version": None, "eval_id": "eval-b"},
        }}
        with patch.dict(os.environ, {"UI_AZURE_HOSTED": "1", "UI_MONITORING_PROJECT_ENDPOINT": "https://project.test",
                                    "UI_MONITORING_CONFIGURATION": json.dumps(config)}), \
                patch("ui.load_demo", return_value=lab), patch("ui.ManagedIdentityCredential"), \
                patch("ui.AIProjectClient") as project_type:
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                ui.monitoring_traffic("regression")
            project = project_type.return_value.__enter__.return_value
            lab.set_project_mode.assert_called_once_with(project, config, "recovery")

    def test_monitoring_job_accepts_only_known_modes(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_JOB_STATE_DIR": directory}), \
                patch("ui.monitoring_traffic", return_value={"requests": [], "regression_route_restored": True}) as run:
            self.assertEqual(self.request("POST", "/api/monitoring", '{"mode":"other"}')[0], 400)
            self.assertEqual(self.request("POST", "/api/monitoring-info", '{"action":"other"}')[0], 400)
            status, body = self.request("POST", "/api/monitoring", '{"mode":"regression"}')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["kind"], "monitoring")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertTrue(job["result"]["regression_route_restored"])
            run.assert_called_once_with("regression")

    def test_semantic_cache_job_uses_a_fresh_gateway_partition(self):
        lab = MagicMock()
        lab.present.return_value = {"requests": [{"response_id": "real-id"}], "reused_response_ids": 1}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_CACHE_ENDPOINT": "https://gateway.test/ai"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.get_bearer_token_provider"), patch("ui.AzureOpenAI") as client_type:
            status, body = self.request("POST", "/api/semantic-cache", "{}")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["kind"], "semantic-cache")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            session = job["result"]["session"]
            self.assertTrue(session.startswith("ui-"))
            self.assertEqual(client_type.call_args.kwargs["default_headers"]["x-workshop-cache-session"], session)
            lab.present.assert_called_once_with(client_type.return_value.__enter__.return_value, "gpt-4.1-mini")

    def test_lifecycle_job_persists_ids_before_work_and_records_failures(self):
        lab = MagicMock()

        def fail_after_checkpoint(project, session, credential, args, report, *, suffix, on_update):
            report["application_id"] = f"/applications/workshop-app-{suffix}"
            on_update(report)
            raise RuntimeError("publish failed")

        lab.lifecycle.side_effect = fail_after_checkpoint
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_LIFECYCLE_PROJECT_ENDPOINT": "https://project.test",
                                        "UI_LIFECYCLE_PROJECT_RESOURCE_ID": "/project"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.AIProjectClient"), patch("ui.requests.Session"):
            status, body = self.request("POST", "/api/agent-lifecycle", "{}")
            self.assertEqual(status, 200)
            started = json.loads(body)
            self.assertEqual(started["application_name"], f"workshop-app-{started['id'][:8]}")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = json.loads(self.request("GET", "/api/job")[1])
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "failed")
            report = json.loads((Path(directory) / "agent-lifecycle" / f"{started['id']}.json").read_text())
            self.assertTrue(report["application_id"].endswith(started["application_name"]))
            self.assertEqual(report["error"]["message"], "publish failed")

    def test_fine_tuning_submission_is_recorded_and_cannot_be_duplicated_while_running(self):
        lab = MagicMock()
        lab.MODEL = "training-model"
        pending = MagicMock()
        pending.model_dump.return_value = {"id": "ftjob-123", "status": "running", "training_file": "file-1"}
        complete = MagicMock()
        complete.model_dump.return_value = {
            "id": "ftjob-123", "status": "succeeded", "fine_tuned_model": "trained-model",
        }

        def submit(client, model, *, on_upload):
            on_upload("file-1")
            return pending

        lab.submit.side_effect = submit
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_FINE_TUNING_ENDPOINT": "https://tuning.openai.azure.com/"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.get_bearer_token_provider"), patch("ui.OpenAI") as factory:
            self.assertEqual(self.request("POST", "/api/fine-tuning", '{"action":"submit"}')[0], 400)
            result = ui.fine_tuning("submit", {"confirm_cost": True})
            self.assertEqual(result["job"]["id"], "ftjob-123")
            self.assertEqual(ui.fine_tuning_state()["training_file"], "file-1")
            with self.assertRaisesRegex(RuntimeError, "still be running"):
                ui.fine_tuning("submit", {"confirm_cost": True})
            lab.submit.assert_called_once()
            client = factory.return_value.__enter__.return_value
            client.fine_tuning.jobs.retrieve.return_value = complete
            result = ui.fine_tuning("inspect")
            self.assertEqual(result["job"]["fine_tuned_model"], "trained-model")
            self.assertEqual(ui.fine_tuning_state()["job"]["status"], "succeeded")
            self.assertEqual(self.request("POST", "/api/fine-tuning",
                                          '{"action":"inspect","job_id":12}')[0], 400)

    def test_fine_tuning_uncertainty_requires_explicit_reconciliation(self):
        lab = MagicMock()
        lab.MODEL = "training-model"

        def fail(client, model, *, on_upload):
            on_upload("file-uncertain")
            raise TimeoutError("response lost")

        lab.submit.side_effect = fail
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_FINE_TUNING_ENDPOINT": "https://tuning.openai.azure.com/"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.get_bearer_token_provider"), patch("ui.OpenAI"):
            with self.assertRaisesRegex(TimeoutError, "response lost"):
                ui.fine_tuning("submit", {"confirm_cost": True})
            state = ui.fine_tuning_state()
            self.assertEqual((state["phase"], state["training_file"]), ("uncertain", "file-uncertain"))
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                ui.fine_tuning("submit", {"confirm_cost": True})
            with self.assertRaises(ValueError):
                ui.fine_tuning("acknowledge")
            ui.fine_tuning("acknowledge", {"inspected_azure": True})
            self.assertEqual(ui.fine_tuning_state()["phase"], "acknowledged")

    def test_guardrails_reports_blocked_agent_separately_from_model_pass(self):
        lab = MagicMock()
        lab.run.return_value = {"status": "PASS"}
        lab.run_agent.return_value = {"status": "BLOCKED"}
        account = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/guard"
        with patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_GUARDRAILS_ACCOUNT_ID": account,
                                    "UI_GUARDRAILS_ENDPOINT": "https://guard.openai.azure.com/",
                                    "UI_GUARDRAILS_PROJECT_ENDPOINT": "https://guard.services.ai.azure.com/api/projects/native-guardrails"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"):
            result = ui.native_guardrails("1234567890abcdef")
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(lab.run_agent.call_args.kwargs["agent_name"], "native-guardrails-1234567890ab")
            with patch.dict(os.environ, {"UI_GUARDRAILS_ENDPOINT": "https://other.openai.azure.com/"}):
                with self.assertRaises(RuntimeError):
                    ui.native_guardrails("1234567890abcdef")

    def test_document_ui_serves_only_approved_invoices_and_uses_cloud_evidence(self):
        status, pdf = self.request("GET", "/invoices/clear.pdf")
        self.assertEqual(status, 200)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(self.request("GET", "/invoices/../demo.py")[0], 404)
        self.assertEqual(self.request("GET", "/invoices/unknown.pdf")[0], 404)
        self.assertEqual(self.request("POST", "/api/document-intelligence",
                                      '{"document_id":"unknown"}')[0], 400)
        lab = MagicMock()
        lab.EXPECTED = {"clear": {}}
        lab.AGENT = "invoice-agent"
        lab.request_invoice.return_value = {"id": "response-1", "status": "completed", "usage": {}}
        lab.tool_extraction.return_value = {"operation_id": "operation-1"}
        lab.response_text.return_value = "answer"
        lab.validate_answer.return_value = {"document_id": "clear"}
        with patch.dict(os.environ, {"UI_AZURE_HOSTED": "0",
                                    "UI_DOCUMENT_PROJECT_ENDPOINT": "https://project.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.AIProjectClient"):
            result = ui.document_intelligence("clear")
            self.assertEqual(result["extraction"]["operation_id"], "operation-1")
            self.assertEqual(result["response_id"], "response-1")
            lab.validate_answer.assert_called_once_with("answer", {"operation_id": "operation-1"})
            lab.request_invoice.return_value = {"status": "failed", "error": "service failure"}
            with self.assertRaisesRegex(RuntimeError, "service failure"):
                ui.document_intelligence("clear")

    def test_voice_job_uses_native_workflow_and_serves_only_its_recordings(self):
        lab = MagicMock()
        lab.live.return_value = {"status": "PASS", "conversationId": "cloud-conversation"}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_VOICE_PROJECT_ENDPOINT": "https://voice.test/api/projects/voice",
                                        "UI_VOICE_MODEL": "gpt-realtime"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential") as credentials:
            status, body = self.request("POST", "/api/voice-agent", "{}")
            self.assertEqual(status, 200)
            job_id = json.loads(body)["id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = ui.read_job()
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["conversationId"], "cloud-conversation")
            lab.live.assert_called_once_with(
                endpoint="https://voice.test/api/projects/voice", model="gpt-realtime",
                agent_name=f"workshop-voice-{job_id[:12]}", credential=credentials.return_value,
                state_dir=Path(directory) / "voice" / job_id,
            )
            path = Path(directory) / "voice" / job_id
            path.mkdir(parents=True)
            (path / "reply.wav").write_bytes(b"RIFF-reply")
            self.assertEqual(self.request("GET", f"/voice/{job_id}/reply.wav"), (200, b"RIFF-reply"))
            self.assertEqual(self.request("GET", f"/voice/{job_id}/conversation.wav")[0], 404)
            self.assertEqual(self.request("GET", f"/voice/{'a' * 32}/reply.wav")[0], 404)
            self.assertEqual(self.request("GET", f"/voice/{job_id}/../current.json")[0], 404)
            self.assertTrue(self.request("GET", "/voice/turn.wav")[1].startswith(b"RIFF"))
            with patch.dict(os.environ, {"UI_VOICE_MODEL": "unsupported"}):
                with self.assertRaisesRegex(RuntimeError, "supported managed voice model"):
                    ui.voice_agent(job_id)

    def test_fleet_job_preserves_blocked_stage_and_uses_isolated_tracer(self):
        lab = MagicMock()
        lab.validate_configuration.return_value = {"resourceGroup": "rg-fleet-governance-demo"}
        lab.run.return_value = {"status": "BLOCKED", "isolatedAzurePolicy": {"status": "BLOCKED"}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_FLEET_CONFIGURATION": '{"resourceGroup":"rg-fleet-governance-demo"}'}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential") as credentials:
            status, body = self.request("POST", "/api/fleet-governance", "{}")
            self.assertEqual(status, 200)
            job_id = json.loads(body)["id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = ui.read_job()
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["status"], "BLOCKED")
            lab.validate_configuration.assert_called_once_with({}, "rg-fleet-governance-demo")
            lab.run.assert_called_once_with({"resourceGroup": "rg-fleet-governance-demo"},
                                            credentials.return_value,
                                            state_dir=Path(directory) / "fleet" / job_id,
                                            register_global=False)
            with patch.dict(os.environ, {"UI_FLEET_CONFIGURATION": "not json"}):
                with self.assertRaisesRegex(RuntimeError, "Configure the lab 46"):
                    ui.fleet_governance(job_id)

    def test_native_tool_search_reports_harness_not_run(self):
        lab = MagicMock()

        async def search(project, credential, endpoint, report, *, state_dir):
            report.update(tool_search_status="PASS", toolbox_deleted=True)

        lab.toolbox_search = AsyncMock(side_effect=search)
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_AZURE_HOSTED": "0", "UI_JOB_STATE_DIR": directory,
                                        "UI_TOOL_SEARCH_PROJECT_ENDPOINT": "https://project.test"}), \
                patch("ui.load_demo", return_value=lab), patch("ui.AzureCliCredential"), \
                patch("ui.AIProjectClient"):
            result = ui.native_tool_search("a" * 32)
            self.assertEqual(result["tool_search_status"], "PASS")
            self.assertEqual(result["harness_status"], "NOT_RUN")
            lab.toolbox_search.assert_awaited_once()
            self.assertEqual(lab.toolbox_search.call_args.kwargs["state_dir"],
                             Path(directory) / "tool-search" / ("a" * 32))
            lab.toolbox_search = AsyncMock()
            with self.assertRaisesRegex(RuntimeError, "cleanup did not complete"):
                ui.native_tool_search("b" * 32)

    def test_evaluator_breadth_uses_managed_identity_and_separate_job_state(self):
        lab = MagicMock()

        def evaluate(project, config, report, *, credential, state_dir):
            report["evaluator_status"] = "PASS"

        lab.cloud_evaluation.side_effect = evaluate
        environment = {
            "UI_AZURE_HOSTED": "1",
            "UI_EVALUATOR_ENDPOINT": "https://evaluator.openai.azure.com/",
            "UI_EVALUATOR_PROJECT_ENDPOINT": "https://evaluator.services.ai.azure.com/api/projects/custom-project",
        }
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {**environment, "UI_JOB_STATE_DIR": directory}), \
                patch("ui.load_demo", return_value=lab), \
                patch("ui.ManagedIdentityCredential") as credentials, \
                patch("ui.AIProjectClient") as projects:
            job_id = "a" * 32
            result = ui.evaluator_breadth(job_id)
            self.assertEqual(result["evaluator_status"], "PASS")
            self.assertEqual(result["dataset_status"], "NOT_RUN")
            self.assertEqual(result["optimizer_status"], "NOT_RUN")
            lab.cloud_evaluation.assert_called_once_with(
                projects.return_value.__enter__.return_value,
                {"endpoint": "https://evaluator.openai.azure.com", "deployment": "gpt-4.1-mini"},
                result, credential=credentials.return_value,
                state_dir=Path(directory) / "evaluator" / job_id,
            )
            with patch.dict(os.environ, {"UI_EVALUATOR_PROJECT_ENDPOINT":
                                        "https://other.services.ai.azure.com/api/projects/evaluation-optimizer"}):
                with self.assertRaisesRegex(RuntimeError, "matching lab 44"):
                    ui.evaluator_breadth(job_id)
            with self.assertRaisesRegex(ValueError, "Invalid evaluator job ID"):
                ui.evaluator_breadth("../state")
            lab.cloud_evaluation.side_effect = None
            with self.assertRaisesRegex(RuntimeError, "evidence is incomplete"):
                ui.evaluator_breadth(job_id)

    def test_evaluator_breadth_runs_as_a_background_job(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"UI_JOB_STATE_DIR": directory}), \
                patch("ui.evaluator_breadth", return_value={"evaluator_status": "PASS",
                                                           "optimizer_status": "NOT_RUN"}) as evaluate:
            status, body = self.request("POST", "/api/evaluator-breadth", "{}")
            self.assertEqual(status, 200)
            job_id = json.loads(body)["id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = ui.read_job()
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["optimizer_status"], "NOT_RUN")
            evaluate.assert_called_once_with(job_id)

    def test_native_memory_uses_authenticated_hosted_presenter_not_request_scope(self):
        tenant = "11111111-1111-1111-1111-111111111111"
        user = "22222222-2222-2222-2222-222222222222"
        claims = base64.b64encode(json.dumps({"claims": [
            {"typ": "tid", "val": tenant}, {"typ": "oid", "val": user},
        ]}).encode()).decode()
        lab = MagicMock(REQUEST_TIMEOUT=60)
        lab.demonstrate_presenter.return_value = {"two_user_isolation": "NOT_RUN", "store_deleted": True}
        environment = {
            "UI_AZURE_HOSTED": "1", "WEBSITE_HOSTNAME": "lab.azurewebsites.net",
            "UI_PRESENTER_TENANT_ID": tenant, "UI_PRESENTER_OBJECT_ID": user,
            "UI_MEMORY_PROJECT_ENDPOINT": "https://memory.services.ai.azure.com/api/projects/custom",
        }
        headers = {"Host": "lab.azurewebsites.net", "Origin": "https://lab.azurewebsites.net"}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {**environment, "UI_JOB_STATE_DIR": directory}), \
                patch("ui.load_demo", return_value=lab), patch("ui.ManagedIdentityCredential") as credentials, \
                patch("ui.AIProjectClient") as projects:
            self.assertEqual(self.request("POST", "/api/native-memory", "{}", headers)[0], 403)
            headers["X-MS-CLIENT-PRINCIPAL"] = claims
            status, body = self.request("POST", "/api/native-memory", '{"scope":"attacker"}', headers)
            self.assertEqual(status, 200)
            job_id = json.loads(body)["id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = ui.read_job()
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["store_name"], f"ui-memory-{job_id}")
            self.assertEqual(job["result"]["two_user_isolation"], "NOT_RUN")
            lab.Identity.assert_called_once_with(tenant, user)
            lab.demonstrate_presenter.assert_called_once_with(
                projects.return_value.__enter__.return_value, "gpt-4.1-mini", "text-embedding-3-small",
                lab.Identity.return_value, f"ui-memory-{job_id}",
            )
            credentials.assert_called_once_with()

    def test_native_memory_local_identity_comes_from_graph(self):
        lab = MagicMock(REQUEST_TIMEOUT=60)
        environment = {
            "UI_AZURE_HOSTED": "0", "AZURE_CONFIG_DIR": "/tmp/workshop-user",
            "UI_MEMORY_PROJECT_ENDPOINT": "https://memory.services.ai.azure.com/api/projects/custom",
        }
        with patch.dict(os.environ, environment), patch("ui.load_demo", return_value=lab), \
                patch("ui.subprocess.check_output", return_value="tenant-id\n"), \
                patch("ui.AzureCliCredential"), patch("ui.AIProjectClient"):
            ui.native_memory("a" * 32)
            lab.authenticated_identity.assert_called_once_with(Path("/tmp/workshop-user").resolve(), "tenant-id")
            with self.assertRaisesRegex(ValueError, "Invalid memory job ID"):
                ui.native_memory("../store")
            with patch.dict(os.environ, {"UI_MEMORY_PROJECT_ENDPOINT": "https://evil.example/project"}):
                with self.assertRaisesRegex(RuntimeError, "lab 31 project"):
                    ui.native_memory("a" * 32)

    def test_hosted_question_uses_managed_identity_and_checks_evidence(self):
        response = {
            "status": "completed",
            "output": [
                {"type": "mcp_call", "name": "knowledge_base_retrieve",
                 "output": "CASE-1001", "error": None},
                {"type": "message", "content": [{"type": "output_text", "text": "CASE-1001"}]},
            ],
        }
        project = MagicMock()
        client = project.get_openai_client.return_value.__enter__.return_value
        client.responses.create.return_value.model_dump.return_value = response
        environment = {
            "UI_AZURE_HOSTED": "1",
            "FOUNDRY_PROJECT_ENDPOINT": "https://example.test/api/projects/project",
        }
        with patch.dict(os.environ, environment), \
                patch("ui.ManagedIdentityCredential") as credential, \
                patch("ui.AIProjectClient") as project_client:
            project_client.return_value.__enter__.return_value = project
            self.assertEqual(ui.ask("question"), "CASE-1001")
        credential.assert_called_once_with()
        client.responses.create.assert_called_once_with(
            input="question", tool_choice="required", timeout=180,
        )

        response["output"][0]["error"] = "failed"
        with patch.dict(os.environ, environment), \
                patch("ui.ManagedIdentityCredential"), \
                patch("ui.AIProjectClient") as project_client:
            project_client.return_value.__enter__.return_value = project
            with self.assertRaisesRegex(RuntimeError, "failed knowledge-base"):
                ui.ask("question")


if __name__ == "__main__":
    unittest.main()
