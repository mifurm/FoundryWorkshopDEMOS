"""Deploy and test only this lab; leave its resources and results available.

Run: python3 <lab>/deploy_lab.py [--subscription ID] [--location eastus2]
Prerequisites: Azure CLI/Bicep, az login as a user, Python with venv support.
First run setup_identity.ps1 in PowerShell, then run this script in that same
session. Its four ENTRA_* variables and recorded identity IDs are required.
Secrets are inherited only through the environment, never logged or persisted.
Creates billable resources and runs local tests plus the live demo.
Results: <lab>/.azure/lab-deployment.json, lab-tests.log, and demo.log.
After presenting, run python3 <lab>/cleanup.py to delete this lab's group.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID

import_path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(import_path))
import deploy as runner


LAB = Path(__file__).resolve().parent
STATE = LAB / ".azure" / "lab-deployment.json"
OWNER = {"managedBy": "foundry-lab-script", "lab": LAB.name}


def identity_settings() -> tuple[str, str]:
    variables = ("ENTRA_TENANT_ID", "ENTRA_BLUEPRINT_CLIENT_ID", "ENTRA_AGENT_ID", "ENTRA_BLUEPRINT_SECRET")
    missing = [name for name in variables if not os.getenv(name, "").strip()]
    if missing:
        raise ValueError("Run setup_identity.ps1 in PowerShell first, then stay in that session. Missing: "
                         + ", ".join(missing))
    try:
        tenant, blueprint, agent = (str(UUID(os.environ[name])) for name in variables[:3])
    except ValueError:
        raise ValueError("Entra tenant, blueprint, and child agent IDs must be UUIDs.") from None
    if blueprint == agent:
        raise ValueError("ENTRA_AGENT_ID must identify the child agent, not the blueprint.")
    identity_path = LAB / ".azure/identity.json"
    if not identity_path.is_file():
        raise ValueError("Missing .azure/identity.json from setup_identity.ps1; cannot track tenant cleanup.")
    identity = json.loads(identity_path.read_text())
    if not isinstance(identity, dict):
        raise ValueError("Identity setup record must be an object.")
    for key, value in (("tenantId", tenant), ("blueprintClientId", blueprint), ("agentIdentityId", agent)):
        recorded = identity.get(key)
        if not isinstance(recorded, str) or recorded.lower() != value:
            raise ValueError(f"Identity setup record does not match {key}; refusing deployment.")
    for key in ("blueprintObjectId", "blueprintPrincipalId"):
        try:
            UUID(identity.get(key, ""))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"Identity setup record has no valid {key}.") from None
    return tenant, agent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subscription", help="Defaults to the Azure CLI subscription.")
    parser.add_argument("--location", default="eastus2")
    parser.add_argument("--prefix", default="rg-foundry-student", help="Dedicated lab group prefix.")
    parser.add_argument("--principal-id", help="Defaults to the signed-in user's object ID.")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}", args.prefix):
        parser.error("--prefix must contain 1-50 letters, numbers, underscores, or hyphens.")
    for required in ("infra/main.bicep", "requirements.txt", "test_demo.py", "demo.py"):
        if not (LAB / required).is_file():
            parser.error(f"Missing lab input: {required}")
    try:
        tenant, agent = identity_settings()
    except (OSError, ValueError) as error:
        STATE.parent.mkdir(exist_ok=True)
        (STATE.parent / "lab-preflight.json").write_text(json.dumps({
            "lab": LAB.name, "status": "blocked", "stage": "identity-prerequisites", "error": str(error),
        }, indent=2) + "\n")
        raise
    account_command = ["az", "account", "show", "-o", "json"]
    if args.subscription:
        account_command.extend(["--subscription", args.subscription])
    account = json.loads(runner.run(account_command, capture=True))
    if account["user"]["type"] != "user":
        parser.error("Run az login as a user; the template assigns a User role.")
    if account["tenantId"].lower() != tenant:
        parser.error("Azure subscription tenant does not match the recorded Entra identity tenant.")
    subscription = account["id"]
    name = re.sub(r"^\d+-", "", LAB.name) + "-demo"
    group = f"{args.prefix}-{name}"
    scope = ["--subscription", subscription]
    if STATE.exists():
        previous = json.loads(STATE.read_text())
        if (previous["subscription"], previous["resourceGroup"]) != (subscription, group) and previous.get("cleanupStatus") != "deleted":
            parser.error("Recorded lab targets a different group/subscription; clean it up first.")
    exists = runner.run(["az", "group", "exists", "--name", group, *scope], capture=True).strip()
    if exists == "true":
        tags = json.loads(runner.run(
            ["az", "group", "show", "--name", group, *scope, "--query", "tags", "-o", "json"],
            capture=True,
        ))
        if not STATE.exists() or not isinstance(tags, dict) or any(tags.get(k) != v for k, v in OWNER.items()):
            parser.error("Refusing to reuse an existing group not owned by this lab script.")
    elif exists != "false":
        raise ValueError(f"Unexpected group existence response: {exists!r}")
    principal = args.principal_id or runner.run(
        ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"], capture=True,
    ).strip()
    if not principal:
        parser.error("Cannot resolve user object ID; provide --principal-id.")
    STATE.parent.mkdir(exist_ok=True)
    result = {"lab": LAB.name, "subscription": subscription, "resourceGroup": group,
              "status": "running", "stage": "local-tests", "outputs": {}}
    STATE.write_text(json.dumps(result, indent=2) + "\n")
    try:
        print(f"Billable Azure deployment: {LAB.name} -> {group} ({subscription})", flush=True)
        python = LAB / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            runner.run([sys.executable, "-m", "venv", str(LAB / ".venv")])
        runner.run([str(python), "-m", "pip", "install", "-r", "requirements.txt"], cwd=LAB)
        tests = subprocess.run(
            [str(python), "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v"],
            cwd=LAB, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        (STATE.parent / "lab-tests.log").write_text(tests.stdout)
        print(tests.stdout, end="", flush=True)
        tests.check_returncode()
        result["stage"] = "deployment"
        template = LAB / "infra/main.bicep"
        compiled = json.loads(runner.run(
            ["az", "bicep", "build", "--file", str(template), "--stdout"], capture=True,
        ))
        runner.run(["az", "bicep", "lint", "--file", str(template)])
        providers = {resource["type"].split("/")[0] for resource in compiled.get("resources", [])
                     if not resource["type"].startswith("Microsoft.Authorization/")}
        for provider in sorted(providers):
            runner.run(["az", "provider", "register", "--namespace", provider, "--wait", *scope])
        runner.run(["az", "group", "create", "--name", group, "--location", args.location,
                    "--tags", *(f"{key}={value}" for key, value in {**OWNER, **runner.SECURITY_TAGS}.items()), *scope, "-o", "none"])
        result["outputs"] = json.loads(runner.run([
            "az", "deployment", "group", "create", "--resource-group", group, "--name", name,
            *scope, "--template-file", str(template), "--parameters",
            f"principalId={principal}", f"location={args.location}", f"agentIdentityId={agent}",
            "--query", "properties.outputs", "-o", "json",
        ], capture=True))
        result["stage"] = "live-demo"
        STATE.write_text(json.dumps(result, indent=2) + "\n")
        runner.run_live(python, LAB, runner.demo_environment(result["outputs"], dict(os.environ)))
        result["status"] = "passed"
        result["stage"] = "complete"
        print(f"PASS: {LAB.name}. Resources retained. Results: {STATE}", flush=True)
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        result["status"] = "failed"
        result["error"] = str(error)
        raise
    finally:
        STATE.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}\nResources are retained; inspect {STATE} and run cleanup.py.", file=sys.stderr)
        sys.exit(1)
