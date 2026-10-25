"""Submit the simplest supervised fine-tuning job, or inspect an existing job."""

import argparse
import json
import os
from pathlib import Path
from time import monotonic, sleep

from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import OpenAI


DATA = Path(__file__).with_name("training.jsonl")
MODEL = "gpt-4.1-mini-2025-04-14"


def validate_data(path: Path) -> int:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len(rows) < 10:
        raise ValueError("Supervised fine-tuning requires at least 10 training examples.")
    for index, row in enumerate(rows, start=1):
        messages = row.get("messages")
        if not isinstance(messages, list) or len(messages) != 3:
            raise ValueError(f"Row {index}: expected system, user, and assistant messages.")
        if [message.get("role") for message in messages] != ["system", "user", "assistant"]:
            raise ValueError(f"Row {index}: invalid message roles.")
        if any(not isinstance(m.get("content"), str) or not m["content"].strip() for m in messages):
            raise ValueError(f"Row {index}: message content must be nonempty text.")
        if messages[-1]["content"] not in {"BILLING", "TECHNICAL", "ACCOUNT"}:
            raise ValueError(f"Row {index}: unsupported target label.")
    return len(rows)


def submit(client: OpenAI, model: str):
    count = validate_data(DATA)
    print(f"Uploading {count} toy examples; submitting one epoch of Global SFT.", flush=True)
    with DATA.open("rb") as training:
        uploaded = client.files.create(file=training, purpose="fine-tune")
    client.files.wait_for_processing(uploaded.id)
    return client.fine_tuning.jobs.create(
        model=model, training_file=uploaded.id, suffix="workshop",
        method={"type": "supervised", "supervised": {"hyperparameters": {"n_epochs": 1}}},
        extra_body={"trainingType": "Global"},
    )


def show_job(job) -> None:
    print(job.model_dump_json(indent=2))
    if job.status in ("failed", "cancelled"):
        raise RuntimeError(f"Fine-tuning job {job.id} ended with {job.status}: {job.error}")
    if job.status == "succeeded" and not job.fine_tuned_model:
        raise RuntimeError("Succeeded job did not return a fine-tuned model ID.")
    if job.status == "succeeded":
        print(f"Training complete. Deploy this model ID for inference: {job.fine_tuned_model}")
    else:
        print(f"Training is NOT complete. Resume with: python demo.py --job-id {job.id} --wait")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--model", default=MODEL, help="Training model ID, not an inference deployment name.")
    parser.add_argument("--job-id", help="Inspect/resume a job without submitting another paid job.")
    parser.add_argument("--wait", action="store_true", help="Poll for up to 30 minutes.")
    args = parser.parse_args()
    if not args.endpoint or not args.endpoint.startswith("https://"):
        parser.error("Set AZURE_OPENAI_ENDPOINT to the Bicep endpoint HTTPS URL.")
    if not args.model.strip() or (args.job_id is not None and not args.job_id.strip()):
        parser.error("Model and job ID must not be empty.")
    with AzureCliCredential() as credential:
        with OpenAI(
            base_url=args.endpoint.rstrip("/") + "/openai/v1/",
            api_key=get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default"),
        ) as client:
            job = client.fine_tuning.jobs.retrieve(args.job_id) if args.job_id else submit(client, args.model)
            print(f"Job ID: {job.id}", flush=True)
            deadline = monotonic() + 1800
            while args.wait and job.status not in ("succeeded", "failed", "cancelled"):
                if monotonic() >= deadline:
                    raise TimeoutError(f"Job {job.id} is still running. Resume with --job-id {job.id}.")
                print(f"Status: {job.status}", flush=True)
                sleep(15)
                job = client.fine_tuning.jobs.retrieve(job.id)
            show_job(job)


if __name__ == "__main__":
    main()
