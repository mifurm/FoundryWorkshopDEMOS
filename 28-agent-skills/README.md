# 28 - Agent using a reusable Foundry skill

Store a reusable workshop procedure in the **preview Foundry Skills API**, pin
its immutable version, and use its downloaded instructions in a real Foundry
prompt agent. This follows the direct-injection concept from
[Microsoft Learn](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/skills).

## What to present

1. Open [the skill](./skills/workshop-brief/SKILL.md): YAML metadata describes when
   it applies; the body defines preparation, safe practice, cleanup, and an exact
   JSON output contract. No executable scripts are included.
2. Run [demo.py](./demo.py). It packages the file into a ZIP, gives the temporary
   skill a unique matching frontmatter/resource name, and uploads it to Foundry.
3. The harness downloads **that specific version**, not the mutable default,
   verifies its contents byte-for-byte in memory, and prints its version.
4. Two independent temporary agent versions receive the same title/model:
   first without the skill, then with its downloaded content added to system
   instructions. Both answers and actual token usage, when returned, are printed.
5. The skill-enabled answer must match every expected field/value with no extra
   fields. A missing marker, wrong title/checklist, invalid JSON, empty answer,
   incomplete response, or service failure exits nonzero.
6. Agent versions and the unique skill are deleted in `finally` blocks,
   including after download, inference, or validation failure.

This is **application-managed direct injection**, not autonomous discovery or a
native `skills=` option on a prompt agent. The agent executes in Foundry; the
Python harness runs locally. Skills supply reusable instructions, not new model
weights or executable tools. There is no Toolbox, MCP resource discovery,
progressive loading, hosted-container deployment, or GitHub token requirement.
The baseline is shown, not required to fail: generative output is variable and
this comparison is not a quality benchmark. The marker and checklist verify a
narrow instruction-following contract, not general correctness or safety.

## Prerequisites

- Python 3.10+, Azure CLI with Bicep, and `az login` as an interactive user.
- Resource creation and role-assignment permissions for deployment.
- GPT-4.1-mini `2025-04-14` GlobalStandard availability and quota in your region.
- Foundry User (formerly Azure AI User) access, assigned at account scope by the
  template, covering its project and model.
- Preview Skills API support in the selected project/region. The SDK client uses
  `allow_preview=True` to enable preview operations/headers. A 400/404 indicating
  missing preview support is an error, not a reason to bypass skill storage and
  substitute local instructions.

Use only synthetic titles. Public networking is enabled for this workshop;
GlobalStandard processing may occur outside the resource region. API-key
authentication is disabled; `AzureCliCredential` uses your signed-in identity.

## Deploy and run

From the repository root, the existing runner discovers this folder:

```bash
az login
python3 deploy.py --subscription "<subscription-id>" --demo 28
```

The runner installs dependencies, runs offline tests, builds/deploys Bicep, and
executes the live demo. It consumes the existing `endpoint`, `deployment`, and
`projectEndpoint` output contracts; no runner changes are required.

Alternatively, from this folder:

```bash
az login
az account set --subscription "<subscription-id>"
RG=rg-foundry-agent-skills-demo
LOCATION=eastus2
PRINCIPAL_ID=$(az ad signed-in-user show --query id -o tsv)
az group create --name "$RG" --location "$LOCATION" --tags SecurityControl=Ignore SecurityExemption=StorageAccountKeyAccess
az deployment group create --name lab28 --resource-group "$RG" \
  --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location="$LOCATION"
export FOUNDRY_PROJECT_ENDPOINT=$(az deployment group show -g "$RG" -n lab28 --query properties.outputs.projectEndpoint.value -o tsv)
export AZURE_OPENAI_DEPLOYMENT=$(az deployment group show -g "$RG" -n lab28 --query properties.outputs.deployment.value -o tsv)
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s . -p 'test_demo.py' -v
python demo.py
python demo.py --title "Responsible AI workshop"
```

Use `--debug-response` to print the complete response JSON for troubleshooting.
RBAC propagation may take several minutes; 403 is an access error, 429 indicates
quota/rate limits, and unsupported model or preview operations remain errors.
Every successful live run makes two paid model requests. No invented token
counts or current-price estimates are reported. The template creates a dedicated
Foundry account, project, model deployment, and presenter role assignment with
the repository's required resource tags.

## Offline checks

```bash
python -m unittest discover -s . -p 'test_demo.py' -v
az bicep build --file infra/main.bicep --stdout >/dev/null
```

Tests use SDK models and mocked service boundaries to check upload/download,
pinned version selection, unchanged prompts, injected instructions, exact output
validation, and cleanup on failures. These are not live Azure verification.

## Cleanup

Normal execution removes its temporary data-plane objects; infrastructure stays
deployed and can incur charges. If interrupted before cleanup completes, the
printed unique skill/agent names identify objects to remove in the project.
Do not delete someone else's objects in a shared project.

To remove this lab's dedicated resource group, confirm the subscription and name:

```bash
az group delete --subscription "<subscription-id>" --name rg-foundry-agent-skills-demo --yes --no-wait
```

## Sources

- [Foundry skills, direct injection, versioning, preview, and RBAC](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/skills)
- [Azure AI Projects skills CRUD sample](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/ai/azure-ai-projects/samples/skills/sample_skills_crud.py)

The pinned `azure-ai-projects==2.7.0` SDK exposes `download_version`; some Learn
examples use `download_content` for the default version. This demo uses the
installed SDK's version-specific operation and never follows the default alias.

## Browser UI

The [lab 27 browser UI](../27-FoundryIQwithKB/README.md#azure-hosted-ui) also
supports this demo. It shows the two actual responses, token usage, skill version,
and contract checks; temporary skill and agent cleanup remains part of the
underlying workflow. Use synthetic titles. Each comparison incurs two model
requests. It is not a native autonomous skills-discovery demonstration.

For local use, after deploying both labs, set `UI_SKILLS_PROJECT_ENDPOINT` to
this lab's `projectEndpoint` output and start lab 27's `ui.py`. The UI uses the
presenter's Azure CLI identity.

For Azure use, first deploy the UI using lab 27's instructions. Give its
system-assigned identity access only to this lab's project:

```bash
UI_RG="<lab-27-ui-resource-group>"
WEBAPP="<lab-27-ui-app-name>"
SKILLS_RG="rg-foundry-agent-skills-demo"
SKILLS_ACCOUNT="<lab-28-foundry-account-name>"
UI_PRINCIPAL="$(az webapp identity show -g "$UI_RG" -n "$WEBAPP" --query principalId -o tsv)"
az deployment group create -g "$SKILLS_RG" -n lab28-ui-role \
  --template-file infra/ui-role.bicep \
  --parameters accountName="$SKILLS_ACCOUNT" principalId="$UI_PRINCIPAL"
```

In the lab 27 UI deployment, set `skillProjectEndpoint` to lab 28's
`projectEndpoint` output (and `skillDeployment` if the model deployment has a
different name). Redeploy that template with all existing parameters including
`publicAccess=true`, then deploy the updated UI package. RBAC can take time to
propagate; errors are shown rather than replaced with fixture answers.

The role assignment is in lab 28's resource group and survives deleting the UI
resource group. Before removing the UI, remove only that assignment:

```bash
ROLE_ID="$(az deployment group show -g "$SKILLS_RG" -n lab28-ui-role \
  --query properties.outputs.roleAssignmentId.value -o tsv)"
az role assignment delete --ids "$ROLE_ID"
```
