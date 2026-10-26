 — Model Router	Passed
2 — Workflow Agent	Passed after retry
3 — WebIQ Agent	Failed: required web-search call/citations missing
4 — Agent Tools	Passed on 2026-10-26: Windows P0v3 in West US 2; real Function -> Toolbox -> agent verified
5 — Decision Model	Passed on 2026-10-26: DataZoneStandard; 3/3 default tickets plus custom invoice request, with explicit throttling retries
6 — Content Safety	Passed
7 — Semantic Cache	Passed
8 — Agent Evaluation	Passed on 2026-10-26: fixed cross-scope response retrieval; real inline-data cloud evaluation completed, 2/2 coherence passes
9 — Red Teaming	Passed after retry
10 — Fine-tuning	Passed on 2026-10-26: corrected trainingType; real paid one-epoch job succeeded, model ID retained; optional inference not run
11 — Foundry IQ	Passed on 2026-10-26: Basic Search in West US 2, Foundry in East US 2; both real grounded retrieval scenarios verified
12 — Entra Agent ID	Blocked by missing interactive identity setup/credentials; fail-fast and mocked Graph cleanup checks passed
13 — Orchestrations	All four live patterns passed after a startup retry

## Lab 10: completed paid fine-tuning on 2026-10-26

Reproduced HTTP 400 `invalidPayload` with
`The fineTuningJob field is required.` The request used an invalid training type:
`"Global"` instead of the documented v1 value `"GlobalStandard"`.
Changing that value alone produced an accepted job; no wrapper field was added.

Deployed the dedicated account and base model in East US 2 under
`rg-foundry-fine-tuning-demo`, then submitted exactly one real paid job using
the original 12 synthetic examples and one supervised epoch.

| Verified property | Actual result |
| --- | --- |
| Job ID | `ftjob-1f5660c4acff48438874163995f91d99` |
| Base model | `gpt-4.1-mini-2025-04-14` |
| Final status | `succeeded`; no error |
| Training type / epochs | `globalStandard` / 1 supervised epoch |
| Trained tokens | 663 |
| Fine-tuned model ID | `gpt-4.1-mini-2025-04-14.ft-1f5660c4acff48438874163995f91d99-workshop` |
| Results file | `file-d594c3b94d3e4ecbbb0cda03301db818`; downloaded, 742 bytes |
| Independent readback | Retrieved final job, checked epoch/model/token count and exactly one account job, downloaded nonempty results |
| Local validation | Seven regression tests, Bicep build/lint |
| Optional trained-model deployment / held-out inference | Not performed; no accuracy improvement claimed |

The service took approximately 71 minutes from submission to success, including
GPU queueing and finalization. The local 30-minute wait expired twice; resumed
the same job using `--job-id`, without another paid submission.
The script now persists the initial job ID and each polled status to ignored
`.azure/fine-tuning-job.json`, retaining the final model ID on success.

Evidence is retained locally in ignored `10-fine-tuning/.azure/`:
`live-before-fix.log`, `live-submit-fixed.log`, `fine-tuning-job.json`,
`live-training-wait.log`, `live-training-resume.log`,
`live-training-finalization.log`, `live-testing-summary.json`, the downloaded
results CSV, and `tests-after-fix.log`. Resources and trained model remain
available; deleting the account/resource group deletes the retained model.
Training was paid; optional trained-model hosting was not provisioned.

## Lab 11: Search capacity fix and managed retrieval on 2026-10-26

The original deployments were blocked by Search capacity in East US 2 and
Sweden Central. Added an independent `searchLocation` parameter, defaulting to
West US 2, while keeping Foundry/model deployment in East US 2.
West US 2 supports agentic retrieval and semantic ranker. Actual deployment to
`rg-foundry-foundry-iq-demo` succeeded: Basic Search is running with one
replica/partition, managed identity, and API keys disabled.

| Live check | Actual result |
| --- | --- |
| Index/source/base | Three exact synthetic documents indexed; managed knowledge source and answer-synthesizing knowledge base configured |
| Default question | Friday at 10:00, 45 minutes, laptop, Python 3.11 and Azure CLI; schedule/equipment citations |
| Custom question | Free attendance, registration before Thursday, no prior AI experience; cost/equipment citations |
| Managed activity | Real query planning, three/two Search subqueries respectively, answer synthesis; no reported activity errors |
| Grounding validation | Every source document matched the exact input; answer citation IDs resolved to the required documents |
| Independent API readback | Document count 3, correct source/base, and cross-region Foundry model endpoint |
| Offline validation | Six lab tests and all 27 deployment-runner tests passed; Bicep build/lint passed |

The root runner already supports the new parameter:

