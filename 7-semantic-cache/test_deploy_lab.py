import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import deploy_lab


class RedisSkuTests(unittest.TestCase):
    def test_selected_sku_is_recorded_and_forwarded_to_bicep(self):
        for sku in ("Balanced_B0", "Balanced_B1", "Balanced_B3", "Balanced_B5"):
            with self.subTest(sku=sku), tempfile.TemporaryDirectory() as directory:
                lab = Path(directory)
                (lab / "infra").mkdir()
                for required in ("infra/main.bicep", "requirements.txt", "test_demo.py", "demo.py"):
                    (lab / required).touch()
                state = lab / ".azure/lab-deployment.json"
                commands = []

                def run(command, **kwargs):
                    commands.append(command)
                    if command[:3] == ["az", "account", "show"]:
                        return json.dumps({"id": "sub", "user": {"type": "user"}})
                    if command[:3] == ["az", "group", "exists"]:
                        return "false"
                    if command[:3] == ["az", "bicep", "build"]:
                        return '{"resources": []}'
                    if command[:4] == ["az", "deployment", "group", "create"]:
                        self.assertEqual(json.loads(state.read_text())["redisSku"], sku)
                        return '{"endpoint": {"value": "https://gateway.example/ai"}}'
                    return ""

                arguments = ["--principal-id", "presenter"]
                if sku != "Balanced_B0":
                    arguments.extend(["--redis-sku", sku])
                with (
                    patch.object(deploy_lab, "LAB", lab),
                    patch.object(deploy_lab, "STATE", state),
                    patch.object(deploy_lab.runner, "run", side_effect=run),
                    patch.object(deploy_lab.runner, "run_live"),
                    patch.object(deploy_lab.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, stdout="Tests passed\n",
                    )),
                    redirect_stdout(io.StringIO()),
                ):
                    deploy_lab.main(arguments)
                deployment = next(command for command in commands
                                  if command[:4] == ["az", "deployment", "group", "create"])
                self.assertIn(f"redisSku={sku}", deployment)
                self.assertIn("location=eastus2", deployment)
                self.assertEqual(json.loads(state.read_text())["redisSku"], sku)

    def test_invalid_sku_is_rejected_before_azure_calls(self):
        with (
            patch.object(deploy_lab.runner, "run") as run,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            deploy_lab.main(["--redis-sku", "unsupported"])
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_cli_and_template_support_the_same_skus(self):
        source = (Path(__file__).parent / "infra/main.bicep").read_text()
        allowed = source.split("param redisSku string", 1)[0].rsplit("@allowed([", 1)[1]
        self.assertEqual(
            [line.strip().strip("'") for line in allowed.splitlines() if "'" in line],
            ["Balanced_B0", "Balanced_B1", "Balanced_B3", "Balanced_B5"],
        )


if __name__ == "__main__":
    unittest.main()
