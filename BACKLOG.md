# Demo backlog

## F001 - Microsoft Foundry: Model Router

**Type:** Feature  
**Status:** Implemented and live-tested on 2026-10-25  
**Demo:** [Model Router](./1-model-router/README.md)

### Goal

As a presenter, I can deploy a single model router endpoint and send different
prompts from my laptop to explain automatic model selection without building an app.

### Acceptance criteria

- [x] Provide a standalone Bicep template for a Foundry account, model router
  deployment, and the presenter's inference role.
- [x] Use Microsoft Entra authentication without storing API keys.
- [x] Provide a local Python example with simple, writing, and reasoning prompts,
  plus a custom-prompt option.
- [x] Display the actual serving model, elapsed time, token usage, and answer.
- [x] Document prerequisites, deployment, execution, limitations, and cleanup.
- [x] Compile/lint the infrastructure and pass local mocked tests.
- [x] Deploy to the target subscription and run the example against Azure.

### Live verification

The all-demo runner deployed the infrastructure to
`rg-foundry-model-router-demo` in East US 2 on 2026-10-25. All six local demo
tests passed, and all three live prompts returned nonempty answers with serving
model, latency, and token usage. The simple/writing prompts selected
`FW-GLM-5.3-Flash`; reasoning selected `grok-4-1-fast-reasoning`. Routing results
are illustrative, not guarantees. Resources remain deployed.

The runner and usage instructions are in [deploy.py](./deploy.py) and the
[repository README](./README.md). Deployment outputs and the live transcript are
saved locally under `1-model-router/.azure/` (ignored by Git).

### Scope

One account, one deployment, one local script. No UI, agents, database, or separate
deployments of the underlying models. Routing choices are not deterministic;
different prompts are not guaranteed to select different models.

## F002 - Microsoft Foundry: Workflow Agent

**Type:** Feature  
**Status:** Implemented; live deployment and presentation smoke test pending  
**Demo:** [Workflow Agent](./2-workflow-agent/README.md)

### Goal

As a presenter, I can show a writer-to-editor agent handoff using a local
Agent Framework workflow backed by a single Foundry model deployment.

### Acceptance criteria

- [x] Provide standalone Bicep for the account, project, model, and presenter RBAC.
- [x] Authenticate with Microsoft Entra ID, without API keys.
- [x] Execute writer then editor and display both stages.
- [x] Support a custom prompt and reject empty/incomplete stage output.
- [x] Include dependencies, offline tests, setup, talk track, costs, and cleanup.
- [x] Compile/lint Bicep and pass local tests.
- [ ] Deploy and smoke-test both stages against the target Azure subscription.

### Scope

Local sequential orchestration; Foundry inference. No hosted workflow, portal
designer, UI, persistence, or additional infrastructure.

## F003 - Microsoft Foundry: Simple WebIQ Agent

**Type:** Feature  
**Status:** Implemented; live deployment and presentation smoke test pending  
**Demo:** [WebIQ Agent](./3-webiq-agent/README.md)

### Goal

As a presenter, I can ask a Foundry agent a public question and show actual web
search activity and source citations rather than relying on model memory.

### Acceptance criteria

- [x] Provide standalone Bicep for the account, project, model, and presenter RBAC.
- [x] Create a prompt agent with built-in Web Search and require tool usage.
- [x] Print the answer, completed search count, source titles, and URLs.
- [x] Fail explicitly when search evidence, citations, or answer text is absent.
- [x] Delete the temporary agent version after success or inference failure.
- [x] Document the WebIQ interpretation, Bing terms, costs, setup, and cleanup.
- [x] Compile/lint Bicep and pass local mocked tests.
- [ ] Deploy and smoke-test search/citations under the target organization's policy.

### Scope

"WebIQ" is the workshop label for built-in Web Search, not an additional product.
Public web only; no private documents, search index, separate Bing connection, or UI.

## F004 - Microsoft Foundry: Agent with Simple Tools

**Type:** Feature  
**Status:** Implemented; live deployment and presentation smoke test pending  
**Demo:** [Agent with Simple Tools](./4-agent-tools/README.md)

### Goal

