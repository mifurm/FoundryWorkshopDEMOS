"""Gate a versioned support prompt using exact correctness and Foundry coherence."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep
from uuid import uuid4

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, TestingCriterionAzureAIEvaluator
from azure.identity import DefaultAzureCredential

HERE = Path(__file__).parent


def exact_answer(text, expected):
    try:
        value = json.loads(text)
        return isinstance(value, dict) and value.get("answer") == expected
    except (ValueError, TypeError):
        return False


def cloud_evaluate(client, items, deployment, report, timeout=600):
    if not items or any(
        not all(isinstance(item.get(key), str) and item[key].strip()
                for key in ("query", "response", "response_id"))
        for item in items
    ):
        raise ValueError("Nonempty queries, actual responses and response IDs are required")
    # Agent-endpoint response IDs aren't retrievable from the project evaluation store.
    content = [{"item": {key: item[key] for key in ("query", "response", "response_id")}}
               for item in items]
    report["evaluation_source"] = {"type": "jsonl", "source": {"type": "file_content", "content": content}}
    evaluation = client.evals.create(
        name="lab23-coherence",
        data_source_config={
            "type": "custom",
            "item_schema": {
                "type": "object",
                "properties": {key: {"type": "string"} for key in ("query", "response", "response_id")},
                "required": ["query", "response", "response_id"],
            },
        },
        testing_criteria=[TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator", name="coherence", evaluator_name="builtin.coherence",
            initialization_parameters={"model": deployment},
            data_mapping={"query": "{{item.query}}", "response": "{{item.response}}"},
        )],
    )
    report["evaluation_id"] = evaluation.id
    run = client.evals.runs.create(
        eval_id=evaluation.id, name="support-regression-gate",
        data_source=report["evaluation_source"],
    )
    report["run_id"] = run.id
    deadline = monotonic() + timeout
    while run.status in ("queued", "in_progress"):
        if monotonic() >= deadline:
            raise TimeoutError(f"Evaluation timeout: {evaluation.id}/{run.id}")
        sleep(5)
        run = client.evals.runs.retrieve(run.id, eval_id=evaluation.id)
    report["cloud_run"] = run.model_dump(mode="json")
    if run.status != "completed":
        raise RuntimeError(f"Evaluation infrastructure status: {run.status}; error: {run.error}")
    counts = run.result_counts
    if (counts.total != len(items) or counts.errored
            or counts.passed + counts.failed != counts.total):
        raise RuntimeError(f"Incomplete/errored evaluation: {counts}")
    output_items = list(client.evals.runs.output_items.list(run.id, eval_id=evaluation.id))
    report["cloud_items"] = [item.model_dump(mode="json") for item in output_items]
    if len(output_items) != len(items) or any(
        item["status"] != "completed" for item in report["cloud_items"]
    ):
        raise RuntimeError("Missing or errored evaluation output items")
    expected = {item["response_id"]: item for item in (row["item"] for row in content)}
    seen = set()
    passed = 0
    for item in report["cloud_items"]:
        source = item.get("datasource_item", {})
        response_id = source.get("response_id")
        results = item.get("results", [])
        if (response_id not in expected or response_id in seen or source != expected[response_id]
                or len(results) != 1 or results[0].get("name") != "coherence"
                or results[0].get("status") != "completed"
                or type(results[0].get("passed")) is not bool):
            raise RuntimeError("Incomplete, mismatched or errored coherence result")
        seen.add(response_id)
        passed += results[0]["passed"]
    if len(seen) != len(items) or passed != counts.passed:
        raise RuntimeError("Per-item coherence results disagree with aggregate counts")
    return counts.passed / counts.total


def release_gate(report, correctness_threshold, coherence_threshold):
    items = report["items"]
    if not items:
        raise RuntimeError("Empty dataset cannot pass")
    accuracy = sum(item["correct"] for item in items) / len(items)
    report["correctness_rate"] = accuracy
    report["passed"] = accuracy >= correctness_threshold and report["coherence_rate"] >= coherence_threshold
    for item in items:
        print(json.dumps(item))
    if not report["passed"]:
        raise RuntimeError(f"Release blocked: correctness={accuracy:.3f}, coherence={report['coherence_rate']:.3f}")


def execute(project, args, dataset, prompt, report):
    # Query real deployment metadata, not a model version supplied by the caller.
    report["model"] = project.deployments.get(args.deployment).as_dict()
    report["judge_model"] = project.deployments.get(args.judge_deployment).as_dict()
    agent = project.agents.create_version(
        agent_name=f"ci-support-{uuid4().hex[:8]}",
        definition=PromptAgentDefinition(model=args.deployment, instructions=prompt),
    )
    report["agent"] = {"name": agent.name, "version": agent.version}
    try:
        report["items"] = []
        with project.get_openai_client(agent_name=agent.name) as client:
            for item in dataset:
                response = client.responses.create(input=item["query"], store=True, timeout=120)
                if response.status != "completed" or not response.output_text.strip():
                    raise RuntimeError(f"Agent response failed: {response.status}; {response.error}")
                report["items"].append({
                    **item, "response_id": response.id, "response": response.output_text,
                    "correct": exact_answer(response.output_text, item["expected"]),
                })
        with project.get_openai_client() as client:
            report["coherence_rate"] = cloud_evaluate(
                client, report["items"],
                args.judge_deployment, report, args.timeout,
            )
        release_gate(report, args.correctness_threshold, args.coherence_threshold)
    finally:
        project.agents.delete_version(agent_name=agent.name, agent_version=agent.version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--judge-deployment")
    parser.add_argument("--prompt", choices=("baseline", "regression"), default="baseline")
    parser.add_argument("--correctness-threshold", type=float, default=1.0)
    parser.add_argument("--coherence-threshold", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--report", type=Path, default=HERE / "evaluation-report.json")
    args = parser.parse_args()
    if not args.project_endpoint or not args.project_endpoint.startswith("https://"):
        parser.error("Set FOUNDRY_PROJECT_ENDPOINT")
    if not all(0 <= threshold <= 1 for threshold in (args.correctness_threshold, args.coherence_threshold)):
        parser.error("Thresholds must be between 0 and 1")
    if args.timeout <= 0:
        parser.error("Timeout must be positive")
    args.judge_deployment = args.judge_deployment or args.deployment
    dataset_bytes = (HERE / "dataset.json").read_bytes()
    prompt_bytes = (HERE / "prompts" / f"{args.prompt}.txt").read_bytes()
    dataset = json.loads(dataset_bytes)
    if not dataset or len({item["id"] for item in dataset}) != len(dataset):
        parser.error("Dataset must be nonempty and have unique IDs")
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "commit": os.getenv("GITHUB_SHA", "local"),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "dataset": dataset, "prompt_variant": args.prompt,
        "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "prompt": prompt_bytes.decode(), "project_endpoint": args.project_endpoint,
        "deployment": args.deployment, "judge_deployment": args.judge_deployment,
        "evaluator": {"name": "builtin.coherence", "initialization_parameters": {"model": args.judge_deployment}},
        "thresholds": {"correctness": args.correctness_threshold, "coherence": args.coherence_threshold},
        "passed": False,
    }
    try:
        with DefaultAzureCredential() as credential, AIProjectClient(
            endpoint=args.project_endpoint, credential=credential,
        ) as project:
            execute(project, args, dataset, prompt_bytes.decode(), report)
    except Exception as error:
        report["passed"] = False
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        print(f"FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Evaluation report: {args.report}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
