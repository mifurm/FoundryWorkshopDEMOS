# Microsoft Foundry - Semantic caching

A 3-minute demo: embed a question, cache its generated answer, then reuse that
answer when another question has a sufficiently similar embedding. Print
HIT/MISS, cosine similarity, elapsed time, and generation/embedding request counts.

**Scope:** this is a small **application-side semantic cache** using Foundry
embeddings and a Python list. It is not a native Foundry cache switch, OpenAI
prefix/prompt caching, exact-string memoization, or persistent distributed caching.
No Redis, API Management, database, or UI is needed for this workshop.

## Deploy

Requires Python 3.10+, Azure CLI with Bicep, a user with resource creation and
role-assignment permissions, and regional quota/access for `gpt-4.1-mini`
(`2025-04-14`, Global Standard) and `text-embedding-3-small` (`1`, Standard).
Bicep creates one Foundry account, the two deployments, and presenter Foundry
User RBAC. API keys are disabled; endpoints are public.

Run from this folder:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
RESOURCE_GROUP="rg-foundry-semantic-cache-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2
az deployment group create --resource-group "$RESOURCE_GROUP" --name demo \
  --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2
export AZURE_OPENAI_ENDPOINT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.endpoint.value -o tsv)"
export AZURE_OPENAI_DEPLOYMENT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.deployment.value -o tsv)"
export AZURE_OPENAI_EMBEDDING_DEPLOYMENT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.embeddingDeployment.value -o tsv)"
```

## Run and present

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
python demo.py --threshold 0.95
```

The first question must miss. The second is a paraphrase and hits only if its
actual cosine similarity reaches the threshold (default `0.85`). The third
repeats the first to demonstrate reuse even if the paraphrase misses.
No fabricated hit or replacement answer is used. A hit avoids a **generation**
request, not the embedding request; latency savings are not guaranteed.
Every process starts with an empty cache.

Only one model, fixed instructions, and public workshop questions share this
cache. Production caching needs tenant/context/model isolation, expiry,
invalidation, and representative threshold tests: semantically similar prompts
can require different answers. Do not reuse this toy cache for private data.

## Validate, costs, and cleanup

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
az group delete --name "$RESOURCE_GROUP" --yes
```

Embedding and generation requests incur usage charges. For 403, wait for RBAC
propagation; for 404, check both deployment names; for quota errors, inspect
both model quotas. Tests verify inclusive thresholds, paraphrase hits without
generation, unrelated misses, and rejection of invalid vectors/incomplete answers.

- [Microsoft semantic caching pattern and production Redis alternative](https://learn.microsoft.com/azure/redis/tutorial-semantic-cache)
- [Feature F007](../BACKLOG.md#f007---microsoft-foundry-semantic-caching)
