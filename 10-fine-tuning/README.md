# Microsoft Foundry - Simplest supervised fine-tuning

A short setup demo of **managed supervised fine-tuning (SFT)**: upload 12 tiny
chat-format examples and submit one training epoch for `gpt-4.1-mini-2025-04-14`.
The task is returning exactly BILLING, TECHNICAL, or ACCOUNT for a support ticket.
No GPU cluster, custom training code, reinforcement learning, or UI is needed.

**Training is asynchronous, not a guaranteed fast live demo.** Submit ahead of
the presentation and retain the job ID. By default the script reports submission
status, not successful training. Use `--job-id` to inspect/resume without paying
for another job, and `--wait` to poll for up to 30 minutes.

## Deploy

Requires Python 3.10+, Azure CLI with Bicep, an Azure user with resource creation
and role-assignment permissions, fine-tuning access/quota, and a supported Global
training region for the model. Check the official matrix before presenting.
East US 2 is a supported Global training region in the referenced documentation;
Standard fine-tuned inference has a different regional matrix.

The template creates one keyless Foundry account, a base `gpt-4.1-mini`
(`2025-04-14`, Global Standard) deployment for optional comparison, and presenter
Foundry User RBAC. Training uses the **model ID**, not this deployment name.
Python submits the data-plane job; Bicep provisions its prerequisites.

Run from this folder:

```bash
az login
az account set --subscription "<subscription-id>"
az provider register --namespace Microsoft.CognitiveServices --wait
RESOURCE_GROUP="rg-foundry-fine-tuning-demo"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"
az group create --name "$RESOURCE_GROUP" --location eastus2
az deployment group create --resource-group "$RESOURCE_GROUP" --name demo \
  --template-file infra/main.bicep \
  --parameters principalId="$PRINCIPAL_ID" location=eastus2
export AZURE_OPENAI_ENDPOINT="$(az deployment group show -g "$RESOURCE_GROUP" \
  -n demo --query properties.outputs.endpoint.value -o tsv)"
```

## Run and present

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py                      # Submits a NEW paid job
python demo.py --job-id "<job-id>"   # Only inspect an existing job
python demo.py --job-id "<job-id>" --wait
```

Show the JSONL messages, one-epoch supervised method, returned job ID/status, and
eventual fine-tuned model ID. Global training can process data outside the account
region; all included data is synthetic and non-sensitive. Twelve examples meet
the minimum-size demonstration requirement, **not** a production-quality dataset.
No improvement is claimed; use held-out validation/test data for real experiments.

### Optional: deploy the trained model and try it

After a job **succeeds**, copy its `fine_tuned_model` value:

```bash
ACCOUNT_NAME="$(az deployment group show -g "$RESOURCE_GROUP" -n demo \
  --query properties.outputs.accountName.value -o tsv)"
az cognitiveservices account deployment create -g "$RESOURCE_GROUP" -n "$ACCOUNT_NAME" \
  --deployment-name workshop-tuned --model-format OpenAI \
  --model-name "<fine-tuned-model-id>" --model-version "1" \
  --sku-name GlobalStandard --sku-capacity 1
```

Then use the same authenticated OpenAI v1 client pattern as `demo.py`:

```python
response = client.chat.completions.create(
    model="workshop-tuned",
    messages=[
        {"role": "system", "content": "Classify the support ticket. Reply with exactly BILLING, TECHNICAL, or ACCOUNT."},
        {"role": "user", "content": "Could you help me recover access to my profile?"},
    ],
)
print(response.choices[0].message.content)
```

This snippet belongs inside the existing client context. Deployment availability,
quota, and hosting charges are separate from training. This optional deployment
is deliberately not created before a trained model exists.

## Validate, costs, and cleanup

```bash
az bicep build --file infra/main.bicep --stdout > /dev/null
az bicep lint --file infra/main.bicep
python -m unittest discover -s . -p 'test_*.py' -v
```

Every run **without `--job-id` submits a new billable job**, including reruns of
the root all-demo runner. Files/jobs remain on the account for inspection.
403 usually indicates RBAC propagation or missing training permissions; a model
or training-region error requires checking fine-tuning access, not changing the
inference deployment name. Failed/cancelled jobs and polling timeouts exit nonzero.
Stopping the local process does not cancel cloud training.

To cancel a still-running job before deleting the dedicated group, use
`client.fine_tuning.jobs.cancel("<job-id>")` in the authenticated client context.
Then delete the group (including any optional trained deployment):

```bash
az group delete --name "$RESOURCE_GROUP" --yes
```

- [Supervised fine-tuning API and workflow](https://learn.microsoft.com/azure/foundry/openai/how-to/fine-tuning)
- [Supported training/deployment regions and costs](https://learn.microsoft.com/azure/foundry/fine-tuning/overview)
- [Feature F010](../BACKLOG.md#f010---microsoft-foundry-supervised-fine-tuning)
