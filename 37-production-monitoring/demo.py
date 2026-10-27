"""Detect and recover a deployed agent regression with native live-traffic evaluation."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointConfig, ContinuousEvaluationRuleAction, EvaluationRule,
    EvaluationRuleEventType, EvaluationRuleFilter, FixedRatioVersionSelectionRule,
    PromptAgentDefinition, ProtocolConfiguration, VersionSelector,
)
from azure.identity import AzureCliCredential
import httpx

HERE = Path(__file__).resolve().parent
STATE = HERE / ".azure"
NAMES = ("monitored-workshop-pricer", "stable-workshop-pricer")
QUESTION = "What is the workshop price per attendee?"


def save(name: str, data: dict) -> None:
    STATE.mkdir(exist_ok=True)
    temporary = STATE / f"{name}.tmp"
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(STATE / name)


def outputs() -> dict:
    path = STATE / "deployment.json"
    if not path.exists():
        raise RuntimeError("Deploy this lab first with deploy_lab.py.")
    data = json.loads(path.read_text())
    return {key: value["value"] for key, value in data.get("outputs", data).items()}


def configuration() -> dict:
    path = STATE / "agents.json"
    if not path.exists():
        raise RuntimeError("Run demo.py --setup first.")
    return json.loads(path.read_text())


def client(credential: AzureCliCredential) -> AIProjectClient:
    return AIProjectClient(outputs()["projectEndpoint"], credential, allow_preview=True)


def route(project: AIProjectClient, name: str, version: str) -> dict:
    result = project.agents.update_details(
        name, agent_endpoint=AgentEndpointConfig(
            version_selector=VersionSelector(version_selection_rules=[
                FixedRatioVersionSelectionRule(agent_version=version, traffic_percentage=100),
            ]),
            protocol_configuration=ProtocolConfiguration(responses={}),
        ),
    )
    rules = result.as_dict().get("agent_endpoint", {}).get("version_selector", {}).get("version_selection_rules")
    if not rules or len(rules) != 1 or str(rules[0]["agent_version"]) != str(version):
        raise RuntimeError(f"Cloud route read-back did not select {name} version {version}: {rules}")
    return result.as_dict()


def setup() -> dict:
    result = configuration() if (STATE / "agents.json").exists() else {"agents": {}}
    with AzureCliCredential() as credential, client(credential) as project, project.get_openai_client() as ai:
        for name in NAMES:
            if name in result["agents"]:
                project.evaluation_rules.get(id=result["agents"][name]["rule_id"])
                continue
            good = project.agents.create_version(
                agent_name=name, definition=PromptAgentDefinition(
                    model=outputs()["deployment"],
                    instructions="For every input, respond with exactly these six characters: USD 25",
                ),
            )
            bad = project.agents.create_version(
                agent_name=name, definition=PromptAgentDefinition(
                    model=outputs()["deployment"],
                    instructions="For every input, respond with exactly these six characters: USD 40",
                ),
            ) if name == NAMES[0] else None
            evaluation = ai.evals.create(
                name=f"Live price correctness - {name}",
                data_source_config={"type": "azure_ai_source", "scenario": "responses"},
                testing_criteria=[{
                    "type": "string_check", "name": "price_correct",
                    "input": "{{sample.output_text}}", "operation": "eq", "reference": "USD 25",
                }],
            )
            rule_id = f"{name}-live-quality"
            rule = project.evaluation_rules.create_or_update(
                id=rule_id, evaluation_rule=EvaluationRule(
                    display_name=f"Live price correctness for {name}",
                    description="Evaluate actual completed responses; bounded to 30 runs/hour.",
                    action=ContinuousEvaluationRuleAction(
                        eval_id=evaluation.id, max_hourly_runs=30, sampling_rate=100,
                    ),
                    event_type=EvaluationRuleEventType.RESPONSE_COMPLETED,
                    filter=EvaluationRuleFilter(agent_name=name), enabled=True,
                ),
            )
            result["agents"][name] = {
                "good_version": good.version, "bad_version": bad.version if bad else None,
                "eval_id": evaluation.id, "rule_id": rule_id, "rule": rule.as_dict(),
            }
            route(project, name, good.version)
            save("agents.json", result)
    return result


def set_mode(mode: str) -> dict:
    if mode not in ("baseline", "regression", "recovery"):
        raise ValueError("mode must be baseline, regression, or recovery")
    config = configuration()
    with AzureCliCredential() as credential, client(credential) as project:
        routes = {}
        for name, agent in config["agents"].items():
            version = agent["bad_version"] if mode == "regression" and name == NAMES[0] else agent["good_version"]
            routes[name] = route(project, name, version)
    return {"mode": mode, "routes": routes}


def traffic(mode: str) -> list[dict]:
    set_mode(mode)
    records = []
    with AzureCliCredential() as credential, client(credential) as project:
        for name in NAMES:
            details = project.agents.get(name).as_dict()
            version = details["agent_endpoint"]["version_selector"]["version_selection_rules"][0]["agent_version"]
            with project.get_openai_client(agent_name=name) as ai:
                start = monotonic()
                response = ai.responses.create(
                    input=QUESTION, max_output_tokens=128, store=True,
                    metadata={"lab": "37", "phase": mode, "run": uuid4().hex},
                )
            if response.status != "completed" or not response.output_text:
                raise RuntimeError(f"Incomplete deployed response: {response.model_dump()}")
            record = {
                "agent": name, "phase": mode, "configured_version": version,
                "response_id": response.id, "answer": response.output_text.strip(),
                "seconds": round(monotonic() - start, 3),
                "usage": response.usage.model_dump() if response.usage else None,
                "expected_pass": not (mode == "regression" and name == NAMES[0]),
            }
            expected = "USD 25" if record["expected_pass"] else "USD 40"
            if record["answer"] != expected:
                raise RuntimeError(f"Deployed endpoint did not demonstrate {mode}: {record}")
            records.append(record)
            print(json.dumps(record), flush=True)
    return records


def evaluation_results() -> list[dict]:
    config = configuration()
    records = []
    with AzureCliCredential() as credential, client(credential) as project, project.get_openai_client() as ai:
        for name, agent in config["agents"].items():
            for run in ai.evals.runs.list(eval_id=agent["eval_id"], limit=30, order="desc").data:
                if (run.metadata or {}).get("trigger_type") == "oneoff":
                    continue
                if run.status in ("failed", "canceled"):
                    raise RuntimeError(f"Native continuous evaluation failed: {run.model_dump()}")
                if run.status != "completed":
                    continue
                items = ai.evals.runs.output_items.list(eval_id=agent["eval_id"], run_id=run.id, limit=100)
                for item in items.data:
                    value = item.model_dump()
                    if value.get("status") != "completed":
                        raise RuntimeError(f"Native evaluator output did not complete successfully: {value}")
                    records.append({
                        "agent": name, "run_id": run.id, "report_url": run.report_url,
                        "item": value,
                    })
    return records


def match_evaluation(record: dict, evaluated: list[dict]) -> dict | None:
    matches = [
        item for item in evaluated if item["agent"] == record["agent"]
        and item["item"].get("datasource_item", {}).get("response_id") == record["response_id"]
    ]
    if not matches:
        return None
    result = matches[0]
    source = result["item"]["datasource_item"]
    if source.get("agent_name") != record["agent"] or str(source.get("agent_version")) != str(record["configured_version"]):
        raise RuntimeError(f"Cloud evaluation served-agent/version mismatch: {source}")
    checks = [
        check for check in result["item"].get("results", [])
        if check.get("name") == "price_correct"
    ]
    if (len(checks) != 1 or type(checks[0].get("passed")) is not bool
            or checks[0].get("status") != "completed"):
        raise RuntimeError(f"Unexpected native grader output shape: {result}")
    if checks[0]["passed"] != record["expected_pass"]:
        raise RuntimeError(f"Native evaluator did not identify the expected quality state: {result}")
    return result


def wait_for_evaluations(records: list[dict], timeout: int = 1200) -> list[dict]:
    deadline = monotonic() + timeout
    while True:
        evaluated = evaluation_results()
        matched = [match_evaluation(record, evaluated) for record in records]
        if all(item is not None for item in matched):
            return matched
        if monotonic() >= deadline:
            save("evaluation-diagnostics.json", {"records": records, "evaluated": evaluated})
            raise TimeoutError("Native live-traffic evaluations did not complete for every response.")
        print(f"Native evaluation evidence: {sum(item is not None for item in matched)}/{len(records)}", flush=True)
        sleep(15)


def telemetry(response_ids: list[str] | None = None) -> dict:
    settings = outputs()
    selected = ""
    if response_ids is not None:
        if not response_ids or any(not value.startswith("resp_") or not value.replace("_", "").isalnum() for value in response_ids):
            raise ValueError("Expected actual cloud response IDs")
        selected = "| where tostring(Properties['gen_ai.response.id']) in (" + ", ".join(
            json.dumps(value) for value in response_ids
        ) + ")\n"
    with AzureCliCredential() as credential:
        token = credential.get_token("https://api.loganalytics.io/.default").token
        response = httpx.post(
            f'https://api.loganalytics.azure.com/v1/workspaces/{settings["workspaceId"]}/query',
            headers={"Authorization": f"Bearer {token}"},
            json={"query": """union isfuzzy=true AppRequests, AppDependencies, AppTraces
