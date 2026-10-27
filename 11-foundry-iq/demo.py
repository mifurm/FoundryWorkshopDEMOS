"""A tiny Foundry IQ knowledge base, following Microsoft Learn's retrieval quickstart."""

import argparse
import json
import os
import re
from time import monotonic, sleep

import httpx
from azure.identity import AzureCliCredential


API_VERSION = "2026-08-01-preview"
INDEX = "workshop-index"
SOURCE = "workshop-source"
BASE = "workshop-knowledge"
DOCUMENTS = [
    {"id": "schedule", "title": "Workshop schedule",
     "content": "The beginner Microsoft Foundry workshop is on Friday at 10:00. It lasts 45 minutes."},
    {"id": "equipment", "title": "What to bring",
     "content": "Bring a laptop with Python 3.11 and Azure CLI installed. No prior AI experience is required."},
    {"id": "cost", "title": "Workshop registration",
     "content": "Attendance at the workshop is free. Register with the workshop organizer before Thursday."},
]
QUESTION = "When is the workshop, how long does it last, and what should I bring?"


def configure(client: httpx.Client, model_endpoint: str, deployment: str) -> None:
    schema = {
        "name": INDEX,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "content", "type": "Edm.String", "searchable": True},
        ],
        "semantic": {"defaultConfiguration": "workshop-semantic", "configurations": [{
            "name": "workshop-semantic",
            "prioritizedFields": {
                "titleField": {"fieldName": "title"},
                "prioritizedContentFields": [{"fieldName": "content"}],
            },
        }]},
    }
    client.put(f"indexes/{INDEX}", json=schema).raise_for_status()
    upload = client.post(f"indexes/{INDEX}/docs/index", json={
        "value": [{"@search.action": "mergeOrUpload", **document} for document in DOCUMENTS],
    })
    upload.raise_for_status()
    results = upload.json().get("value", [])
    if len(results) != len(DOCUMENTS) or any(item.get("status") is not True for item in results):
        raise RuntimeError(f"Document indexing failed: {results}")
    # Indexing is asynchronous: wait for visibility, not an arbitrary long delay.
    deadline = monotonic() + 60
    while True:
        response = client.get(f"indexes/{INDEX}/docs/$count")
        response.raise_for_status()
        if int(response.text) == len(DOCUMENTS):
            break
        if monotonic() >= deadline:
            raise TimeoutError("Documents are not yet searchable; rerun after indexing completes.")
        sleep(2)
    client.put(f"knowledgesources/{SOURCE}", json={
        "name": SOURCE, "kind": "searchIndex",
        "description": "Three synthetic workshop fact sheets.",
        "searchIndexParameters": {
            "searchIndexName": INDEX,
            "sourceDataFields": [{"name": "id"}, {"name": "title"}, {"name": "content"}],
        },
    }).raise_for_status()
    client.put(f"knowledgebases/{BASE}", json={
        "name": BASE, "knowledgeSources": [{"name": SOURCE}],
        "models": [{"kind": "azureOpenAI", "azureOpenAIParameters": {
            "resourceUri": model_endpoint, "deploymentId": deployment, "modelName": "gpt-4.1-mini",
        }}],
        "outputMode": "answerSynthesis",
        "answerInstructions": "Answer in two short sentences using only the retrieved workshop facts. Cite sources.",
    }).raise_for_status()


def retrieve(client: httpx.Client, question: str) -> dict:
    response = client.post(f"knowledgebases/{BASE}/retrieve", json={
        "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
        "knowledgeSourceParams": [{
            "kind": "searchIndex", "knowledgeSourceName": SOURCE,
            "includeReferences": True, "includeReferenceSourceData": True, "alwaysQuerySource": True,
        }],
        "includeActivity": True, "retrievalReasoningEffort": {"kind": "low"},
    })
    response.raise_for_status()
    result = response.json()
    text = "\n".join(
        content["text"] for message in result.get("response", [])
        for content in message.get("content", []) if content.get("type") == "text"
    )
    if not text.strip() or not result.get("references") or not result.get("activity"):
        raise RuntimeError("Retrieval returned no grounded answer, references, or activity.")
    if any(item.get("error") for item in result["activity"]):
        raise RuntimeError(f"Retrieval activity contains an error: {result['activity']}")
    print(f"\nAnswer:\n{text}\n\nRetrieval activity:")
    print(json.dumps(result["activity"], indent=2))
    print("\nSource references:")
    print(json.dumps(result["references"], indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-endpoint", default=os.getenv("AZURE_SEARCH_ENDPOINT"))
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
                        help="Foundry project endpoint for display; retrieval still uses Azure AI Search.")
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args()
    if any(not value or not value.startswith("https://")
           for value in (args.search_endpoint, args.endpoint)):
        parser.error("Set AZURE_SEARCH_ENDPOINT and AZURE_OPENAI_ENDPOINT to the Bicep HTTPS outputs.")
    if not args.deployment.strip() or not args.question.strip():
        parser.error("Deployment and question must not be empty.")
    if args.project_endpoint is not None and not re.fullmatch(
        r"https://[a-zA-Z0-9-]+\.services\.ai\.azure\.com/api/projects/[a-zA-Z0-9_.-]+/?",
        args.project_endpoint,
    ):
        parser.error("Use the Bicep projectEndpoint output, not the model or Search endpoint.")
    if args.project_endpoint:
        print(f"Foundry project: {args.project_endpoint}", flush=True)
    with AzureCliCredential() as credential:
        token = credential.get_token("https://search.azure.com/.default").token
        with httpx.Client(
            base_url=args.search_endpoint.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"},
            params={"api-version": API_VERSION}, timeout=120,
        ) as client:
            configure(client, args.endpoint, args.deployment)
            print(f"Knowledge base: {BASE}; source: {SOURCE}; index: {INDEX}", flush=True)
            retrieve(client, args.question)


if __name__ == "__main__":
    main()
