"""Delete only this lab's recorded resource group; keep local results.

Run: python3 <lab>/cleanup.py
Prompts for the full group name. Use --yes only for unattended cleanup.
Uses .azure/lab-deployment.json, NOT the currently selected Azure subscription.
Deletes ALL resources in that dedicated group, including unfinished training.
Does not delete resources deployed by the repository-wide deploy.py.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


LAB = Path(__file__).resolve().parent
STATE = LAB / ".azure" / "lab-deployment.json"


def az(*arguments: str) -> str:
    return subprocess.run(["az", *arguments], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="Skip the group-name confirmation.")
    args = parser.parse_args(argv)
    if not STATE.is_file():
        parser.error("No lab deployment record. Nothing will be deleted; run deploy_lab.py first.")
    result = json.loads(STATE.read_text())
    subscription = result.get("subscription")
    group = result.get("resourceGroup")
    suffix = re.escape(re.sub(r"^\d+-", "", LAB.name) + "-demo")
    if (result.get("lab") != LAB.name or not isinstance(subscription, str) or not subscription.strip()
            or not isinstance(group, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,50}-" + suffix, group)):
        parser.error("Invalid or mismatched lab deployment record; refusing deletion.")
    scope = ("--subscription", subscription)
    try:
        exists = az("group", "exists", "--name", group, *scope)
        if exists not in ("true", "false"):
            raise ValueError(f"Unexpected group existence response: {exists!r}")
        if exists == "true":
            tags = json.loads(az("group", "show", "--name", group, *scope, "--query", "tags", "-o", "json"))
            if not isinstance(tags, dict) or tags.get("lab") != LAB.name or tags.get("managedBy") != "foundry-lab-script":
                parser.error("Group ownership tags do not match this lab; refusing deletion.")
            print(f"Delete ALL resources in {group}, subscription {subscription}.", flush=True)
            if not args.yes and input("Type the full resource group name to confirm: ").strip() != group:
                parser.error("Confirmation did not match. Nothing was deleted.")
            az("group", "delete", "--name", group, *scope, "--yes")
            if az("group", "exists", "--name", group, *scope) != "false":
                raise RuntimeError("Azure still reports the group exists; cleanup is not complete.")
        result["cleanupStatus"] = "deleted"
        result.pop("cleanupError", None)
        print(f"Verified absent: {group}. Local logs and results retained in {STATE.parent}.", flush=True)
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        result["cleanupStatus"] = "failed"
        result["cleanupError"] = str(error)
        raise
    finally:
        STATE.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
