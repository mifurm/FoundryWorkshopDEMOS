from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch
from zipfile import ZipFile

from mcp import types
from openai.types.responses import Response

import demo


URI = "skill://workshop-brief/SKILL.md"
REFERENCE_URI = "skill://workshop-brief/references/checklist.md"


def response(text=None, call_uri=None, status="completed"):
    output = [{
        "type": "function_call", "name": "read_skill", "call_id": "call-1",
        "arguments": json.dumps({"uri": call_uri}), "id": "fc-1", "status": "completed",
    }] if call_uri else [{
        "type": "message", "role": "assistant", "id": "msg-1", "status": "completed",
        "content": [{"type": "output_text", "text": text or "", "annotations": []}],
    }]
    return Response.model_validate({
        "id": "resp-1", "object": "response", "created_at": 0, "status": status,
        "model": "test", "output": output, "parallel_tool_calls": False,
        "tool_choice": "auto", "tools": [],
    })


class PackageTests(TestCase):
    def test_all_packages_have_unique_frontmatter_and_expected_contract(self):
        for slug, _, expected in demo.CASES:
            with ZipFile(BytesIO(demo.package_skill(slug, f"{slug}-unique"))) as archive:
                self.assertEqual(
                    archive.namelist(),
                    ["SKILL.md", "references/checklist.md"] if slug == "workshop-brief" else ["SKILL.md"],
                )
                body = archive.read("SKILL.md").decode()
            self.assertIn(f"name: {slug}-unique\n", body)
            self.assertIn(expected["skill_code"], body)
            self.assertIn(f"name: {slug}\n", (demo.SKILLS / slug / "SKILL.md").read_text())


class DiscoveryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = AsyncMock()
        self.resource = types.Resource(uri=URI, name="workshop-brief", description="A briefing skill.")
        self.resources = {URI: self.resource}
        self.expected = demo.CASES[0][2]
        self.client = Mock()
        self.client.responses.create.side_effect = [
            response(call_uri=URI), response(call_uri=REFERENCE_URI),
            response(json.dumps(self.expected)),
        ]
        self.session.read_resource.side_effect = [
            types.ReadResourceResult(contents=[types.TextResourceContents(
                uri=URI, text="BRIEF-29\nRead [the workshop checklist](references/checklist.md)",
            )]),
            types.ReadResourceResult(contents=[
                types.TextResourceContents(uri=REFERENCE_URI, text=json.dumps(self.expected)),
            ]),
        ]

    async def run_case(self):
        with redirect_stdout(StringIO()):
            await demo.run_case(self.session, self.client, "test", self.resources,
                                "Prepare a briefing.", self.expected)

    async def test_paginates_without_reading_bodies(self):
        self.session.list_resources.side_effect = [
            types.ListResourcesResult(resources=[self.resource], nextCursor="next"),
            types.ListResourcesResult(resources=[]),
        ]
        self.assertEqual(await demo.discover(self.session), self.resources)
        self.session.read_resource.assert_not_called()
        self.assertEqual(self.session.list_resources.call_args_list[1].kwargs, {"cursor": "next"})

    async def test_rejects_missing_duplicate_and_unbounded_discovery(self):
        pages = [
            [types.ListResourcesResult(resources=[])],
            [types.ListResourcesResult(resources=[self.resource], nextCursor="next"),
             types.ListResourcesResult(resources=[self.resource])],
            [types.ListResourcesResult(resources=[], nextCursor="next")] * 2,
            [types.ListResourcesResult(resources=[
                types.Resource(uri=URI, name="missing-description"),
            ])],
        ]
        for sequence in pages:
            self.session.list_resources.side_effect = sequence
            with self.subTest(sequence=sequence), self.assertRaises(RuntimeError):
                await demo.discover(self.session)

    async def test_model_selects_and_reads_skill_then_follows_exact_contract(self):
        await self.run_case()
        self.assertEqual(self.session.read_resource.await_count, 2)
        calls = self.client.responses.create.call_args_list
        self.assertIsNone(calls[0].kwargs["previous_response_id"])
        self.assertNotIn("BRIEF-29", calls[0].kwargs["instructions"])
        self.assertEqual(calls[1].kwargs["previous_response_id"], "resp-1")
        self.assertIn("BRIEF-29", calls[1].kwargs["input"][0]["output"])
        self.assertNotIn("CHECKLIST-29", calls[1].kwargs["input"][0]["output"])
        self.assertIn("CHECKLIST-29", calls[2].kwargs["input"][0]["output"])

    async def test_requires_real_mcp_read_not_just_correct_model_text(self):
        self.client.responses.create.side_effect = [response(json.dumps(self.expected))]
        with self.assertRaisesRegex(RuntimeError, "relevant skill"):
            await self.run_case()

    async def test_rejects_wrong_uri_blob_wrong_source_or_wrong_contract(self):
        for variant in ("unknown-uri", "blob", "wrong-source", "wrong-contract", "wrong-skill"):
            self.setUp()
            if variant == "unknown-uri":
                self.client.responses.create.side_effect = [response(call_uri="skill://not-discovered/x")]
            elif variant == "blob":
                self.session.read_resource.side_effect = [types.ReadResourceResult(contents=[
                    types.BlobResourceContents(uri=URI, blob="eA=="),
                ])]
            elif variant == "wrong-source":
                self.session.read_resource.side_effect = [types.ReadResourceResult(contents=[
                    types.TextResourceContents(uri="skill://other/x", text="BRIEF-29"),
                ])]
            elif variant == "wrong-skill":
                self.session.read_resource.side_effect = [types.ReadResourceResult(contents=[
                    types.TextResourceContents(uri=URI, text="SUMMARY-29"),
                ])]
            else:
                self.client.responses.create.side_effect = [
                    response(call_uri=URI), response(call_uri=REFERENCE_URI),
                    response('{"wrong":true}'),
                ]
            with self.subTest(variant=variant), self.assertRaises(RuntimeError):
                await self.run_case()

    async def test_supplementary_read_is_required_and_cannot_escape_the_skill_directory(self):
        for second_uri in (None, "skill://workshop-brief/../secret.md"):
            self.setUp()
            self.client.responses.create.side_effect = [
                response(call_uri=URI),
                response(call_uri=second_uri) if second_uri else response(json.dumps(self.expected)),
            ]
            with self.subTest(second_uri=second_uri), self.assertRaises(RuntimeError):
                await self.run_case()

    async def test_reference_is_not_loaded_for_instruction_only_skills(self):
        self.expected = demo.CASES[1][2]
        self.session.read_resource.side_effect = [types.ReadResourceResult(contents=[
            types.TextResourceContents(uri=URI, text="TRIAGE-29"),
        ])]
        self.client.responses.create.side_effect = [
            response(call_uri=URI), response(json.dumps(self.expected)),
        ]
        await self.run_case()
        self.session.read_resource.assert_awaited_once()

    async def test_repeated_reads_incomplete_response_and_service_errors_surface(self):
        for sequence in (
            [response(call_uri=URI), response(call_uri=URI)],
            [response("text", status="incomplete")],
            [RuntimeError("service unavailable")],
        ):
            self.client.responses.create.side_effect = sequence
            with self.assertRaises(RuntimeError):
                await self.run_case()

    async def test_cleanup_in_reverse_dependency_order_after_mcp_failure(self):
        project = Mock()
        project.beta.skills.create_from_files.side_effect = [
            Mock(version="1"), Mock(version="2"), Mock(version="3"),
        ]
        project.toolboxes.create_version.return_value = Mock(name="toolbox-test", version="7")
        cleanup = []
        project.toolboxes.delete.side_effect = lambda **_: cleanup.append("toolbox")
        project.beta.skills.delete.side_effect = lambda **_: cleanup.append("skill")
        credential = Mock()
        credential.get_token.side_effect = RuntimeError("token unavailable")
        with redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, "token unavailable"):
            await demo.demonstrate(project, credential, "https://project.example/api/projects/p", "test")
        self.assertEqual(cleanup, ["toolbox", "skill", "skill", "skill"])
        args = project.toolboxes.create_version.call_args.kwargs
        self.assertEqual(args["tools"], [])
        self.assertEqual([item.version for item in args["skills"]], ["1", "2", "3"])

    async def test_partial_upload_failure_still_deletes_created_skills(self):
        project = Mock()
        project.beta.skills.create_from_files.side_effect = [
            Mock(version="1"), RuntimeError("upload failed"),
        ]
        with redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, "upload failed"):
            await demo.demonstrate(project, Mock(), "https://project.example/api/projects/p", "test")
        project.beta.skills.delete.assert_called_once()
        project.toolboxes.create_version.assert_not_called()
