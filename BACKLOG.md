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
