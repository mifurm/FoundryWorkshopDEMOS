#!/usr/bin/env bash
# Delete dedicated workshop resource groups, never every group in a subscription.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./removeAll.sh [--subscription ID_OR_NAME] [--execute] [--yes]
                      [--prefix PREFIX]... [--group NAME]... [--dry-run]

Defaults to a read-only preview in the currently selected Azure subscription.
Requires Bash, Azure CLI (az login), and jq.

Targets:
  * All 33 labs' root-runner names (rg-foundry-<lab>-demo).
  * Standalone runner names (rg-foundry-student-<lab>-demo).
  * Manual/historical group names documented in the labs.
  * Groups recorded in each lab's .azure/{lab-deployment,deployment}.json,
    but only when their subscription matches the selected subscription.
    Legacy output-only deployment.json files are reported and skipped because
    they do not identify a subscription or group; known names still apply.

--prefix adds another deployment prefix; it is NOT a wildcard match.
--group adds an exact group name for an unrecorded, manually renamed deployment.
--execute deletes the listed groups after confirmation; --yes skips that prompt.
--dry-run explicitly selects preview mode (the default).

Deletion waits for each group and verifies its absence. Failures are reported,
remaining groups are still attempted, and any failure produces a nonzero exit.
ALL resources in each selected group are deleted, including unfinished training.
Resource locks/permissions can block deletion; locks are not removed automatically.
Tenant identities, local files, GitHub configuration, and soft-deleted resources
are NOT purged. See labs 12, 14, and 23 for separate identity/configuration cleanup.
Run separately for every subscription in which you deployed labs.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

subscription=''
execute=false
yes=false
prefixes=(rg-foundry rg-foundry-student)
extra_groups=()
while (($#)); do
    case "$1" in
        --subscription|--prefix|--group)
            (($# >= 2)) && [[ -n "$2" && "$2" != --* ]] || fail "$1 requires a value."
            case "$1" in
                --subscription) subscription=$2 ;;
                --prefix)
                    [[ "$2" =~ ^[a-zA-Z0-9_-]{1,50}$ ]] || fail "Invalid deployment prefix: $2"
                    prefixes+=("$2")
                    ;;
                --group) extra_groups+=("$2") ;;
            esac
            shift 2
            ;;
        --execute) execute=true; shift ;;
        --dry-run) execute=false; shift ;;
        --yes) yes=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown argument: $1 (see --help)." ;;
    esac
done

command -v az >/dev/null 2>&1 || fail "Azure CLI is required; install it and run az login."
command -v jq >/dev/null 2>&1 || fail "jq is required to validate deployment records and Azure responses."
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
account_args=(account show --output json)
if [[ -n "$subscription" ]]; then
    account_args+=(--subscription "$subscription")
fi
account=$(az "${account_args[@]}")
subscription=$(jq -er '.id | select(type == "string" and length > 0) | ascii_downcase' <<< "$account")
account_name=$(jq -er '.name | select(type == "string" and length > 0)' <<< "$account")
scope=(--subscription "$subscription")

