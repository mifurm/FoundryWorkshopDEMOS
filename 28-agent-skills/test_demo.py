from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zipfile import BadZipFile, ZipFile

from openai.types.responses import Response

import demo


TITLE = "Introduction to Microsoft Foundry"


def answer(title=TITLE):
    return json.dumps({
        "title": title,
        "before": "Sign in with Azure CLI.",
        "during": "Use synthetic data only.",
        "after": "Delete the demo resource group.",
        "skill_code": "WORKSHOP-BRIEF-V1",
    })


def response(text, status="completed"):
    return Response.model_validate({
        "id": "resp-test", "object": "response", "created_at": 0,
        "model": "test-model", "status": status, "parallel_tool_calls": False,
        "tool_choice": "none", "tools": [],
        "output": [{
            "id": "msg-test", "type": "message", "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                  "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
    })


class SkillsTests(unittest.TestCase):
    def setUp(self):
        self.project = Mock()
        self.client = Mock()
        self.project.get_openai_client.return_value.__enter__ = Mock(return_value=self.client)
        self.project.get_openai_client.return_value.__exit__ = Mock(return_value=False)
        self.client.responses.create.side_effect = [
            response("A general workshop briefing."), response(answer()),
        ]
        self.project.agents.create_version.side_effect = lambda **kwargs: SimpleNamespace(
            name=kwargs["agent_name"], version="1",
        )

        def upload(*, name, content):
            filename, stream = content.as_dict()["files"][0]
            self.assertEqual(filename, "workshop-brief.zip")
            self.assertIsInstance(stream, BytesIO)
            self.package = stream.read()
            self.assertTrue(self.package.startswith(b"PK"))
            return SimpleNamespace(name=name, version="7")

        self.project.beta.skills.create_from_files.side_effect = upload
        self.project.beta.skills.download_version.side_effect = lambda *_: iter([
            self.package[:20], self.package[20:],
        ])
        self.output = StringIO()

    def run_demo(self, **kwargs):
        with redirect_stdout(self.output):
            demo.demonstrate(self.project, "test-deployment", TITLE, **kwargs)

    def assert_skill_deleted(self):
        created_name = self.project.beta.skills.create_from_files.call_args.kwargs["name"]
        self.project.beta.skills.delete.assert_called_once_with(name=created_name)

    def test_skill_file_metadata_and_roundtrip(self):
        package, expected = demo.skill_package("workshop-brief-test")
        self.assertTrue(expected.startswith(b"---\nname: workshop-brief-test\ndescription: Use when"))
        self.assertIn(b"\n---\n", expected[4:])
        self.assertIn(b"WORKSHOP-BRIEF-V1", expected)
        self.assertEqual(demo.downloaded_instructions(package, expected), expected.decode("utf-8"))
        self.assertIn("name: workshop-brief\n", demo.SKILL_PATH.read_text())

    def test_rejects_changed_invalid_or_extra_downloaded_files(self):
        package, expected = demo.skill_package("workshop-brief-test")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            demo.downloaded_instructions(package, expected + b"changed")
        with self.assertRaises(BadZipFile):
            demo.downloaded_instructions(b"not a ZIP", expected)
        for filenames in ([], ["other.md"], ["SKILL.md", "extra.txt"]):
            stream = BytesIO()
            with ZipFile(stream, "w") as archive:
                for filename in filenames:
                    archive.writestr(filename, expected)
            with self.subTest(filenames=filenames), self.assertRaisesRegex(RuntimeError, "exactly one"):
                demo.downloaded_instructions(stream.getvalue(), expected)

    def test_exact_contract_rejects_missing_wrong_extra_and_non_json(self):
        self.assertTrue(demo.matches_contract(answer(), TITLE))
        self.assertTrue(demo.matches_contract(answer("Custom workshop"), "Custom workshop"))
        value = json.loads(answer())
        variants = [
            "plain text", "```json\n" + answer() + "\n```", "[]", "null",
            answer("Wrong title"),
        ]
        for field in value:
            changed = dict(value)
            changed[field] = "wrong"
            variants.append(json.dumps(changed))
            missing = dict(value)
            del missing[field]
            variants.append(json.dumps(missing))
        variants.append(json.dumps({**value, "extra": "not allowed"}))
        for text in variants:
            with self.subTest(text=text):
                self.assertFalse(demo.matches_contract(text, TITLE))

    def test_downloads_pinned_version_injects_only_second_request_and_cleans_up(self):
        self.run_demo(debug_response=True)
        skill_name = self.project.beta.skills.create_from_files.call_args.kwargs["name"]
        self.project.beta.skills.download_version.assert_called_once_with(skill_name, "7")
        definitions = [call.kwargs["definition"]
                       for call in self.project.agents.create_version.call_args_list]
        self.assertEqual(definitions[0].instructions, demo.BASE_INSTRUCTIONS)
        self.assertNotIn("WORKSHOP-BRIEF-V1", definitions[0].instructions)
        self.assertIn("WORKSHOP-BRIEF-V1", definitions[1].instructions)
        self.assertIn(f"name: {skill_name}", definitions[1].instructions)
        self.assertTrue(all(definition.model == "test-deployment" for definition in definitions))
        self.assertTrue(all(definition.tools == [] for definition in definitions))
        calls = self.client.responses.create.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs, calls[1].kwargs)
        self.assertNotIn("WORKSHOP-BRIEF-V1", calls[0].kwargs["input"])
        self.assertEqual(calls[0].kwargs["tool_choice"], "none")
        self.assertEqual(calls[0].kwargs["timeout"], 120.0)
        self.assertEqual(self.project.agents.delete_version.call_count, 2)
        self.assert_skill_deleted()
        self.assertIn("Exact skill contract match: False", self.output.getvalue())
        self.assertIn("Exact skill contract match: True", self.output.getvalue())
        self.assertIn("Token usage:", self.output.getvalue())
        self.assertIn('"id": "resp-test"', self.output.getvalue())

    def test_skill_contract_failure_is_not_replaced_and_all_objects_are_deleted(self):
        self.client.responses.create.side_effect = [
            response("Baseline."), response("I did not follow the skill."),
        ]
        with self.assertRaisesRegex(RuntimeError, "exact workshop-brief contract"):
            self.run_demo()
        self.assertEqual(self.project.agents.delete_version.call_count, 2)
        self.assert_skill_deleted()

    def test_baseline_is_not_required_to_fail_contract(self):
        self.client.responses.create.side_effect = [response(answer()), response(answer())]
        self.run_demo()
        self.assertEqual(self.client.responses.create.call_count, 2)
        self.assert_skill_deleted()

    def test_incomplete_or_empty_response_fails_and_cleans_up(self):
        for result in (response("", "completed"), response(answer(), "incomplete")):
            with self.subTest(status=result.status):
                self.setUp()
                self.client.responses.create.side_effect = [result]
                with self.assertRaisesRegex(RuntimeError, "did not complete"):
                    self.run_demo()
                self.project.agents.delete_version.assert_called_once()
                self.assert_skill_deleted()

    def test_inference_exception_surfaces_and_cleans_up(self):
        self.client.responses.create.side_effect = RuntimeError("service unavailable")
        with self.assertRaisesRegex(RuntimeError, "service unavailable"):
            self.run_demo()
        self.project.agents.delete_version.assert_called_once()
        self.assert_skill_deleted()

    def test_download_error_or_mismatch_never_falls_back_to_local_skill(self):
        for error in (RuntimeError("preview unavailable"), None):
            with self.subTest(error=error):
                self.setUp()
                if error:
                    self.project.beta.skills.download_version.side_effect = error
                else:
                    package, _ = demo.skill_package("different-name")
                    self.project.beta.skills.download_version.side_effect = lambda *_: iter([package])
                with self.assertRaises(RuntimeError):
                    self.run_demo()
                self.project.agents.create_version.assert_not_called()
                self.assert_skill_deleted()

    def test_agent_creation_failure_still_deletes_skill(self):
        self.project.agents.create_version.side_effect = RuntimeError("agent creation failed")
        with self.assertRaisesRegex(RuntimeError, "agent creation failed"):
            self.run_demo()
        self.project.agents.delete_version.assert_not_called()
        self.assert_skill_deleted()

    def test_upload_failure_does_not_delete_unknown_resource(self):
        self.project.beta.skills.create_from_files.side_effect = RuntimeError("upload denied")
        with self.assertRaisesRegex(RuntimeError, "upload denied"):
            self.run_demo()
        self.project.beta.skills.delete.assert_not_called()

    def test_cleanup_failure_is_visible(self):
        self.project.agents.delete_version.side_effect = RuntimeError("agent deletion denied")
        with self.assertRaisesRegex(RuntimeError, "agent deletion denied"):
            self.run_demo()
        self.assert_skill_deleted()

    def test_cli_rejects_missing_endpoint_and_blank_values_before_auth(self):
        for arguments in (
            [], ["--project-endpoint", "http://example"],
            ["--project-endpoint", "https://example", "--title", " "],
            ["--project-endpoint", "https://example", "--deployment", " "],
        ):
            with (
                self.subTest(arguments=arguments),
                patch.dict("os.environ", {}, clear=True),
                patch("demo.AzureCliCredential") as credential,
                redirect_stdout(self.output),
                patch("sys.stderr", self.output),
                self.assertRaises(SystemExit) as error,
            ):
                demo.main(arguments)
            self.assertEqual(error.exception.code, 2)
            credential.assert_not_called()


if __name__ == "__main__":
    unittest.main()
