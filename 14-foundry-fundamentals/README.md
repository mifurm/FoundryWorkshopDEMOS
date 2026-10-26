# 14 — Foundry fundamentals

Create a Foundry account, project, and GPT-4.1-mini deployment; use **Microsoft
Entra ID, never an API key**, for a first answer and a streamed answer with usage.
Default execution is noninteractive and makes real cloud calls.

## Prerequisites and availability

Python 3.10+, Azure CLI with Bicep, `az login`, an Azure subscription, and model
quota are required. The deploying identity needs resource creation and role
assignment rights (for example Contributor plus User Access Administrator).
The inference identity gets **Cognitive Services OpenAI User**, scoped only to the
primary account. Set `principalType=ServicePrincipal` for an application identity.
`DefaultAzureCredential` supports CLI, managed identity, or service-principal
environment credentials; interactive browser authentication is disabled.

The default is `eastus2`, GPT-4.1-mini version `2025-04-14`, GlobalStandard.
Check **your subscription's** model/version/SKU availability and quota first;
region support, access, and capacity are not guaranteed:

```bash
az cognitiveservices model list --location eastus2 -o table
az cognitiveservices usage list --location eastus2 -o table
```

## Deploy, run, test

Run from this directory. Use a **dedicated** resource group:

```bash
az login
az account set --subscription YOUR_SUBSCRIPTION_ID
RG=foundry-lab14
LOCATION=eastus2
PRINCIPAL_ID=$(az ad signed-in-user show --query id -o tsv)
az group create --name "$RG" --location "$LOCATION"
az deployment group create --name lab14 --resource-group "$RG" \
  --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location="$LOCATION" \
  --query properties.outputs -o json > deployment.json
export AZURE_OPENAI_ENDPOINT=$(az deployment group show -g "$RG" -n lab14 --query properties.outputs.endpoint.value -o tsv)
export AZURE_OPENAI_DEPLOYMENT=$(az deployment group show -g "$RG" -n lab14 --query properties.outputs.deployment.value -o tsv)
export FOUNDRY_PROJECT_ENDPOINT=$(az deployment group show -g "$RG" -n lab14 --query properties.outputs.projectEndpoint.value -o tsv)
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
python -m unittest discover -s . -p 'test_demo.py' -v
az bicep build --file infra/main.bicep --stdout >/dev/null
```

Tests are offline: stream aggregation and explicitly labeled SDK error fixtures
do not authenticate or call Azure. No test fixture is presented as a live answer.
Allow several minutes for new RBAC assignments to propagate, then rerun if needed.
The template serializes the primary model and project writes: concurrent account
child operations can fail with Azure `RequestConflict`.

## What to observe

* `FOUNDRY_PROJECT_ENDPOINT` ends in `/api/projects/workshop-tutor`: it is the
  **project management/agent** endpoint, not a chat completions base URL.
* `AZURE_OPENAI_ENDPOINT` is the account inference endpoint; the OpenAI client
  appends `/openai/v1/`. This lab does not use the project SDK for inference.
* The catalog model is `gpt-4.1-mini`; its deployment alias is `tutor-mini`.
  Pass the **alias** to `model=...`. Token counts are service-reported; a stream
  can contain a final usage-only chunk with no choices.
* A deliberately nonexistent deployment prints the actual HTTP 404 service body.
* A **second account and model** have deliberately no scoped inference assignment.
  The client derives its `-deny` hostname (or accepts `--denied-endpoint` /
  `WORKSHOP_DENIED_ENDPOINT`) and prints the actual permission error, normally 403.
  Fix by assigning OpenAI User to the target identity at that account, then wait
  for propagation. Azure management Contributor alone is not inference access.

Inherited subscription/resource-group inference roles can make the denied probe
succeed. The script reports that honestly and exits 1 instead of manufacturing a
403. Use an identity without inherited data-plane access for the intended demo.
A 401 indicates token/authentication problems, not proof of missing inference
permissions. Use `--skip-negative-probes` to run only the successful-response path.
Do not put actual secrets in prompts or publish unredacted tenant error details.

## Contracts, costs, and limitations

Bicep accepts `principalId`, `location`; emits `endpoint`, `deployment`,
`projectEndpoint`, and `deniedEndpoint`. The root runner maps all four to the
corresponding environment variables. Without an explicit denied endpoint the
script also derives its default from the primary hostname.
Custom `accountName` must leave five characters for `-deny` (max 64 total).

Default run: **four requests** (normal, streamed, wrong-deployment, denied).
Successful model responses incur pay-as-you-go token charges; errors are not
assumed to be billable successes. Two model deployments reserve quota, not
provisioned-throughput billing. Check the current
[Azure OpenAI prices](https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/).
No fixed total is promised. GlobalStandard may process outside the account region;
public networking is enabled for workshop convenience. This is not production
network isolation, a retry framework, or a guarantee of answer quality.

## Cleanup

Deletes **both accounts**, project, deployments, and scoped role assignment:

```bash
az group delete --name foundry-lab14 --yes --no-wait
```

## Official API references

* [Keyless OpenAI/Azure endpoint differences](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/switching-endpoints)
* [Azure OpenAI role-based access](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/role-based-access-control)
* [Foundry account schema](https://learn.microsoft.com/azure/templates/microsoft.cognitiveservices/2025-06-01/accounts)
* [Project schema](https://learn.microsoft.com/azure/templates/microsoft.cognitiveservices/2025-06-01/accounts/projects)
