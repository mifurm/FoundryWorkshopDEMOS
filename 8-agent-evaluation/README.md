# Microsoft Foundry - Agent evaluation

A small real **Foundry cloud evaluation**: create a temporary workshop tutor agent,
ask two questions, retain its stored response IDs, and evaluate those interactions
using `builtin.coherence`. Print the run metadata, result counts, and per-item
scores. This is not a local string comparison presented as a Foundry evaluation.

## Deploy

Requires Python 3.10+, Azure CLI with Bicep, resource creation and role-assignment
permissions, and Foundry Agent Service/cloud evaluation support and model quota
in the selected region. Cloud response evaluation uses preview Foundry APIs.
The template creates a Foundry account, project, `gpt-4.1-mini` deployment
(`2025-04-14`, Global Standard), presenter RBAC, and project identity RBAC
for evaluator inference. API keys are disabled; endpoints are public.

Run from this folder:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
RESOURCE_GROUP="rg-foundry-agent-evaluation-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2
az deployment group create --resource-group "$RESOURCE_GROUP" --name demo \
  --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2
export FOUNDRY_PROJECT_ENDPOINT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.projectEndpoint.value -o tsv)"
export AZURE_OPENAI_DEPLOYMENT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.deployment.value -o tsv)"
```

## Run and present

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
```

1. Show the agent's two answers and their stored response IDs.
2. Explain that Foundry retrieves the stored interactions and uses the deployed
   model as a coherence judge; the script does not invent a local score.
3. Compare the per-item results and open the reported run/report URL when present.

The script polls for up to ten minutes. A failed run, errored item, incomplete
counts, or timeout exits nonzero. A completed low-scoring evaluation is a valid
result, not a transport failure. The temporary agent version is deleted even
when evaluation fails; stored responses/evaluation runs remain for inspection.
Timed-out cloud runs may continue and incur charges.

## Validate, costs, and cleanup

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
az group delete --name "$RESOURCE_GROUP" --yes
```

Generation and judge requests are billed. Both project and presenter RBAC may
need time to propagate after deployment. Use **projectEndpoint**, not the
account inference URL. A coherence score on two benign questions is neither a
safety certification nor a comprehensive agent-quality benchmark.

- [Evaluate deployed interactions by response ID](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-deployed-interactions)
- [Feature F008](../BACKLOG.md#f008---microsoft-foundry-agent-evaluation)
