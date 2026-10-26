# Microsoft Foundry - Simple WebIQ Agent

A 3-minute terminal demo: a Foundry prompt agent searches the public web,
answers a question about model router, and prints **source titles and URLs**.
Here, the workshop label "WebIQ" means Foundry's built-in **Web Search** tool;
it is not a separate WebIQ product or a private enterprise search integration.

## Infrastructure and prerequisites

Bicep creates a Foundry account, project, `gpt-5-mini` Global Standard deployment
(`2025-08-07`), and a Foundry User role for the presenter. The Python script
creates a temporary server-side prompt-agent version with `WebSearchTool`,
invokes it, and deletes that version in `finally`, even on inference failure.
No separate Bing resource, connection, or API key is required for this path.

Requires Python 3.10+, Azure CLI with Bicep, model access/quota in **East US 2**,
and permissions to create resources and assign roles. Your organization must
permit Web Search; access policies can disable it independently of deployment.
Contributor alone cannot assign roles.

**Costs and data:** model requests and web search incur charges. Web Search uses
Grounding with Bing, whose terms apply; data can leave Azure compliance and
geographic boundaries, and the Microsoft DPA does not apply to that data.
Use only public, non-sensitive questions. Public endpoints and Global Standard
processing are intentional workshop simplifications.

## Deploy

From this folder in Bash/Zsh:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
RESOURCE_GROUP="rg-foundry-webiq-agent-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2
az deployment group create --resource-group "$RESOURCE_GROUP" \
  --name webiq-agent-demo --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2

export FOUNDRY_PROJECT_ENDPOINT="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" --name webiq-agent-demo \
  --query properties.outputs.projectEndpoint.value -o tsv)"
export AZURE_OPENAI_DEPLOYMENT="$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" --name webiq-agent-demo \
  --query properties.outputs.deployment.value -o tsv)"
```

If Graph is unavailable, supply your user object ID directly. Model name/version,
deployment name, and capacity are parameters for subscriptions where the default
model is unavailable. Select a model that supports Web Search.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
python demo.py --prompt "Search Microsoft Learn for Foundry Agent Service and summarize its purpose with citations."
python demo.py --debug-response
```

`--project-endpoint` and `--deployment` can replace environment variables.
The default deployment name is `gpt-5-mini`. Redeploy the Bicep template to create
the new model deployment; changing the Python deployment name alone does not
upgrade an existing `gpt-4.1-mini` deployment. Refresh the environment variables
from the deployment outputs afterward, especially if a previous session set
`AZURE_OPENAI_DEPLOYMENT` to the old name.
The [root runner](../README.md) deploys and executes all four demos.

### Presenter talk track

1. Show that the agent definition contains `WebSearchTool()` and that the request
   sets `tool_choice="required"`.
2. Run the example. Point out the actual web-search call count and source URLs.
3. Explain that citations provide provenance, not a guarantee of truth. Visit
   the source and verify important claims; search results and wording can change.

The script rejects responses without completed searches, text, and URL citations.
It never substitutes a made-up grounded answer.

## Validate and troubleshoot

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
```

Tests mock service calls and use real SDK response objects. They do not prove
subscription access; perform a separate live smoke test before presenting.
Every response prints its status, output item types, search-call count/statuses,
and structured URL-citation count before validation, including on failure.
`--debug-response` additionally prints the full response JSON. Use only public,
non-sensitive prompts with this flag; captured output may contain response text
and source data. Raw JSON is not printed by default.

If grounding validation fails:

- **Citations but no search-call output:** inspect the Foundry agent trace and raw
  response for how this API surfaces tool execution. Do not assume the model did
  not search solely because the response lacks `web_search_call`.
- **Search-call output but no citations:** inspect message annotations. Plain-text
  URLs do not count as structured `url_citation` annotations.
- **Neither:** compare the same model and Web Search configuration in the Foundry
  playground and check tool availability and organizational policy. Try a public,
  time-sensitive question, such as the latest Microsoft Foundry announcement and
  its publication date, explicitly requesting sources.

These diagnostics do not relax the checks: completed search calls, nonempty answer
text, and structured citations are still required. Failed or unfinished searches
report their statuses and fail explicitly.

For 403, check RBAC propagation and organizational Web Search policy. For 404,
use the Bicep **projectEndpoint** and correct deployment name. For 429 or deployment
failure, check model quota and region. If cleanup fails, the error remains visible;
remove the named agent version in Foundry or delete the dedicated group.

## Cleanup

The agent version is deleted automatically; the account/project/model remain:

```bash
az group delete --name "$RESOURCE_GROUP" --yes
```

- [Foundry Web Search setup, terms, and limitations](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/web-search?pivots=python)
- [Feature F003](../BACKLOG.md#f003---microsoft-foundry-simple-webiq-agent)
