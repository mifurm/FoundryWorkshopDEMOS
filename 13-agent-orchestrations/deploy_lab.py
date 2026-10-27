"""Deploy and test only this lab; leave its resources and results available.

Run: python3 <lab>/deploy_lab.py [--subscription ID] [--location eastus2]
Prerequisites: Azure CLI/Bicep, az login as a user, Python with venv support.
Creates billable resources and runs local tests plus the live demo.
Results: <lab>/.azure/lab-deployment.json, lab-tests.log, and demo.log.
After presenting, run python3 <lab>/cleanup.py to delete this lab's group.
Retries reuse the saved account name. After cleanup, use --start-fresh to
generate a new name without restoring or purging the soft-deleted account.
Use --prepare-only to save the lifecycle for a manual Bicep deployment.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import_path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(import_path))
import deploy as runner


LAB = Path(__file__).resolve().parent
STATE = LAB / ".azure" / "lab-deployment.json"
OWNER = {"managedBy": "foundry-lab-script", "lab": LAB.name}


def save_state(result: dict) -> None:
    temporary = STATE.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(result, indent=2) + "\n")
        temporary.replace(STATE)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subscription", help="Defaults to the Azure CLI subscription.")
    parser.add_argument("--location", default="eastus2")
    parser.add_argument("--prefix", default="rg-foundry-student", help="Dedicated lab group prefix.")
    parser.add_argument("--principal-id", help="Defaults to the signed-in user's object ID.")
    parser.add_argument("--start-fresh", action="store_true",
                        help="Generate a new account name only after the previous group is deleted.")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Save the account name for manual deployment; do not create Azure resources.")
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
    previous = None
    same_target = False
    if STATE.exists():
        previous = json.loads(STATE.read_text())
        if (not isinstance(previous, dict)
                or not isinstance(previous.get("subscription"), str)
                or not previous["subscription"].strip()
                or not isinstance(previous.get("resourceGroup"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}-" + re.escape(name), previous["resourceGroup"])
                or previous.get("lab", LAB.name) != LAB.name):
            parser.error("Invalid or mismatched lab deployment record; refusing to overwrite it.")
        same_target = (previous["subscription"], previous["resourceGroup"]) == (subscription, group)
        if not same_target:
            if previous.get("cleanupStatus") != "deleted" or not args.start_fresh:
                parser.error("Recorded lab targets a different group/subscription; run cleanup.py, then --start-fresh.")
            old_exists = runner.run([
                "az", "group", "exists", "--name", previous["resourceGroup"],
                "--subscription", previous["subscription"],
            ], capture=True).strip()
            if old_exists != "false":
                parser.error("Cannot verify the previous group is absent; run cleanup.py before starting fresh.")
    exists = runner.run(["az", "group", "exists", "--name", group, *scope], capture=True).strip()
    if exists == "true":
        tags = json.loads(runner.run(
            ["az", "group", "show", "--name", group, *scope, "--query", "tags", "-o", "json"],
            capture=True,
        ))
        if not same_target or not isinstance(tags, dict) or any(tags.get(k) != v for k, v in OWNER.items()):
            parser.error("Refusing to reuse an existing group not owned by this lab script.")
        if args.start_fresh:
            parser.error("--start-fresh requires the group to be absent. Run cleanup.py first.")
        if previous.get("cleanupStatus") == "deleted":
            parser.error("The deleted group has reappeared; inspect its ownership before running cleanup.py.")
    elif exists != "false":
        raise ValueError(f"Unexpected group existence response: {exists!r}")
    if previous is not None and not args.start_fresh:
        if previous.get("cleanupStatus") == "deleted":
            parser.error("This lifecycle was deleted. Use --start-fresh to create a new account name.")
        account_name = previous.get("accountName")
        if not isinstance(account_name, str) or not re.fullmatch(r"foundry-patterns-[0-9a-f]{32}", account_name):
            parser.error("Saved lifecycle has no valid accountName. Run cleanup.py, then deploy with --start-fresh.")
        if previous.get("location", args.location) != args.location:
            parser.error("A lifecycle cannot change location. Run cleanup.py, then --start-fresh.")
    else:
        account_name = f"foundry-patterns-{uuid4().hex}"
    principal = args.principal_id or runner.run(
        ["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"], capture=True,
    ).strip()
    if not principal:
        parser.error("Cannot resolve user object ID; provide --principal-id.")
    STATE.parent.mkdir(exist_ok=True)
    result = {"lab": LAB.name, "subscription": subscription, "resourceGroup": group,
              "location": args.location, "accountName": account_name,
              "status": "prepared" if args.prepare_only else "running",
              "stage": "prepared" if args.prepare_only else "local-tests",
              "outputs": previous.get("outputs", {}) if previous is not None and not args.start_fresh else {}}
    save_state(result)
    print(f"Lab lifecycle: {group} / {account_name} ({subscription}, {args.location})", flush=True)
    if args.prepare_only:
        print(f"Saved {STATE}. No Azure resources were created. Reuse accountName for manual deployment.", flush=True)
        return
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
            f"principalId={principal}", f"location={args.location}", f"accountName={account_name}",
            "--query", "properties.outputs", "-o", "json",
        ], capture=True))
        result["stage"] = "live-demo"
        save_state(result)
        runner.run_live(python, LAB, runner.demo_environment(result["outputs"], dict(os.environ)))
        result["status"] = "passed"
        result["stage"] = "complete"
        print(f"PASS: {LAB.name}. Resources retained. Results: {STATE}", flush=True)
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        result["status"] = "failed"
        result["error"] = str(error)
        raise
    finally:
        save_state(result)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}\nResources are retained; inspect {STATE} and run cleanup.py.", file=sys.stderr)
        sys.exit(1)
