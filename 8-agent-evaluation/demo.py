"""Evaluate stored agent responses with Foundry's cloud coherence evaluator."""

import argparse
import os
from time import monotonic, sleep
from uuid import uuid4

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, TestingCriterionAzureAIEvaluator
from azure.identity import AzureCliCredential


QUESTIONS = [
    "What is Microsoft Foundry? Explain in two short sentences.",
    "How is model inference different from model training?",
]


def evaluate(client, response_ids: list[str], deployment: str, timeout: float = 600):
    if not response_ids or any(not value.strip() for value in response_ids):
        raise ValueError("At least one nonempty stored response ID is required.")
    evaluation = client.evals.create(
        name="Workshop agent coherence",
        data_source_config={"type": "azure_ai_source", "scenario": "responses"},
        testing_criteria=[TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator", name="coherence", evaluator_name="builtin.coherence",
            initialization_parameters={"model": deployment},
        )],
    )
    run = client.evals.runs.create(
        eval_id=evaluation.id, name="two-question-smoke-test",
        data_source={
            "type": "azure_ai_responses",
            "item_generation_params": {
                "type": "response_retrieval",
                "data_mapping": {"response_id": "{{item.resp_id}}"},
                "source": {"type": "file_content", "content": [
                    {"item": {"resp_id": value}} for value in response_ids
                ]},
            },
        },
    )
    print(f"Evaluation: {evaluation.id}; run: {run.id}", flush=True)
    deadline = monotonic() + timeout
    while run.status in ("queued", "in_progress"):
        if monotonic() >= deadline:
            raise TimeoutError(f"Evaluation still running: {evaluation.id}/{run.id}")
        sleep(5)
        run = client.evals.runs.retrieve(run.id, eval_id=evaluation.id)
    if run.status != "completed":
        raise RuntimeError(f"Evaluation failed: {run.model_dump_json(indent=2)}")
    counts = run.result_counts
    if counts.total != len(response_ids) or counts.errored or counts.passed + counts.failed != counts.total:
        raise RuntimeError(f"Incomplete evaluation results: {counts}")
    print(run.model_dump_json(indent=2))
    items = list(client.evals.runs.output_items.list(run.id, eval_id=evaluation.id))
    if len(items) != len(response_ids) or any(item.status != "pass" and item.status != "fail"
                                             for item in items):
        raise RuntimeError("Evaluation returned missing or errored output items.")
    for item in items:
        print(item.model_dump_json(indent=2))
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    args = parser.parse_args()
    if not args.project_endpoint or not args.project_endpoint.startswith("https://"):
        parser.error("Set FOUNDRY_PROJECT_ENDPOINT to the Bicep projectEndpoint HTTPS URL.")
    if not args.deployment.strip():
        parser.error("Deployment must not be empty.")
    with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=args.project_endpoint, credential=credential) as project,
    ):
        agent = project.agents.create_version(
            agent_name=f"eval-demo-{uuid4().hex[:8]}",
            definition=PromptAgentDefinition(
                model=args.deployment, instructions="You are a concise AI workshop tutor.",
            ),
        )
        try:
            with project.get_openai_client(agent_name=agent.name) as agent_client:
                ids = []
                for question in QUESTIONS:
                    response = agent_client.responses.create(input=question, store=True, timeout=120)
                    if response.status != "completed" or not response.output_text.strip():
                        raise RuntimeError(f"Agent response failed: {response.status}; {response.error}")
                    print(f"{question}\n{response.output_text}\nStored response: {response.id}")
                    ids.append(response.id)
            with project.get_openai_client() as evaluator_client:
                evaluate(evaluator_client, ids, args.deployment)
        finally:
            project.agents.delete_version(agent_name=agent.name, agent_version=agent.version)


if __name__ == "__main__":
    main()
