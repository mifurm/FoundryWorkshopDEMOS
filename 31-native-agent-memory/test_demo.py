from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from azure.ai.projects import AIProjectClient, models
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.core.utils import CaseInsensitiveDict
from openai.types.responses import Response

import demo


TENANT = "11111111-1111-1111-1111-111111111111"
FIRST = demo.Identity(TENANT, "22222222-2222-2222-2222-222222222222")
SECOND = demo.Identity(TENANT, "33333333-3333-3333-3333-333333333333")


def usage():
    return models.MemoryStoreOperationUsage(
        embedding_tokens=1, input_tokens=2, output_tokens=3, total_tokens=5,
        input_tokens_details=models.ResponseUsageInputTokensDetails(cached_tokens=0, cache_write_tokens=0),
        output_tokens_details=models.ResponseUsageOutputTokensDetails(reasoning_tokens=0),
    )


def item(scope, content):
    return models.MemoryItem(
        memory_id="memory-test", scope=scope, content=content, kind="user_profile",
        updated_at=datetime.now(timezone.utc),
    )


def response(text, number=1, status="completed"):
    return Response.model_validate({
        "id": f"resp-{number}", "object": "response", "created_at": 0, "model": "test",
        "status": status, "parallel_tool_calls": False, "tool_choice": "none", "tools": [],
        "output": [{"id": f"msg-{number}", "type": "message", "role": "assistant",
                    "status": "completed", "content": [
                        {"type": "output_text", "text": text, "annotations": []},
                    ]}],
    })


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.project = MagicMock()
        self.store = self.project.beta.memory_stores
        self.saved = {}
        self.clients = []
        self.answers = []
        self.pollers = []
        self.output = StringIO()
        self.store.delete.return_value = models.DeleteMemoryStoreResult(name="test", deleted=True)
        self.project.agents.delete.return_value = models.DeleteAgentResponse(name="test", deleted=True)
        self.project.agents.create_version.side_effect = lambda **kw: SimpleNamespace(
            name=kw["agent_name"], version="1",
        )

        def update(**kw):
            text = kw["items"][0]["content"]
            self.saved[kw["scope"]] = text
            result = models.MemoryStoreUpdateCompletedResult(
                memory_operations=[models.MemoryOperation(
                    kind=models.MemoryOperationKind.CREATE, memory_item=item(kw["scope"], text),
                )], usage=usage(),
            )
            poller = Mock()
            poller.update_id = "update-test"
            poller.superseded_by = None
            poller.result.return_value = result
            poller.done.return_value = True
            poller.status.return_value = models.MemoryStoreUpdateStatus.COMPLETED
            self.pollers.append(poller)
            return poller

        def search(**kw):
            content = self.saved.get(kw["scope"])
            return models.MemoryStoreSearchResult(
                search_id="search-test", usage=usage(),
                memories=[models.MemorySearchItem(memory_item=item(kw["scope"], content))]
                if content else [],
            )

        def delete_scope(**kw):
            self.saved.pop(kw["scope"], None)
            return models.MemoryStoreDeleteScopeResult(name=kw["name"], scope=kw["scope"], deleted=True)

        def openai_client(**kw):
            client = MagicMock()
            client.__enter__.return_value = client
            number = len(self.clients) + 1
            client.conversations.create.return_value = SimpleNamespace(id=f"conv-{number}")

            def answer(**request):
                if request["input"] != demo.QUESTION:
                    text = "Acknowledged."
                else:
                    instructions = self.project.agents.create_version.call_args.kwargs["definition"].instructions
                    memories = json.loads(instructions.split("Retrieved native memories: ", 1)[1])
                    code = memories[0].split("blend is ", 1)[1].split(".", 1)[0] if memories else None
                    text = json.dumps({"coffee": code})
                result = response(text, number)
                self.answers.append(result)
                return result

            client.responses.create.side_effect = answer
            self.clients.append(client)
            return client

        self.store.begin_update_memories.side_effect = update
        self.store.search_memories.side_effect = search
        self.store.delete_scope.side_effect = delete_scope
        self.store.list_memories.return_value = []
        self.project.get_openai_client.side_effect = openai_client

    def run_demo(self):
        with redirect_stdout(self.output):
            demo.demonstrate(self.project, "chat", "embedding", FIRST, SECOND)

    def assert_store_cleaned(self):
        name = self.store.create.call_args.kwargs["name"]
        self.store.delete.assert_called_once_with(name=name)

    def test_native_extraction_independent_recall_scoped_forgetting_and_cleanup(self):
        self.run_demo()
        self.assertEqual(len(self.clients), 5)
        self.assertEqual(self.project.agents.delete.call_count, 5)
        self.assert_store_cleaned()
        definition = self.store.create.call_args.kwargs["definition"]
        self.assertIsInstance(definition, models.MemoryStoreDefaultDefinition)
        self.assertEqual(definition.as_dict()["options"]["default_ttl_seconds"], 86400)
        self.assertEqual(definition.embedding_model, "embedding")
        self.assertFalse(definition.options.chat_summary_enabled)
        self.assertIn("PASS:", self.output.getvalue())
        for index, client in enumerate(self.clients):
            request = client.responses.create.call_args.kwargs
            self.assertEqual(request["conversation"], f"conv-{index + 1}")
            self.assertNotIn("previous_response_id", request)
            self.assertNotIn("previous_search_id", request)
            self.assertNotIn("extra_headers", request)
            self.assertIsInstance(request["input"], str)
            self.assertEqual(request["extra_body"]["agent_reference"]["version"], "1")
            client.responses.delete.assert_called_once_with(f"resp-{index + 1}")
            client.conversations.delete.assert_called_once_with(f"conv-{index + 1}")
            if index >= 2:
                self.assertEqual(request["input"], demo.QUESTION)
                self.assertNotIn("ORCHID", request["input"])
                self.assertNotIn("CEDAR", request["input"])
        agents = self.project.agents.create_version.call_args_list
        self.assertTrue(all(call.kwargs["definition"].tools == [] for call in agents))
        self.assertIn("ORCHID", agents[2].kwargs["definition"].instructions)
        self.assertNotIn("CEDAR", agents[2].kwargs["definition"].instructions)
        self.assertIn("CEDAR", agents[3].kwargs["definition"].instructions)
        self.assertNotIn("ORCHID", agents[3].kwargs["definition"].instructions)
        self.assertNotIn("ORCHID", agents[4].kwargs["definition"].instructions)
        updates = self.store.begin_update_memories.call_args_list
        self.assertEqual([call.kwargs["scope"] for call in updates], [FIRST.scope, SECOND.scope])
        for i, call in enumerate(updates):
            self.assertEqual(call.kwargs["items"], [
                demo.message(self.clients[i].responses.create.call_args.kwargs["input"]),
            ])
            self.assertEqual(call.kwargs["update_delay"], 0)
            self.assertNotIn("previous_update_id", call.kwargs)
            self.pollers[i].result.assert_called_once_with(timeout=180)
        for call in self.store.search_memories.call_args_list:
            self.assertEqual(call.kwargs["items"], [demo.message(demo.QUESTION)])
            self.assertIsInstance(call.kwargs["options"], models.MemorySearchOptions)
            self.assertNotIn("previous_search_id", call.kwargs)
        self.store.delete_scope.assert_called_once_with(
            name=self.store.create.call_args.kwargs["name"], scope=FIRST.scope,
        )
        self.assertNotIn(FIRST.scope, self.saved)
        self.assertIn(SECOND.scope, self.saved)

    def test_creation_failure_still_attempts_store_delete(self):
        self.store.create.side_effect = RuntimeError("create failed")
        with self.assertRaisesRegex(RuntimeError, "create failed"):
            self.run_demo()
        self.assert_store_cleaned()

    def test_extraction_failure_cleans_up_and_does_not_claim_success(self):
        self.store.begin_update_memories.side_effect = RuntimeError("extraction failed")
        with self.assertRaisesRegex(RuntimeError, "extraction failed"):
            self.run_demo()
        self.assert_store_cleaned()
        self.project.agents.delete.assert_called_once()
        self.assertNotIn("PASS:", self.output.getvalue())

    def test_inference_failure_deletes_conversation_agent_and_store(self):
        with patch.object(demo, "check_answer", side_effect=RuntimeError("wrong answer")):
            with self.assertRaisesRegex(RuntimeError, "wrong answer"):
                self.run_demo()
        self.assert_store_cleaned()
        self.assertEqual(self.project.agents.delete.call_count, 3)
        self.clients[-1].responses.delete.assert_called_once()
        self.clients[-1].conversations.delete.assert_called_once()

    def test_all_cleanup_attempted_even_if_response_delete_fails(self):
        with patch.object(demo, "check_answer", side_effect=RuntimeError("wrong answer")):
            self.project.agents.delete.side_effect = RuntimeError("delete failed")
            with self.assertRaisesRegex(RuntimeError, "Cleanup failed"):
                self.run_demo()
        self.assert_store_cleaned()
        self.clients[0].responses.delete.assert_called_once()
        self.clients[0].conversations.delete.assert_called_once()

    def test_cleanup_errors_chain_primary_and_do_not_stop_later_actions(self):
        later = Mock()
        with self.assertRaisesRegex(RuntimeError, "Cleanup failed") as raised:
            with demo.cleanup_resources() as actions:
                actions.append(("later", later))
                actions.append(("failure", Mock(side_effect=ValueError("cleanup"))))
                raise LookupError("primary")
        self.assertIsInstance(raised.exception.__cause__, LookupError)
        later.assert_called_once()

    def test_incomplete_or_failed_model_request_cleans_openai_before_client_close(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                client = MagicMock()
                client.__enter__.return_value = client
                client.conversations.create.return_value = SimpleNamespace(id="conv-failure")
                if failed:
                    client.responses.create.side_effect = RuntimeError("inference request failed")
                else:
                    client.responses.create.return_value = response("", status="incomplete")
                self.project.get_openai_client.side_effect = None
                self.project.get_openai_client.return_value = client
                with redirect_stdout(self.output), self.assertRaises(RuntimeError):
                    demo.fresh_conversation(self.project, "chat", demo.QUESTION, [])
                client.conversations.delete.assert_called_once_with("conv-failure")
                if not failed:
                    client.responses.delete.assert_called_once_with("resp-1")

    def test_search_errors_never_become_empty_memory(self):
        self.store.search_memories.side_effect = RuntimeError("search failed")
        with self.assertRaisesRegex(RuntimeError, "search failed"):
            demo.wait_for_search(self.store, "s", FIRST, None)

    def test_search_rejects_wrong_scope_and_foreign_preference(self):
        self.store.search_memories.side_effect = None
        self.store.search_memories.return_value = models.MemoryStoreSearchResult(
            search_id="s", usage=usage(),
            memories=[models.MemorySearchItem(memory_item=item(SECOND.scope, "secret"))],
        )
        with self.assertRaisesRegex(RuntimeError, "outside"):
            demo.search(self.store, "store", FIRST)
        self.store.search_memories.return_value = models.MemoryStoreSearchResult(
            search_id="s", usage=usage(),
            memories=[models.MemorySearchItem(memory_item=item(FIRST.scope, "OTHER"))],
        )
        with self.assertRaisesRegex(RuntimeError, "leakage"):
            demo.wait_for_search(self.store, "store", FIRST, "MINE", ("OTHER",))

    def test_search_timeout_is_not_success(self):
        with patch.object(demo, "monotonic", side_effect=[0, 181]):
            with self.assertRaises(TimeoutError):
                demo.wait_for_search(self.store, "store", FIRST, "MISSING")

    def test_extract_rejects_timeout_superseded_failed_and_empty_operations(self):
        for state in ("timeout", "superseded", "failed", "empty"):
            with self.subTest(state=state):
                poller = Mock(update_id="u", superseded_by=None)
                poller.done.return_value = state != "timeout"
                poller.status.return_value = "failed" if state == "failed" else "completed"
                if state == "superseded":
                    poller.superseded_by = "new"
                poller.result.return_value = models.MemoryStoreUpdateCompletedResult(
                    memory_operations=[], usage=usage(),
                )
                self.store.begin_update_memories.side_effect = None
                self.store.begin_update_memories.return_value = poller
                with redirect_stdout(self.output), self.assertRaises((RuntimeError, TimeoutError)):
                    demo.extract(self.store, "s", FIRST, "preference", "CODE")

    def test_delete_not_confirmed_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, "confirm"):
            demo.require_deleted(models.DeleteMemoryStoreResult(name="s", deleted=False), "store")

    def test_forgetting_failure_does_not_claim_success_and_cleans_store(self):
        self.store.delete_scope.side_effect = RuntimeError("forget failed")
        with self.assertRaisesRegex(RuntimeError, "forget failed"):
            self.run_demo()
        self.assert_store_cleaned()
        self.assertNotIn("PASS:", self.output.getvalue())

    def test_exact_answer_rejects_missing_wrong_extra_and_invalid_json(self):
        for text in ('{"coffee":"wrong"}', "{}", '{"coffee":null,"extra":1}', "not JSON"):
            with self.subTest(text=text), self.assertRaises((RuntimeError, ValueError)):
                demo.check_answer(text, "CODE")

    def test_preflight_missing_config_fails_before_any_cloud_client(self):
        with patch.dict(demo.os.environ, {}, clear=True), patch.object(demo, "AIProjectClient") as client:
            with self.assertRaises(SystemExit):
                demo.main(["--preflight"])
            client.assert_not_called()

    def test_preflight_rejects_same_user_and_same_directory(self):
        with patch.object(Path, "is_dir", return_value=True), \
             patch.object(demo, "authenticated_identity", return_value=FIRST), \
             patch.dict(demo.os.environ, {"AZURE_CONFIG_DIR": "first-login"}):
            with self.assertRaisesRegex(ValueError, "different real"):
                demo.preflight(TENANT, "second-login")
            with self.assertRaisesRegex(ValueError, "distinct existing"):
                demo.preflight(TENANT, "first-login")

    def test_preflight_mode_authenticates_without_constructing_foundry_client(self):
        with patch.object(demo, "preflight", return_value=(FIRST, SECOND)) as identities, \
             patch.object(demo, "AIProjectClient") as project, redirect_stdout(self.output):
            demo.main(["--preflight", "--tenant", TENANT, "--second-config", "second-login"])
        identities.assert_called_once_with(TENANT, "second-login")
        project.assert_not_called()
        self.assertIn("permissions/availability are not yet verified", self.output.getvalue())

    def test_real_graph_me_identity_not_caller_supplied_name(self):
        result = SimpleNamespace(stdout=json.dumps({"accessToken": "sensitive-test-token"}))
        http = MagicMock()
        http.__enter__.return_value = StringIO(json.dumps({"id": FIRST.user}))
        with patch.object(demo.subprocess, "run", return_value=result) as command, \
             patch.object(demo, "urlopen", return_value=http) as graph:
            identity = demo.authenticated_identity(Path("first-login"), TENANT)
        self.assertEqual(identity, FIRST)
        self.assertIn("--tenant", command.call_args.args[0])
        self.assertEqual(command.call_args.kwargs["timeout"], 60)
        self.assertEqual(graph.call_args.args[0].full_url, "https://graph.microsoft.com/v1.0/me?$select=id")
        self.assertEqual(graph.call_args.kwargs["timeout"], 30)
        self.assertNotIn("sensitive-test-token", self.output.getvalue())

    def test_scope_is_stable_tenant_bound_and_not_a_name(self):
        self.assertEqual(FIRST.scope, demo.Identity(TENANT.upper(), FIRST.user.upper()).scope)
        self.assertNotEqual(FIRST.scope, SECOND.scope)
        self.assertNotEqual(FIRST.scope, demo.Identity(SECOND.user, FIRST.user).scope)
        with self.assertRaises(ValueError):
            demo.Identity(TENANT, "alice")
        with self.assertRaises(ValueError):
            demo.demonstrate(self.project, "chat", "emb", FIRST, FIRST)
        self.store.create.assert_not_called()


