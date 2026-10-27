import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from unittest.mock import patch

import cleanup
import deploy_lab


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.lab = Path(directory) / "13-agent-orchestrations"
        (self.lab / "infra").mkdir(parents=True)
        for required in ("infra/main.bicep", "requirements.txt", "test_demo.py", "demo.py"):
            (self.lab / required).touch()
        self.state = self.lab / ".azure/lab-deployment.json"
        self.group = "rg-foundry-student-agent-orchestrations-demo"
        self.exists = False
        self.commands = []
        self.deployed_names = []
        self.fail_deployment = False
        self.fail_tests = False
        self.owner = dict(deploy_lab.OWNER)
        self.stack.enter_context(patch.object(deploy_lab, "LAB", self.lab))
        self.stack.enter_context(patch.object(deploy_lab, "STATE", self.state))
        self.stack.enter_context(patch.object(cleanup, "STATE", self.state))
        self.stack.enter_context(patch.object(deploy_lab.runner, "run", side_effect=self.run_command))
        self.live = self.stack.enter_context(patch.object(deploy_lab.runner, "run_live"))
        self.local_tests = self.stack.enter_context(patch.object(
            deploy_lab.subprocess, "run", side_effect=self.run_tests,
        ))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.errors = self.stack.enter_context(redirect_stderr(io.StringIO()))

    def record(self):
        return json.loads(self.state.read_text())

    def run_tests(self, *args, **kwargs):
        return subprocess.CompletedProcess(args[0], int(self.fail_tests), stdout="offline tests\n")

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if command[:3] == ["az", "account", "show"]:
            return json.dumps({"id": "sub", "user": {"type": "user"}})
        if command[:3] == ["az", "group", "exists"]:
            return str(self.exists).lower()
        if command[:3] == ["az", "group", "show"]:
            return json.dumps(self.owner)
        if command[:3] == ["az", "bicep", "build"]:
            return '{"resources": []}'
        if command[:3] == ["az", "group", "create"]:
            self.exists = True
        if command[:4] == ["az", "deployment", "group", "create"]:
            name = next(item.split("=", 1)[1] for item in command if item.startswith("accountName="))
            self.assertEqual(self.record()["accountName"], name)
            self.deployed_names.append(name)
            if self.fail_deployment:
                raise subprocess.CalledProcessError(1, command)
            return json.dumps({
                "endpoint": {"value": f"https://{name}.openai.azure.com/"},
                "projectEndpoint": {"value": f"https://{name}.services.ai.azure.com/api/projects/orchestration-demo"},
                "deployment": {"value": "gpt-4.1-mini"},
            })
        return ""

    def deploy(self, *arguments):
        deploy_lab.main(["--principal-id", "presenter", *arguments])

    def test_initial_deployment_persists_random_name_before_deploying(self):
        self.deploy()
        result = self.record()
        self.assertRegex(result["accountName"], r"^foundry-patterns-[0-9a-f]{32}$")
        self.assertEqual(result["location"], "eastus2")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(self.deployed_names, [result["accountName"]])
        self.live.assert_called_once()

    def test_retry_after_deployment_failure_and_update_reuse_name(self):
        self.fail_deployment = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.deploy()
        failed = self.record()
        self.assertEqual(failed["status"], "failed")
        self.fail_deployment = False
        self.deploy()
        self.deploy()
        self.assertEqual(self.deployed_names, [failed["accountName"]] * 3)

    def test_failure_before_resource_creation_keeps_name(self):
        self.fail_tests = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.deploy()
        name = self.record()["accountName"]
        self.assertFalse(self.exists)
        self.fail_tests = False
        self.deploy()
        self.assertEqual(self.deployed_names, [name])

    def test_prepare_only_saves_same_name_without_creating_resources(self):
        self.deploy("--prepare-only")
        name = self.record()["accountName"]
        self.deploy("--prepare-only")
        self.assertEqual(self.record()["accountName"], name)
        self.assertEqual(self.record()["status"], "prepared")
        self.assertFalse(self.exists)
        self.assertFalse(self.deployed_names)
        self.local_tests.assert_not_called()
        self.live.assert_not_called()
        self.deploy()
        self.assertEqual(self.deployed_names, [name])

    def test_fresh_rebuild_refuses_existing_group_and_preserves_state(self):
        self.deploy()
        before = self.state.read_text()
        with self.assertRaises(SystemExit):
            self.deploy("--start-fresh")
        self.assertEqual(self.state.read_text(), before)
        self.assertIn("cleanup.py", self.errors.getvalue())

    def test_cleanup_then_explicit_fresh_rotates_name_without_purge(self):
        self.deploy()
        original = self.record()["accountName"]

        def cleanup_az(*arguments):
            if arguments[:2] == ("group", "exists"):
                return str(self.exists).lower()
            if arguments[:2] == ("group", "show"):
                return json.dumps(self.owner)
            if arguments[:2] == ("group", "delete"):
                self.exists = False
                return ""
            self.fail(f"Unexpected cleanup command: {arguments}")

        with patch.object(cleanup, "az", side_effect=cleanup_az):
            cleanup.main(["--yes"])
        deleted = self.state.read_text()
        with self.assertRaises(SystemExit):
            self.deploy()
        self.assertEqual(self.state.read_text(), deleted)
        self.deploy("--start-fresh")
        self.assertNotEqual(self.record()["accountName"], original)
        self.assertNotIn("cleanupStatus", self.record())
        self.assertFalse(any("purge" in argument for command in self.commands for argument in command))

    def test_external_group_deletion_requires_explicit_rotation(self):
        self.deploy()
        original = self.record()["accountName"]
        self.exists = False
        self.deploy("--start-fresh")
        self.assertNotEqual(self.record()["accountName"], original)

    def test_live_failure_keeps_name_and_outputs(self):
        self.live.side_effect = RuntimeError("live failed")
        with self.assertRaisesRegex(RuntimeError, "live failed"):
            self.deploy()
        previous = self.record()
        self.assertEqual(previous["status"], "failed")
        self.assertTrue(previous["outputs"])
        self.live.side_effect = None
        self.deploy()
        self.assertEqual(self.record()["accountName"], previous["accountName"])

    def test_refuses_unowned_group_even_with_fresh_flag(self):
        self.exists = True
        for arguments in ((), ("--start-fresh",)):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                self.deploy(*arguments)
        self.assertFalse(self.state.exists())

    def test_refuses_missing_or_invalid_name_in_existing_state(self):
        self.deploy("--prepare-only")
        original = self.record()
        for name in (None, "", "../bad", 42):
            result = dict(original)
            if name is None:
                result.pop("accountName")
            else:
                result["accountName"] = name
            self.state.write_text(json.dumps(result))
            before = self.state.read_text()
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self.deploy()
            self.assertEqual(self.state.read_text(), before)

    def test_legacy_record_can_only_migrate_after_group_deletion(self):
        self.deploy("--prepare-only")
        result = self.record()
        result.pop("accountName")
        self.state.write_text(json.dumps(result))
        self.exists = True
        with self.assertRaises(SystemExit):
            self.deploy("--start-fresh")
        self.exists = False
        self.deploy("--start-fresh")
        self.assertIn("accountName", self.record())

    def test_scope_and_location_changes_require_completed_cleanup(self):
        self.deploy("--prepare-only")
        before = self.state.read_text()
        for arguments in (
            ("--prefix", "different"),
            ("--location", "westus"),
            ("--start-fresh", "--prefix", "different"),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                self.deploy(*arguments)
            self.assertEqual(self.state.read_text(), before)

    def test_corrupt_state_is_not_overwritten(self):
        self.state.parent.mkdir()
        self.state.write_text("{broken")
        with self.assertRaises(ValueError):
            self.deploy()
        self.assertEqual(self.state.read_text(), "{broken")


if __name__ == "__main__":
    unittest.main()
