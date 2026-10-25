# Microsoft Foundry - Super-basic Foundry IQ

A 3-minute terminal example of the **knowledge layer behind Foundry IQ**:
create an index containing three tiny workshop fact sheets, reference it from a
knowledge source and knowledge base, then ask a compound question. Azure AI Search
plans retrieval, searches, and synthesizes an answer with references.

This follows [Microsoft Learn's agentic-retrieval quickstart](https://learn.microsoft.com/azure/search/search-get-started-agentic-retrieval),
using its REST API pattern with a much smaller, synthetic dataset. The three
documents are included directly in the script, so no downloads are needed.
It uses the real managed knowledge-base API, **not a local RAG imitation**.

To keep it small, this example uses a text index and semantic ranker rather than
the quickstart's vector index/NASA corpus. No embedding model, storage account,
hosted agent, Foundry portal connection, or UI is needed. It demonstrates the
agentic-retrieval foundation of IQ, not SharePoint permissions or a full enterprise
IQ deployment.

## Deploy

Requires Python 3.10+, Azure CLI with Bicep, an interactive Azure user with resource
creation and role-assignment permissions, a region supporting agentic retrieval,
and `gpt-4.1-mini` quota. The template deploys:

- One keyless Foundry account and `gpt-4.1-mini` (`2025-04-14`, Global Standard).
- One **Basic Azure AI Search** service with managed identity and semantic ranker.
- Search Service Contributor, Search Index Data Contributor, and Search Index
  Data Reader for the presenter.
- Cognitive Services User on Foundry for the search service's managed identity.

Run from this folder in Bash/Zsh:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
az provider register --namespace Microsoft.Search --wait
RESOURCE_GROUP="rg-foundry-foundry-iq-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2
az deployment group create -g "$RESOURCE_GROUP" -n demo \
  --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2
export AZURE_OPENAI_ENDPOINT="$(az deployment group show -g "$RESOURCE_GROUP" -n demo \
  --query properties.outputs.endpoint.value -o tsv)"
export AZURE_OPENAI_DEPLOYMENT="$(az deployment group show -g "$RESOURCE_GROUP" -n demo \
  --query properties.outputs.deployment.value -o tsv)"
export AZURE_SEARCH_ENDPOINT="$(az deployment group show -g "$RESOURCE_GROUP" -n demo \
  --query properties.outputs.searchEndpoint.value -o tsv)"
```

The knowledge-base APIs use **`2026-08-01-preview`**, matching the linked Learn
quickstart's REST reference. Confirm preview/region availability before presenting.
Both services use public endpoints and Entra ID, not API keys.

## Run and present

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
python demo.py --question "Is the workshop free, and do I need prior AI experience?"
```

1. Show the three fact sheets and the index -> knowledge source -> knowledge base
   relationship in the script.
2. Ask when the workshop is, how long it lasts, and what to bring.
3. Show the actual answer, retrieval activity/subqueries, and source references.

The script creates/updates only its fixed workshop names, checks every document
upload, and waits up to 60 seconds for index visibility. Reruns reuse the same
index/source/base; resources remain available for inspection. The API decides
how to plan a query: multiple subqueries are not guaranteed for every question.
Missing grounding evidence or reported retrieval errors fail explicitly.

## Validate and clean up

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
az group delete --name "$RESOURCE_GROUP" --yes
```

**Basic Search has ongoing charges while provisioned**, even with no queries.
Agentic retrieval and planning/answer synthesis can also incur usage charges;
the semantic free allowance is limited. For 403, allow both presenter and search
identity RBAC to propagate. For 404/API-version errors, verify preview availability
and endpoint names; don't replace the managed pipeline with local search.

- [Learn quickstart and source sample links](https://learn.microsoft.com/azure/search/search-get-started-agentic-retrieval)
- [Foundry IQ overview](https://learn.microsoft.com/azure/foundry/agents/concepts/what-is-foundry-iq)
- [Feature F011](../BACKLOG.md#f011---microsoft-foundry-foundry-iq)