class WireResponse(HttpResponse):
    def __init__(self, request, payload):
        super().__init__(request, None)
        self.status_code = 200
        self.headers = CaseInsensitiveDict({"content-type": "application/json"})
        self.content_type = "application/json"
        self.payload = payload

    def body(self):
        return json.dumps(self.payload).encode()

    def text(self, encoding=None):
        return self.body().decode(encoding or "utf-8")

    def json(self):
        return self.payload

    def read(self):
        return self.body()

    def iter_bytes(self):
        yield self.body()

    def iter_raw(self):
        yield self.body()


class WireTransport(HttpTransport):
    """Exercise installed SDK serialization and poller without making HTTP requests."""

    def __init__(self):
        self.requests = []

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        self.requests.append(request)
        wire_item = {
            "memory_id": "m", "scope": FIRST.scope, "content": "ORCHID123",
            "kind": "user_profile", "updated_at": 1793048400,
        }
        if ":update_memories" in request.url or "/updates/" in request.url:
            payload = {
                "update_id": "u", "status": "completed",
                "result": {"memory_operations": [{"kind": "create", "memory_item": wire_item}],
                           "usage": usage().as_dict()},
            }
        elif ":search_memories" in request.url:
            payload = {"search_id": "s", "memories": [{"memory_item": wire_item}], "usage": usage().as_dict()}
        elif ":delete_scope" in request.url:
            payload = {"name": "wire", "scope": FIRST.scope, "deleted": True}
        elif request.method == "DELETE":
            payload = {"name": "wire", "deleted": True}
        else:
            payload = {
                "id": "store-id", "name": "wire", "created_at": 1793048400,
                "definition": {"kind": "default", "chat_model": "chat", "embedding_model": "emb"},
            }
        response = WireResponse(request, payload)
        if ":update_memories" in request.url:
            response.status_code = 202
            response.payload = {"update_id": "u", "status": "queued"}
        return response


