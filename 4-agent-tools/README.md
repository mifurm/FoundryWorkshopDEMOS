# Microsoft Foundry - Agent with Simple Tools

A 3-5 minute terminal demo: ask an agent for the workshop price for 12 attendees.
The agent discovers a tool through **Foundry Toolbox**, calls a deployed
**Azure Function**, and explains the returned price: USD 25 per attendee,
USD 300 total. The script prints the real tool result before the answer.

## What runs where

- **Bicep:** a Foundry account/project, `gpt-4.1-mini` Global Standard deployment
  (`2025-04-14`), presenter Foundry User role, and a Windows Consumption Function
  App with storage. The function's JavaScript is deployed inline by Bicep.
  No zip upload, external code package, or manual portal editing is needed.
- **Foundry Tools:** Python registers the function's OpenAPI contract in a
  temporary toolbox version. Foundry exposes it as an authenticated MCP endpoint.
- **Local Python:** an Agent Framework agent uses `FoundryToolbox` to discover and
  call the tool, with model inference in Foundry. The agent itself is not hosted.
  The toolbox version is deleted in `finally`; the Azure resources remain.

This is a read-only, toy pricing tool: no payment, booking, database, or local
function-call substitute. The Function API is intentionally **anonymous and
public** so Foundry can call it without extra connection/secrets plumbing.
It contains no private data or write operations. Do not reuse this authentication
design for private or privileged APIs. Storage credentials stay in App Service
settings and are not returned by the deployment. Foundry API keys are disabled.

## Prerequisites

Python 3.10+, Azure CLI with Bicep, an interactive Azure user, model access/quota
in **East US 2**, and resource-creation plus role-assignment permissions.
Contributor alone cannot assign roles. Foundry Toolbox is a preview dependency;
the compatible preview hosting integration is explicitly pinned in requirements.
The hosting package supplies `FoundryToolbox` even though this agent runs locally.
No local Node.js is needed to run the demo; Node.js is optional for tool tests.

Public endpoints, Global Standard inference, and Consumption compute/storage
incur usage charges and are workshop rather than production defaults.

## Deploy

From this folder in Bash/Zsh:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
az provider register --namespace Microsoft.Web --wait
az provider register --namespace Microsoft.Storage --wait
RESOURCE_GROUP="rg-foundry-agent-tools-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2
az deployment group create --resource-group "$RESOURCE_GROUP" \
  --name agent-tools-demo --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2

export FOUNDRY_PROJECT_ENDPOINT="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" --name agent-tools-demo \
  --query properties.outputs.projectEndpoint.value -o tsv)"
export AZURE_OPENAI_DEPLOYMENT="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" --name agent-tools-demo \
  --query properties.outputs.deployment.value -o tsv)"
export WORKSHOP_TOOL_ENDPOINT="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" --name agent-tools-demo \
  --query properties.outputs.toolEndpoint.value -o tsv)"
```

If Graph access is blocked, supply your user's object ID directly. Model
name/version, deployment name, and capacity are parameters if quota or access
requires a different tool-capable model.

## Run

```bash
# Optional: verify the deployed function independently.
curl --fail-with-body "$WORKSHOP_TOOL_ENDPOINT/workshop-price?attendees=12"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
python demo.py --attendees 20
```

The three endpoint/deployment arguments can replace the environment variables.
Valid attendee counts are 1-999. The [root runner](../README.md) also supports
this demo and passes both endpoint outputs automatically.

### Presenter talk track

1. Show the tiny pricing function and its OpenAPI `getWorkshopPrice` operation.
2. Show the toolbox registration and the versioned MCP endpoint in Python.
3. Run it: show `Tool result`, the source marker `workshop-price-tool-v1`, and
   the agent answer. For 20 attendees the tool returns USD 500.
4. Explain that the business rule runs in Azure, not in the model or local Python;
   the toolbox provides a reusable, managed tool-discovery surface.

## Validate and troubleshoot

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
```

Tests mock Foundry/MCP calls. When Node.js is installed, they also execute the
actual deployed JavaScript source for valid counts and invalid input boundaries;
otherwise that one test is explicitly skipped.

For 403, allow RBAC propagation and check Toolbox access. For a failed tool call,
try the direct `curl` above; the Function host may need a short cold start after
deployment. Confirm the `/api` suffix in `toolEndpoint`. For 404/429 on inference,
check model name, region, and quota. Missing tool results or the source marker
cause an explicit failure, not a model-only fallback. If version cleanup fails,
the error is surfaced; remove the printed toolbox version or delete the group.

## Cleanup

The temporary toolbox version is removed automatically. Delete all persistent
infrastructure only by deleting the dedicated resource group:

```bash
az group delete --name "$RESOURCE_GROUP" --yes
```

- [OpenAPI tools and FoundryToolbox integration](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/openapi?pivots=python)
- [Foundry Toolbox](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/toolbox)
- [Inline Function deployment pattern](https://github.com/Azure/azure-quickstart-templates/tree/master/quickstarts/microsoft.web/function-http-trigger)
- [Feature F004](../BACKLOG.md#f004---microsoft-foundry-agent-with-simple-tools)