As a presenter, I can deploy a tiny pricing API, register it in Foundry Tools
through a toolbox, and show an agent discovering and invoking that hosted tool.

### Acceptance criteria

- [x] Provide standalone Bicep for Foundry, model, presenter RBAC, Function, and storage.
- [x] Deploy the actual JavaScript tool through Bicep without a separate code upload.
- [x] Register the OpenAPI tool in a Foundry toolbox and consume its MCP endpoint.
- [x] Show actual tool output and reject model-only answers or failed tool calls.
- [x] Support 1-999 attendees and test the actual tool's pricing/input boundaries.
- [x] Clean up the temporary toolbox version on success and inference failure.
- [x] Document the public read-only API, preview dependency, costs, setup, and cleanup.
- [x] Compile/lint Bicep and pass local tests.
- [ ] Deploy and smoke-test Function -> Toolbox -> agent in the target subscription.

### Scope

One anonymous, read-only toy Azure Function; one toolbox; one local agent.
No payments, bookings, database, private data, hosted agent, or UI.

## F005 - Microsoft Foundry: Microsoft Decision Model

**Type:** Feature

**Status:** Implemented; live deployment and presentation smoke test pending

**Demo:** [Microsoft Decision Model](./5-decision-model/README.md)

### Goal

As a presenter, I can deploy Microsoft-Decision-1 and show typed classification
of toy support tickets without using a generative chat model.

### Acceptance criteria

- [x] Standalone Bicep deploys the Microsoft-format model, account, and user RBAC.
- [x] Local Python calls the dedicated Decision API using Entra authentication.
- [x] Display selected/expected teams, latency, and illustrative accuracy.
- [x] Reject HTTP errors and invalid or unsupported typed decisions.
- [x] Include dependencies, offline tests, deployment/run/cleanup instructions.
- [x] Compile/lint Bicep and pass local tests.
- [ ] Deploy and smoke-test with Microsoft-Decision-1 access in the target region.

### Scope

One account, one specialized model, three toy tickets; no real ticket routing,
free-form explanations, consequential decisions about people, or UI.

## F006 - Microsoft Foundry: Content Safety

**Type:** Feature

**Status:** Implemented; live deployment and presentation smoke test pending

**Demo:** [Content Safety](./6-content-safety/README.md)

### Goal

As a presenter, I can analyze text with Content Safety, explain the four harm
categories, and show how an application applies a severity threshold.

### Acceptance criteria

- [x] Standalone Bicep creates a keyless S0 Content Safety resource and user RBAC.
- [x] Local Python uses Entra ID and prints all four category severities.
- [x] Apply an inclusive ALLOW/BLOCK threshold on the 0/2/4/6 scale.
- [x] Missing categories, invalid scores, and service failures are not ALLOWs.
- [x] Tests cover both sides of the threshold without harmful example text.
- [x] Include dependencies, setup/run/cleanup documentation; compile/lint Bicep.
- [ ] Deploy and smoke-test the service in the target subscription.

### Scope

One standalone Content Safety account and a local script. No model deployment,
custom classifier, image moderation, production policy, or UI.

## F007 - Microsoft Foundry: Semantic Caching

**Type:** Feature

**Status:** Implemented; live deployment and presentation smoke test pending

**Demo:** [Semantic caching](./7-semantic-cache/README.md)

### Goal

As a presenter, I can show Foundry embeddings enabling reuse of model answers
for semantically similar prompts without deploying a cache server.

### Acceptance criteria

- [x] Standalone keyless Bicep creates generation and embedding deployments/RBAC.
- [x] Cache locally by cosine similarity rather than exact prompt equality.
- [x] Display actual HIT/MISS, similarity, latency, and inference request counts.
- [x] Tests prove inclusive threshold hits skip generation and unrelated prompts miss.
- [x] Reject invalid vectors, invalid thresholds, and incomplete model answers.
- [x] Document application-side scope, cache isolation limitations, charges/cleanup.
- [x] Compile/lint Bicep and pass local tests; wire embedding output into the runner.
- [ ] Deploy and observe real embedding similarities and generation savings.

### Scope

An in-memory, per-process cache over public workshop questions. No Redis, API
Management, persistence, native cache toggle, cross-user reuse, or speed guarantees.

