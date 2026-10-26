 — Model Router	Passed
2 — Workflow Agent	Passed after retry
3 — WebIQ Agent	Failed: required web-search call/citations missing
4 — Agent Tools	Blocked: subscription Y1 VM quota is zero
5 — Decision Model	Blocked: required model feature unavailable
6 — Content Safety	Passed
7 — Semantic Cache	Passed
8 — Agent Evaluation	Failed: evaluation response ID returned HTTP 404
9 — Red Teaming	Passed after retry
10 — Fine-tuning	Failed: API requires fineTuningJob payload field
11 — Foundry IQ	Blocked by Azure capacity in both East US 2 and Sweden Central
12 — Entra Agent ID	Blocked by missing interactive identity setup/credentials; fail-fast and mocked Graph cleanup checks passed
13 — Orchestrations	All four live patterns passed after a startup retry

## Added labs: live validation on 2026-10-25

The results above for labs 1-13 are historical and were not rerun in this pass.
All added labs 14-26 were attempted using the current Azure CLI subscription.
Provisioned resources remain deployed. Foundry resources are in East US 2;
lab 17's Search service is in West US 2 after an East US 2 capacity failure.

**Summary: 10 passed, two partially validated, one blocked.** Lab 23's cloud
evaluation passed, but its GitHub Actions/OIDC execution remains unverified.
Mocked tests and successful ARM deployment alone are not counted as live passes.

| Lab | Result | Live evidence |
| --- | --- | --- |
| [14 - Foundry fundamentals](./14-foundry-fundamentals/README.md) | Partial | Real inference, streaming/usage, and wrong-deployment HTTP 404 passed. The intended HTTP 403 cannot be reproduced because the presenter inherits subscription-level Foundry Owner. The negative probe honestly exits nonzero. |
| [15 - Model benchmark](./15-model-benchmark/README.md) | Passed | 54 real requests across mini/full/router; task accuracy 83.3%/83.3%/50%. Genuine schema and truncation failures remain visible in the measurements. Benchmark execution passed, not every model prediction. |
| [16 - Structured outputs](./16-structured-outputs/README.md) | Passed | All 12 live strategy comparisons correct. Additional probes observed actual `length` truncation and HTTP 400 `content_filter`, distinguished from an assistant-message refusal. |
| [17 - RAG fundamentals](./17-rag-fundamentals/README.md) | Passed after region change | East US 2 Search returned `InsufficientResourcesAvailable`; `searchLocation=westus2` succeeded. Two chunk sizes produced 11/3 chunks; 24 real cases yielded 18 validated cited answers and six abstentions. Recall@3=1; MRR=0.833 for 12-word keyword chunks and 1 otherwise. |
| [18 - Agent tracing](./18-agent-tracing/README.md) | Passed | Queried nine actually ingested correlated spans. Observed the slow tool at 2005 ms and verified the failed tool's failure flag and safe error label. |
| [19 - Conversation state](./19-conversation-state/README.md) | Passed | 18 real calls verified the default scenario, separate-process persistence/resume, session isolation, history truncation, reset, and deletion. |
| [20 - Human approval](./20-human-approval/README.md) | Passed after fix | Real model proposals exercised rejection, exact approval, replay, wrong-digest rejection, and changed-payload rejection. Exactly one synthetic booking was created. |
| [21 - Permission-aware retrieval](./21-permission-aware-retrieval/README.md) | Blocked | Actual tenant-specific preflight exited 2 because `SECOND_AZURE_CONFIG_DIR` is missing. No second user was simulated; live permission isolation was not claimed. |
| [22 - Prompt injection](./22-prompt-injection/README.md) | Passed after fix | Prompt Shields flagged the document override but not the tool-output override. Azure inference filtering blocked both raw baselines. Both separately tested protected paths returned the validated `09:00` fact. |
| [23 - Evaluation CI/CD](./23-evaluation-cicd/README.md) | Partial: cloud passed; GitHub not run | Baseline exited 0 at 100% correctness/100% coherence; deliberate regression exited 1 at 0% correctness/100% coherence. Real cloud evaluation ran; the GitHub workflow/OIDC configuration was unavailable. |
| [24 - Agent lifecycle](./24-agent-lifecycle/README.md) | Passed | Published the actual application, verified six endpoint responses across versions 1 -> 2 -> 1, confirmed three successful Activity Log writes and final ARM version-1 readback. |
| [25 - Resilience and cost](./25-resilience-cost/README.md) | Passed | Two real model calls returned 30 input/37 output tokens after local injected faults. Observed model budget 5/6, tool budget 4/4, permanent-error/deadline stops, and one idempotent synthetic booking. |
| [26 - Multimodal](./26-multimodal/README.md) | Passed | Both synthetic images matched every expected truth field, including unreadable/missing values. |

### Defects fixed and verified

- Serialized affected model/project ARM writes after real `RequestConflict` failures.
- Accepted lab 20's observed complete tool call with finish reason `stop`, while
  retaining exact tool, argument, refusal, and truncation validation.
- Reported lab 22's real `content_filter` block explicitly instead of crashing or
  fabricating a baseline answer; unrelated API errors still fail.
- Corrected tracing exporter-provider wiring and preserved `workshop.error_type`
  when the exporter discarded the reserved `error.type` attribute.
- Replaced lab 23's failing response-ID evaluation route (HTTP 404) with the
  documented inline-JSONL route over the exact generated responses and real
  `builtin.coherence`; result/count/source checks and thresholds remain strict.
- Added a Search-region override for lab 17 and exposed `--search-location` in
  the root deployment runner so the successful regional deployment is reproducible.
- Corrected live probe classification/false-success handling and benchmark pacing
  for the observed 10-RPM limit.

### Reproduce and inspect

Regression validation passed: 90 lab unit tests and 27 deployment-runner tests
(117 total). Live-fix syntax, report coverage, local documentation links, and
diff whitespace were also checked. These offline checks are separate from the
live outcomes above.

Run labs individually so a blocked prerequisite does not stop unrelated labs:

```bash
python3 deploy.py --demo 20
python3 deploy.py --demo 17 --location eastus2 --search-location westus2
```

Keep the same Search-region override when redeploying lab 17: an existing Azure
Search service cannot be moved to a different region in place.

Full commands, service errors, measurements, trace/evaluation IDs, and version
readbacks are retained locally in ignored evidence files:

- Labs 14/15/16/26: `14-foundry-fundamentals/.azure/live-validation-summary.json`.
- Labs 17/19/21: each lab's `.azure/live-result.json`.
- Labs 18/23/24: each lab's `.azure/live-testing-summary.json`.
- Lab 20: `20-human-approval/.azure/live-approval-paths.log`.
- Lab 22: `22-prompt-injection/.azure/live-test-after-fix.log`.
- Lab 25: `25-resilience-cost/.azure/demo.log`.

To complete the remaining live coverage:

1. Lab 14 needs an inference identity without the presenter's inherited Foundry
   Owner rights; do not remove existing subscription roles just to force a 403.
2. Lab 21 needs a genuine second user session in an isolated Azure CLI
   configuration, supplied through `SECOND_AZURE_CONFIG_DIR`, with the documented
   tenant/Graph prerequisites.
3. Lab 23 needs the documented workflow installed and its GitHub variables,
   environment, Azure identity, OIDC federation, and RBAC configured before a
   passing and deliberately failing Actions run can be verified.

No tenant identities, consent grants, or federated credentials were created.
Search and monitoring resources may continue charging while provisioned.