class SDKWireTests(unittest.TestCase):
    def test_installed_sdk_serializes_native_requests_and_deserializes_results(self):
        credential = SimpleNamespace(get_token=lambda *args, **kwargs: AccessToken("offline-only", 9999999999))
        transport = WireTransport()
        with AIProjectClient(
            endpoint="https://offline.services.ai.azure.com/api/projects/test",
            credential=credential, allow_preview=True, transport=transport,
        ) as project:
            store = project.beta.memory_stores
            store.create(name="wire", definition=models.MemoryStoreDefaultDefinition(
                chat_model="chat", embedding_model="emb",
                options=models.MemoryStoreDefaultOptions(
                    user_profile_enabled=True, chat_summary_enabled=False,
                ),
            ))
            with redirect_stdout(StringIO()):
                demo.extract(store, "wire", FIRST, "My blend is ORCHID123", "ORCHID123")
            self.assertEqual(demo.search(store, "wire", FIRST), ["ORCHID123"])
            demo.require_deleted(store.delete_scope(name="wire", scope=FIRST.scope), "scope")
            demo.require_deleted(store.delete(name="wire"), "store")
        updates = [request for request in transport.requests if ":update_memories" in request.url]
        self.assertEqual(len(updates), 1)
        body = json.loads(updates[0].body)
        self.assertEqual(body["scope"], FIRST.scope)
        self.assertEqual(body["items"], [{
            "type": "message", "role": "user",
            "content": "My blend is ORCHID123",
        }])
        self.assertEqual(body["update_delay"], 0)
        searches = [request for request in transport.requests if ":search_memories" in request.url]
        self.assertEqual(json.loads(searches[0].body)["scope"], FIRST.scope)
        self.assertTrue(any("/updates/u" in request.url for request in transport.requests))
        self.assertTrue(all("api-version=" in request.url for request in transport.requests))


if __name__ == "__main__":
    unittest.main()
