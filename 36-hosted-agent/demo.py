"""Publish and verify a custom-code Foundry agent; never run its workflow locally."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from time import monotonic, sleep
from uuid import uuid4

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointConfig, ContainerConfiguration, FixedRatioVersionSelectionRule,
    HostedAgentDefinition, ProtocolConfiguration, ProtocolVersionRecord, VersionSelector,
)
from azure.identity import AzureCliCredential
import httpx

HERE = Path(__file__).resolve().parent
STATE = HERE / ".azure"
NAME = "hosted-workshop-pricer"


def read_outputs() -> dict:
    path = STATE / "deployment.json"
    if not path.exists():
        raise RuntimeError("Deploy this lab first with deploy_lab.py.")
    data = json.loads(path.read_text())
    return {key: value["value"] for key, value in data.get("outputs", data).items()}


def save(name: str, data: dict) -> None:
    STATE.mkdir(exist_ok=True)
    (STATE / name).write_text(json.dumps(data, indent=2) + "\n")


def project_client(outputs: dict, credential: AzureCliCredential) -> AIProjectClient:
    return AIProjectClient(outputs["projectEndpoint"], credential, allow_preview=True)


def publish() -> dict:
    outputs = read_outputs()
    tag = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    image = f'{outputs["registryServer"]}/workshop-agent:{tag}'
    subprocess.run([
        "az", "acr", "build", "--registry", outputs["registryName"],
        "--image", f"workshop-agent:{tag}", "--platform", "linux/amd64", str(HERE / "runtime"),
    ], check=True)
    with AzureCliCredential() as credential, project_client(outputs, credential) as project:
        version = project.agents.create_version(
            agent_name=NAME,
            definition=HostedAgentDefinition(
                cpu="1", memory="2Gi",
                container_configuration=ContainerConfiguration(image=image),
                protocol_versions=[ProtocolVersionRecord(protocol="invocations", version="2.0.0")],
                environment_variables={
                    "MODEL_DEPLOYMENT_NAME": outputs["deployment"], "PYTHONUNBUFFERED": "1",
                    "OTEL_TRACES_SAMPLER": "always_on",
                    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
                },
            ),
        )
        record = {"name": NAME, "version": version.version, "image": image,
                  "project_endpoint": outputs["projectEndpoint"]}
        save("agent.json", record)
        deadline = monotonic() + 1200
        while True:
            current = project.agents.get_version(NAME, version.version)
            status = current.as_dict().get("status")
            print(f"Hosted version {version.version}: {status}", flush=True)
            if status == "active":
                break
            if status == "failed":
                raise RuntimeError(f"Hosted provisioning failed: {current.as_dict()}")
            if monotonic() >= deadline:
                raise TimeoutError(f"Hosted provisioning timed out: {current.as_dict()}")
            sleep(10)
        if not current.instance_identity or not current.instance_identity.principal_id:
            raise RuntimeError("The hosted version has no service-created agent identity.")
        record["agent_identity"] = current.instance_identity.as_dict()
        subprocess.run([
            "az", "role", "assignment", "create",
            "--assignee-object-id", current.instance_identity.principal_id,
            "--assignee-principal-type", "ServicePrincipal",
            "--role", "Monitoring Metrics Publisher",
            "--scope", outputs["applicationInsightsResourceId"], "-o", "none",
        ], check=True)
        project.agents.update_details(
            NAME, agent_endpoint=AgentEndpointConfig(
                version_selector=VersionSelector(version_selection_rules=[
                    FixedRatioVersionSelectionRule(agent_version=version.version, traffic_percentage=100),
                ]),
                protocol_configuration=ProtocolConfiguration(invocations={}),
            ),
        )
        record["resource"] = project.agents.get(NAME).as_dict()
        save("agent.json", record)
        return record


def invoke(attendees: int, session_id: str) -> dict:
    if type(attendees) is not int or not 1 <= attendees <= 20:
        raise ValueError("attendees must be an integer from 1 to 20")
    if not isinstance(session_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", session_id):
        raise ValueError("Invalid session ID")
    outputs = read_outputs()
    with AzureCliCredential() as credential:
        token = credential.get_token("https://ai.azure.com/.default").token
        response = httpx.post(
            f'{outputs["projectEndpoint"]}/agents/{NAME}/endpoint/protocols/invocations',
            params={"api-version": "v1", "agent_session_id": session_id},
            headers={"Authorization": f"Bearer {token}"},
            json={"attendees": attendees}, timeout=240, follow_redirects=False,
        )
        response.raise_for_status()
    result = response.json()
    if result.get("quote") != {
        "attendees": attendees, "currency": "USD", "unit_price": 25,
        "total": attendees * 25, "source": "hosted-read-only-price-tool",
    } or not result.get("response_id") or not result.get("tool_call_id"):
        raise RuntimeError(f"Hosted response failed independent validation: {result}")
    return result


def query_traces(outputs: dict, trace_ids: list[str]) -> dict:
    if not trace_ids or any(not re.fullmatch(r"[a-f0-9]{32}", value) for value in trace_ids):
        raise ValueError("Expected actual 32-character hexadecimal trace IDs")
    selected_ids = ", ".join(json.dumps(value) for value in trace_ids)
    with AzureCliCredential() as credential:
        token = credential.get_token("https://api.loganalytics.io/.default").token
        response = httpx.post(
            f'https://api.loganalytics.azure.com/v1/workspaces/{outputs["workspaceId"]}/query',
            headers={"Authorization": f"Bearer {token}"},
            json={"query": f"""union isfuzzy=true AppRequests, AppDependencies, AppTraces
