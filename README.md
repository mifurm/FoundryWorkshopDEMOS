# Foundry workshop demos

## Deploy and test everything

Requires Python 3.10-3.13 for running all demos (the red-teaming SDK restricts
Python versions), Azure CLI, Bicep (`az bicep version`), and an interactive
Azure user with resource creation and role-assignment permissions. See each
demo's prerequisites for model quota and access requirements.

Before running **all 13 demos**, complete the tenant setup in
[Entra Agent ID](./12-entra-agent-id/README.md). It requires separate tenant
roles/admin consent and sets `ENTRA_TENANT_ID`, `ENTRA_BLUEPRINT_CLIENT_ID`,
`ENTRA_AGENT_ID`, and `ENTRA_BLUEPRINT_SECRET`. Run the root command from that
same PowerShell session (change back to the repository root). The runner checks
these prerequisites before any Azure changes and passes only the non-secret
agent ID to Bicep. The blueprint secret is not a deployment parameter or output.
To present individual demos, use their standalone deployment instructions instead.

From the repository root, after that setup:

```bash
az login
python3 deploy.py --subscription "<subscription-id>"
```

If `--subscription` is omitted, the script uses the currently selected Azure CLI
subscription. It does not change that selection. Optional flags:
`--location eastus2`, `--prefix rg-foundry`, and `--principal-id <user-object-id>`
(useful when Microsoft Graph access is restricted).

The script:

1. Discovers every immediate demo folder containing `infra/main.bicep` or
   `requirements.txt`, rejecting incomplete folders rather than silently skipping them.
2. Compiles/lints Bicep, creates/reuses each demo's `.venv`, installs its
   requirements, and runs its local unit tests before making Azure changes.
3. Registers the resource providers referenced by the compiled templates and creates a dedicated resource group
   named `<prefix>-<demo-folder-without-number-prefix>-demo` for each demo.
4. Deploys the existing template, passing `principalId` and `location`.
5. Saves deployment outputs to `<demo>/.azure/deployment.json`, maps the `endpoint`
   output plus optional `deployment`, `embeddingDeployment`, `projectEndpoint`, and `toolEndpoint`
   outputs to the demo's environment, and runs `demo.py`.
   Foundry IQ also uses `searchEndpoint`; Entra Agent ID uses `agentIdentityId`.
6. Saves live output to `<demo>/.azure/demo.log` and stops with a nonzero exit code
   if any step fails. Newly assigned 403 access errors and 404
   `DeploymentNotFound` errors, plus explicit DNS name-resolution failures, are
   visibly retried at 30-second intervals, up to 11 attempts. Other errors
   (including TLS/certificate errors) fail immediately. Each retry restarts the
   demo, so prompts completed before the error may be sent again and incur charges.

If DNS failures persist, compare the endpoint in `<demo>/.azure/deployment.json`
with the account's endpoints in Azure, then check your local DNS/VPN/network.
A successful ARM deployment does not guarantee the endpoint is immediately
resolvable from your machine. The runner preserves every failed attempt in the
live log and exits nonzero if its retry limit is reached; it does not change
endpoints, bypass certificate validation, or replace failed responses.

**This command creates billable Azure resources, makes live model requests,
runs managed evaluation/red teaming, and submits a paid fine-tuning job.**
Fine-tuning only reports job submission by default; it does not wait for training
to finish. Every rerun submits another training job. Use that demo's `--job-id`
option directly to inspect an existing job instead.
Resources remain deployed for presenting. Rerunning uses the same resource groups
and deployment names and repeats the tests and live calls. The script does not
automatically delete resources on failure. The ignored `.azure` state/logs contain
resource identifiers and demo responses, not API keys or access tokens.
Red-team reports/SDK logs may contain disturbing safety-test text; do not
project raw transcripts or commit them.