```bash
python3 deploy.py --demo 11 --location eastus2 --search-location westus2
```

Search cannot be moved between regions in place; use a new dedicated group
when changing the Search region. Capacity varies and is not reserved by this
successful deployment.

Evidence is retained locally in ignored `11-foundry-iq/.azure/`:
`deployment.json`, `live-default.log`, `live-custom.log`,
`live-testing-summary.json`, and `tests-after-fix.log`.
Resources remain deployed; Basic Search incurs continuous provisioning charges
and planning/synthesis incur usage charges.

## Lab 8: response-ID evaluation fix on 2026-10-26

Reproduced the original HTTP 404 against a fresh East US 2 deployment in
`rg-foundry-agent-evaluation-demo`. Failed cloud run
`evalrun_1a2899c0709440e8ab01b6f241ec9ec4` reported
`Response with id ... not found`. A same-ID probe confirmed **agent-scoped
retrieval HTTP 200 with identical text, project-scoped retrieval HTTP 404**.
The script had created agent-scoped responses then asked the project evaluator
to retrieve them; `store=True` did not bridge those scopes.

Changed the evaluation source to documented inline JSONL containing the exact
generated questions/answers and response IDs, with explicit coherence mappings.
This is still real cloud `builtin.coherence`, not a local surrogate or mock.
Corrected output-item validation to require processing status `completed` and
separate completed grader results, source matching, and aggregate consistency.
Serialized model/project deployment after a reproduced ARM `RequestConflict`.

Fixed run `evalrun_a87a335f1e5943f79ea9dbaa1522db58` completed with two
actual output items: **2 passed, 0 failed, 0 errored**, coherence **5/5** each.
Independently retrieved the cloud run/items and verified the two actual response
IDs. Temporary agent deletion was separately confirmed by HTTP 404.
Eight regression tests and Bicep lint passed, covering exact source mapping,
infrastructure failure, timeout, missing/mismatched/duplicate results,
valid low scores, and cleanup on evaluation failure.
Coherence does not prove factual accuracy or groundedness.

Evidence is retained locally in ignored `8-agent-evaluation/.azure/`:
`live-before-fix.log`, `response-scope-probe.json`, `live-after-fix.log`,
`live-testing-summary.json`, and deployment outputs. Resources remain deployed;
generation and judge inference are billable.

## Lab 5: Data Zone Standard verification on 2026-10-26

Changed `Microsoft-Decision-1` version `1` from `GlobalStandard` to
`DataZoneStandard` and deployed to `rg-foundry-decision-model-demo` in East US 2.
The former SKU failed with `SpecialFeatureOrQuotaIdRequired`; the Data Zone SKU
has subscription quota and deployed successfully. Capacity defaults to 10
instead of 1 after observed HTTP 429 responses.

All three default tickets returned the expected billing/technical/account
decisions, and a custom invoice request returned billing. Actual runtime
throttling remained stricter than ARM's reported 10 requests/minute. Explicit
bounded retries followed observed `retry-after-ms` delays of about 29-58 seconds.
Each ticket permits at most two retries, each no longer than 65 seconds.
Missing/excessive retry delays, exhausted retries, and other HTTP errors fail.
Allow several minutes for the live sequence under these observed limits.

Five offline tests and Bicep lint passed. Deployment outputs and successful
transcripts are retained locally in ignored
`5-decision-model/.azure/deployment.json`, `live-default-verified.log`, and
`live-custom-verified.log` in the same directory. Resources remain deployed;
actual inference is billable.

## Lab 4: non-Y1 hosting and live verification on 2026-10-26

Resolved the earlier Y1 quota blocker with a single-instance Windows **P0v3
dedicated App Service plan** in **West US 2**, with Always On. Foundry stays in
East US 2; `functionLocation=westus2` is now the template default for Function
hosting and storage independently of the Foundry region.

East US 2 ARM preflight rejected P0v3, P1v3, P0v4, and S1 with effective zero
quota. The quota-list API's reported P0v3 capacity did not match that preflight.
West US 2 P0v3 passed preflight and the actual deployment. A subsequent storage
shared-key error was fixed by using the Function's system-assigned identity
and storage-scoped Storage Blob Data Owner role. Shared-key access remains
disabled; no storage keys or Azure Files content connection string are needed.