| where TimeGenerated > ago(2h)
| where OperationId in ({selected_ids})
| project TimeGenerated, OperationId, Name=column_ifexists("Name", ""),
          Properties, DurationMs=column_ifexists("DurationMs", real(null))
| take 200"""}, timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"Telemetry query returned an error: {data['error']}")
        return data


def live_test() -> dict:
    outputs = read_outputs()
    session = f"live-{uuid4().hex}"
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "passed": False}
    try:
        first = invoke(3, session)
        second = invoke(4, session)
        independent = invoke(2, f"live-{uuid4().hex}")
        if first["state"]["visits"] != 1 or second["state"]["visits"] != 2:
            raise RuntimeError("Hosted session did not persist its visit count.")
        if first["state"]["marker"] != second["state"]["marker"]:
            raise RuntimeError("Hosted session lost its persisted marker.")
        if independent["state"]["visits"] != 1 or independent["state"]["marker"] == first["state"]["marker"]:
            raise RuntimeError("Independent hosted sessions did not isolate their state.")
        report["invocations"] = [first, second, independent]
        trace_ids = [item["trace_id"] for item in report["invocations"]]
        deadline = monotonic() + 900
        while True:
            traces = query_traces(outputs, trace_ids)
            rows = traces.get("tables", [{}])[0].get("rows", [])
            if all(any(row[1] == trace_id and row[2] for row in rows) for trace_id in trace_ids):
                report["telemetry"] = traces
                break
            if monotonic() >= deadline:
                raise TimeoutError("No hosted-agent telemetry ingested within 15 minutes.")
            print("Waiting for real hosted-agent trace ingestion...", flush=True)
            sleep(20)
        report["passed"] = True
        return report
    finally:
        save("live-report.json", report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--live-test", action="store_true")
    args = parser.parse_args()
    manifest_path = STATE / "agent.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    endpoint = read_outputs()["projectEndpoint"]
    if args.publish or not manifest.get("resource") or manifest.get("project_endpoint", endpoint) != endpoint:
        published = publish()
        if args.publish:
            print(json.dumps(published, indent=2))
            return
    result = live_test()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
