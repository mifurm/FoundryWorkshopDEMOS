# Lab 27 - Foundry IQ with a Storage-backed Knowledge Base

Create a **persistent agent in the new Microsoft Foundry portal**, connected to a
real **Foundry IQ knowledge base**, backed by two private Azure Blob containers.
Unlike [lab 11](../11-foundry-iq/README.md), this lab includes Storage, managed
ingestion/embeddings, a Foundry project, and a portal-visible agent. It is not
classic Agent Service file search, a direct Search index tool, or local RAG.

```text
Storage account (Entra ID; anonymous access and shared keys disabled)
  support-cases/*.txt -> managed Blob knowledge source: surface-support-cases
  surface-kb/*.txt    -> managed Blob knowledge source: surface-reference
                         |
               Azure AI Search managed ingestion
               chunking -> embeddings -> indexes; hourly refresh
                         |
               Foundry IQ: surface-support-kb
               LLM planning + multi-source retrieval + references
                         |
               knowledge-base MCP endpoint
               RemoteTool / ProjectManagedIdentity connection
                         |
               Foundry project: surface-support
               agent: surface-support-agent
               New Foundry -> Build -> Agents -> playground
```

## Synthetic scenario

[data/support-cases](./data/support-cases) contains four fictional computer cases:
Surface display/dock trouble, travel battery drain, an Arm64 VPN incompatibility,
and a desktop cable issue. [data/surface-kb](./data/surface-kb) contains four
fictional Surface troubleshooting/evidence runbooks. Case IDs link to runbook IDs.
All documents explicitly say **SYNTHETIC WORKSHOP DATA**. These are not official
Microsoft repair instructions, product guarantees, warranty policies, or real
customer records. No external documents are downloaded or copied.

## Prerequisites and cost

- Python 3.10+, Azure CLI with Bicep, and interactive `az login`.
- Permission to create resources and role assignments, including the
  **Foundry Project Manager** role used to create project connections.
- Quota for `gpt-4.1-mini` (`2025-04-14`, Global Standard) and
  `text-embedding-3-small` (`1`, Standard) in East US 2.
- Agentic retrieval/semantic ranker and the **`2026-08-01-preview`** Search API
  in the Search region (West US 2 by default). Project connections use
  `2025-10-01-preview`; agent SDK is pinned to `azure-ai-projects==2.7.0`.
- The **new** Foundry portal at <https://ai.azure.com>, not the classic portal.
  Portal paths and preview availability can change.

Basic Search incurs ongoing charges even when idle. Storage, model planning,
embeddings, agent inference, and scheduled ingestion also incur usage charges.
The template uses public network endpoints with Entra authentication, not public
blob access; private networking and production hardening are outside this lab.
RBAC is scoped to each resource: presenter can upload blobs/manage Search and
Foundry; Search identity reads blobs and calls models; project identity has
Search Index Data Reader. No API keys, SAS URLs, or connection-string secrets.
The trusted workshop participants all see the same synthetic corpus.

## Deploy and run

From the repository root:

```bash
az login
python3 deploy.py --subscription "<subscription-id>" --demo 27 \
  --location eastus2 --search-location westus2
```

This compiles/lints Bicep, installs dependencies in the lab's virtual environment,
runs offline tests, deploys a dedicated resource group, then uploads and reads
back all eight documents. The **Blob knowledge-source service**, not this Python
script, creates indexes, indexers, skillsets, chunks, and embeddings. The script
waits for successful ingestion and visible indexed content, creates the knowledge
base and both project connections, creates/reads back an agent version, displays
actual retrieval activity/references, and invokes the agent through MCP.

The default group is `rg-foundry-FoundryIQwithKB-demo`. State is saved in ignored
`.azure/deployment.json`; actual ingestion, definitions, agent version, retrieval,
and agent response evidence are retained in `.azure/run.json`. A failed run
leaves `complete: false` and exits nonzero. No fallback/local answer is used.
Newly assigned HTTP 403 access is visibly retried at most ten times; schema and
other HTTP failures fail immediately. Ingestion has a 15-minute per-source limit.

Standalone deployment (run from this folder; use a dedicated group):

