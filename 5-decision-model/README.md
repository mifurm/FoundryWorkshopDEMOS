# Microsoft Foundry - Microsoft Decision Model

A 2-minute terminal demo: route three toy support tickets to **billing**,
**technical**, or **account** with `Microsoft-Decision-1`. This is Microsoft's
typed Decision API, not a chat model pretending to return a classification.
The script prints the selected team, expected team, latency, and toy accuracy.

## Deploy

Requires Python 3.10+, Azure CLI with Bicep, `az login` as a user, resource
creation and role-assignment permissions, and Microsoft-Decision-1 access/quota
in the selected region. The standalone template deploys one Foundry account,
the Microsoft model (version `1`, Global Standard, capacity `1`), and presenter
Foundry User RBAC. It disables API keys and uses public endpoints.

Run from this folder:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
RESOURCE_GROUP="rg-foundry-decision-model-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2
az deployment group create --resource-group "$RESOURCE_GROUP" --name demo \
  --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2
export AZURE_OPENAI_ENDPOINT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.endpoint.value -o tsv)"
export AZURE_OPENAI_DEPLOYMENT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.deployment.value -o tsv)"
```

Despite the shared runner's `AZURE_OPENAI_ENDPOINT` variable name, this demo
uses the **services.ai.azure.com** endpoint and `/providers/microsoft/v1/systemone`,
not OpenAI chat completions. No project or generative model is required.

## Run and present

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
python demo.py --ticket "Please send me a copy of my invoice."
```

Show the `choice` question and its fixed criteria in the script. Run the three
examples and explain that bounded decisions differ from generated prose.
Three samples are illustrative, not evidence of production accuracy or
probability calibration. No ticket is actually routed or changed.

## Validate, costs, and cleanup

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
az group delete --name "$RESOURCE_GROUP" --yes
```

Tests are offline. Live inference is billed and model/region availability can
change. Allow time for RBAC propagation after a 403; check the model deployment
and endpoint after a 404. HTTP errors and malformed decisions fail explicitly.
Use only non-sensitive tickets; don't use the model as the sole decision-maker
for consequential decisions about people.

- [Official model and Decision API documentation](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision)
- [Feature F005](../BACKLOG.md#f005---microsoft-foundry-microsoft-decision-model)
