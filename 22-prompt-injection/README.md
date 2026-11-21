# Prompt-injection trust boundaries

Two synthetic sources contain a workshop start time and a harmless instruction
to output `UNRELATED_MARKER`: one represents a document, the other a tool result.
Compare actual raw model behavior with a protected path that projects only an
allowed factual field, constrains JSON output, validates the result, and exposes
no executable tools. The baseline may already resist the override; results are
observations, not a guaranteed attack success or safety certification.

## Browser UI

The [shared UI](../27-FoundryIQwithKB/README.md#azure-hosted-ui) runs one of
the lab's two fixed synthetic sources per request and displays the actual
Prompt Shields classification, baseline observation, and validated protected
result. It always calls Prompt Shields; it does not offer the CLI's explicit
`--without-shields` option.

For local use, set `UI_INJECTION_ENDPOINT` and `UI_INJECTION_SAFETY_ENDPOINT`
before starting the UI; `UI_INJECTION_DEPLOYMENT` defaults to `gpt-4.1-mini`.
For Azure hosting, pass `injectionEndpoint` and `injectionSafetyEndpoint` to
the shared UI template, retaining the other existing parameters. Grant its
system-assigned identity the same scoped inference and safety roles as the lab,
with `RG` set to the lab 22 resource group:

```bash
az deployment group create -g "$RG" -n ui-model-access \
  --template-file ../0-model-basics/infra/ui-role.bicep \
  --parameters accountName="<lab-22-model-account>" principalId="<ui-system-identity-object-id>"
az deployment group create -g "$RG" -n ui-safety-access \
  --template-file ../0-model-basics/infra/ui-role.bicep \
  --parameters accountName="<lab-22-safety-account>" principalId="<ui-system-identity-object-id>" \
    roleDefinitionGuid=a97b65f3-24c7-4388-baec-2e87135dc908
```

Remove these role assignments when removing the UI, or delete this lab's resource
group when no longer needed. Browser sign-in and managed-identity calls must be
verified separately from deployment.

## Deploy and run

Requires Python 3.10+, Azure CLI/Bicep, resource/role-assignment permissions,
`gpt-4.1-mini` quota, structured outputs support, and Content Safety Prompt Shields
availability in your selected region. Uses Entra ID, public endpoints, no keys.

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
RG=rg-foundry-prompt-injection-demo
az group create -n "$RG" -l eastus2 --tags SecurityControl=Ignore SecurityExemption=StorageAccountKeyAccess
az deployment group create -g "$RG" -n demo --template-file infra/main.bicep \
  --parameters principalId="$(az ad signed-in-user show --query id -o tsv)"
export AZURE_OPENAI_ENDPOINT="$(az deployment group show -g "$RG" -n demo \
  --query properties.outputs.endpoint.value -o tsv)"
export AZURE_OPENAI_DEPLOYMENT="$(az deployment group show -g "$RG" -n demo \
  --query properties.outputs.deployment.value -o tsv)"
export CONTENT_SAFETY_ENDPOINT="$(az deployment group show -g "$RG" -n demo \
  --query properties.outputs.contentSafetyEndpoint.value -o tsv)"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python demo.py
```

Show the two raw sources in `SOURCES` in `demo.py`, then Prompt Shields' actual
classifications, raw baseline
answers, and validated protected answers. `--without-shields` explicitly omits
the classifier for comparison; it does not claim a successful shield analysis.
Classification failures are fatal rather than treated as "safe."
Azure inference guardrails may block the raw baseline with `content_filter`
before an answer exists. This is explicitly reported as a blocked observation,
not a successful model response; the protected factual projection is still
tested separately. Other API errors propagate. Do not disable service filters
to force an instruction-override demonstration.

The classifier is an independent observable layer; this lab always uses the
factual projection rather than trusting a negative classification. Model output
must equal the projected fact, not merely parse as JSON. No booking/write tool
is available, so generated text cannot authorize an action. See lab 20 for an
application-enforced approval gate.

## Limits, tests, and cleanup

Projection works here because a toy record has one strictly defined field.
It is not a general sanitizer for arbitrary documents, nor proof that the
source's factual value is truthful. A production retrieval application needs
source authorization, provenance, application-specific validation, and ongoing
adversarial evaluation. Prompt Shields differs from content-category filtering;
false positives and false negatives remain possible.

```bash
python -m unittest discover -s . -p 'test_*.py' -v
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
az group delete -n "$RG" --yes
```

The default attempts four model requests and two safety analyses; a filtered
request is not a successful generation. Actual service usage determines charges.
The template deploys GPT-4.1-mini `2025-04-14`, GlobalStandard capacity 10, plus
an S0 Content Safety account in East US 2 by default. It grants the presenter
Cognitive Services OpenAI User for inference and Cognitive Services User for
Content Safety. GlobalStandard processing may leave the account region.
Allow RBAC propagation after deployment. No tenant objects or durable source
data are created.

- [Prompt Shields quickstart](https://learn.microsoft.com/azure/ai-services/content-safety/quickstart-jailbreak)
- [Tool trust boundaries](https://learn.microsoft.com/azure/foundry/agents/concepts/tool-best-practice)
- [Backlog F022](../BACKLOG.md#f022---microsoft-foundry-prompt-injection-defenses)