```bash
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
az provider register --namespace Microsoft.Search --wait
az provider register --namespace Microsoft.Storage --wait
RG="rg-foundry-FoundryIQwithKB-demo"
az group create -n "$RG" -l eastus2 --tags SecurityControl=Ignore SecurityExemption=StorageAccountKeyAccess
az deployment group create -g "$RG" -n lab27 --template-file infra/main.bicep \
  --parameters principalId="$(az ad signed-in-user show --query id -o tsv)"
mkdir -p .azure
az deployment group show -g "$RG" -n lab27 \
  --query '{outputs:properties.outputs}' -o json > .azure/deployment.json
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
```

Reruns use the same blobs/sources/base/connections and call `create_version`
for the same agent name, not a disposable agent. The service can reuse a version
for an identical definition (observed in live testing). The agent is deliberately
**not deleted** after the script exits so it remains usable in the portal.
Existing versions remain until the resource group is deleted.

## New Foundry portal walkthrough (5-10 minutes)

1. Open <https://ai.azure.com>. Turn **New Foundry** on if the toggle is shown.
   Select the deployed account and project **surface-support** (confirm the
   project resource ID against deployment outputs if other projects have similar names).
2. Select **Build -> Knowledge**. Use the Search connection **surface-iq-search**
   and open **surface-support-kb**. If your portal first asks to connect Search,
   choose the already deployed service/connection; do not create a duplicate KB.
   Inspect its two Blob knowledge sources, container names, embedding deployment,
   and hourly ingestion schedule. This is the actual managed knowledge base.
3. Select **Build -> Agents -> surface-support-agent** and the latest version.
   Inspect its knowledge/tool configuration: the **surface-support-kb-mcp**
   project connection targets `/knowledgebases/surface-support-kb/mcp`, uses the
   project identity, and allows only `knowledge_base_retrieve`. Depending on the
   preview portal build, it can appear as Knowledge or as an MCP tool.
4. Open the agent playground and paste the compound question below. Inspect the
   knowledge-base tool invocation and source references, not just fluent text.
   Compare CASE-1001/1002 with KB-SURFACE-001/002 in Storage.
5. Ask the Arm64 follow-up and the unanswerable warranty question. Answers should
   separate case evidence from runbook advice and abstain on unknown information.
   Missing evidence is not permission to use the model's pretrained knowledge.

Suggested prompts:

> A Surface Laptop 5 external monitor is blank after sleep, and a Surface Laptop 6
> for Business loses battery in a bag. Compare the previous case outcomes and
> recommend the runbook steps and escalation criteria for both. Cite the CASE and KB document IDs.

> Why did the workshop VPN fail on Surface Laptop 7th Edition, and what approved
> replacement and escalation path does our runbook recommend? Cite the case and article.

> What is the exact warranty replacement price and next year's Surface Laptop specification?

The last question has no answer in this corpus. Expect
**"I don't know based on this knowledge base"**, not an invented price/specification.
The SDK run checks that an actual successful MCP retrieval occurred and that
any cited CASE/KB IDs exist in tool evidence; this is not a full factuality evaluator.

From this folder, query the same persistent agent without re-uploading or
creating another version:

```bash
.venv/bin/python demo.py --ask-only --question \
  "Why did the workshop VPN fail on Surface Laptop 7th Edition? Cite the case and runbook."
.venv/bin/python demo.py --ask-only --question \
  "What is the exact warranty replacement price and next year's Surface Laptop specification?"
```