## F008 - Microsoft Foundry: Agent Evaluation

**Type:** Feature

**Status:** Implemented; live deployment and presentation smoke test pending

**Demo:** [Agent evaluation](./8-agent-evaluation/README.md)

### Goal

As a presenter, I can evaluate real stored agent interactions with Foundry's
managed coherence evaluator and inspect actual run and per-item results.

### Acceptance criteria

- [x] Standalone Bicep deploys account/project/model and presenter/project RBAC.
- [x] Generate two interactions with a temporary Foundry tutor agent.
- [x] Submit stored response IDs to Foundry cloud `builtin.coherence` evaluation.
- [x] Poll with a deadline and reject failed, errored, or incomplete results.
- [x] Display run metadata/counts/per-item scores and clean up the agent version.
- [x] Include dependencies, offline tests, preview/cost/setup/cleanup documentation.
- [x] Compile/lint Bicep and pass local tests.
- [ ] Deploy and confirm managed evaluation/judge permissions and completed results.

### Scope

Two responses, one deployed judge, one cloud evaluation. No local surrogate
score, telemetry pipeline, comprehensive benchmark, or safety certification.

## F009 - Microsoft Foundry: Agent Red Teaming

**Type:** Feature

**Status:** Implemented; live deployment and presentation smoke test pending

**Demo:** [Agent red teaming](./9-agent-red-teaming/README.md)

### Goal

As a presenter, I can run a small managed Foundry red-team scan against an
authorized tutor agent and explain its attack-success scorecard.

### Acceptance criteria

- [x] Standalone Bicep deploys account/project/model and user/project RBAC.
- [x] Use the official RedTeam SDK against a real model-backed agent callback.
- [x] Bound the demo to one category/objective with no strategy expansion.
- [x] Save report and SDK files in a new ignored directory; print the scorecard.
- [x] Reject missing results/scorecards and incomplete, skipped, or errored evaluations.
- [x] Document Python/preview/region requirements, sensitive reports, costs/cleanup.
- [x] Compile/lint Bicep and pass local tests without generating attack content.
- [ ] Deploy and complete an authorized scan in a supported target region.

### Scope

One baseline objective in one category, single-turn text only. No embedded
attack corpus, jailbreak strategy expansion, production targets, or safety claim.

## F010 - Microsoft Foundry: Supervised Fine-Tuning

**Type:** Feature

**Status:** Implemented; paid training and live inference verification pending

**Demo:** [Fine-tuning](./10-fine-tuning/README.md)

### Goal

As a presenter, I can upload a tiny synthetic dataset and submit Foundry's
managed supervised fine-tuning without operating training infrastructure.

### Acceptance criteria

- [x] Standalone Bicep deploys a keyless account, base model, and presenter RBAC.
- [x] Include 12 balanced chat-format toy support-ticket training examples.
- [x] Validate the dataset, upload/process it, and submit one epoch of Global SFT.
- [x] Print job status/ID honestly; do not equate submission with completed training.
- [x] Inspect/resume existing jobs without resubmission; support bounded polling.
- [x] Reject failed/cancelled jobs and successful jobs missing a model ID.
- [x] Document optional trained-model deployment/inference, cancellation, costs/cleanup.
- [x] Compile/lint Bicep and pass offline tests.
- [ ] Complete a paid job in the target subscription and retain its model ID.
- [ ] Optionally deploy that model and compare on held-out examples.

### Scope

Managed SFT, one epoch, toy classification, no GPU cluster or RL. Training is
asynchronous; submit ahead of a presentation. Minimum-size data demonstrates
the API, not improved accuracy or production training quality.

## F011 - Microsoft Foundry: Foundry IQ

**Type:** Feature

**Status:** Implemented; live deployment and retrieval smoke test pending

**Demo:** [Foundry IQ](./11-foundry-iq/README.md)

### Goal

As a presenter, I can reuse Microsoft Learn's agentic-retrieval pattern to show
a tiny managed knowledge base answering a compound workshop question with sources.

### Acceptance criteria