# These are aliases beyond the two runners' generated names.
candidates=(
    foundry-lab14 foundry-lab15 foundry-lab16 foundry-lab26
    rg-lab17 rg-lab19 rg-lab21
    lab18-tracing lab23-evaluation lab24-lifecycle
    rg-foundry-native-memory-demo rg-browser-runbook
    rg-foundry-webiq-agent-live-20261026
)
if ((${#extra_groups[@]})); then
    candidates+=("${extra_groups[@]}")
fi
lab_count=0
for template in "$root"/[0-9]*-*/infra/main.bicep; do
    [[ -f "$template" ]] || continue
    lab_dir=${template%/infra/main.bicep}
    lab=${lab_dir##*/}
    lab_count=$((lab_count + 1))
    for prefix in "${prefixes[@]}"; do
        candidates+=("$prefix-${lab#*-}-demo")
    done
    for state in "$lab_dir/.azure/deployment.json" "$lab_dir/.azure/lab-deployment.json"; do
        [[ -f "$state" ]] || continue
        if [[ "$state" == "$lab_dir/.azure/deployment.json" ]] && jq -e '
            type == "object" and length > 0 and
            (has("subscription") | not) and (has("resourceGroup") | not) and
            all(.[];
                type == "object" and has("value") and
                (.type | type == "string" and length > 0))
        ' "$state" >/dev/null; then
            printf 'WARNING: Output-only deployment record has no subscription/group: %s. Using known group names; add --group for any custom name.\n' "$state" >&2
            continue
        fi
        if ! jq -e '
            type == "object" and
            (.subscription | type == "string" and test("^[A-Za-z0-9-]+$")) and
            (.resourceGroup | type == "string" and
                test("^[A-Za-z0-9_.()-]{1,90}$") and (endswith(".") | not))
        ' "$state" >/dev/null; then
            fail "Invalid deployment record: $state. No groups have been deleted."
        fi
        recorded_subscription=$(jq -r '.subscription | ascii_downcase' "$state")
        if [[ "$recorded_subscription" == "$subscription" ]]; then
            candidates+=("$(jq -r '.resourceGroup' "$state")")
        else
            printf 'Skipping record in another subscription (%s): %s\n' "$recorded_subscription" "$state" >&2
        fi
    done
done
((lab_count > 0)) || fail "No lab templates found beside this script."
for group in "${candidates[@]}"; do
    [[ "$group" =~ ^[a-zA-Z0-9_.\(\)-]{1,90}$ && "$group" != *. ]] ||
        fail "Invalid resource group name: $group"
done

candidate_names=$(printf '%s\n' "${candidates[@]}")
groups_json=$(az group list "${scope[@]}" --output json)
targets=$(jq -er --arg names "$candidate_names" '
    if type != "array" or any(.[]; (.name | type) != "string") then
        error("Invalid Azure resource group list")
    else
        ($names | split("\n") | map(ascii_downcase) | unique) as $allowed |
        [.[] | .name | select(ascii_downcase as $name | $allowed | index($name))] |
        unique_by(ascii_downcase) | sort_by(ascii_downcase) | join("\n")
    end
' <<< "$groups_json")

printf '\nSubscription: %s (%s)\nReviewed labs: %s\n' "$account_name" "$subscription" "$lab_count"
if [[ -z "$targets" ]]; then
    printf 'No matching workshop resource groups exist. Nothing to delete.\n'
    exit 0
fi
printf '\nThe following groups and ALL their resources are selected:\n%s\n' "$targets"
printf '\nTenant objects and external/local configuration will not be deleted.\n'
if ! "$execute"; then
    printf '\nDRY RUN ONLY. Rerun with --execute to delete these groups.\n'
    exit 0
fi
if ! "$yes"; then
    printf '\nType DELETE to confirm deletion in subscription %s: ' "$subscription"
    IFS= read -r confirmation || fail "Confirmation input unavailable. Nothing was deleted."
    [[ "$confirmation" == DELETE ]] || fail "Confirmation did not match. Nothing was deleted."
fi

failures=0
while IFS= read -r group; do
    printf '\nDeleting %s ...\n' "$group"
    if ! az group delete --name "$group" "${scope[@]}" --yes --output none; then
        printf 'ERROR: Deletion failed for %s.\n' "$group" >&2
        failures=$((failures + 1))
        continue
    fi
    if ! exists=$(az group exists --name "$group" "${scope[@]}" --output tsv); then
        printf 'ERROR: Could not verify deletion of %s.\n' "$group" >&2
        failures=$((failures + 1))
    elif [[ "$exists" != false ]]; then
        printf 'ERROR: Expected %s to be absent; Azure returned %s.\n' "$group" "$exists" >&2
        failures=$((failures + 1))
    else
        printf 'Verified deleted: %s\n' "$group"
    fi
done <<< "$targets"
if ((failures)); then
    fail "$failures resource group deletion(s) failed or could not be verified. See errors above."
fi
printf '\nAll selected resource groups are verified absent. Local deployment records were retained.\n'
