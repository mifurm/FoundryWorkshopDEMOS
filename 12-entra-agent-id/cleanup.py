"""Delete this lab's recorded Azure group AND its recorded Entra identities.

Run: python3 <lab>/cleanup.py
Prompts for the full group name. Use --yes only for unattended cleanup.
Uses .azure/lab-deployment.json, NOT the currently selected Azure subscription.
Deletes ALL resources in that dedicated group, including unfinished training.
Does not delete resources deployed by the repository-wide deploy.py.
Tenant cleanup requires PowerShell 7, Microsoft.Graph.Authentication, and an
authorized Graph sign-in with the three Agent ID DeleteRestore scopes.
Only IDs in .azure/identity.json are targeted, including partial setup.
Use --azure-only to explicitly retain tenant identities for later cleanup.
Successful tenant cleanup archives the IDs and removes identity.json so setup
can run again. Remove Env:ENTRA_BLUEPRINT_SECRET in the calling PowerShell too.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID


LAB = Path(__file__).resolve().parent
STATE = LAB / ".azure" / "lab-deployment.json"

GRAPH_CLEANUP = r'''
$ErrorActionPreference = "Stop"
Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
$ids = $env:LAB_IDENTITY_CLEANUP | ConvertFrom-Json
Connect-MgGraph -TenantId $ids.tenantId -ContextScope Process -NoWelcome -Scopes @(
    "AgentIdentity.DeleteRestore.All",
    "AgentIdentityBlueprintPrincipal.DeleteRestore.All",
    "AgentIdentityBlueprint.DeleteRestore.All"
)
if ((Get-MgContext).TenantId -ne $ids.tenantId) { throw "Graph tenant mismatch; refusing deletion." }
$targets = @()
if ($ids.agentIdentityId) {
    $targets += "beta/servicePrincipals/$($ids.agentIdentityId)/microsoft.graph.agentIdentity"
}
if ($ids.blueprintPrincipalId) {
    $targets += "v1.0/servicePrincipals/$($ids.blueprintPrincipalId)/microsoft.graph.agentIdentityBlueprintPrincipal"
}
$targets += "v1.0/applications/$($ids.blueprintObjectId)/microsoft.graph.agentIdentityBlueprint"
foreach ($target in $targets) {
    try {
        Invoke-MgGraphRequest -Method DELETE -Uri "https://graph.microsoft.com/$target" | Out-Null
        Write-Host "Deleted recorded tenant object: $target"
    } catch {
        if ($_.Exception.Response.StatusCode -eq 404 -or $_.Exception.ResponseStatusCode -eq 404) {
            Write-Host "Recorded tenant object already absent: $target"
        } else {
            throw
        }
    }
}
'''


def identity_record() -> dict[str, str]:
    record = json.loads((LAB / ".azure/identity.json").read_text())
    if not isinstance(record, dict):
        raise ValueError("Identity setup record must be an object.")
    ids = {}
    for key in ("tenantId", "blueprintObjectId", "blueprintClientId", "blueprintPrincipalId", "agentIdentityId"):
        if key in ("blueprintPrincipalId", "agentIdentityId") and key not in record:
            continue
        try:
            ids[key] = str(UUID(record.get(key, "")))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"Invalid identity setup field {key}; refusing tenant deletion.") from None
    object_ids = [ids[key] for key in ("blueprintObjectId", "blueprintPrincipalId", "agentIdentityId") if key in ids]
    if len(set(object_ids)) != len(object_ids) or ids.get("agentIdentityId") == ids["blueprintClientId"]:
        raise ValueError("Identity setup reuses object IDs; refusing tenant deletion.")
    return ids


def az(*arguments: str) -> str:
    return subprocess.run(["az", *arguments], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def cleanup_azure(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="Skip group-name and blueprint-ID confirmations.")
    parser.add_argument("--azure-only", action="store_true", help="Leave recorded Entra identities intact.")
    args = parser.parse_args(argv)
    identity_path = LAB / ".azure/identity.json"
    if not STATE.is_file() and not identity_path.is_file():
        parser.error("No recorded Azure deployment or tenant identity. Nothing will be deleted.")
    ids = identity_record() if identity_path.is_file() and not args.azure_only else None
    if ids is not None and STATE.is_file():
        deployed = json.loads(STATE.read_text()).get("outputs", {}).get("agentIdentityId", {}).get("value")
        if deployed and deployed != ids.get("agentIdentityId"):
            raise ValueError("Recorded tenant identity does not match the Azure deployment; refusing cleanup.")
    if STATE.is_file():
        cleanup_azure(args, parser)
    if args.azure_only:
        print("Azure-only cleanup requested. Tenant identities and their credential are NOT deleted.", flush=True)
        return
    if ids is None:
        if json.loads(STATE.read_text()).get("identityCleanupStatus") != "deleted":
            raise RuntimeError("Identity record is missing; tenant cleanup cannot be verified.")
        print("Recorded tenant identities were already deleted.", flush=True)
        return
    print(f"Delete recorded Entra objects in tenant {ids['tenantId']}; blueprint {ids['blueprintObjectId']}.",
          flush=True)
    if not args.yes and input("Type the blueprint object ID to confirm tenant deletion: ").strip() != ids["blueprintObjectId"]:
        parser.error("Confirmation did not match. Tenant identities were not deleted.")
    report = {"lab": LAB.name, "identities": ids, "status": "running"}
    try:
        environment = {key: value for key, value in os.environ.items() if key != "ENTRA_BLUEPRINT_SECRET"}
        environment["LAB_IDENTITY_CLEANUP"] = json.dumps(ids)
        subprocess.run(["pwsh", "-NoProfile", "-Command", GRAPH_CLEANUP], env=environment, check=True)
        report["status"] = "deleted"
        (LAB / ".azure/identity-cleanup.json").write_text(json.dumps(report, indent=2) + "\n")
        if STATE.is_file():
            result = json.loads(STATE.read_text())
            result["identityCleanupStatus"] = "deleted"
            STATE.write_text(json.dumps(result, indent=2) + "\n")
        identity_path.unlink()
        print("Recorded tenant identities deleted; non-secret IDs archived. "
              "Remove Env:ENTRA_BLUEPRINT_SECRET in the calling PowerShell session.", flush=True)
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError) as error:
        report["status"] = "failed"
        report["error"] = str(error) if not isinstance(error, subprocess.CalledProcessError) else (
            f"Graph cleanup exited with code {error.returncode}; identity record retained for retry."
        )
        (LAB / ".azure/identity-cleanup.json").write_text(json.dumps(report, indent=2) + "\n")
        raise


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
