# Microsoft Foundry - Microsoft Decision Model

A 2-minute terminal demo: route three toy support tickets to **billing**,
**technical**, or **account** with `Microsoft-Decision-1`. This is Microsoft's
typed Decision API, not a chat model pretending to return a classification.
The script prints the selected team, expected team, latency, and toy accuracy.

## Deploy

Requires Python 3.10+, Azure CLI with Bicep, `az login` as a user, resource
creation and role-assignment permissions, and Microsoft-Decision-1 access/quota
in the selected region. The standalone template deploys one Foundry account,
the Microsoft model (version `1`, Data Zone Standard, capacity `10`), and presenter
Foundry User RBAC. It disables API keys and uses public endpoints.

The template uses `DataZoneStandard`, which is listed for this model in East
US 2 and has quota in the tested subscription. The previous `GlobalStandard`
configuration failed with `SpecialFeatureOrQuotaIdRequired`. Model access and
quota are deployment-type-specific; changing regions alone does not guarantee
access. Data Zone Standard keeps inference within the selected data zone.
Capacity `1` deployed successfully but hit HTTP 429 during the three-ticket
live demo; the default is `10` to allow the short sequence. Capacity remains
configurable, consumes model quota, and does not guarantee absence of throttling.
The script reports HTTP 429 and honors `retry-after-ms` or numeric `Retry-After`
for at most two retries per ticket, with a maximum 65-second delay per retry.
Missing or excessive retry delays and exhausted retries fail explicitly.

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

### Live verification on 2026-10-26

The Data Zone Standard deployment succeeded in East US 2 in
`rg-foundry-decision-model-demo`. All three example tickets selected the expected
team, and a custom invoice request selected `billing`. Actual HTTP 429 responses
required service-directed waits of approximately 29-58 seconds; allow several
minutes for this demo under the observed rate limits, even at capacity 10.
ARM reported 10 requests/minute, while observed runtime throttling was stricter.
Do not assume changing capacity guarantees the nominal request rate.
Deployment outputs and successful transcripts are retained locally in ignored
`.azure/deployment.json`, `.azure/live-default-verified.log`, and
`.azure/live-custom-verified.log`. Five offline tests and Bicep lint passed.
Resources remain deployed; inference is billable.

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
