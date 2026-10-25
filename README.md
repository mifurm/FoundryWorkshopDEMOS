# Foundry workshop demos

## Deploy and test everything

Requires Python 3.10+, Azure CLI, Bicep (`az bicep version`), and an interactive
Azure user with resource creation and role-assignment permissions. See each
demo's prerequisites for model quota and access requirements.

From the repository root:

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
   and `deployment` outputs plus optional `projectEndpoint` and `toolEndpoint`
   outputs to the demo's environment, and runs `demo.py`.
6. Saves live output to `<demo>/.azure/demo.log` and stops with a nonzero exit code
   if any step fails. Newly assigned 403 access errors and 404
   `DeploymentNotFound` errors are visibly retried at 30-second intervals, up to
   11 attempts. Other errors fail immediately.

**This command creates billable Azure resources and makes live model requests.**
Resources remain deployed for presenting. Rerunning uses the same resource groups
and deployment names and repeats the tests and live calls. The script does not
automatically delete resources on failure. The ignored `.azure` state/logs contain
resource identifiers and demo responses, not API keys or access tokens.

To add another demo, use the same folder convention: `infra/main.bicep`,
`requirements.txt`, `demo.py`, and `test_demo.py`. Templates must accept `principalId`
and `location` and expose string outputs `endpoint` and `deployment`; Python demos
consume `AZURE_OPENAI_DEPLOYMENT` and the endpoint appropriate to their API, exit
nonzero on failure, and validate the live response. Account-level inference uses
`AZURE_OPENAI_ENDPOINT`; project APIs use `FOUNDRY_PROJECT_ENDPOINT` from
`projectEndpoint`; the hosted pricing tool uses `WORKSHOP_TOOL_ENDPOINT` from
`toolEndpoint`. Other output contracts require updating the runner.

## Demos and tracking

- [Model Router instructions and cleanup](./1-model-router/README.md)
- [Workflow Agent: writer -> editor](./2-workflow-agent/README.md)
- [Simple WebIQ Agent: web search with citations](./3-webiq-agent/README.md)
- [Agent with Simple Tools: Azure Function through Foundry Toolbox](./4-agent-tools/README.md)
- [Backlog](./BACKLOG.md)

Each demo has standalone Bicep, local Python code, dependencies, tests, and manual
deployment/cleanup instructions. The three new demos use `gpt-4.1-mini` by default;
the Model Router uses its own model-router deployment. Model access, quota,
regional availability, Web Search policy, and Toolbox preview availability must
be checked before presenting. All examples use non-sensitive workshop data.

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