| Check | Actual result |
| --- | --- |
| Resource group | `rg-foundry-agent-tools-demo` |
| Function host | Running, West US 2, P0v3, one instance, Always On |
| Direct valid API requests | 1/12/20/999 attendees returned USD 25/300/500/24975 |
| Direct invalid API requests | 0/1000/1.5/abc returned HTTP 400 and explicit errors |
| Agent, 12 attendees | Real Toolbox call returned `workshop-price-tool-v1`, USD 300, and an agent answer |
| Agent, 20 attendees | Real Toolbox call returned `workshop-price-tool-v1`, USD 500, and an agent answer |
| Temporary toolboxes | Both versions deleted after execution; independent Azure readback returned HTTP 404 |
| Offline validation | Six unit tests passed, including compiled ARM hosting/storage checks; Bicep lint passed |

Evidence is retained locally in ignored `4-agent-tools/.azure/deployment.json`,
`live-function-results.json`, `live-agent-12.log`, and `live-agent-20.log`.
The dedicated plan remains deployed and incurs continuous charges even when
idle or the app is stopped; delete the dedicated resource group after use.

## Added labs: live validation on 2026-10-25, labs 14/23 follow-up on 2026-10-26

The results above for labs 1-13 are historical and were not rerun in this pass.
All added labs 14-26 were attempted using the current Azure CLI subscription.
Provisioned resources remain deployed. Foundry resources are in East US 2;
lab 17's Search service is in West US 2 after an East US 2 capacity failure.

**Current summary: 12 passed, one blocked.** Lab 23's
GitHub Actions/OIDC coverage was completed on 2026-10-26; it had been unverified
during the original 2026-10-25 pass. Lab 14's real permission denial was also
completed on 2026-10-26 with a restricted certificate-authenticated identity.
Mocked tests and successful ARM deployment alone are not counted as live passes.

