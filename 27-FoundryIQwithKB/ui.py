"""Interface to the existing Foundry IQ agent."""

import argparse
import asyncio
import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from threading import Lock, Thread
from time import perf_counter, time
import traceback
from types import SimpleNamespace
from uuid import uuid4

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import AzureError
from agent_framework.exceptions import AgentFrameworkException
from azure.identity import AzureCliCredential, ManagedIdentityCredential, get_bearer_token_provider
from azure.identity.aio import AzureCliCredential as AsyncAzureCliCredential
from azure.identity.aio import ManagedIdentityCredential as AsyncManagedIdentityCredential
import httpx
from openai import AzureOpenAI, OpenAI, OpenAIError
import requests

import demo

HERE = Path(__file__).resolve().parent
operation = Lock()
job_lock = Lock()
job_write_error = None
LAB_GUIDES = {
    "9": "9-agent-red-teaming",
    "10": "10-fine-tuning",
    "12": "12-entra-agent-id",
    "21": "21-permission-aware-retrieval",
    "31": "31-native-agent-memory",
    "32": "32-browser-runbook",
    "35": "35-work-iq",
    "39": "39-gateway-token-limits",
    "42": "42-managed-harness-tool-search",
    "43": "43-agent-routines",
    "44": "44-evaluation-optimizer",
    "45": "45-governed-iq",
}


class ApprovalPermissionError(Exception):
    pass


def ask(question: str) -> str:
    hosted = os.environ.get("UI_AZURE_HOSTED") == "1"
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"] if hosted else demo.load_config().projectEndpoint
    if not endpoint.startswith("https://"):
        raise RuntimeError("The Foundry project endpoint must use HTTPS.")
    credential = ManagedIdentityCredential() if hosted else AzureCliCredential()
    with credential:
        with AIProjectClient(endpoint=endpoint, credential=credential) as project:
            with project.get_openai_client(agent_name=demo.AGENT) as client:
                response = client.responses.create(input=question, tool_choice="required", timeout=180)
    result = response.model_dump(mode="json")
    demo.validate_agent_response(result)
    return "\n".join(
        content.get("text", "") for item in result.get("output", [])
        if item.get("type") == "message" for content in item.get("content", [])
        if content.get("type") == "output_text"
    ).strip()


def load_demo(directory: str, *, package: bool = False):
    path = HERE.parent / directory / "demo.py"
    name = "workshop_" + directory.replace("-", "_")
    if package and name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if package else None,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load the {directory} implementation.")
    module = importlib.util.module_from_spec(spec)
    if package:
        sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if package:
            del sys.modules[name]
        raise
    return module


