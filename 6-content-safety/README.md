# Microsoft Foundry - Content Safety

A 2-minute demo: send workshop text to **Azure AI Content Safety**, print severity
for Hate, SelfHarm, Sexual, and Violence, and apply an explicit ALLOW/BLOCK policy.
This is a standalone Content Safety resource, a capability available within the
Foundry ecosystem, not the model's built-in inference content filter.

## Deploy

Requires Python 3.10+, Azure CLI with Bicep, an interactive Azure user with resource
creation and role-assignment permissions, and Content Safety availability in
East US 2 (or change `location`). The template creates one S0 Content Safety
account with a custom subdomain and Cognitive Services User role for the
presenter. No model deployment is needed. API keys are disabled.

Run from this folder:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
RESOURCE_GROUP="rg-foundry-content-safety-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2
az deployment group create --resource-group "$RESOURCE_GROUP" --name demo \
  --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2
export AZURE_OPENAI_ENDPOINT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.endpoint.value -o tsv)"
```

The shared endpoint variable contains a **cognitiveservices.azure.com** URL here;
it is passed to `ContentSafetyClient`, not an OpenAI client.

## Run and present

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
python demo.py --text "Thank you for helping me learn Python." --threshold 4
```

The default is deliberately benign. Use only approved, non-sensitive test text
if presenting other categories. The service's four-level scale is **0, 2, 4, 6**;
the app blocks at or above the selected threshold. BLOCK is an application
decision, not an SDK error, so both ALLOW and BLOCK are successful analyses.
An ALLOW does not guarantee text is safe for every audience or use case.
Offline tests prove both sides of the threshold without including harmful text.

## Validate, costs, and cleanup

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
az group delete --name "$RESOURCE_GROUP" --yes
```

Requests are billed against S0; this is a public-endpoint workshop resource,
not a production networking design. For 403, check the Azure CLI identity and
allow for RBAC propagation. Missing categories, invalid scores, and service
failures are errors, never substituted with an ALLOW.

- [Content Safety Python SDK and Entra authentication](https://learn.microsoft.com/python/api/overview/azure/ai-contentsafety-readme)
- [Feature F006](../BACKLOG.md#f006---microsoft-foundry-content-safety)
