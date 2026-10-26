"""Deploy and test only this lab; leave its resources and results available.

Run: python3 <lab>/deploy_lab.py [--subscription ID] [--location eastus2]
Prerequisites: Azure CLI/Bicep, az login as a user, Python with venv support.
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

import_path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(import_path))
import deploy as runner


LAB = Path(__file__).resolve().parent
STATE = LAB / ".azure" / "lab-deployment.json"
OWNER = {"managedBy": "foundry-lab-script", "lab": LAB.name}


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
    account_command = ["az", "account", "show", "-o", "json"]
    if args.subscription:
        account_command.extend(["--subscription", args.subscription])
    account = json.loads(runner.run(account_command, capture=True))
    if account["user"]["type"] != "user":
        parser.error("Run az login as a user; the template assigns a User role.")
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
            f"principalId={principal}", f"location={args.location}",
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