For a browser interface to the same agent, run `.venv/bin/python ui.py` from
this folder and open <http://127.0.0.1:8027>. The UI validates that the agent
used the knowledge-base tool and that cited IDs appear in its evidence. Each
question is a new request and incurs Azure usage charges. The server uses the
presenter's Azure CLI credentials and binds only to loopback by default. The
same page also supports [lab 0](../0-model-basics/README.md#browser-ui),
[lab 1](../1-model-router/README.md#browser-ui),
[lab 2](../2-workflow-agent/README.md#browser-ui),
[lab 3](../3-webiq-agent/README.md#browser-ui),
[lab 4](../4-agent-tools/README.md#browser-ui),
[lab 5](../5-decision-model/README.md#browser-ui),
[lab 6](../6-content-safety/README.md#browser-ui),
[lab 7](../7-semantic-cache/README.md#browser-ui),
[lab 8](../8-agent-evaluation/README.md#browser-ui),
[lab 10](../10-fine-tuning/README.md#browser-ui),
[lab 11](../11-foundry-iq/README.md#browser-ui),
[lab 13](../13-agent-orchestrations/README.md#browser-ui),
[lab 14](../14-foundry-fundamentals/README.md#browser-ui),
[lab 15](../15-model-benchmark/README.md#browser-ui),
[lab 16](../16-structured-outputs/README.md#browser-ui),
[lab 17](../17-rag-fundamentals/README.md#browser-ui),
[lab 18](../18-agent-tracing/README.md#browser-ui),
[lab 19](../19-conversation-state/README.md#browser-ui),
[lab 20](../20-human-approval/README.md#browser-ui),
[lab 22](../22-prompt-injection/README.md#browser-ui),
[lab 23](../23-evaluation-cicd/README.md#browser-ui),
[lab 24](../24-agent-lifecycle/README.md#browser-ui),
[lab 25](../25-resilience-cost/README.md#browser-ui),
[lab 26](../26-multimodal/README.md#browser-ui),
[lab 28](../28-agent-skills/README.md#browser-ui),
[lab 29](../29-toolbox-skill-discovery/README.md#browser-ui),
[lab 30](../30-skill-data-analyst/README.md#browser-ui),
[lab 33](../33-skill-release-governance/README.md#browser-ui),
[lab 34](../34-document-intelligence/README.md#browser-ui),
[lab 36](../36-hosted-agent/README.md#shared-azure-hosted-ui),
[lab 37](../37-production-monitoring/README.md#shared-azure-hosted-ui),
[lab 38](../38-native-guardrails/README.md#browser-ui),
[lab 40](../40-voice-agent/README.md#browser-ui),
[lab 41](../41-content-understanding/README.md#browser-ui), and
[lab 46](../46-fleet-governance/README.md#browser-ui) when their separate Azure endpoints are configured.
[Lab 31](../31-native-agent-memory/README.md#browser-ui-presenter-portion-only)
demonstrates one authenticated presenter's memory lifecycle, not two-user isolation.
[Lab 42](../42-managed-harness-tool-search/README.md#browser-ui-tool-search-portion-only)
has a partial UI for native tool search; its managed harness remains standalone.
[Lab 44](../44-evaluation-optimizer/README.md#browser-ui-evaluator-portion-only)
has a partial UI for cloud evaluators; dataset generation and optimization remain
standalone.
Other labs have read-only guides in the UI, clearly labeled as not executable
there. The local Polish Phi exercise in lab 10 also remains guide-only.

### Azure-hosted UI

The optional UI hosting template uses App Service authentication restricted to
one presenter object ID. A system-assigned identity calls Foundry; a separate
user-assigned identity provides a federated client assertion for sign-in. No
presenter token or client secret is packaged. This is a single-presenter
workshop UI, not document-level authorization or a production multi-user app.
The Free App Service tier has limited compute and can sleep; model and Search
charges still apply. The selected subscription must have Free Linux App Service
quota in the hosting region, which may differ from the Foundry region.

Deploy the lab and run `demo.py` first. The following example creates a **new**
Entra application registration as well as an App Service; it needs permission
to create app registrations and Azure role assignments. Use a globally unique
`WEBAPP`, the deployed Foundry account name, and a region with quota. Run from
this folder, signed in to the intended subscription:

```bash
RG="rg-foundry-FoundryIQwithKB-demo"
WEBAPP="<globally-unique-web-app-name>"
LOCATION="swedencentral"
ACCOUNT="<deployed-foundry-account-name>"
PROJECT_ENDPOINT="https://${ACCOUNT}.services.ai.azure.com/api/projects/surface-support"
USER_ID="$(az ad signed-in-user show --query id -o tsv)"
TENANT="$(az account show --query tenantId -o tsv)"

az deployment group create -g "$RG" -n lab27-ui-identity \
  --template-file infra/ui-identity.bicep --query properties.outputs -o json
IDENTITY_ID="$(az identity show -g "$RG" -n surface-iq-ui-auth --query id -o tsv)"
IDENTITY_CLIENT="$(az identity show -g "$RG" -n surface-iq-ui-auth --query clientId -o tsv)"
IDENTITY_OBJECT="$(az identity show -g "$RG" -n surface-iq-ui-auth --query principalId -o tsv)"
APP_ID="$(az ad app create --display-name "${WEBAPP}-signin" \
  --sign-in-audience AzureADMyOrg --enable-id-token-issuance true \
  --web-redirect-uris "https://${WEBAPP}.azurewebsites.net/.auth/login/aad/callback" \
  --query appId -o tsv)"
az ad sp create --id "$APP_ID"
az ad app federated-credential create --id "$APP_ID" --parameters \
  "{\"name\":\"lab27-app-service\",\"issuer\":\"https://login.microsoftonline.com/${TENANT}/v2.0\",\"subject\":\"${IDENTITY_OBJECT}\",\"audiences\":[\"api://AzureADTokenExchange\"]}"
```

Save `APP_ID` for cleanup: the tenant-level registration is **not** deleted with
the resource group. Provision the web app with public access disabled first:

```bash
az deployment group create -g "$RG" -n lab27-ui --template-file infra/ui.bicep \
  --parameters location="$LOCATION" webAppName="$WEBAPP" authClientId="$APP_ID" \
  authIdentityId="$IDENTITY_ID" authIdentityClientId="$IDENTITY_CLIENT" \
  allowedUserId="$USER_ID" projectEndpoint="$PROJECT_ENDPOINT" foundryAccountName="$ACCOUNT"
```

In the Azure portal, verify Authentication is enabled, authentication is required,
and the allowed identity is the intended presenter. Then repeat that same
deployment adding `publicAccess=true` to the `--parameters` list. From the
repository root, package only the required runtime files and deploy them:

```bash
cd ..
zip -j -FS 27-FoundryIQwithKB/.azure/ui.zip 27-FoundryIQwithKB/requirements.txt
zip 27-FoundryIQwithKB/.azure/ui.zip \
  27-FoundryIQwithKB/ui.py 27-FoundryIQwithKB/demo.py 27-FoundryIQwithKB/index.html \
  0-model-basics/demo.py 1-model-router/demo.py 2-workflow-agent/demo.py 3-webiq-agent/demo.py \
  4-agent-tools/demo.py 4-agent-tools/openapi.json \
  5-decision-model/demo.py 6-content-safety/demo.py 7-semantic-cache/demo.py \
  8-agent-evaluation/demo.py 10-fine-tuning/demo.py 10-fine-tuning/training.jsonl 11-foundry-iq/demo.py \
  13-agent-orchestrations/demo.py 13-agent-orchestrations/sequential.py \
  13-agent-orchestrations/concurrent_sample.py 13-agent-orchestrations/group_chat.py 13-agent-orchestrations/magentic.py \
  14-foundry-fundamentals/demo.py 15-model-benchmark/demo.py 16-structured-outputs/demo.py \
  17-rag-fundamentals/demo.py 18-agent-tracing/demo.py \
  19-conversation-state/demo.py 20-human-approval/demo.py 22-prompt-injection/demo.py \
  23-evaluation-cicd/demo.py 23-evaluation-cicd/dataset.json \
  23-evaluation-cicd/prompts/baseline.txt 23-evaluation-cicd/prompts/regression.txt 24-agent-lifecycle/demo.py \
  25-resilience-cost/demo.py 26-multimodal/demo.py \
  26-multimodal/images/clear.png 26-multimodal/images/ambiguous.png \
  28-agent-skills/demo.py 28-agent-skills/skills/workshop-brief/SKILL.md \
  29-toolbox-skill-discovery/demo.py 29-toolbox-skill-discovery/skills/workshop-brief/SKILL.md \
  29-toolbox-skill-discovery/skills/workshop-brief/references/checklist.md \
  29-toolbox-skill-discovery/skills/support-triage/SKILL.md \
  29-toolbox-skill-discovery/skills/incident-summary/SKILL.md \
  30-skill-data-analyst/demo.py 30-skill-data-analyst/skills/csv-analyst/SKILL.md \
  31-native-agent-memory/demo.py \
  33-skill-release-governance/demo.py 33-skill-release-governance/skills/pricing-v1/SKILL.md \
  33-skill-release-governance/skills/pricing-v2/SKILL.md \
  34-document-intelligence/demo.py 34-document-intelligence/openapi.json \
  34-document-intelligence/samples/manifest.json 34-document-intelligence/samples/*.pdf \
  36-hosted-agent/demo.py 37-production-monitoring/demo.py 38-native-guardrails/demo.py \
  40-voice-agent/demo.py 40-voice-agent/assets/turn.wav \
  41-content-understanding/demo.py 41-content-understanding/assets/notice.pdf \
  41-content-understanding/assets/notice.wav \
  42-managed-harness-tool-search/demo.py 44-evaluation-optimizer/demo.py \
  46-fleet-governance/demo.py 46-fleet-governance/data/fleet.json \
  9-agent-red-teaming/README.md 10-fine-tuning/README.md 12-entra-agent-id/README.md \
  21-permission-aware-retrieval/README.md 31-native-agent-memory/README.md \
  32-browser-runbook/README.md 35-work-iq/README.md 39-gateway-token-limits/README.md \
  42-managed-harness-tool-search/README.md 43-agent-routines/README.md \
  44-evaluation-optimizer/README.md 45-governed-iq/README.md
az webapp deploy -g "$RG" -n "$WEBAPP" \
  --src-path 27-FoundryIQwithKB/.azure/ui.zip --type zip --clean true --track-status false
```

The template replaces App Service settings. Before updating an existing UI,
preserve all current `lab27-ui` deployment parameters and add any new lab
parameters; omitting an existing value can disable an already configured lab.

Open `https://<web-app-name>.azurewebsites.net/` and sign in as the allowed
presenter. Anonymous API requests must return 401; other users must be denied.
Validate a real answer after login. A successful ARM deployment alone does not
prove browser sign-in, managed-identity permissions, or live retrieval.
Removing the resource group deletes the hosting resources, but also delete the
dedicated Entra application registration with `az ad app delete --id "$APP_ID"`.
If labs 0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 22, 23, 24, 25, 26, 28, 29, 30, 31, 33, 34, 36, 37, 38, 40, 41, 42, 44 or 46 were enabled, remove their separate role assignments as
described in their READMEs. Do not delete an application registration shared
with other workloads.

## Capabilities to demonstrate

Based on [Microsoft Learn: Foundry IQ capabilities](https://learn.microsoft.com/azure/foundry/agents/concepts/what-is-foundry-iq?tabs=portal#capabilities):

| Capability | What to show here | Boundary |
| --- | --- | --- |
| One KB across multiple knowledge sources | Case-history and runbook containers feed one KB and agent | Both sources are Blob; not SharePoint/OneLake/web |
| Automated chunking, embeddings, and metadata | Inspect each source's generated index, skillset, and indexer in Search | `minimal` extraction for text; no Content Understanding/image demo |
| Incremental refresh | Hourly schedule plus the manual refresh exercise below | Scheduled runs and indexing are asynchronous; not instant sync |
| Keyword/vector/hybrid retrieval | Generated indexes have searchable text/vector fields and vectorizers; inspect the pipeline | This lab uses managed retrieval, not an application-selected three-mode benchmark (see lab 17) |
| LLM query planning, source selection, parallel searches, aggregation | Inspect real `activity` in `.azure/run.json` alongside the compound answer | Query/subquery count and parallelism are model decisions, not guaranteed |
| Extractive evidence with citations | KB uses `extractiveData`; agent reasons over returned content, cites CASE/KB IDs | Check tool evidence against documents; citations alone don't guarantee correctness |
| Reuse one KB across agents | Optionally create a second agent in Build -> Agents and attach this same KB | No re-ingestion needed; default script creates only one agent |
| ACLs, Purview labels, caller identity enforcement | Discuss as supported-source capabilities | **Not configured or demonstrated here.** Project identity is not end-user document authorization |
| Copilot Studio integration | Explain the documented connection of an Agent Service agent to Copilot Studio | Not deployed; requires separate Copilot Studio setup |

Never claim this lab proves ACL synchronization, sensitivity-label enforcement,
or per-user security trimming. Plain Blob containers here have no document ACL
ingestion or end-user token forwarding. The project identity intentionally sees
the entire synthetic corpus. Use [lab 21](../21-permission-aware-retrieval/README.md)
for a separate permission-aware exercise.

### Incremental refresh exercise

1. Ask: "What is the workshop battery triage review window?" Confirm **2 business days**
   from KB-SURFACE-002.
2. Change only that window in [KB-SURFACE-002.txt](./data/surface-kb/KB-SURFACE-002.txt)
   to **1 business day** (retain its synthetic label).
3. Run `.venv/bin/python demo.py --refresh-only`. The script uploads/readbacks
   the text and requests a managed indexer run, waiting for success. It does not
   manually patch a Search index or recreate the agent. The hourly schedule is
   an alternative to this immediate manual run.
   Unchanged blobs are read/verified but not rewritten, preserving their
   timestamps so incremental indexing processes only changed content.
4. Ask again in a **new playground conversation** or with `--ask-only`. Verify the
   new value and citation. Restore the original text and refresh when finished.
   Avoid reusing previous conversation history as evidence that ingestion changed.

## Validation, troubleshooting, cleanup

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

Offline tests verify Blob ingestion contracts, source/base and MCP wiring,
readback/error handling, exact document coverage, polling, and grounded-response validation. They don't
establish live Azure access or portal rendering.

### Live verification on 2026-10-26

Deployed and left running in `rg-foundry-FoundryIQwithKB-demo`, with Foundry
and Storage in East US 2 and Basic Search in West US 2:

- Foundry account **surface-iq-hvjxeur5pdz74**, project **surface-support**,
  persistent agent **surface-support-agent**, version **1**.
- Actual Storage readback verified all eight documents. Independent Search
  readback matched every indexed snippet to the exact corresponding local text:
  four case chunks and four runbook chunks, with source Blob URLs.
- Both generated indexes have text/vector fields, managed Azure OpenAI
  vectorizers, and hourly indexer schedules. No embedding/index upload loop runs
  in the application.
- The compound question produced LLM query planning and **six actual Blob
  subqueries** across both sources. The agent made a successful
  `knowledge_base_retrieve` MCP call and cited CASE-1001/1002 and
  KB-SURFACE-001/002.
- The Arm64 question cited CASE-1003 and KB-SURFACE-003 and recommended the
  approved Arm64 VPN client. The warranty/future-spec question explicitly
  abstained after a real MCP retrieval.
- Incremental refresh changed the battery review window from **2 business days
  to 1 business day**. The runbook indexer processed exactly **one** changed
  document; the case indexer processed **zero**. A fresh call to the same agent
  returned the new value and citation. Both local and deployed content were
  restored, and a fresh agent call confirmed **2 business days** again.
- Independent readback verified the persistent agent and both project
  connections, including the project-managed-identity MCP authentication.
- **16 lab tests**, **32 root runner tests**, Python compilation, and Bicep
  build/lint passed. Test counts reflect the repository state at validation time.

The browser reached the Foundry sign-in screen, so **authenticated portal
rendering and playground interaction were not visually verified**. The deployed
project, portal-discoverable Search connection, and actual agent/MCP behavior
were verified through Azure APIs; sign in and use the walkthrough above.

Actual run evidence is retained in ignored `.azure/live-verification.json`,
`.azure/live-final.json`, `.azure/live-arm64.json`, `.azure/live-unknown.json`,
and `.azure/live-refresh-*.json`, with transcripts alongside them. Six recorded
live scenarios were checked independently. These runs are proof of this
synthetic scenario, not production-quality or permission-trimming certification.

- **403:** confirm presenter, Search, and project identity assignments. Allow
  propagation; an enduring 403 is an error, not a successful empty result.
- **Search provisioning capacity:** use a supported `--search-location` in a new
  dedicated group. Existing Search resources cannot move region in place.
- **Ingestion failure:** inspect generated indexer status/errors in Search.
  Confirm Search can read Storage and call the embedding deployment. Warnings
  and failed items fail explicitly so missing chunks aren't hidden.
- **404/API/tool failure:** verify endpoints, region/preview availability, the
  MCP connection audience, and the latest agent version. Never substitute classic
  file search or a direct index tool for this knowledge-base MCP endpoint.
- **Knowledge not listed:** select the correct project and Search connection in
  the new portal; reload after connection creation. The lab also creates an AAD
  `CognitiveSearch` connection so the Search-backed KB can be discovered.

Resources and scheduled indexers remain deployed and billable. Delete only the
dedicated lab group after the presentation:

```bash
az group delete --subscription "<subscription-id>" \
  --name rg-foundry-FoundryIQwithKB-demo --yes
```

References:
- [Foundry IQ overview and new portal workflow](https://learn.microsoft.com/azure/foundry/agents/concepts/what-is-foundry-iq?tabs=portal)
- [Managed Blob knowledge sources](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-blob)
- [Knowledge-base MCP integration with Foundry agents](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect)
- [F027 acceptance criteria](../BACKLOG.md#f027---microsoft-foundry-foundry-iq-with-a-storage-backed-knowledge-base)
