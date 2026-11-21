# Microsoft Foundry - Workflow Agent

The infrastructure deploys the model before the project to avoid concurrent
Foundry account-child writes failing with `RequestConflict`.

A 3-minute terminal demo: a **writer** drafts a workshop invitation, then an
**editor** improves it. Both outputs are printed so the handoff is visible.
Microsoft Agent Framework runs the sequential workflow locally; Microsoft Foundry
runs the model inference. This is not a portal-authored or hosted workflow.

## Browser UI

The [shared UI](../27-FoundryIQwithKB/README.md#azure-hosted-ui) runs this same
workflow and displays both the writer draft and editor result. It does not send
the invitation. For local use, set `UI_WORKFLOW_PROJECT_ENDPOINT` to this lab's
project endpoint before starting the UI; `UI_WORKFLOW_DEPLOYMENT` defaults to
`gpt-4.1-mini`. Each run incurs two model requests.

For Azure hosting, pass `workflowProjectEndpoint` and optionally `workflowDeployment`
to the shared UI template, retaining the other existing parameters. Grant its
system-assigned identity Foundry User at this lab's account scope, with
`RESOURCE_GROUP` set to the lab 2 resource group:

```bash
az deployment group create -g "$RESOURCE_GROUP" -n ui-access \
  --template-file ../0-model-basics/infra/ui-role.bicep \
  --parameters accountName="<lab-2-account-name>" principalId="<ui-system-identity-object-id>" \
    roleDefinitionGuid=53ca6127-db72-4b80-b1b0-d745d6d5456d
```

Remove this role assignment when removing the UI, or delete this lab's resource
group when no longer needed. Browser sign-in and managed-identity inference
must be verified separately from deployment.

## Infrastructure and prerequisites

The standalone Bicep template creates a Foundry account, project, `gpt-4.1-mini`
Global Standard deployment (`2025-04-14`), and Foundry User role for the presenter.
API keys are disabled; authentication uses the same interactive `az login` user.

Requires Python 3.10+, Azure CLI with Bicep, model access/quota in **East US 2**,
and permissions to create resources and assign roles. Contributor alone is not
enough to assign roles. Public endpoints and Global Standard processing make
this a workshop setup, not a production networking or data-residency design.

## Deploy

Run from this folder in Bash/Zsh:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
RESOURCE_GROUP="rg-foundry-workflow-agent-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2 --tags SecurityControl=Ignore SecurityExemption=StorageAccountKeyAccess
az deployment group create --resource-group "$RESOURCE_GROUP" \
  --name workflow-agent-demo --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2

export FOUNDRY_PROJECT_ENDPOINT="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" --name workflow-agent-demo \
  --query properties.outputs.projectEndpoint.value -o tsv)"
export AZURE_OPENAI_DEPLOYMENT="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" --name workflow-agent-demo \
  --query properties.outputs.deployment.value -o tsv)"
```

If Graph access is restricted, use your administrator-provided user object ID.
Model name, version, deployment name, and capacity are Bicep parameters if the
default model is unavailable in your subscription. Match the name and version.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
python demo.py --prompt "Invite developers to a 30-minute beginner Python workshop."
```

Alternatively, use `--project-endpoint` and `--deployment`. To deploy and run
**all demos**, use the [root runner](../README.md).

### Presenter talk track

1. Show `SequentialBuilder(participants=[writer, editor])` in the Python script.
2. Run it and compare **Writer draft** with **Editor final**.
3. Explain that ordering and handoff are deterministic, while the wording is not.
   Both agents use the same deployed model; no second model is needed.

There are two model requests per run, with usage charges. Nothing is booked,
sent, or published. The workflow and agents exist only in the local process.

## Validate and troubleshoot

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
```

Tests are offline; Azure deployment and inference require a separate live run.
For 403, check the signed-in identity and allow several minutes for RBAC
propagation. For 404, use **projectEndpoint**, not the account's OpenAI endpoint,
and check the deployment name. For deployment/quota errors or 429, check regional
model availability/quota; do not retry continuously. Empty stages and service
errors fail explicitly rather than producing a substitute answer.

## Cleanup

Delete only the dedicated resource group:

```bash
az group delete --name "$RESOURCE_GROUP" --yes
```

- [Agent Framework sequential workflows](https://learn.microsoft.com/agent-framework/workflows/orchestrations/sequential?pivots=programming-language-python)
- [Foundry workflow migration guidance](https://learn.microsoft.com/azure/foundry/agents/how-to/workflow)
- [Feature F002](../BACKLOG.md#f002---microsoft-foundry-workflow-agent)