| where TimeGenerated > ago(2h)
| where tostring(Properties) contains "workshop-pricer" or Name contains "workshop-pricer"
""" + selected + """| project TimeGenerated, OperationId, Name=column_ifexists("Name", ""),
          Properties, DurationMs=column_ifexists("DurationMs", real(null))
| take 100"""}, timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            raise RuntimeError(f"Telemetry query returned an error: {result['error']}")
        return result


def live_test() -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "passed": False, "requests": []}
    try:
        for mode in ("baseline", "regression"):
            requests = traffic(mode)
            report["requests"].extend(requests)
            save("live-report.json", report)
            report[f"{mode}_evaluations"] = wait_for_evaluations(requests)
    finally:
        set_mode("recovery")
        save("live-report.json", report)
    requests = traffic("recovery")
    report["requests"].extend(requests)
    try:
        report["recovery_evaluations"] = wait_for_evaluations(requests)
        deadline = monotonic() + 900
        while True:
            response_ids = [record["response_id"] for record in report["requests"]]
            traces = telemetry(response_ids)
            rows = traces.get("tables", [{}])[0].get("rows", [])
            if all(any(response_id in json.dumps(row) for row in rows) for response_id in response_ids):
                report["telemetry"] = traces
                break
            if monotonic() >= deadline:
                raise TimeoutError("Both deployed agents must have ingested server-side traces.")
            print("Waiting for both agents' actual Azure Monitor traces...", flush=True)
            sleep(20)
        report["passed"] = True
        return report
    finally:
        save("live-report.json", report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--live-test", action="store_true")
    args = parser.parse_args()
    if args.setup:
        result = setup()
    else:
        setup()
        result = live_test()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