def compare_skills(title: str) -> dict:
    endpoint = os.environ.get("UI_SKILLS_PROJECT_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_SKILLS_PROJECT_ENDPOINT to the lab 28 project HTTPS endpoint.")
    skills = load_demo("28-agent-skills")
    hosted = os.environ.get("UI_AZURE_HOSTED") == "1"
    credential = ManagedIdentityCredential() if hosted else AzureCliCredential()
    deployment = os.environ.get("UI_SKILLS_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not deployment:
        raise RuntimeError("UI_SKILLS_DEPLOYMENT must not be blank.")
    with credential:
        with AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project:
            return skills.demonstrate(project, deployment, title)


def workflow_agent(data: dict) -> dict:
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
        raise ValueError("Prompt must be 1-4000 characters.")
    endpoint = os.environ.get("UI_WORKFLOW_PROJECT_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_WORKFLOW_PROJECT_ENDPOINT to the lab 2 project HTTPS endpoint.")
    deployment = os.environ.get("UI_WORKFLOW_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not deployment:
        raise RuntimeError("UI_WORKFLOW_DEPLOYMENT must not be blank.")
    credential = AsyncManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AsyncAzureCliCredential()
    lab = load_demo("2-workflow-agent")
    return asyncio.run(lab.run_workflow(endpoint, deployment, prompt.strip(), credential))


def web_search_comparison() -> dict:
    endpoint = os.environ.get("UI_WEB_SEARCH_PROJECT_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_WEB_SEARCH_PROJECT_ENDPOINT to the lab 3 project HTTPS endpoint.")
    deployment = os.environ.get("UI_WEB_SEARCH_DEPLOYMENT", "gpt-5-mini").strip()
    if not deployment:
        raise RuntimeError("UI_WEB_SEARCH_DEPLOYMENT must not be blank.")
    lab = load_demo("3-webiq-agent")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        with AIProjectClient(endpoint=endpoint, credential=credential) as project:
            return lab.compare(project, deployment, lab.DEFAULT_PROMPT)


def tool_agent(data: dict) -> dict:
    attendees = data.get("attendees")
    if type(attendees) is not int or not 1 <= attendees <= 999:
        raise ValueError("Attendees must be an integer from 1 to 999.")
    endpoint = os.environ.get("UI_TOOLS_PROJECT_ENDPOINT", "").strip()
    tool_endpoint = os.environ.get("UI_TOOLS_FUNCTION_ENDPOINT", "").strip()
    if not endpoint.startswith("https://") or not tool_endpoint.startswith("https://"):
        raise RuntimeError("Set the lab 4 project and function HTTPS endpoints.")
    deployment = os.environ.get("UI_TOOLS_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not deployment:
        raise RuntimeError("UI_TOOLS_DEPLOYMENT must not be blank.")
    lab = load_demo("4-agent-tools")
    credential = AsyncManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AsyncAzureCliCredential()
    return asyncio.run(asyncio.wait_for(lab.run_agent(endpoint, deployment, tool_endpoint, attendees, credential),
                                        timeout=180))


def trace_agent(data: dict) -> dict:
    scenario = data.get("scenario")
    if scenario not in ("normal", "slow", "failed"):
        raise ValueError("Choose normal, slow, or failed.")
    endpoint = os.environ.get("UI_TRACING_ENDPOINT", "").strip()
    connection_string = os.environ.get("UI_TRACING_CONNECTION_STRING", "").strip()
    deployment = os.environ.get("UI_TRACING_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not endpoint.startswith("https://") or not connection_string or not deployment:
        raise RuntimeError("Set the lab 18 model endpoint, telemetry connection string and deployment.")
    lab = load_demo("18-agent-tracing")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        traces = lab.run_scenarios(endpoint, deployment, connection_string, credential, (scenario,))
    return {"traces": traces, "note": "Flush does not prove ingestion; verify in Application Insights."}


def skill_discovery(data: dict) -> dict:
    case = data.get("case")
    if case not in ("workshop-brief", "support-triage", "incident-summary"):
        raise ValueError("Choose a skill discovery case.")
    endpoint = os.environ.get("UI_DISCOVERY_PROJECT_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_DISCOVERY_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not endpoint.startswith("https://") or not deployment:
        raise RuntimeError("Set the lab 29 project HTTPS endpoint and deployment.")
    lab = load_demo("29-toolbox-skill-discovery")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        with AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project:
            return asyncio.run(asyncio.wait_for(
                lab.demonstrate(project, credential, endpoint, deployment, case), timeout=180))


def foundry_iq(data: dict) -> dict:
    question = data.get("question")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
        raise ValueError("Question must be 1-2000 characters.")
    search_endpoint = os.environ.get("UI_IQ_SEARCH_ENDPOINT", "").strip()
    model_endpoint = os.environ.get("UI_IQ_MODEL_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_IQ_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not search_endpoint.startswith("https://") or not model_endpoint.startswith("https://") or not deployment:
        raise RuntimeError("Set the lab 11 Search and model HTTPS endpoints and deployment.")
    lab = load_demo("11-foundry-iq")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        token = credential.get_token("https://search.azure.com/.default").token
        with httpx.Client(base_url=search_endpoint.rstrip("/") + "/",
                          headers={"Authorization": f"Bearer {token}"},
                          params={"api-version": lab.API_VERSION}, timeout=120) as client:
            lab.configure(client, model_endpoint, deployment)
            return lab.retrieve(client, question.strip())


def job_path() -> Path:
    default = "/home/data/workshop-jobs" if os.environ.get("UI_AZURE_HOSTED") == "1" else str(HERE / ".azure" / "ui-jobs")
    return Path(os.environ.get("UI_JOB_STATE_DIR", default)) / "current.json"


def read_job() -> dict:
    if job_write_error is not None:
        raise RuntimeError(f"Unable to persist workshop job state: {job_write_error}")
    path = job_path()
    if not path.exists():
        return {"status": "idle"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise RuntimeError(f"Unable to read workshop job state: {error}") from error
    if not isinstance(value, dict) or value.get("status") not in ("running", "completed", "failed", "interrupted"):
        raise RuntimeError("Invalid workshop job state.")
    return value


def write_job(value: dict) -> None:
    path = job_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def recover_interrupted_job() -> None:
    with job_lock:
        job = read_job()
        if job["status"] == "running":
            job.update(status="interrupted", error="The UI restarted during this run; inspect Azure resources before retrying.")
            write_job(job)


def release_governance(regression: bool) -> dict:
    endpoint = os.environ.get("UI_RELEASE_PROJECT_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_RELEASE_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not endpoint.startswith("https://") or not deployment:
        raise RuntimeError("Set the lab 33 project HTTPS endpoint and deployment.")
    lab = load_demo("33-skill-release-governance")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        with AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project:
            return lab.demonstrate(project, deployment, regression=regression)


def agent_evaluation() -> dict:
    endpoint = os.environ.get("UI_EVALUATION_PROJECT_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_EVALUATION_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not endpoint.startswith("https://") or not deployment:
        raise RuntimeError("Set the lab 8 project HTTPS endpoint and deployment.")
    lab = load_demo("8-agent-evaluation")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        with AIProjectClient(endpoint=endpoint, credential=credential) as project:
            return lab.demonstrate(project, deployment)


def csv_analyst() -> dict:
    endpoint = os.environ.get("UI_ANALYST_PROJECT_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_ANALYST_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not endpoint.startswith("https://") or not deployment:
        raise RuntimeError("Set the lab 30 project HTTPS endpoint and deployment.")
    lab = load_demo("30-skill-data-analyst")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential, AIProjectClient(
        endpoint=endpoint, credential=credential, allow_preview=True,
        connection_timeout=15, read_timeout=lab.TIMEOUT, retry_total=0,
    ) as project, project.get_openai_client(timeout=lab.TIMEOUT, max_retries=0) as client:
        result = lab.demonstrate(project, client, deployment)
    result["chart_base64"] = base64.b64encode(result.pop("chart")).decode("ascii")
    return result


def hosted_agent(attendees: int, session_id: str) -> dict:
    endpoint = os.environ.get("UI_HOSTED_AGENT_PROJECT_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set the lab 36 project HTTPS endpoint.")
    lab = load_demo("36-hosted-agent")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        return lab.invoke(attendees, session_id, project_endpoint=endpoint, credential=credential)


def rag_case(chunk_words: int, mode: str, question_index: int, index_name: str) -> dict:
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    endpoint = os.environ.get("UI_RAG_ENDPOINT", "").strip()
    search_endpoint = os.environ.get("UI_RAG_SEARCH_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_RAG_DEPLOYMENT", "rag-chat").strip()
    embedding = os.environ.get("UI_RAG_EMBEDDING_DEPLOYMENT", "rag-embedding").strip()
    if not endpoint.startswith("https://") or not search_endpoint.startswith("https://") or not deployment or not embedding:
        raise RuntimeError("Set the lab 17 model and Search HTTPS endpoints and deployments.")
    lab = load_demo("17-rag-fundamentals")
    question, expected_source = lab.QUESTIONS[question_index]
    documents = lab.chunks(lab.DOCUMENTS, chunk_words)
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    print(f"Lab 17 temporary index: {index_name}", flush=True)
    with credential, SearchIndexClient(search_endpoint, credential) as indexes, AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default"),
        api_version="2024-10-21",
        timeout=120,
    ) as client:
        lab.create_index(indexes, index_name)
        try:
            with SearchClient(search_endpoint, index_name, credential) as search:
                vectors = sorted(
                    client.embeddings.create(model=embedding, input=[item["content"] for item in documents]).data,
                    key=lambda item: item.index,
                )
                if len(vectors) != len(documents) or [item.index for item in vectors] != list(range(len(documents))):
                    raise RuntimeError("Embedding response did not match the indexed documents.")
                upload = search.upload_documents([
                    {**document, "vector": vector.embedding}
                    for document, vector in zip(documents, vectors)
                ])
                if any(not item.succeeded for item in upload) or len(upload) != len(documents):
                    raise RuntimeError(f"Search upload failed: {upload}")
                lab.wait_for_index(search, len(documents))
                question_vectors = client.embeddings.create(model=embedding, input=question).data
                if len(question_vectors) != 1 or question_vectors[0].index != 0:
                    raise RuntimeError("Embedding response did not match the question.")
                question_vector = question_vectors[0].embedding
                hits = lab.retrieve(search, mode, question, question_vector)
                answer = lab.grounded_answer(client, deployment, question, hits)
                score = lab.metrics(hits, expected_source)
                return {
                    "index": index_name, "chunk_words": chunk_words, "chunk_count": len(documents),
                    "mode": mode, "question": question, "expected_source": expected_source,
                    "hits": [{"id": hit["id"], "source": hit["source"], "content": hit["content"]} for hit in hits],
                    "answer": answer,
                    "recall_at_3": score[0] if score is not None else None,
                    "mrr_at_3": score[1] if score is not None else None,
                    "abstained_on_unanswerable": answer["answer"] == lab.ABSTENTION if expected_source is None else None,
                }
        finally:
            indexes.delete_index(index_name)


def content_understanding(run_id: str) -> dict:
    endpoint = os.environ.get("UI_CU_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_CU_DEPLOYMENT", "cu-ga-completion").strip()
    embedding = os.environ.get("UI_CU_EMBEDDING_DEPLOYMENT", "cu-embedding").strip()
    if not endpoint.startswith("https://") or not deployment or not embedding:
        raise RuntimeError("Set the lab 41 Content Understanding HTTPS endpoint and deployments.")
    lab = load_demo("41-content-understanding")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        return lab.demonstrate(endpoint, deployment, embedding, credential,
                               job_path().parent / "content-understanding" / run_id)


def semantic_cache() -> dict:
    endpoint = os.environ.get("UI_CACHE_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_CACHE_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not endpoint.startswith("https://") or not deployment:
        raise RuntimeError("Set the lab 7 gateway HTTPS endpoint and deployment.")
    lab = load_demo("7-semantic-cache")
    session = f"ui-{uuid4().hex}"
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential, AzureOpenAI(
        azure_endpoint=endpoint.rstrip("/"), api_version="2024-10-21",
        azure_ad_token_provider=get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default"),
        default_headers={"x-workshop-cache-session": session},
    ) as client:
        return {"session": session, **lab.present(client, deployment)}


def agent_lifecycle(run_id: str) -> dict:
    endpoint = os.environ.get("UI_LIFECYCLE_PROJECT_ENDPOINT", "").strip()
    resource_id = os.environ.get("UI_LIFECYCLE_PROJECT_RESOURCE_ID", "").strip()
    deployment = os.environ.get("UI_LIFECYCLE_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not deployment:
        raise RuntimeError("Set the lab 24 model deployment.")
    lab = load_demo("24-agent-lifecycle")
    lab.validate_project(endpoint, resource_id)
    args = SimpleNamespace(project_endpoint=endpoint, project_resource_id=resource_id, deployment=deployment)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "rollback_verified": False}
    path = job_path().parent / "agent-lifecycle" / f"{run_id}.json"

    def persist(value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    try:
        with credential, requests.Session() as session, AIProjectClient(endpoint=endpoint, credential=credential) as project:
            lab.lifecycle(project, session, credential, args, report, suffix=run_id[:8], on_update=persist)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        persist(report)
    return report


def fine_tuning_state() -> dict:
    path = job_path().parent / "fine-tuning.json"
    if not path.exists():
        return {"phase": "empty"}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Unable to read fine-tuning state: {error}") from error
    if not isinstance(state, dict) or state.get("phase") not in ("submitting", "uncertain", "submitted", "acknowledged"):
        raise RuntimeError("Invalid fine-tuning state; inspect Azure before proceeding.")
    if state["phase"] == "submitted":
        saved_job = state.get("job")
        if (not isinstance(saved_job, dict) or not isinstance(saved_job.get("id"), str)
                or not isinstance(saved_job.get("status"), str)):
            raise RuntimeError("Invalid saved fine-tuning job; inspect Azure before proceeding.")
    return state


def write_fine_tuning_state(state: dict) -> None:
    path = job_path().parent / "fine-tuning.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def fine_tuning_job(job) -> dict:
    data = job.model_dump(mode="json")
    return {key: data.get(key) for key in (
        "id", "status", "model", "fine_tuned_model", "trained_tokens",
        "created_at", "finished_at", "training_file", "result_files", "error",
    )}


def fine_tuning(action: str, data: dict | None = None) -> dict:
    data = data or {}
    endpoint = os.environ.get("UI_FINE_TUNING_ENDPOINT", "").strip()
    if not re.fullmatch(r"https://[a-zA-Z0-9-]+\.openai\.azure\.com/?", endpoint):
        raise RuntimeError("Set UI_FINE_TUNING_ENDPOINT to the lab 10 Azure OpenAI HTTPS endpoint.")
    state = fine_tuning_state()
    if state.get("endpoint") and state["endpoint"].rstrip("/") != endpoint.rstrip("/"):
        raise RuntimeError("Saved fine-tuning state belongs to another account; inspect it before changing endpoints.")
    if action == "acknowledge":
        if state["phase"] not in ("submitting", "uncertain") or data.get("inspected_azure") is not True:
            raise ValueError("Acknowledge only after inspecting Azure for an uncertain submission.")
        state.update(phase="acknowledged", acknowledged_at=datetime.now(timezone.utc).isoformat())
        write_fine_tuning_state(state)
        return {"submission": state}
    if action == "submit":
        if data.get("confirm_cost") is not True:
            raise ValueError("Confirm that a new paid training job will be submitted.")
        if state["phase"] in ("submitting", "uncertain"):
            raise RuntimeError("A prior submission is uncertain. Inspect Azure and acknowledge it before another submission.")
        if state["phase"] == "submitted" and state.get("job", {}).get("status") not in ("succeeded", "failed", "cancelled"):
            raise RuntimeError("The saved training job may still be running; inspect it before submitting another.")
    elif action != "inspect":
        raise ValueError("Choose submit, inspect, or acknowledge.")

    lab = load_demo("10-fine-tuning")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=endpoint.rstrip("/") + "/openai/v1/", api_key=token,
                    max_retries=0, timeout=120) as client:
            if action == "inspect":
                job_id = data.get("job_id", "")
                if not isinstance(job_id, str):
                    raise ValueError("Enter a valid fine-tuning job ID.")
                job_id = job_id.strip()
                if not job_id:
                    job_id = state.get("job", {}).get("id", "")
                if not job_id:
                    return {"submission": state, "message": "No saved job ID. Inspect the Azure account for any uncertain submission."}
                if not re.fullmatch(r"ftjob-[a-zA-Z0-9_-]{1,128}", job_id):
                    raise ValueError("Enter a valid fine-tuning job ID.")
                job = fine_tuning_job(client.fine_tuning.jobs.retrieve(job_id))
                if job["status"] == "succeeded" and not job["fine_tuned_model"]:
                    raise RuntimeError("Succeeded job did not return a fine-tuned model ID.")
                if state["phase"] == "submitted" and state.get("job", {}).get("id") == job_id:
                    state["job"] = job
                    write_fine_tuning_state(state)
                return {"job": job, "submission": state}

            state = {"phase": "submitting", "endpoint": endpoint,
                     "started_at": datetime.now(timezone.utc).isoformat()}
            write_fine_tuning_state(state)

            def uploaded(file_id: str) -> None:
                state["training_file"] = file_id
                write_fine_tuning_state(state)

            try:
                job = fine_tuning_job(lab.submit(client, lab.MODEL, on_upload=uploaded))
                if (not isinstance(job["id"], str)
                        or not re.fullmatch(r"ftjob-[a-zA-Z0-9_-]{1,128}", job["id"])
                        or not isinstance(job["status"], str)):
                    raise RuntimeError("Fine-tuning submission returned an invalid job ID or status.")
                state.update(phase="submitted", job=job)
                write_fine_tuning_state(state)
            except Exception as error:
                state.update(phase="uncertain", error=f"{type(error).__name__}: {error}")
                write_fine_tuning_state(state)
                raise
    return {"job": job, "submission": state}


def native_guardrails(run_id: str) -> dict:
    endpoint = os.environ.get("UI_GUARDRAILS_ENDPOINT", "").strip().rstrip("/")
    account = os.environ.get("UI_GUARDRAILS_ACCOUNT_ID", "").strip()
    project_endpoint = os.environ.get("UI_GUARDRAILS_PROJECT_ENDPOINT", "").strip().rstrip("/")
    deployment = os.environ.get("UI_GUARDRAILS_DEPLOYMENT", "gpt-4.1-mini").strip()
    agent_deployment = os.environ.get("UI_GUARDRAILS_AGENT_DEPLOYMENT", "agent-baseline").strip()
    match = re.fullmatch(
        r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/([^/]+)",
        account,
    )
    if (not match or endpoint != f"https://{match[1]}.openai.azure.com"
            or project_endpoint != f"https://{match[1]}.services.ai.azure.com/api/projects/native-guardrails"
            or not deployment or not agent_deployment):
        raise RuntimeError("Set the matching lab 38 account, project and deployment settings.")
    lab = load_demo("38-native-guardrails")
    config = {"endpoint": endpoint, "deployment": deployment, "account": account}
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        model = lab.run(config=config, credential=credential)
        agent = lab.run_agent(config=config, project_endpoint=project_endpoint, deployment=agent_deployment,
                              credential=credential, agent_name=f"native-guardrails-{run_id[:12]}")
    return {"status": "PASS" if model["status"] == agent["status"] == "PASS" else "BLOCKED",
            "model": model, "agent": agent}


def document_intelligence(document_id: str) -> dict:
    endpoint = os.environ.get("UI_DOCUMENT_PROJECT_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_DOCUMENT_PROJECT_ENDPOINT to the lab 34 project HTTPS endpoint.")
    lab = load_demo("34-document-intelligence")
    if document_id not in lab.EXPECTED:
        raise ValueError("Choose an approved synthetic invoice.")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential, AIProjectClient(endpoint=endpoint, credential=credential) as project:
        with project.get_openai_client(agent_name=lab.AGENT, max_retries=0) as client:
            response = lab.request_invoice(client, document_id)
    if response.get("status") != "completed" or response.get("error"):
        raise RuntimeError(f"Document agent failed: {response.get('status')}; {response.get('error')}")
    extraction = lab.tool_extraction(response, document_id)
    answer = lab.validate_answer(lab.response_text(response), extraction)
    return {"answer": answer, "extraction": extraction, "response_id": response.get("id"),
            "usage": response.get("usage")}


def voice_agent(run_id: str) -> dict:
    endpoint = os.environ.get("UI_VOICE_PROJECT_ENDPOINT", "").strip()
    model = os.environ.get("UI_VOICE_MODEL", "gpt-realtime").strip()
    if not endpoint.startswith("https://") or model not in ("gpt-realtime", "gpt-realtime-2.1"):
        raise RuntimeError("Set the lab 40 project HTTPS endpoint and a supported managed voice model.")
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid voice job ID.")
    lab = load_demo("40-voice-agent")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    return lab.live(endpoint=endpoint, model=model, agent_name=f"workshop-voice-{run_id[:12]}",
                    credential=credential, state_dir=job_path().parent / "voice" / run_id)


def fleet_governance(run_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid fleet job ID.")
    try:
        config = json.loads(os.environ.get("UI_FLEET_CONFIGURATION", ""))
    except ValueError as error:
        raise RuntimeError("Configure the lab 46 fleet endpoints and resource IDs.") from error
    if not isinstance(config, dict):
        raise RuntimeError("The lab 46 fleet configuration must be an object.")
    lab = load_demo("46-fleet-governance")
    group = config.pop("resourceGroup", None)
    config = lab.validate_configuration(config, group)
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        return lab.run(config, credential, state_dir=job_path().parent / "fleet" / run_id,
                       register_global=False)


def native_tool_search(run_id: str) -> dict:
    endpoint = os.environ.get("UI_TOOL_SEARCH_PROJECT_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set the lab 42 project HTTPS endpoint.")
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid tool-search job ID.")
    lab = load_demo("42-managed-harness-tool-search")
    report = {"tool_search_status": "NOT_RUN", "harness_status": "NOT_RUN"}
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential, AIProjectClient(endpoint, credential, allow_preview=True,
                                     connection_timeout=30, read_timeout=180, retry_total=0) as project:
        asyncio.run(asyncio.wait_for(
            lab.toolbox_search(project, credential, endpoint, report,
                               state_dir=job_path().parent / "tool-search" / run_id),
            timeout=180,
        ))
    if report["tool_search_status"] != "PASS" or not report.get("toolbox_deleted"):
        raise RuntimeError("Native tool search or temporary Toolbox cleanup did not complete.")
    return report


def evaluator_breadth(run_id: str) -> dict:
    endpoint = os.environ.get("UI_EVALUATOR_ENDPOINT", "").strip().rstrip("/")
    project_endpoint = os.environ.get("UI_EVALUATOR_PROJECT_ENDPOINT", "").strip().rstrip("/")
    deployment = os.environ.get("UI_EVALUATOR_DEPLOYMENT", "gpt-4.1-mini").strip()
    match = re.fullmatch(r"https://([a-z0-9-]+)\.openai\.azure\.com", endpoint)
    project_match = re.fullmatch(
        r"https://([a-z0-9-]+)\.services\.ai\.azure\.com/api/projects/[a-zA-Z0-9][a-zA-Z0-9_-]*",
        project_endpoint,
    )
    if not match or not project_match or match[1] != project_match[1] or not deployment:
        raise RuntimeError("Set the matching lab 44 model endpoint, project endpoint and deployment.")
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid evaluator job ID.")
    lab = load_demo("44-evaluation-optimizer")
    report = {"dataset_status": "NOT_RUN", "evaluator_status": "NOT_RUN", "optimizer_status": "NOT_RUN"}
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential, AIProjectClient(project_endpoint, credential, allow_preview=True,
                                     connection_timeout=30, read_timeout=120, retry_total=2) as project:
        lab.cloud_evaluation(project, {"endpoint": endpoint, "deployment": deployment}, report,
                             credential=credential, state_dir=job_path().parent / "evaluator" / run_id)
    if report["evaluator_status"] != "PASS":
        raise RuntimeError("Native cloud evaluator evidence is incomplete.")
    return report


def native_memory(run_id: str) -> dict:
    endpoint = os.environ.get("UI_MEMORY_PROJECT_ENDPOINT", "").strip().rstrip("/")
    deployment = os.environ.get("UI_MEMORY_DEPLOYMENT", "gpt-4.1-mini").strip()
    embedding = os.environ.get("UI_MEMORY_EMBEDDING", "text-embedding-3-small").strip()
    if not re.fullmatch(r"https://[a-zA-Z0-9-]+\.services\.ai\.azure\.com/api/projects/[a-zA-Z0-9_-]+", endpoint):
        raise RuntimeError("Set the lab 31 project HTTPS endpoint.")
    if not deployment or not embedding:
        raise RuntimeError("Set the lab 31 chat and embedding deployments.")
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Invalid memory job ID.")
    lab = load_demo("31-native-agent-memory")
    hosted = os.environ.get("UI_AZURE_HOSTED") == "1"
    if hosted:
        identity = lab.Identity(os.environ["UI_PRESENTER_TENANT_ID"], os.environ["UI_PRESENTER_OBJECT_ID"])
    else:
        tenant = subprocess.check_output(
            ["az", "account", "show", "--query", "tenantId", "-o", "tsv"], text=True, timeout=30,
        ).strip()
        config_dir = Path(os.environ.get("AZURE_CONFIG_DIR", "~/.azure")).expanduser().resolve()
        identity = lab.authenticated_identity(config_dir, tenant)
    credential = ManagedIdentityCredential() if hosted else AzureCliCredential()
    with credential, AIProjectClient(endpoint, credential, allow_preview=True,
                                     connection_timeout=15, read_timeout=lab.REQUEST_TIMEOUT,
                                     retry_total=0) as project:
        return lab.demonstrate_presenter(project, deployment, embedding, identity, f"ui-memory-{run_id}")


def monitoring_settings(lab) -> tuple[str, dict]:
    endpoint = os.environ.get("UI_MONITORING_PROJECT_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set the lab 37 project HTTPS endpoint.")
    try:
        config = json.loads(os.environ.get("UI_MONITORING_CONFIGURATION", ""))
    except ValueError as error:
        raise RuntimeError("Configure the lab 37 agent versions and evaluation IDs.") from error
    if not isinstance(config, dict) or not isinstance(config.get("agents"), dict) or set(config["agents"]) != set(lab.NAMES):
        raise RuntimeError("The lab 37 configuration must contain both deployed agents.")
    for name in lab.NAMES:
        agent = config["agents"][name]
        if (not isinstance(agent, dict) or not agent.get("good_version") or not agent.get("eval_id")
                or (name == lab.NAMES[0] and not agent.get("bad_version"))):
            raise RuntimeError(f"The lab 37 configuration is incomplete for {name}.")
    return endpoint, config


def monitoring_traffic(mode: str) -> dict:
    lab = load_demo("37-production-monitoring")
    endpoint, config = monitoring_settings(lab)
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential, AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project:
        try:
            records = lab.project_traffic(project, config, mode)
        finally:
            if mode == "regression":
                lab.set_project_mode(project, config, "recovery")
        return {"requests": records, "regression_route_restored": mode == "regression"}


def monitoring_info(action: str) -> dict:
    if action not in ("evaluations", "traces"):
        raise ValueError("Choose evaluations or traces.")
    lab = load_demo("37-production-monitoring")
    endpoint, config = monitoring_settings(lab)
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        if action == "traces":
            workspace = os.environ.get("UI_MONITORING_WORKSPACE_ID", "").strip()
            if not re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", workspace):
                raise RuntimeError("Set the lab 37 Log Analytics workspace ID.")
            return lab.query_telemetry(credential, workspace)
        with AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project:
            return {"evaluations": lab.project_evaluations(project, config)}


def evaluation_gate(variant: str) -> dict:
    endpoint = os.environ.get("UI_CICD_PROJECT_ENDPOINT", "").strip()
    deployment = os.environ.get("UI_CICD_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not endpoint.startswith("https://") or not deployment:
        raise RuntimeError("Set the lab 23 project HTTPS endpoint and deployment.")
    lab = load_demo("23-evaluation-cicd")
    dataset = json.loads((lab.HERE / "dataset.json").read_text(encoding="utf-8"))
    prompt = (lab.HERE / "prompts" / f"{variant}.txt").read_text(encoding="utf-8")
    if not dataset or len({item["id"] for item in dataset}) != len(dataset):
        raise RuntimeError("Dataset must be nonempty and have unique IDs.")
    args = SimpleNamespace(deployment=deployment, judge_deployment=deployment, timeout=600,
                           correctness_threshold=1.0, coherence_threshold=1.0)
    report = {"prompt_variant": variant, "passed": False}
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        with AIProjectClient(endpoint=endpoint, credential=credential) as project:
            lab.execute(project, args, dataset, prompt, report, raise_on_failure=False)
    if isinstance(report.get("cloud_run"), dict):
        report["cloud_run"].pop("created_by", None)
    return report


def run_job(job: dict) -> None:
    global job_write_error
    try:
        try:
            if job["kind"] == "skill-release":
                result = release_governance(job["regression"])
            elif job["kind"] == "agent-evaluation":
                result = agent_evaluation()
            elif job["kind"] == "evaluation-gate":
                result = evaluation_gate(job["variant"])
            elif job["kind"] == "csv-analyst":
                result = csv_analyst()
            elif job["kind"] == "hosted-agent":
                result = hosted_agent(job["attendees"], job["session_id"])
            elif job["kind"] == "rag":
                result = rag_case(job["chunk_words"], job["mode"], job["question_index"], job["index_name"])
            elif job["kind"] == "content-understanding":
                result = content_understanding(job["id"])
            elif job["kind"] == "monitoring":
                result = monitoring_traffic(job["mode"])
            elif job["kind"] == "semantic-cache":
                result = semantic_cache()
            elif job["kind"] == "agent-lifecycle":
                result = agent_lifecycle(job["id"])
            elif job["kind"] == "fine-tuning":
                result = fine_tuning("submit", {"confirm_cost": True})
            elif job["kind"] == "native-guardrails":
                result = native_guardrails(job["id"])
            elif job["kind"] == "document-intelligence":
                result = document_intelligence(job["document_id"])
            elif job["kind"] == "voice-agent":
                result = voice_agent(job["id"])
            elif job["kind"] == "fleet-governance":
                result = fleet_governance(job["id"])
            elif job["kind"] == "native-tool-search":
                result = native_tool_search(job["id"])
            elif job["kind"] == "evaluator-breadth":
                result = evaluator_breadth(job["id"])
            elif job["kind"] == "native-memory":
                result = native_memory(job["id"])
            else:
                raise RuntimeError("Unknown workshop job kind.")
            final = {**job, "status": "completed", "result": result}
        except Exception as error:
            traceback.print_exc()
            final = {**job, "status": "failed", "error": f"{type(error).__name__}: {error}"}
        with job_lock:
            try:
                write_job(final)
            except OSError as error:
                traceback.print_exc()
                job_write_error = str(error)
    finally:
        operation.release()


def start_job(kind: str, data: dict) -> dict:
    if kind not in ("skill-release", "agent-evaluation", "evaluation-gate", "csv-analyst",
                    "hosted-agent", "rag", "content-understanding", "monitoring", "semantic-cache", "agent-lifecycle",
                    "fine-tuning", "native-guardrails", "document-intelligence", "voice-agent",
                    "fleet-governance", "native-tool-search", "evaluator-breadth", "native-memory"):
        raise ValueError("Unknown workshop job kind.")
    regression = data.get("regression", False) if kind == "skill-release" else False
    if type(regression) is not bool:
        raise ValueError("Regression must be true or false.")
    variant = data.get("variant") if kind == "evaluation-gate" else None
    if kind == "evaluation-gate" and variant not in ("baseline", "regression"):
        raise ValueError("Choose the baseline or regression prompt.")
    if kind == "hosted-agent":
        attendees, session_id = data.get("attendees"), data.get("session_id")
        if type(attendees) is not int or not 1 <= attendees <= 20:
            raise ValueError("Attendees must be an integer from 1 to 20.")
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", session_id):
            raise ValueError("Invalid hosted-agent session ID.")
    if kind == "rag":
        chunk_words, mode, question_index = data.get("chunk_words"), data.get("mode"), data.get("question_index")
        if type(chunk_words) is not int or chunk_words not in (12, 48):
            raise ValueError("Choose a 12- or 48-word chunk.")
        if mode not in ("keyword", "vector", "hybrid"):
            raise ValueError("Choose keyword, vector, or hybrid retrieval.")
        if type(question_index) is not int or not 0 <= question_index < 4:
            raise ValueError("Choose a valid labeled question.")
    if kind == "monitoring" and data.get("mode") not in ("baseline", "regression", "recovery"):
        raise ValueError("Choose a valid monitoring scenario.")
    if kind == "fine-tuning" and data.get("confirm_cost") is not True:
        raise ValueError("Confirm that a new paid training job will be submitted.")
    if kind == "document-intelligence":
        document_id = data.get("document_id")
        if not isinstance(document_id, str) or document_id not in load_demo("34-document-intelligence").EXPECTED:
            raise ValueError("Choose an approved synthetic invoice.")
    with job_lock:
        previous = read_job()
        if previous["status"] == "running":
            raise RuntimeError("A workshop job is already running.")
        if previous["status"] == "interrupted" and data.get("acknowledge_interrupted") is not True:
            raise ValueError("Inspect the interrupted run's Azure resources and acknowledge before starting another.")
        job = {"id": uuid4().hex, "status": "running", "kind": kind, "regression": regression}
        if variant is not None:
            job["variant"] = variant
        if kind == "hosted-agent":
            job.update(attendees=attendees, session_id=session_id)
        if kind == "rag":
            job.update(chunk_words=chunk_words, mode=mode, question_index=question_index,
                       index_name=f"ui-rag-{job['id'][:12]}")
        if kind == "monitoring":
            job["mode"] = data["mode"]
        if kind == "agent-lifecycle":
            job.update(application_name=f"workshop-app-{job['id'][:8]}",
                       agent_name=f"workshop-{job['id'][:8]}")
        if kind == "native-guardrails":
            job["agent_name"] = f"native-guardrails-{job['id'][:12]}"
        if kind == "document-intelligence":
            job["document_id"] = document_id
        if kind == "voice-agent":
            job["agent_name"] = f"workshop-voice-{job['id'][:12]}"
        if kind == "native-memory":
            job["store_name"] = f"ui-memory-{job['id']}"
        write_job(job)
        try:
            Thread(target=run_job, args=(job,), daemon=True).start()
        except Exception as error:
            job.update(status="failed", error=f"Could not start worker: {error}")
            write_job(job)
            raise
    return job.copy()


def orchestration_pattern(data: dict) -> list:
    pattern = data.get("pattern")
    if pattern not in ("sequential", "concurrent", "group-chat", "magentic"):
        raise ValueError("Choose a valid orchestration pattern.")
    endpoint = os.environ.get("UI_ORCHESTRATION_PROJECT_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_ORCHESTRATION_PROJECT_ENDPOINT to the lab 13 project HTTPS endpoint.")
    deployment = os.environ.get("UI_ORCHESTRATION_DEPLOYMENT", "gpt-4.1-mini").strip()
    if not deployment:
        raise RuntimeError("UI_ORCHESTRATION_DEPLOYMENT must not be blank.")
    lab = load_demo("13-agent-orchestrations", package=True)
    credential = AsyncManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AsyncAzureCliCredential()
    return asyncio.run(lab.run_samples(endpoint, deployment, pattern, credential, timeout=180))


def model_basics(data: dict) -> dict:
    basics = load_demo("0-model-basics")
    mode = data.get("mode")
    if mode not in ("concepts", "sampling", "compare", "batch"):
        raise ValueError("Choose concepts, sampling, compare, or batch.")
    temperature, top_p = data.get("temperature", 1), data.get("top_p", 1)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           for value in (temperature, top_p)):
        raise ValueError("Temperature and top-p must be numbers.")
    if mode == "concepts":
        probabilities = [0.5, 0.3, 0.15, 0.05]
        return {
            "note": "Synthetic teaching probabilities, not model probabilities.",
            "probabilities": probabilities,
            "temperature_distribution": basics.temperature_distribution(probabilities, temperature),
            "nucleus_indexes": basics.nucleus(probabilities, top_p),
        }
    endpoint = os.environ.get("UI_BASICS_ENDPOINT", "").strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_BASICS_ENDPOINT to the lab 0 Azure OpenAI HTTPS endpoint.")
    hosted = os.environ.get("UI_AZURE_HOSTED") == "1"
    credential = ManagedIdentityCredential() if hosted else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token,
                    max_retries=0 if mode == "batch" else basics.CHAT_MAX_RETRIES,
                    timeout=120) as client:
            if mode == "sampling":
                return basics.ui_sampling(client, os.environ.get("UI_BASICS_CHAT", "basics-chat"),
                                          temperature, top_p)
            if mode == "compare":
                return basics.ui_compare(client, os.environ.get("UI_BASICS_CHAT", "basics-chat"),
                                         os.environ.get("UI_BASICS_REASONING", "basics-reasoning"),
                                         data.get("effort", "medium"))
            state = Path(os.environ.get("UI_BATCH_STATE_DIR", str(HERE.parent / "0-model-basics" / ".azure" / "ui-batch")))
            return basics.run_batch(client, endpoint, os.environ.get("UI_BASICS_BATCH", "basics-batch"),
                                    state, None, 0, allow_pending=True)


def route_model(data: dict) -> dict:
    router = load_demo("1-model-router")
    preset = data.get("preset")
    if preset == "Custom":
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
            raise ValueError("Custom prompt must be 1-4000 characters.")
        prompt = prompt.strip()
    else:
        prompts = dict(router.PROMPTS)
        if not isinstance(preset, str) or preset not in prompts:
            raise ValueError("Choose a valid router prompt.")
        prompt = prompts[preset]
    endpoint = os.environ.get("UI_ROUTER_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_ROUTER_ENDPOINT to the lab 1 Azure OpenAI HTTPS endpoint.")
    hosted = os.environ.get("UI_AZURE_HOSTED") == "1"
    credential = ManagedIdentityCredential() if hosted else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with AzureOpenAI(azure_endpoint=endpoint, azure_ad_token_provider=token,
                         api_version="2024-10-21", timeout=120, max_retries=0) as client:
            return router.route(client, os.environ.get("UI_ROUTER_DEPLOYMENT", "model-router"), prompt)


def decision_model(data: dict) -> dict:
    decision = load_demo("5-decision-model")
    selection = data.get("selection")
    if selection == "custom":
        ticket = data.get("ticket")
        if not isinstance(ticket, str) or not 1 <= len(ticket.strip()) <= 4000:
            raise ValueError("Custom ticket must be 1-4000 characters.")
        ticket, expected = ticket.strip(), None
    else:
        examples = {expected: ticket for ticket, expected in decision.EXAMPLES}
        if not isinstance(selection, str) or selection not in examples:
            raise ValueError("Choose a valid example ticket.")
        ticket, expected = examples[selection], selection
    endpoint = os.environ.get("UI_DECISION_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_DECISION_ENDPOINT to the lab 5 HTTPS endpoint.")
    hosted = os.environ.get("UI_AZURE_HOSTED") == "1"
    credential = ManagedIdentityCredential() if hosted else AzureCliCredential()
    with credential:
        token = credential.get_token("https://cognitiveservices.azure.com/.default").token
        with httpx.Client(base_url=endpoint.rstrip("/") + "/",
                          headers={"Authorization": f"Bearer {token}"}, timeout=60) as client:
            started = perf_counter()
            team = decision.classify(client, os.environ.get("UI_DECISION_DEPLOYMENT", "decision"), ticket)
            elapsed = perf_counter() - started
    return {"ticket": ticket, "team": team, "expected": expected, "seconds": elapsed}


def content_safety(data: dict) -> dict:
    text, threshold = data.get("text"), data.get("threshold")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise ValueError("Text must be 1-4000 characters.")
    if type(threshold) is not int or threshold not in (2, 4, 6):
        raise ValueError("Threshold must be 2, 4, or 6.")
    endpoint = os.environ.get("UI_SAFETY_ENDPOINT", "").strip()
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_SAFETY_ENDPOINT to the lab 6 HTTPS endpoint.")
    safety = load_demo("6-content-safety")
    hosted = os.environ.get("UI_AZURE_HOSTED") == "1"
    credential = ManagedIdentityCredential() if hosted else AzureCliCredential()
    with credential:
        with safety.ContentSafetyClient(endpoint, credential) as client:
            return safety.analyze_result(client, text.strip(), threshold)


def prompt_injection(data: dict) -> dict:
    source = data.get("source")
    if source not in ("retrieved-document", "tool-output"):
        raise ValueError("Choose a valid synthetic source.")
    endpoint = os.environ.get("UI_INJECTION_ENDPOINT", "").strip().rstrip("/")
    safety_endpoint = os.environ.get("UI_INJECTION_SAFETY_ENDPOINT", "").strip()
    if not endpoint.startswith("https://") or not safety_endpoint.startswith("https://"):
        raise RuntimeError("Set the lab 22 model and Prompt Shields HTTPS endpoints.")
    lab = load_demo("22-prompt-injection")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token, timeout=45, max_retries=0) as client:
            safety_token = credential.get_token("https://cognitiveservices.azure.com/.default").token
            return lab.evaluate_source(client, os.environ.get("UI_INJECTION_DEPLOYMENT", "gpt-4.1-mini"),
                                       source, safety_endpoint, safety_token)


def resilience_cost() -> dict:
    endpoint = os.environ.get("UI_RESILIENCE_ENDPOINT", "").strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_RESILIENCE_ENDPOINT to the lab 25 HTTPS endpoint.")
    lab = load_demo("25-resilience-cost", package=True)
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token, timeout=20, max_retries=0) as client:
            return lab.demonstrate(client, os.environ.get("UI_RESILIENCE_DEPLOYMENT", "gpt-4.1-mini"))


def benchmark_case(data: dict) -> dict:
    lab = load_demo("15-model-benchmark")
    case = next((item for item in lab.CASES if item["id"] == data.get("case")), None)
    if case is None:
        raise ValueError("Choose a valid benchmark case.")
    endpoint = os.environ.get("UI_BENCHMARK_ENDPOINT", "").strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_BENCHMARK_ENDPOINT to the lab 15 HTTPS endpoint.")
    deployments = {
        "mini": os.environ.get("UI_BENCHMARK_MINI", "benchmark-mini"),
        "full": os.environ.get("UI_BENCHMARK_FULL", "benchmark-full"),
        "router": os.environ.get("UI_BENCHMARK_ROUTER", "model-router"),
    }
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token, timeout=60, max_retries=0) as client:
            results = [
                {"slot": slot, "deployment": deployment,
                 **lab.run_one(client, deployment, case, lab.DEFAULT_RATES.get(slot))}
                for slot, deployment in deployments.items()
            ]
    if all("transport_error" in result for result in results):
        raise RuntimeError("All benchmark requests failed: " +
                           "; ".join(f"{result['slot']}: {result['transport_error']}" for result in results))
    return {"case": case, "results": results}


def structured_output(data: dict) -> dict:
    lab = load_demo("16-structured-outputs")
    mode, case_id = data.get("mode"), data.get("case")
    if mode not in ("instructions", "few-shot", "structured"):
        raise ValueError("Choose a valid prompting mode.")
    case = next((item for item in lab.CASES if item["id"] == case_id), None)
    if case is None:
        raise ValueError("Choose a valid ticket.")
    endpoint = os.environ.get("UI_STRUCTURED_ENDPOINT", "").strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_STRUCTURED_ENDPOINT to the lab 16 HTTPS endpoint.")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token, max_retries=0, timeout=60) as client:
            result = lab.call(client, os.environ.get("UI_STRUCTURED_DEPLOYMENT", "ticket-extractor"),
                              mode, case["text"], case["expected"])
    if result["status"] == "request_error":
        raise RuntimeError(result["reason"])
    return {"mode": mode, "ticket": case["text"], "expected": case["expected"], **result}


def receipt_extraction(data: dict) -> dict:
    lab = load_demo("26-multimodal")
    image = data.get("image")
    if not isinstance(image, str) or image not in lab.TRUTH:
        raise ValueError("Choose a valid synthetic receipt.")
    endpoint = os.environ.get("UI_VISION_ENDPOINT", "").strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise RuntimeError("Set UI_VISION_ENDPOINT to the lab 26 HTTPS endpoint.")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token, max_retries=0, timeout=60) as client:
            result = lab.extract(client, os.environ.get("UI_VISION_DEPLOYMENT", "receipt-vision"),
                                 HERE.parent / "26-multimodal" / "images" / image, lab.TRUTH[image])
    if result["status"] == "request_error":
        raise RuntimeError(result["reason"])
    return {"image": image, "truth": lab.TRUTH[image], **result,
            "estimated_usd": lab.estimated_cost(result["usage"], .40, 1.60)}


def conversation_state(data: dict) -> dict:
    action, user = data.get("action"), data.get("user")
    if action not in ("turn", "show", "reset", "delete") or user not in ("alice", "bob"):
        raise ValueError("Choose a valid action and logical user.")
    prompt = data.get("prompt")
    if action == "turn" and (not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000):
        raise ValueError("Prompt must be 1-2000 characters.")
    lab = load_demo("19-conversation-state")
    path = os.environ.get("UI_CONVERSATION_STATE_PATH", str(HERE.parent / "19-conversation-state" / ".state" / "ui.sqlite3"))
    store = lab.Store(path)
    thread = "workshop"
    if action == "reset":
        store.reset(user, thread)
    elif action == "delete":
        store.delete(user, thread)
    elif action == "turn":
        endpoint = os.environ.get("UI_CONVERSATION_ENDPOINT", "").strip().rstrip("/")
        if not endpoint.startswith("https://"):
            raise RuntimeError("Set UI_CONVERSATION_ENDPOINT to the lab 19 HTTPS endpoint.")
        credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
        with credential:
            token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
            with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token, max_retries=0, timeout=60) as client:
                answer = lab.turn(store, client, os.environ.get("UI_CONVERSATION_DEPLOYMENT", "gpt-4.1-mini"),
                                  user, thread, prompt.strip())
        return {"action": action, "logical_user": user, "answer": answer, "history": store.load(user, thread)}
    return {"action": action, "logical_user": user, "history": store.load(user, thread)}


def fundamentals() -> dict:
    endpoint = os.environ.get("UI_FUNDAMENTALS_ENDPOINT", "").strip().rstrip("/")
    denied_endpoint = os.environ.get("UI_FUNDAMENTALS_DENIED_ENDPOINT", "").strip().rstrip("/")
    deployment = os.environ.get("UI_FUNDAMENTALS_DEPLOYMENT", "tutor-mini").strip()
    if not endpoint.startswith("https://") or not denied_endpoint.startswith("https://") or not deployment:
        raise RuntimeError("Set the lab 14 model and denied HTTPS endpoints and deployment.")
    lab = load_demo("14-foundry-fundamentals")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token, max_retries=0, timeout=60) as client:
            response = lab.first_response(client, deployment)
            if (not response.choices or response.choices[0].finish_reason != "stop"
                    or not response.choices[0].message.content
                    or not response.choices[0].message.content.strip()
                    or response.choices[0].message.refusal):
                raise RuntimeError("The first model response was incomplete or empty.")
            chunks = []
            finishes = []
            with lab.streamed_response(client, deployment) as stream:
                def observed_stream():
                    for chunk in stream:
                        if len(chunk.choices) > 1:
                            raise RuntimeError("Unexpected multiple streaming choices.")
                        if chunk.choices and chunk.choices[0].finish_reason is not None:
                            finishes.append(chunk.choices[0].finish_reason)
                        yield chunk
                stream_usage = lab.stream_response(observed_stream(), chunks.append)
            streamed_text = "".join(chunks).strip()
            if not streamed_text or finishes != ["stop"]:
                raise RuntimeError("The streamed model response was incomplete or empty.")
            missing = lab.probe_result(client, "deliberately-missing-deployment", 404)
        with OpenAI(base_url=denied_endpoint + "/openai/v1/", api_key=token,
                    max_retries=0, timeout=60) as denied:
            permission = lab.probe_result(denied, deployment)
    return {
        "first": {"text": response.choices[0].message.content, "serving_model": response.model,
                  "usage": response.usage.model_dump() if response.usage else None},
        "stream": {"text": streamed_text, "usage": stream_usage},
        "missing_deployment": missing,
        "permission_denial": permission,
        "negative_probes_verified": missing["matched"] and permission["matched"],
    }


def hosted_presenter(headers) -> str:
    tenant = os.environ.get("UI_PRESENTER_TENANT_ID", "").strip()
    allowed = os.environ.get("UI_PRESENTER_OBJECT_ID", "").strip()
    if not tenant or not allowed:
        raise RuntimeError("Configure the hosted presenter's tenant and object IDs.")
    encoded = headers.get("X-MS-CLIENT-PRINCIPAL", "")
    try:
        if not isinstance(encoded, str) or len(encoded) > 32768:
            raise ValueError("Invalid principal header")
        principal = json.loads(base64.b64decode(encoded, validate=True))
        claims = principal["claims"]
        if not isinstance(claims, list) or not all(isinstance(claim, dict) for claim in claims):
            raise ValueError("Invalid principal claims")
        object_ids = {claim.get("val", "").lower() for claim in claims
                      if claim.get("typ") in ("oid", "http://schemas.microsoft.com/identity/claims/objectidentifier")
                      and isinstance(claim.get("val"), str)}
        tenant_ids = {claim.get("val", "").lower() for claim in claims
                      if claim.get("typ") in ("tid", "http://schemas.microsoft.com/identity/claims/tenantid")
                      and isinstance(claim.get("val"), str)}
    except (ValueError, TypeError, KeyError) as error:
        raise ApprovalPermissionError("The authenticated presenter claims are missing or invalid.") from error
    if object_ids != {allowed.lower()} or tenant_ids != {tenant.lower()}:
        raise ApprovalPermissionError("The authenticated presenter identity is missing or does not match.")
    return f"{tenant.lower()}/{allowed.lower()}"


def approval_actor(headers, credential, lab) -> str:
    if os.environ.get("UI_AZURE_HOSTED") == "1":
        return hosted_presenter(headers)
    return lab.actor_from_token(credential.get_token(lab.SCOPE).token)


def human_approval(data: dict, headers) -> dict:
    action = data.get("action")
    if action not in ("propose", "approve", "reject"):
        raise ValueError("Choose propose, approve, or reject.")
    lab = load_demo("20-human-approval")
    credential = ManagedIdentityCredential() if os.environ.get("UI_AZURE_HOSTED") == "1" else AzureCliCredential()
    with credential:
        actor = approval_actor(headers, credential, lab)
        if action == "propose":
            attendees = data.get("attendees")
            lab.validate({"workshop": "foundry-intro", "attendees": attendees})
            endpoint = os.environ.get("UI_APPROVAL_ENDPOINT", "").strip().rstrip("/")
            deployment = os.environ.get("UI_APPROVAL_DEPLOYMENT", "gpt-4.1-mini").strip()
            if not endpoint.startswith("https://") or not deployment:
                raise RuntimeError("Set the lab 20 model HTTPS endpoint and deployment.")
            token = get_bearer_token_provider(credential, lab.SCOPE)
            with OpenAI(base_url=endpoint + "/openai/v1/", api_key=token,
                        max_retries=0, timeout=45) as client:
                proposed = lab.model_action(client, deployment, attendees)
        else:
            proposal_id, digest = data.get("proposal_id"), data.get("digest")
            if (not isinstance(proposal_id, str) or not re.fullmatch(r"[a-f0-9]{32}", proposal_id)
                    or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
                raise ValueError("Supply the exact proposal ID and digest.")
        default = "/home/data/human-approval-ui/bookings.db" if os.environ.get("UI_AZURE_HOSTED") == "1" else str(HERE.parent / "20-human-approval" / ".azure" / "ui-bookings.db")
        db = lab.connect(Path(os.environ.get("UI_APPROVAL_STATE_PATH", default)))
        try:
            if action == "propose":
                proposal_id, digest = lab.propose(db, actor, proposed, time())
                return {"action": "proposed", "proposal": proposed, "proposal_id": proposal_id,
                        "digest": digest, "expires_in_seconds": 900, "booking_created": False}
            try:
                result = lab.decide(db, actor, proposal_id, digest, action == "approve", time())
            except PermissionError as error:
                raise ApprovalPermissionError(str(error)) from error
            return {"action": action, "proposal_id": proposal_id, "result": result}
        finally:
            db.close()


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, data: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self) -> bool:
        if os.environ.get("UI_AZURE_HOSTED") == "1":
            host = os.environ["WEBSITE_HOSTNAME"]
            return self.headers.get("Host") == host and self.headers.get("Origin") in (
                None, f"https://{host}",
            )
        hosts = (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
        return self.headers.get("Host") in hosts and self.headers.get("Origin") in (
            None, *(f"http://{host}" for host in hosts),
        )

    def do_GET(self) -> None:
        if not self.allowed():
            self.respond(403, b'{"error":"Loopback origin required"}')
        elif self.path == "/":
            self.respond(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path in ("/images/clear.png", "/images/ambiguous.png"):
            self.respond(200, (HERE.parent / "26-multimodal" / self.path.lstrip("/")).read_bytes(), "image/png")
        elif self.path.startswith("/guides/"):
            number = self.path.removeprefix("/guides/")
            if number not in LAB_GUIDES:
                self.respond(404, b'{"error":"Not found"}')
            else:
                self.respond(200, (HERE.parent / LAB_GUIDES[number] / "README.md").read_bytes(),
                             "text/plain; charset=utf-8")
        elif self.path.startswith("/invoices/"):
            document_id = self.path.removeprefix("/invoices/").removesuffix(".pdf")
            lab = load_demo("34-document-intelligence")
            if self.path != f"/invoices/{document_id}.pdf" or document_id not in lab.EXPECTED:
                self.respond(404, b'{"error":"Not found"}')
            else:
                self.respond(200, (lab.HERE / "samples" / f"{document_id}.pdf").read_bytes(), "application/pdf")
        elif self.path == "/voice/turn.wav":
            self.respond(200, (HERE.parent / "40-voice-agent" / "assets" / "turn.wav").read_bytes(), "audio/wav")
        elif self.path.startswith("/voice/"):
            match = re.fullmatch(r"/voice/([a-f0-9]{32})/(reply|conversation)\.wav", self.path)
            try:
                with job_lock:
                    job = read_job()
                if (not match or job.get("kind") != "voice-agent" or job.get("status") != "completed"
                        or job.get("id") != match[1]):
                    self.respond(404, b'{"error":"Not found"}')
                else:
                    path = job_path().parent / "voice" / match[1] / f"{match[2]}.wav"
                    if not path.is_file():
                        self.respond(404, b'{"error":"Not found"}')
                    else:
                        self.respond(200, path.read_bytes(), "audio/wav")
            except (OSError, RuntimeError) as error:
                traceback.print_exc()
                self.respond(502, json.dumps({"error": str(error)}).encode())
        elif self.path == "/api/job":
            try:
                with job_lock:
                    job = read_job()
                self.respond(200, json.dumps(job).encode())
            except RuntimeError as error:
                traceback.print_exc()
                self.respond(502, json.dumps({"error": str(error)}).encode())
        else:
            self.respond(404, b'{"error":"Not found"}')

    def do_POST(self) -> None:
        if not self.allowed():
            self.respond(403, b'{"error":"Loopback origin required"}')
            return
        if self.path not in ("/api/ask", "/api/skills", "/api/basics", "/api/router", "/api/decision",
                             "/api/safety", "/api/structured", "/api/receipt", "/api/conversation",
                             "/api/workflow", "/api/web-search", "/api/orchestration", "/api/injection",
                             "/api/resilience", "/api/benchmark", "/api/tools", "/api/tracing",
                             "/api/discovery", "/api/iq", "/api/release", "/api/evaluation", "/api/cicd",
                             "/api/fundamentals", "/api/approval", "/api/analyst", "/api/hosted-agent",
                             "/api/rag", "/api/content-understanding", "/api/monitoring", "/api/monitoring-info",
                             "/api/semantic-cache", "/api/agent-lifecycle", "/api/fine-tuning",
                             "/api/native-guardrails", "/api/document-intelligence", "/api/voice-agent",
                             "/api/fleet-governance", "/api/native-tool-search", "/api/evaluator-breadth",
                             "/api/native-memory"):
            self.respond(404, b'{"error":"Not found"}')
            return
        if not operation.acquire(blocking=False):
            self.respond(409, b'{"error":"Another question is running"}')
            return
        handed_off = False
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 8192:
                raise ValueError("Request body must be 1-8192 bytes")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            if self.path == "/api/basics":
                result = model_basics(data)
            elif self.path == "/api/router":
                result = route_model(data)
            elif self.path == "/api/decision":
                result = decision_model(data)
            elif self.path == "/api/safety":
                result = content_safety(data)
            elif self.path == "/api/structured":
                result = structured_output(data)
            elif self.path == "/api/receipt":
                result = receipt_extraction(data)
            elif self.path == "/api/conversation":
                result = conversation_state(data)
            elif self.path == "/api/fundamentals":
                result = fundamentals()
            elif self.path == "/api/approval":
                result = human_approval(data, self.headers)
            elif self.path == "/api/workflow":
                result = workflow_agent(data)
            elif self.path == "/api/web-search":
                result = web_search_comparison()
            elif self.path == "/api/orchestration":
                result = orchestration_pattern(data)
            elif self.path == "/api/injection":
                result = prompt_injection(data)
            elif self.path == "/api/resilience":
                result = resilience_cost()
            elif self.path == "/api/benchmark":
                result = benchmark_case(data)
            elif self.path == "/api/tools":
                result = tool_agent(data)
            elif self.path == "/api/tracing":
                result = trace_agent(data)
            elif self.path == "/api/discovery":
                result = skill_discovery(data)
            elif self.path == "/api/iq":
                result = foundry_iq(data)
            elif self.path == "/api/release":
                result = start_job("skill-release", data)
                handed_off = True
            elif self.path == "/api/evaluation":
                result = start_job("agent-evaluation", data)
                handed_off = True
            elif self.path == "/api/cicd":
                result = start_job("evaluation-gate", data)
                handed_off = True
            elif self.path == "/api/analyst":
                result = start_job("csv-analyst", data)
                handed_off = True
            elif self.path == "/api/hosted-agent":
                result = start_job("hosted-agent", data)
                handed_off = True
            elif self.path == "/api/rag":
                result = start_job("rag", data)
                handed_off = True
            elif self.path == "/api/content-understanding":
                result = start_job("content-understanding", data)
                handed_off = True
            elif self.path == "/api/monitoring":
                result = start_job("monitoring", data)
                handed_off = True
            elif self.path == "/api/monitoring-info":
                result = monitoring_info(data.get("action"))
            elif self.path == "/api/semantic-cache":
                result = start_job("semantic-cache", data)
                handed_off = True
            elif self.path == "/api/agent-lifecycle":
                result = start_job("agent-lifecycle", data)
                handed_off = True
            elif self.path == "/api/fine-tuning":
                action = data.get("action")
                if action == "submit":
                    result = start_job("fine-tuning", data)
                    handed_off = True
                else:
                    result = fine_tuning(action, data)
            elif self.path == "/api/native-guardrails":
                result = start_job("native-guardrails", data)
                handed_off = True
            elif self.path == "/api/document-intelligence":
                result = start_job("document-intelligence", data)
                handed_off = True
            elif self.path == "/api/voice-agent":
                result = start_job("voice-agent", data)
                handed_off = True
            elif self.path == "/api/fleet-governance":
                result = start_job("fleet-governance", data)
                handed_off = True
            elif self.path == "/api/native-tool-search":
                result = start_job("native-tool-search", data)
                handed_off = True
            elif self.path == "/api/evaluator-breadth":
                result = start_job("evaluator-breadth", data)
                handed_off = True
            elif self.path == "/api/native-memory":
                if os.environ.get("UI_AZURE_HOSTED") == "1":
                    hosted_presenter(self.headers)
                result = start_job("native-memory", data)
                handed_off = True
            else:
                field, limit = ("question", 4000) if self.path == "/api/ask" else ("title", 200)
                if not isinstance(data.get(field), str):
                    raise ValueError(f"Expected a {field} string")
                value = data[field].strip()
                if not 1 <= len(value) <= limit:
                    raise ValueError(f"{field.capitalize()} must be 1-{limit} characters")
                result = {"answer": ask(value)} if self.path == "/api/ask" else compare_skills(value)
            self.respond(200, json.dumps(result).encode())
        except (ValueError, UnicodeDecodeError) as error:
            self.respond(400, json.dumps({"error": str(error)}).encode())
        except ApprovalPermissionError as error:
            self.respond(403, json.dumps({"error": str(error)}).encode())
        except (AzureError, AgentFrameworkException, OpenAIError, httpx.HTTPError,
                RuntimeError, OSError, sqlite3.Error) as error:
            traceback.print_exc()
            self.respond(502, json.dumps({"error": str(error)}).encode())
        finally:
            if not handed_off:
                operation.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    hosted = os.environ.get("UI_AZURE_HOSTED") == "1"
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")) if hosted else 8027)
    args = parser.parse_args()
    recover_interrupted_job()
    server = ThreadingHTTPServer(("0.0.0.0" if hosted else "127.0.0.1", args.port), Handler)
    print(f"Lab 27 UI listening on port {server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