| Lab | Result | Live evidence |
| --- | --- | --- |
| [14 - Foundry fundamentals](./14-foundry-fundamentals/README.md) | Passed after identity/status fix on 2026-10-26 | The same restricted service principal passed real inference (83 tokens), streaming/usage (71 tokens), HTTP 404, and actual HTTP 401 `PermissionDenied`; full exit 0. The service uses 401, not the originally assumed 403, for the verified missing inference data action. Presenter roles were not changed. |
| [15 - Model benchmark](./15-model-benchmark/README.md) | Passed | 54 real requests across mini/full/router; task accuracy 83.3%/83.3%/50%. Genuine schema and truncation failures remain visible in the measurements. Benchmark execution passed, not every model prediction. |
| [16 - Structured outputs](./16-structured-outputs/README.md) | Passed | All 12 live strategy comparisons correct. Additional probes observed actual `length` truncation and HTTP 400 `content_filter`, distinguished from an assistant-message refusal. |
| [17 - RAG fundamentals](./17-rag-fundamentals/README.md) | Passed after region change | East US 2 Search returned `InsufficientResourcesAvailable`; `searchLocation=westus2` succeeded. Two chunk sizes produced 11/3 chunks; 24 real cases yielded 18 validated cited answers and six abstentions. Recall@3=1; MRR=0.833 for 12-word keyword chunks and 1 otherwise. |
| [18 - Agent tracing](./18-agent-tracing/README.md) | Passed | Queried nine actually ingested correlated spans. Observed the slow tool at 2005 ms and verified the failed tool's failure flag and safe error label. |
| [19 - Conversation state](./19-conversation-state/README.md) | Passed | 18 real calls verified the default scenario, separate-process persistence/resume, session isolation, history truncation, reset, and deletion. |
| [20 - Human approval](./20-human-approval/README.md) | Passed after fix | Real model proposals exercised rejection, exact approval, replay, wrong-digest rejection, and changed-payload rejection. Exactly one synthetic booking was created. |
| [21 - Permission-aware retrieval](./21-permission-aware-retrieval/README.md) | Blocked; reconfirmed 2026-10-26 | Actual presenter Graph profile/memberships succeeded and ten offline tests passed. `SECOND_AZURE_CONFIG_DIR` is missing and no `.identity-b` session exists. A different real same-tenant user must log in before deployment and six live question/user checks. No second user was simulated; live permission isolation is not claimed. |
| [22 - Prompt injection](./22-prompt-injection/README.md) | Passed after fix | Prompt Shields flagged the document override but not the tool-output override. Azure inference filtering blocked both raw baselines. Both separately tested protected paths returned the validated `09:00` fact. |
| [23 - Evaluation CI/CD](./23-evaluation-cicd/README.md) | Passed, including GitHub on 2026-10-26 | [Baseline Actions run](https://github.com/mifurm/FoundryWorkshopDEMOS/actions/runs/40281692169) succeeded at 100% correctness/100% coherence. [Regression Actions run](https://github.com/mifurm/FoundryWorkshopDEMOS/actions/runs/40281830195) failed at the intended gate with 0% correctness/100% coherence. Both used real OIDC login, environment approval, and uploaded verified cloud reports. |
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

### Lab 23 GitHub configuration and verification on 2026-10-26

- Installed `.github/workflows/foundry-evaluation.yml` and kept the lab's example
  identical, enforced by an eighth unit test.
- Created dedicated user-assigned managed identity `foundry-evaluation-github`
  in the existing lab resource group, with only Foundry User access to the lab's
  Foundry account. No subscription Contributor/Owner or stored Azure secret.
- Configured environment `foundry-evaluation`: required reviewer `mifurm`,
  administrator bypass disabled, allowed `main` and `refs/pull/*/merge` refs,
  and all five non-secret environment variables. Fork PRs remain excluded.
  Self-review is allowed for this single-maintainer workshop.
- Verified GitHub uses immutable subjects. Initial run `40281468905` failed
  `AADSTS700213` because a name-only example did not match the actual claim.
  Federation now exactly matches
  `repo:mifurm@16135464/FoundryWorkshopDEMOS@1433238170:environment:foundry-evaluation`;
  immutable subject protection was not disabled.
- Approved both reviewed main-branch verification runs through the required
  environment gate. Azure login, eight unit tests, and artifact upload succeeded
  in both. Downloaded reports confirmed three actual responses per run and
  completed cloud evaluation, with unchanged 100% release thresholds.
- Baseline cloud evaluation: `eval_375e42d5feb2486bb9e439f61013a8bc` /
  `evalrun_1443c330f96b4289b8ea467b90e7235c`.
- Regression cloud evaluation: `eval_e645335034c54fd9bfa5a1cdd8ec2a0c` /
  `evalrun_84d576532ded41abb03dd6ae7ac1ae02`. Its failure was
  `Release blocked: correctness=0.000, coherence=1.000`, not infrastructure.
- Exact run metadata and downloaded artifacts are retained locally under
  `23-evaluation-cicd/.azure/github-baseline*` and
  `23-evaluation-cicd/.azure/github-regression*`.

Repository-wide branch protection was left unchanged: requiring a path-filtered
workflow for every PR could block unrelated changes indefinitely. Environment
approval and the workflow's failing exit status are enforced, but a failed check
does not itself create a repository-wide merge restriction.

To complete the remaining live coverage:

1. Lab 21 needs a genuine second user session in an isolated Azure CLI
   configuration, supplied through `SECOND_AZURE_CONFIG_DIR`, with the documented
   tenant/Graph prerequisites. The presenter's Graph profile and membership
   requests passed on 2026-10-26; the second user needs no Azure subscription or
   Search/model role. See the lab README's second-user unblock instructions.
The original 2026-10-25 pass created no tenant identities or federated credentials.
The 2026-10-26 follow-up created the dedicated lab 23 managed identity and its
environment-bound federation; no client secrets or tenant consent grants were
created.
Search and monitoring resources may continue charging while provisioned.

### Lab 14 restricted-identity follow-up (2026-10-26)

Two causes were reproduced: the presenter's inherited subscription Foundry Owner
allows inference on the deliberately unassigned account, and the real inference
service returns HTTP 401 `PermissionDenied` for missing RBAC rather than the
lab's assumed 403. An independent call to the versioned `2024-10-21` inference
API also returned 401 and explicitly named the principal's missing
`Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action`.
This was an authorization denial, not an invalid-token response.

Created a dedicated one-day certificate service principal without default roles.
Deployed the existing Bicep with `principalType=ServicePrincipal`; role readback
confirmed exactly one Cognitive Services OpenAI User assignment on the primary
account, no inherited assignments, and none on the denied account. The second
account's `tutor-mini` deployment is healthy. Presenter permissions are unchanged.

The complete demo under that same identity exited **0**: normal answer/usage,
streamed answer/usage, real HTTP 404 `DeploymentNotFound`, and real HTTP 401
`PermissionDenied`. The denial message was `Principal does not have access to
API/Operation.`; another observed response named the exact missing data action.
The corrected permission check requires 401/403 plus that specific code and an
explicit authorization message. Arbitrary authentication errors, network blocks,
and unrelated statuses remain failures; no status is rewritten as 403.

Nine focused offline tests passed, including all-or-nothing credential settings,
same-identity use on both accounts, no default-credential fallback after certificate
failure, and rejection of unrelated error bodies/statuses. Live evidence remains
ignored under `14-foundry-fundamentals/.azure/`: `live-restricted-identity.log`,
`live-validation.json`, `versioned-denial-probe.json`, and `restricted-roles.json`.
The private PEM has mode 600; Entra credential expiry is **2026-10-27 10:00:19 UTC**.
The lab README documents reuse, expiry, and separate tenant/local cleanup.