To add another demo, use the same folder convention: `infra/main.bicep`,
`requirements.txt`, `demo.py`, and `test_demo.py`. Templates must accept `principalId`
and `location` and expose a string output `endpoint`, plus `deployment` when
using an inference model; Python demos consume `AZURE_OPENAI_DEPLOYMENT` when
applicable and the endpoint appropriate to their API, exit
nonzero on failure, and validate the live response. Account-level inference uses
`AZURE_OPENAI_ENDPOINT`; project APIs use `FOUNDRY_PROJECT_ENDPOINT` from
`projectEndpoint`; the hosted pricing tool uses `WORKSHOP_TOOL_ENDPOINT` from
`toolEndpoint`. Embeddings use `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` from
`embeddingDeployment`. Content Safety has no model deployment and consumes its
Cognitive Services URL through the shared endpoint variable. The Decision Model
consumes its Foundry services URL through that variable. Foundry IQ uses
`AZURE_SEARCH_ENDPOINT` from `searchEndpoint`; the Entra identity demo receives
`ENTRA_AGENT_ID` from `agentIdentityId` and needs its separate tenant setup.
Other output contracts
require updating the runner. Numbered folders are processed in numeric order.

## Demos and tracking

- [Model Router instructions and cleanup](./1-model-router/README.md)
- [Workflow Agent: writer -> editor](./2-workflow-agent/README.md)
- [Simple WebIQ Agent: web search with citations](./3-webiq-agent/README.md)
- [Agent with Simple Tools: Azure Function through Foundry Toolbox](./4-agent-tools/README.md)
- [Microsoft Decision Model: typed support-ticket routing](./5-decision-model/README.md)
- [Content Safety: category severity and a simple policy gate](./6-content-safety/README.md)
- [Semantic caching: Foundry embeddings and a local answer cache](./7-semantic-cache/README.md)
- [Agent evaluation: Foundry cloud coherence evaluation](./8-agent-evaluation/README.md)
- [Agent red teaming: a small managed baseline scan](./9-agent-red-teaming/README.md)
- [Fine-tuning: a one-epoch supervised training job](./10-fine-tuning/README.md)
- [Foundry IQ: a tiny Learn-based managed knowledge base](./11-foundry-iq/README.md)
- [Entra Agent ID: an Agent Framework tutor with its own identity](./12-entra-agent-id/README.md)
- [Agent Framework: Sequential, Concurrent, Group Chat, and Magentic](./13-agent-orchestrations/README.md)
- [Backlog](./BACKLOG.md)

Each demo has standalone Bicep, local Python code, dependencies, tests, and manual
deployment/cleanup instructions. Generative agent demos use `gpt-4.1-mini` by
default; Model Router and Microsoft Decision use their own specialized models.
Content Safety needs no model, and semantic caching adds an embedding deployment.
The cache is an application pattern, not a native Foundry cache toggle.
Model access, quota, regional availability, Web Search policy, Toolbox/evaluation/
red-teaming/agentic-retrieval availability, fine-tuning access, and Entra Agent ID
tenant permissions must be checked before presenting. Foundry IQ provisions
Basic Azure AI Search, which has ongoing charges while deployed. The Entra demo
uses a one-day local-development blueprint secret; production should use
managed identity federation/certificates. All included example data is
synthetic/non-sensitive.

Demos 1-7 are intended for short terminal presentations. Managed evaluation and
red teaming can take several minutes; submit fine-tuning ahead of time and show
the existing job during the presentation. Each demo can be deployed and run
independently using its own instructions. The new templates and offline tests
are locally validated; this does not claim successful Azure deployment or live
service access in your subscription.

## Test the runner without Azure

```bash
python3 -m unittest discover -s . -p 'test_deploy.py' -v
```

For cleanup, delete each dedicated resource group explicitly using the subscription
and resource group recorded in its deployment state. For the default Model Router
group:

```bash
az group delete --subscription "<subscription-id>" \
  --name rg-foundry-model-router-demo --yes
```

**Entra tenant objects aren't deleted with the resource group.** Follow demo 12's
separate cleanup instructions for its child agent, blueprint principal, blueprint,
and development credential.
