"""Publish real managed Agent Applications, update their deployment, and roll back."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from time import monotonic, sleep
from uuid import uuid4

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential
import requests

ARM_VERSION = "2025-10-01-preview"
DATA_VERSION = "2025-11-15-preview"
QUESTIONS = ("What is the workshop price?", "How much does the workshop cost?")
HERE = Path(__file__).parent
SECURITY_TAGS = {
    "SecurityControl": "Ignore",
    "SecurityExemption": "StorageAccountKeyAccess",
}


def arm(session, credential, method, resource_id, body=None, params=None):
    response = session.request(
        method, f"https://management.azure.com{resource_id}",
        params=params or {"api-version": ARM_VERSION},
        headers={"Authorization": f"Bearer {credential.get_token('https://management.azure.com/.default').token}"},
        json=body, timeout=60, allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"ARM {method} failed ({response.status_code}): {response.text}")
    return response.json() if response.content else {}


def select_version(session, credential, deployment_id, agent_name, version, timeout=300):
    arm(session, credential, "PUT", deployment_id, {"properties": {
        "tags": SECURITY_TAGS,
        "displayName": "Workshop managed deployment", "deploymentType": "Managed",
        "protocols": [{"protocol": "Responses", "version": "1.0"}],
        "agents": [{"agentName": agent_name, "agentVersion": version}],
    }})
    deadline = monotonic() + timeout
    while True:
        deployment = arm(session, credential, "GET", deployment_id)
        properties = deployment.get("properties", {})
        if properties.get("state") in ("Failed", "Stopped", "Deleted"):
            raise RuntimeError(f"Deployment not running: {json.dumps(deployment)}")
        agents = properties.get("agents", [])
        if (properties.get("state") == "Running" and len(agents) == 1
                and agents[0].get("agentName") == agent_name
                and str(agents[0].get("agentVersion")) == str(version)):
            return deployment
        if monotonic() >= deadline:
            raise TimeoutError(f"Version {version} did not become Running: {json.dumps(deployment)}")
        sleep(5)


def invoke(session, credential, endpoint, version, expected, report):
    for question in QUESTIONS:
        request_id = uuid4().hex
        start = monotonic()
        response = session.post(
            endpoint, params={"api-version": DATA_VERSION},
            headers={
                "Authorization": f"Bearer {credential.get_token('https://ai.azure.com/.default').token}",
                "x-ms-client-request-id": request_id,
            },
            json={"input": question}, timeout=120, allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Application invocation failed ({response.status_code}): {response.text}")
        data = response.json()
        text = "".join(
            content.get("text", "") for item in data.get("output", [])
            for content in item.get("content", []) if content.get("type") == "output_text"
        ).strip()
        record = {
            "question": question, "configured_version": version,
            "response_id": data.get("id"), "request_id": request_id,
            "server_request_id": response.headers.get("x-request-id") or response.headers.get("apim-request-id"),
            "seconds": round(monotonic() - start, 3), "output": text,
            "status": data.get("status"), "usage": data.get("usage"),
            "correct": text == expected,
        }
        report["requests"].append(record)
        print(json.dumps(record), flush=True)
        if data.get("status") != "completed" or not text:
            raise RuntimeError(f"Incomplete application response: {data.get('error')}")
        if text != expected:
            raise RuntimeError(f"Endpoint behavior mismatch for selected version {version}: {text!r}")


def publish_application(session, credential, application_id, name, timeout=300):
    arm(session, credential, "PUT", application_id, {"properties": {
        "tags": SECURITY_TAGS,
        "displayName": "Workshop lifecycle application",
        "agents": [{"agentName": name}],
    }})
    deadline = monotonic() + timeout
    while True:
        application = arm(session, credential, "GET", application_id)
        state = application.get("properties", {}).get("provisioningState")
        if state in ("Failed", "Canceled"):
            raise RuntimeError(f"Application provisioning failed: {json.dumps(application)}")
        if state in (None, "Succeeded"):
            return application
        if monotonic() >= deadline:
            raise TimeoutError("Application provisioning timed out")
        sleep(5)


def lifecycle(project, session, credential, args, report):
    suffix = uuid4().hex[:8]
    name = f"workshop-{suffix}"
    app_name = f"workshop-app-{suffix}"
    application_id = f"{args.project_resource_id}/applications/{app_name}"
    deployment_id = f"{application_id}/agentDeployments/active"
    endpoint = f"{args.project_endpoint.rstrip('/')}/applications/{app_name}/protocols/openai/responses"
    report.update(application_id=application_id, deployment_id=deployment_id, endpoint=endpoint, requests=[])
    good = project.agents.create_version(
        agent_name=name, definition=PromptAgentDefinition(
            model=args.deployment, instructions="For every input reply with exactly: USD 25",
        ),
    )
    report["agent_name"] = name
    report["good_version"] = good.version
    report["application"] = publish_application(session, credential, application_id, name)
    report["initial_deployment"] = select_version(session, credential, deployment_id, name, good.version)
    try:
        invoke(session, credential, endpoint, good.version, "USD 25", report)
        changed = project.agents.create_version(
            agent_name=name, definition=PromptAgentDefinition(
                model=args.deployment, instructions="For every input reply with exactly: USD 40",
            ),
        )
        report["changed_version"] = changed.version
        report["changed_deployment"] = select_version(session, credential, deployment_id, name, changed.version)
        invoke(session, credential, endpoint, changed.version, "USD 40", report)
    finally:
        # Always restore the known-good route, including when rollout verification fails.
        report["rollback_deployment"] = select_version(session, credential, deployment_id, name, good.version)
        invoke(session, credential, endpoint, good.version, "USD 25", report)
        report["rollback_verified"] = True
    subscription = args.project_resource_id.split("/")[2]
    report["activity_log"] = arm(
        session, credential, "GET",
        f"/subscriptions/{subscription}/providers/microsoft.insights/eventtypes/management/values",
        params={
            "api-version": "2015-04-01",
            "$filter": f"eventTimestamp ge '{report['started_at']}' and resourceUri eq '{deployment_id}'",
        },
    )
    print(f"Activity Log records (eventually consistent): {len(report['activity_log'].get('value', []))}")
    print("Rollback verified against the same questions. Published application remains available.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--project-resource-id", default=os.getenv("FOUNDRY_PROJECT_RESOURCE_ID"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    args = parser.parse_args()
    state = HERE / ".azure" / "deployment.json"
    if not args.project_resource_id and state.exists():
        args.project_resource_id = json.loads(state.read_text())["outputs"]["projectResourceId"]["value"]
    pattern = r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/([^/]+)/projects/([^/]+)"
    match = re.fullmatch(pattern, args.project_resource_id or "")
    if not match:
        parser.error("Set FOUNDRY_PROJECT_RESOURCE_ID to the Bicep projectResourceId")
    expected_endpoint = f"https://{match[1]}.services.ai.azure.com/api/projects/{match[2]}"
    if args.project_endpoint != expected_endpoint:
        parser.error("FOUNDRY_PROJECT_ENDPOINT must match the ARM projectResourceId (public Azure only)")
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "rollback_verified": False}
    try:
        with DefaultAzureCredential() as credential, requests.Session() as session, AIProjectClient(
            endpoint=args.project_endpoint, credential=credential,
        ) as project:
            lifecycle(project, session, credential, args, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        (HERE / "lifecycle-report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
