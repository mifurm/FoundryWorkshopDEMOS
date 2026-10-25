"""Deploy every demo, run its local tests, and run its live Python example."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> str:
    print(f"\n> {shlex.join(command)}", flush=True)
    result = subprocess.run(
        command, cwd=cwd, env=env, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def discover(root: Path) -> list[Path]:
    demos = sorted({
        *[template.parent.parent for template in root.glob("*/infra/main.bicep")],
        *[requirements.parent for requirements in root.glob("*/requirements.txt")],
    })
    if not demos:
        raise ValueError(f"No demo templates found under {root}.")
    for demo in demos:
        for required in ("infra/main.bicep", "demo.py", "requirements.txt", "test_demo.py"):
            if not (demo / required).is_file():
                raise ValueError(f"{demo.name} is missing {required}.")
    return demos


def demo_environment(outputs: dict, base: dict[str, str]) -> dict[str, str]:
    values = {}
    for output, variable in (
        ("endpoint", "AZURE_OPENAI_ENDPOINT"),
        ("deployment", "AZURE_OPENAI_DEPLOYMENT"),
    ):
        value = outputs.get(output, {}).get("value")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Bicep must return a nonempty '{output}' output.")
        values[variable] = value
    for output, variable in (
        ("projectEndpoint", "FOUNDRY_PROJECT_ENDPOINT"),
        ("toolEndpoint", "WORKSHOP_TOOL_ENDPOINT"),
    ):
        if output in outputs:
            value = outputs[output].get("value")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Bicep output '{output}' must be a nonempty string.")
            values[variable] = value
    return {**base, **values, "PYTHONUNBUFFERED": "1"}


def run_live(python: Path, demo: Path, environment: dict[str, str]) -> None:
    log = demo / ".azure" / "demo.log"
    for attempt in range(11):
        print(f"\nRunning live demo: {demo.name} (attempt {attempt + 1}/11)", flush=True)
        result = subprocess.run(
            [str(python), "demo.py"], cwd=demo, env=environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        print(result.stdout, end="", flush=True)
        with log.open("a") as stream:
            stream.write(f"\n--- Attempt {attempt + 1} ---\n{result.stdout}")
        if result.returncode == 0:
            return
        propagating = (
            "openai.PermissionDeniedError: Error code: 403" in result.stdout
            or (
                "azure.core.exceptions.HttpResponseError" in result.stdout
                and ("(PermissionDenied)" in result.stdout or "(Forbidden)" in result.stdout)
            )
            or (
                "openai.NotFoundError: Error code: 404" in result.stdout
                and "DeploymentNotFound" in result.stdout
            )
        )
        if not propagating or attempt == 10:
            raise RuntimeError(f"Live demo {demo.name} failed; see {log}.")
        print("Azure access/deployment is not ready. Retrying in 30 seconds.", flush=True)
        time.sleep(30)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", help="Subscription ID/name; defaults to the Azure CLI selection.")
    parser.add_argument("--location", default="eastus2")
    parser.add_argument("--prefix", default="rg-foundry", help="Resource group prefix (default: rg-foundry).")
    parser.add_argument("--principal-id", help="Presenter user object ID; defaults to the signed-in user.")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", args.prefix):
        parser.error("--prefix must contain 1-50 letters, numbers, underscores, or hyphens.")

    demos = discover(ROOT)
    account_command = ["az", "account", "show", "-o", "json"]
    if args.subscription:
        account_command.extend(["--subscription", args.subscription])
    account = json.loads(run(account_command, capture=True))
    subscription = account["id"]
    if account["user"]["type"] != "user":
        parser.error("Sign in with az login as a user; the Bicep role assignment requires a User.")
    principal = args.principal_id or run(
        ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
        capture=True,
    ).strip()
    if not principal:
        parser.error("Could not resolve the presenter object ID; provide --principal-id.")
    print(f"Subscription: {account['name']} ({subscription})", flush=True)
    print("This deploys billable Azure resources and sends live model requests.", flush=True)

    providers = set()
    for demo in demos:
        template = json.loads(run(
            ["az", "bicep", "build", "--file", str(demo / "infra/main.bicep"), "--stdout"],
            capture=True,
        ))
        providers.update(
            resource["type"].split("/")[0] for resource in template.get("resources", [])
            if not resource["type"].startswith("Microsoft.Authorization/")
        )
        run(["az", "bicep", "lint", "--file", str(demo / "infra/main.bicep")])
        python = demo / ".venv" / "bin" / "python"
        if not python.is_file():
            run([sys.executable, "-m", "venv", str(demo / ".venv")])
        run([str(python), "-m", "pip", "install", "-r", "requirements.txt"], cwd=demo)
        run(
            [str(python), "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v"],
            cwd=demo,
        )

    for provider in sorted(providers):
        run([
            "az", "provider", "register", "--namespace", provider,
            "--wait", "--subscription", subscription,
        ])
    for demo in demos:
        demo_name = re.sub(r"^\d+-", "", demo.name)
        deployment_name = f"{demo_name}-demo"
        group = f"{args.prefix}-{deployment_name}"
        run([
            "az", "group", "create", "--name", group, "--location", args.location,
            "--subscription", subscription, "-o", "none",
        ])
        state = demo / ".azure"
        state.mkdir(exist_ok=True)
        outputs = json.loads(run([
            "az", "deployment", "group", "create", "--resource-group", group,
            "--name", deployment_name, "--subscription", subscription,
            "--template-file", str(demo / "infra/main.bicep"),
            "--parameters", f"principalId={principal}", f"location={args.location}",
            "--query", "properties.outputs", "-o", "json",
        ], capture=True))
        (state / "deployment.json").write_text(json.dumps({
            "subscription": subscription, "resourceGroup": group, "outputs": outputs,
        }, indent=2) + "\n")
        environment = demo_environment(outputs, dict(os.environ))
        run_live(demo / ".venv" / "bin" / "python", demo, environment)
        print(f"\nPASS: {demo.name}. Resource group: {group}.", flush=True)
    print("\nAll demo deployments, local tests, and live runs passed.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        sys.exit(1)
