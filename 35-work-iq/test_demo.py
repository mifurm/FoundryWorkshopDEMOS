from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx

import demo
from configure_connection import connection_body


TENANT = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"
SECOND = "33333333-3333-3333-3333-333333333333"
ACTOR = {"user_id": USER, "user_principal_name": "user@example.test"}
PROJECT = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/lab/"
    "providers/Microsoft.CognitiveServices/accounts/lab/projects/work-iq"
)
CONFIG = {
    "tenantId": TENANT, "deployment": "model", "projectResourceId": PROJECT,
    "projectEndpoint": "https://lab.services.ai.azure.com/api/projects/work-iq",
    "connectionId": PROJECT + "/connections/" + demo.CONNECTION,
}


def answer_response(actor=None):
    return {"id": "resp-one", "status": "completed", "error": None, "output": [{
        "type": "message", "status": "completed",
        "content": [{"type": "output_text", "text": json.dumps(actor or ACTOR)}],
    }]}


def reply(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("GET", "https://example.test/"))


class WorkIQTests(unittest.TestCase):
    def test_native_tool_has_no_shared_token_or_local_runner(self):
        value = demo.agent_definition(CONFIG).as_dict()
        self.assertEqual(value["tools"], [{
            "type": "work_iq_preview", "project_connection_id": CONFIG["connectionId"],
        }])
        self.assertNotIn(USER, demo.QUESTION + demo.INSTRUCTIONS)
        self.assertNotIn(ACTOR["user_principal_name"], demo.QUESTION + demo.INSTRUCTIONS)

    def test_config_uses_outputs_and_rejects_mismatched_destination(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(demo, "HERE", Path(folder)):
            path = Path(folder) / ".azure"
            path.mkdir()
            (path / "deployment.json").write_text(json.dumps({
                "outputs": {key: {"value": value} for key, value in CONFIG.items()},
            }))
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(demo.load_config(), CONFIG)
            with patch.dict(os.environ, {"FOUNDRY_PROJECT_ENDPOINT": "https://untrusted.test"}):
                with self.assertRaisesRegex(ValueError, "must match"):
                    demo.load_config()

    def test_connection_body_is_delegated_and_tenant_pinned(self):
        props = connection_body(TENANT, SECOND, "test-secret")["properties"]
        self.assertEqual(props["authType"], "OAuth2")
        self.assertEqual(props["category"], "RemoteA2A")
        self.assertEqual(props["target"], demo.TARGET)
        self.assertEqual(props["Scopes"], [
            "api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask", "offline_access",
        ])
        self.assertIn(TENANT, props["AuthorizationUrl"])
        with self.assertRaises(ValueError):
            connection_body(TENANT, SECOND, "")

    def test_connection_rejects_shared_or_cross_tenant_auth(self):
        props = connection_body(TENANT, SECOND, "test-secret")["properties"]
        http, credential = MagicMock(), MagicMock()
        http.get.return_value = reply(200, {"properties": props})
        demo.check_connection(http, credential, CONFIG)
        for field, value in (("authType", "ManagedIdentity"), ("category", "RemoteTool"),
                             ("target", "https://example.test"),
                             ("Scopes", ["offline_access"]),
                             ("TokenUrl", "https://login.microsoftonline.com/common/oauth2/v2.0/token")):
            with self.subTest(field=field):
                http.get.return_value = reply(200, {"properties": {**props, field: value}})
                with self.assertRaises(RuntimeError):
                    demo.check_connection(http, credential, CONFIG)
        http.get.return_value = reply(404, {})
        with self.assertRaisesRegex(RuntimeError, "missing"):
            demo.check_connection(http, credential, CONFIG)
        http.get.return_value = reply(403, {})
        with self.assertRaises(httpx.HTTPStatusError):
            demo.check_connection(http, credential, CONFIG)

    def test_ground_truth_comes_from_authenticated_graph(self):
        http, credential = MagicMock(), MagicMock()
        http.get.return_value = reply(200, {"id": USER, "userPrincipalName": "User@Example.Test"})
        self.assertEqual(demo.caller_identity(http, credential), ACTOR)
        self.assertIn("/me?$select=id,userPrincipalName", http.get.call_args.args[0])
        http.get.return_value = reply(403, {"error": "denied"})
        with self.assertRaises(httpx.HTTPStatusError):
            demo.caller_identity(http, credential)

    def test_exact_identity_gate_rejects_other_browser_account(self):
        self.assertEqual(demo.validate_answer(answer_response(), ACTOR), ACTOR)
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            demo.validate_answer(answer_response({**ACTOR, "user_id": SECOND}), ACTOR)

    def test_identity_gate_rejects_incomplete_refusal_and_invalid_json(self):
        for raw in (
            {**answer_response(), "status": "failed"},
            {**answer_response(), "error": {"code": "denied"}},
            {**answer_response(), "output": []},
            answer_response({**ACTOR, "extra": True}),
            answer_response({**ACTOR, "user_principal_name": None}),
        ):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                demo.validate_answer(raw, ACTOR)

    def test_consent_is_private_and_bound_to_caller_and_version(self):
        binding = demo.context_binding(CONFIG, ACTOR, "1")
        raw = {"id": "resp-consent", "output": [{
            "type": "oauth_consent_request", "consent_link": "https://consent.example.test/login?state=opaque",
        }]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "consent.json"
            self.assertTrue(demo.validate_consent(raw, binding, path))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(demo.resume_response(path, binding), "resp-consent")
            for other in ({**binding, "actor": {**ACTOR, "user_id": SECOND}},
                          {**binding, "version": "2"}, {**binding, "tenant": SECOND}):
                with self.assertRaisesRegex(ValueError, "different"):
                    demo.resume_response(path, other)
            state = json.loads(path.read_text())
            state["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            path.write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError, "expired"):
                demo.resume_response(path, binding)
        self.assertFalse(demo.validate_consent(answer_response(), binding, Path("unused")))

    def test_invalid_consent_never_becomes_a_success(self):
        for link in ("http://example.test", "https://user:password@example.test"):
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                demo.validate_consent({"id": "resp", "output": [{
                    "type": "oauth_consent_request", "consent_link": link,
                }]}, {}, Path("unused"))

    def test_two_user_comparison_requires_distinct_grounded_requests(self):
        first = {
            **demo.context_binding(CONFIG, ACTOR, "1"), "complete": True,
            "cases": [{"response_id": "resp-first", "answer": ACTOR}],
        }
        actor2 = {**ACTOR, "user_id": SECOND, "user_principal_name": "second@example.test"}
        second = {
            **demo.context_binding(CONFIG, actor2, "1"), "complete": True,
            "cases": [{"response_id": "resp-second", "answer": actor2}],
        }
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ("first.json", "second.json")]
            for path, record in zip(paths, (first, second)):
                path.write_text(json.dumps(record))
            self.assertTrue(demo.verify_isolation(paths)["complete"])
            for bad in (
                first, {**second, "complete": False}, {**second, "version": "2"},
                {**second, "cases": []},
                {**second, "cases": [{"response_id": "resp-first", "answer": actor2}]},
                {**second, "cases": [{"response_id": "resp-second", "answer": ACTOR}]},
            ):
                paths[1].write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    demo.verify_isolation(paths)

    def test_live_request_pins_version_forces_native_tool_and_resets_history(self):
        project = MagicMock()
        agent = MagicMock(name="saved-agent")
        agent.version = "1"
        agent.definition = demo.agent_definition(CONFIG)
        project.agents.create_version.return_value = agent
        project.agents.get_version.return_value = agent
        client = project.get_openai_client.return_value.__enter__.return_value
        client.responses.with_raw_response.create.return_value.http_response.json.side_effect = [
            answer_response(), {**answer_response(), "id": "resp-two"},
        ]
        with (
            tempfile.TemporaryDirectory() as folder, patch.object(demo, "HERE", Path(folder)),
            patch.object(demo, "load_config", return_value=CONFIG),
            patch.object(demo, "caller_identity", return_value=ACTOR),
            patch.object(demo, "check_connection"),
            patch.object(demo, "AIProjectClient") as factory,
            patch.object(demo, "AzureCliCredential"),
        ):
            factory.return_value.__enter__.return_value = project
            demo.main([])
            calls = client.responses.with_raw_response.create.call_args_list
            self.assertEqual(len(calls), 2)
            for call in calls:
                self.assertEqual(call.kwargs["tool_choice"], "required")
                self.assertEqual(call.kwargs["extra_body"]["agent_reference"]["version"], "1")
                self.assertNotIn("previous_response_id", call.kwargs)
            record = json.loads(next((Path(folder) / ".azure").glob("*-report.json")).read_text())
            self.assertTrue(record["complete"])

    def test_missing_connection_leaves_failed_report_and_never_calls_model(self):
        project = MagicMock()
        agent = MagicMock()
        agent.version = "1"
        agent.definition = demo.agent_definition(CONFIG)
        project.agents.get_version.return_value = agent
        with (
            tempfile.TemporaryDirectory() as folder, patch.object(demo, "HERE", Path(folder)),
            patch.object(demo, "load_config", return_value=CONFIG),
            patch.object(demo, "caller_identity", return_value=ACTOR),
            patch.object(demo, "check_connection", side_effect=RuntimeError("missing connection")),
            patch.object(demo, "AIProjectClient") as factory,
            patch.object(demo, "AzureCliCredential"),
        ):
            factory.return_value.__enter__.return_value = project
            with self.assertRaisesRegex(RuntimeError, "missing connection"):
                demo.main([])
            project.get_openai_client.assert_not_called()
            record = json.loads(next((Path(folder) / ".azure").glob("*-report.json")).read_text())
            self.assertFalse(record["complete"])

    def test_consent_request_is_not_reported_as_success(self):
        project = MagicMock()
        agent = MagicMock()
        agent.version = "1"
        agent.definition = demo.agent_definition(CONFIG)
        project.agents.get_version.return_value = agent
        client = project.get_openai_client.return_value.__enter__.return_value
        client.responses.with_raw_response.create.return_value.http_response.json.return_value = {
            "id": "resp-consent", "status": "completed", "output": [{
                "type": "oauth_consent_request", "consent_link": "https://consent.example.test",
            }],
        }
        with (
            tempfile.TemporaryDirectory() as folder, patch.object(demo, "HERE", Path(folder)),
            patch.object(demo, "load_config", return_value=CONFIG),
            patch.object(demo, "caller_identity", return_value=ACTOR),
            patch.object(demo, "check_connection"),
            patch.object(demo, "AIProjectClient") as factory,
            patch.object(demo, "AzureCliCredential"),
        ):
            factory.return_value.__enter__.return_value = project
            with self.assertRaises(demo.ConsentRequired):
                demo.main([])
            record = json.loads(next((Path(folder) / ".azure").glob("*-report.json")).read_text())
            self.assertFalse(record["complete"])
            self.assertEqual(record["cases"], [])
            self.assertEqual(client.responses.with_raw_response.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