- [x] Standalone Bicep deploys keyless Foundry, a model, Basic Search, and RBAC.
- [x] Grant the Search managed identity permission to call the planning/answer model.
- [x] Include three synthetic documents; create a semantic text index, knowledge
  source, and knowledge base through the real managed APIs.
- [x] Request answer synthesis, references, and retrieval activity; print actual results.
- [x] Check document upload/visibility and reject missing grounding or activity errors.
- [x] Cite the Learn quickstart and document the simplified, vector-free scope.
- [x] Include dependencies, offline tests, deployment/run/cost/cleanup instructions.
- [x] Compile/lint Bicep, pass tests, and wire the Search endpoint into the runner.
- [ ] Deploy in a supported region and show a grounded live answer.

### Scope

The Azure AI Search knowledge layer underpinning Foundry IQ, not a hosted agent
or portal integration. One text index, one source, one base, three documents.
No vectors, SharePoint, enterprise permission synchronization, storage, or UI.
Basic Search has ongoing provisioning charges.

## F012 - Microsoft Agent Framework: Entra Agent ID

**Type:** Feature

**Status:** Implemented; authorized tenant provisioning and live inference pending

**Demo:** [Entra Agent ID](./12-entra-agent-id/README.md)

### Goal

As a presenter, I can explain blueprint versus child identity and show an Agent
Framework tutor authenticating to Foundry as a real Entra Agent ID, not a user.

### Acceptance criteria

- [x] Provide tenant setup using typed Graph blueprint/principal/agent endpoints,
  explicit consent scopes, OData headers, and an accountable human sponsor.
- [x] Keep the one-day development blueprint secret in the process environment,
  never in source, saved state, Bicep parameters, outputs, or normal console output.
- [x] Record non-secret created object IDs incrementally for cleanup after failures.
- [x] Standalone Bicep deploys Foundry/model and account-scoped inference RBAC
  for the actual child identity; no automatic user/key fallback.
- [x] Demonstrate the documented two-stage autonomous token exchange and use
  the child resource token in the Agent Framework client.
- [x] Document tenant prerequisites, production credential alternatives, and
  separate Azure and tenant cleanup.
- [x] Fail root runner preflight before Azure changes if tenant setup is missing.
- [x] Compile/lint Bicep; test token exchange, client wiring, and mocked Graph setup.
- [ ] Obtain authorized tenant/admin consent, provision identity, and verify live
  Foundry authorization and Entra sign-in evidence.

### Scope

One blueprint, one sponsored child agent identity, one one-day local-development
credential, one model-backed tutor. No Graph data access, agent user account,
user delegation, broad resource grants, or production credential framework.
Graph tenant objects are intentionally provisioned separately from ARM/Bicep.

## F013 - Microsoft Agent Framework: Four Orchestration Patterns

**Type:** Feature

**Status:** Implemented; live model-backed presentation smoke tests pending

**Demo:** [Agent orchestrations](./13-agent-orchestrations/README.md)

### Goal

As a presenter, I can run one tiny, separate example for Sequential, Concurrent,
Group Chat, and Magentic orchestration using Microsoft Agent Framework and Foundry.

### Acceptance criteria

- [x] Standalone keyless Bicep deploys one account/project/model and presenter RBAC.
- [x] Sequential sample passes an invitation from writer to editor.
- [x] Concurrent sample independently requests a title and snack suggestion.
- [x] Group Chat sample shares a planner/reviewer discussion with bounded turns.
- [x] Magentic sample uses a model-driven manager with two workshop-card specialists.
- [x] Keep each builder/scenario in its own simple file and offer individual/all
  execution through the demo entrypoint.
- [x] Print participant/final outputs; reject empty output and bound workflow runtime.
- [x] Execute real framework graphs in offline tests, proving handoff, overlapping
  concurrent calls, shared chat, and manager coordination of both specialists.
- [x] Include pinned framework dependencies, Learn links, setup/run/cost/cleanup
  documentation; compile/lint Bicep and pass local tests.
- [ ] Deploy and run each of the four patterns against the target Foundry project.

### Scope

Local orchestration with Foundry inference, one deployment shared by all four
samples. No tools, web research, hosted workflows, database, or UI.
Manager decisions are model-driven and not guaranteed to follow a fixed order.
