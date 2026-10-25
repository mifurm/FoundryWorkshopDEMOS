import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import deploy


class DeployTests(unittest.TestCase):
    def test_discovers_all_demo_templates_and_requires_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "No demo templates"):
                deploy.discover(root)
            for name in ("second", "first"):
                demo = root / name
                (demo / "infra").mkdir(parents=True)
                (demo / "infra/main.bicep").touch()
                for entrypoint in ("demo.py", "requirements.txt", "test_demo.py"):
                    (demo / entrypoint).touch()
            self.assertEqual([p.name for p in deploy.discover(root)], ["first", "second"])
            (root / "first/demo.py").unlink()
            with self.assertRaisesRegex(ValueError, "first is missing demo.py"):
                deploy.discover(root)

    def test_incomplete_demo_is_not_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "incomplete").mkdir()
            (root / "incomplete/requirements.txt").touch()
            with self.assertRaisesRegex(ValueError, "incomplete is missing infra/main.bicep"):
                deploy.discover(root)

    def test_outputs_override_stale_environment(self) -> None:
        result = deploy.demo_environment(
            {"endpoint": {"value": "https://new.example/"}, "deployment": {"value": "new"}},
            {"AZURE_OPENAI_ENDPOINT": "stale", "PATH": "/bin"},
        )
        self.assertEqual(result["AZURE_OPENAI_ENDPOINT"], "https://new.example/")
        self.assertEqual(result["AZURE_OPENAI_DEPLOYMENT"], "new")
        self.assertEqual(result["PATH"], "/bin")
        for outputs in ({}, {"endpoint": {"value": ""}}):
            with self.assertRaises(ValueError):
                deploy.demo_environment(outputs, {})

    def test_project_and_tool_outputs_override_stale_environment(self) -> None:
        outputs = {
            "endpoint": {"value": "https://account.openai.azure.com/"},
            "deployment": {"value": "model"},
            "projectEndpoint": {"value": "https://account.services.ai.azure.com/api/projects/demo"},
            "toolEndpoint": {"value": "https://function.azurewebsites.net/api"},
        }
        result = deploy.demo_environment(outputs, {
            "FOUNDRY_PROJECT_ENDPOINT": "stale", "WORKSHOP_TOOL_ENDPOINT": "stale",
        })
        self.assertEqual(result["FOUNDRY_PROJECT_ENDPOINT"], outputs["projectEndpoint"]["value"])
        self.assertEqual(result["WORKSHOP_TOOL_ENDPOINT"], outputs["toolEndpoint"]["value"])
        for name in ("projectEndpoint", "toolEndpoint"):
            invalid = {**outputs, name: {"value": ""}}
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                deploy.demo_environment(invalid, {})

    def test_all_listed_demos_have_runner_entrypoints(self) -> None:
        self.assertEqual(
            [demo.name for demo in deploy.discover(Path(__file__).parent)],
            ["1-model-router", "2-workflow-agent", "3-webiq-agent", "4-agent-tools"],
        )

    def test_runs_tests_then_deploys_and_persists_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            demo = root / "1-example"
            demo.mkdir()
            outputs = {
                "endpoint": {"value": "https://example.openai.azure.com/"},
                "deployment": {"value": "router"},
            }
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if command[:3] == ["az", "account", "show"]:
                    return json.dumps({"id": "sub", "name": "test", "user": {"type": "user"}})
                if command[:4] == ["az", "deployment", "group", "create"]:
                    return json.dumps(outputs)
                if command[:3] == ["az", "bicep", "build"]:
                    return json.dumps({"resources": [
                        {"type": "Microsoft.CognitiveServices/accounts"},
                        {"type": "Microsoft.Storage/storageAccounts"},
                        {"type": "Microsoft.Web/sites"},
                        {"type": "Microsoft.Authorization/roleAssignments"},
                    ]})
                return ""

            with (
                patch("deploy.ROOT", root),
                patch("deploy.discover", return_value=[demo]),
                patch("deploy.run", side_effect=fake_run),
                patch("deploy.run_live") as live,
                redirect_stdout(io.StringIO()),
            ):
                deploy.main(["--principal-id", "presenter"])
            state = json.loads((demo / ".azure/deployment.json").read_text())
            self.assertEqual(state["resourceGroup"], "rg-foundry-example-demo")
            self.assertEqual(state["outputs"], outputs)
            live.assert_called_once()
            self.assertEqual(live.call_args.args[2]["AZURE_OPENAI_DEPLOYMENT"], "router")
            test_index = next(i for i, cmd in enumerate(commands) if "unittest" in cmd)
            deploy_index = next(i for i, cmd in enumerate(commands) if "group" in cmd and "deployment" in cmd)
            self.assertLess(test_index, deploy_index)
            self.assertIn("--subscription", commands[deploy_index])
            self.assertEqual(
                commands[deploy_index][commands[deploy_index].index("--name") + 1],
                "example-demo",
            )
            registered = {
                cmd[cmd.index("--namespace") + 1]
                for cmd in commands if cmd[:3] == ["az", "provider", "register"]
            }
            self.assertEqual(
                registered, {"Microsoft.CognitiveServices", "Microsoft.Storage", "Microsoft.Web"}
            )

    def test_local_failures_prevent_deployment(self) -> None:
        with (
            patch("deploy.discover", return_value=[Path("/demo")]),
            patch("deploy.run") as run,
            patch("deploy.run_live") as live,
            redirect_stdout(io.StringIO()),
        ):
            run.side_effect = [
                json.dumps({"id": "sub", "name": "test", "user": {"type": "user"}}),
                subprocess.CalledProcessError(1, ["az", "bicep", "build"]),
            ]
            with self.assertRaises(subprocess.CalledProcessError):
                deploy.main(["--principal-id", "presenter"])
            live.assert_not_called()
            self.assertFalse(any("create" in call.args[0] for call in run.call_args_list))

    def test_live_retry_is_bounded_and_logs_failures(self) -> None:
        failure = subprocess.CompletedProcess(
            [], 1, stdout="openai.PermissionDeniedError: Error code: 403"
        )
        success = subprocess.CompletedProcess([], 0, stdout="Serving model: example\nAnswer: Hello\n")
        with tempfile.TemporaryDirectory() as directory:
            demo = Path(directory)
            (demo / ".azure").mkdir()
            with (
                patch("deploy.subprocess.run", side_effect=[failure, success]) as run,
                patch("deploy.time.sleep") as sleep,
                redirect_stdout(io.StringIO()),
            ):
                deploy.run_live(Path("/python"), demo, {})
                self.assertEqual(run.call_count, 2)
                sleep.assert_called_once_with(30)
            self.assertIn("Answer: Hello", (demo / ".azure/demo.log").read_text())
            with (
                patch("deploy.subprocess.run", return_value=failure) as run,
                patch("deploy.time.sleep"),
                redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(RuntimeError, "failed"),
            ):
                deploy.run_live(Path("/python"), demo, {})
            self.assertEqual(run.call_count, 11)

    def test_non_propagation_errors_are_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            demo = Path(directory)
            (demo / ".azure").mkdir()
            with (
                patch(
                    "deploy.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 1, stdout="Invalid input"),
                ),
                patch("deploy.time.sleep") as sleep,
                redirect_stdout(io.StringIO()),
                self.assertRaises(RuntimeError),
            ):
                deploy.run_live(Path("/python"), demo, {})
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
