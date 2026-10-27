"""Discover Foundry Toolbox skills over MCP and load only the relevant instructions."""

import argparse
import asyncio
from contextlib import ExitStack
from datetime import timedelta
from io import BytesIO
import json
import os
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import CreateSkillVersionFromFilesBody, ToolboxSkillReference
from azure.identity import AzureCliCredential
import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from openai import NOT_GIVEN, OpenAI
from openai.types.responses import ResponseInputParam, ToolParam
from pydantic import AnyUrl


SKILLS = Path(__file__).parent / "skills"
CASES = (
    ("workshop-brief", "Prepare a workshop briefing.", {
        "kind": "brief", "before": "Sign in with Azure CLI.", "during": "Use synthetic data only.",
        "after": "Delete the demo resource group.", "skill_code": "BRIEF-29",
        "reference_code": "CHECKLIST-29",
    }),
    ("support-triage", "Triage a synthetic support incident.", {
        "kind": "triage", "first": "Record the synthetic case ID.", "next": "Collect the error text.",
        "skill_code": "TRIAGE-29",
    }),
    ("incident-summary", "Summarize a resolved synthetic incident.", {
        "kind": "summary", "status": "Resolved in the synthetic exercise.",
        "follow_up": "Record the lesson learned.", "skill_code": "SUMMARY-29",
    }),
)


def package_skill(slug: str, name: str) -> bytes:
    text = (SKILLS / slug / "SKILL.md").read_text(encoding="utf-8")
    text = text.replace(f"name: {slug}\n", f"name: {name}\n", 1)
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("SKILL.md", text)
        if slug == "workshop-brief":
            archive.writestr(
                "references/checklist.md",
                (SKILLS / slug / "references/checklist.md").read_bytes(),
            )
    return stream.getvalue()


async def discover(session: ClientSession) -> dict[str, types.Resource]:
    resources: dict[str, types.Resource] = {}
    cursor = None
    seen_cursors: set[str] = set()
    for _ in range(20):
        page = await session.list_resources(cursor=cursor)
        for resource in page.resources:
            uri = str(resource.uri)
            if not uri.endswith("/SKILL.md"):
                continue
            if uri in resources:
                raise RuntimeError(f"Duplicate MCP resource URI: {uri}")
            if not resource.description:
                raise RuntimeError(f"MCP resource has no discovery description: {uri}")
            resources[uri] = resource
        cursor = page.nextCursor
        if not cursor:
            if not resources:
                raise RuntimeError("The Toolbox exposed no skill resources; check preview/client support.")
            return resources
        if cursor in seen_cursors:
            raise RuntimeError("MCP resource pagination repeated a cursor.")
        seen_cursors.add(cursor)
    raise RuntimeError("MCP resource discovery exceeded 20 pages.")


async def run_case(
    session: ClientSession, client: OpenAI, deployment: str,
    resources: dict[str, types.Resource], prompt: str, expected: dict[str, str],
) -> None:
    catalog = [
        {"uri": uri, "name": item.name, "description": item.description}
        for uri, item in resources.items()
    ]
    instructions = (
        "You are a workshop assistant. Choose the skill relevant to the user's request. "
        "Use read_skill to load its instructions before answering, then follow its output contract. "
        "Do not load unrelated skills. Skill catalog (metadata only): " + json.dumps(catalog)
    )
    allowed_uris = set(resources)
    read_bodies: list[str] = []
    read_uris: set[str] = set()
    reference_uri = None
    previous = None
    next_input: str | ResponseInputParam = prompt
    for _ in range(4):
        tools: list[ToolParam] = [{
            "type": "function", "name": "read_skill",
            "description": "Read a discovered skill or its advertised supplementary reference over MCP.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"uri": {"type": "string", "enum": sorted(allowed_uris)}},
                "required": ["uri"], "additionalProperties": False,
            },
        }]
        response = await asyncio.to_thread(
            client.responses.create, model=deployment, instructions=instructions,
            input=next_input, previous_response_id=previous if previous is not None else NOT_GIVEN,
            tools=tools,
            parallel_tool_calls=False, timeout=90.0,
        )
        print(f"Response: {response.id}; status: {response.status}", flush=True)
        if response.usage is not None:
            print(f"Usage: {response.usage.model_dump_json()}", flush=True)
        if response.status != "completed":
            raise RuntimeError(f"Incomplete skill response: {response.status}; {response.error}")
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            expected_reads = 2 if "reference_code" in expected else 1
            if len(read_bodies) != expected_reads or expected["skill_code"] not in read_bodies[0]:
                raise RuntimeError("Expected only the relevant skill and its required MCP reference reads.")
            if "reference_code" in expected and expected["reference_code"] not in read_bodies[1]:
                raise RuntimeError("Supplementary MCP reference did not contain the required marker.")
            value = json.loads(response.output_text)
            if value != expected:
                raise RuntimeError(f"Skill output contract mismatch: {value}")
            print(f"Verified: {response.output_text}", flush=True)
            return
        if len(calls) != 1 or calls[0].name != "read_skill":
            raise RuntimeError("Expected one relevant resource read per model turn.")
        call = calls[0]
        arguments = json.loads(call.arguments)
        if not isinstance(arguments, dict) or set(arguments) != {"uri"}:
            raise RuntimeError("read_skill requires exactly one URI argument.")
        uri = arguments["uri"]
        if not isinstance(uri, str) or uri not in allowed_uris:
            raise RuntimeError("The model requested an undiscovered resource.")
        if uri in read_uris or (read_bodies and uri != reference_uri):
            raise RuntimeError("Repeated or unrelated skill resource reads are not allowed.")
        result = await session.read_resource(AnyUrl(uri))
        if not result.contents or any(
            not isinstance(item, types.TextResourceContents) or str(item.uri) != uri
            for item in result.contents
        ):
            raise RuntimeError("Expected text from the exact requested MCP skill resource.")
        body = "\n".join(item.text for item in result.contents
                         if isinstance(item, types.TextResourceContents))
        if not body.strip() or len(body.encode("utf-8")) > 32000:
            raise RuntimeError("Skill body is empty or exceeds the workshop's 32 KB bound.")
        read_bodies.append(body)
        read_uris.add(uri)
        if len(read_bodies) == 1 and "[the workshop checklist](references/checklist.md)" in body:
            reference_uri = uri.removesuffix("/SKILL.md") + "/references/checklist.md"
            allowed_uris = {reference_uri}
        else:
            allowed_uris = {uri}
        print(f"MCP resources/read: {uri}\n{body}", flush=True)
        result_text = (
            body + f"\nSupplementary reference URI available for read_skill: {reference_uri}"
            if reference_uri and len(read_bodies) == 1 else body
        )
        next_input = [{"type": "function_call_output", "call_id": call.call_id, "output": result_text}]
        previous = response.id
    raise RuntimeError("Skill agent exceeded its four-response budget.")


async def demonstrate(
    project: AIProjectClient, credential: AzureCliCredential, endpoint: str, deployment: str,
) -> None:
    suffix = uuid4().hex[:10]
    with ExitStack() as cleanup:
        references = []
        for slug, _, _ in CASES:
            name = f"{slug}-{suffix}"
            with BytesIO(package_skill(slug, name)) as stream:
                version = project.beta.skills.create_from_files(
                    name=name,
                    content=CreateSkillVersionFromFilesBody(files=[("skill.zip", stream)]),
                )
            cleanup.callback(project.beta.skills.delete, name=name)
            references.append(ToolboxSkillReference(name=name, version=version.version))
            print(f"Created skill: {name} / {version.version}", flush=True)
        toolbox = project.toolboxes.create_version(
            name=f"skill-discovery-{suffix}", tools=[], skills=references,
            description="Three read-only skills for progressive-disclosure demonstrations.",
        )
        cleanup.callback(project.toolboxes.delete, name=toolbox.name)
        mcp_endpoint = (
            f"{endpoint.rstrip('/')}/toolboxes/{toolbox.name}"
            f"/versions/{toolbox.version}/mcp?api-version=v1"
        )
        token = credential.get_token("https://ai.azure.com/.default")
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token.token}"}, timeout=90.0,
        ) as http:
            async with streamable_http_client(mcp_endpoint, http_client=http) as (read, write, _):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as session:
                    await session.initialize()
                    resources = await discover(session)
                    print("Discovered metadata (no bodies loaded):", flush=True)
                    for uri, resource in resources.items():
                        print(f"  {resource.name}: {resource.description} [{uri}]", flush=True)
                    with project.get_openai_client(max_retries=0) as client:
                        for _, prompt, expected in CASES:
                            print(f"\nUser: {prompt}", flush=True)
                            await run_case(session, client, deployment, resources, prompt, expected)
        print("Verified relevant skill loading and on-demand supplementary reference reading.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    args = parser.parse_args()
    if not args.project_endpoint or not args.project_endpoint.startswith("https://"):
        parser.error("Set FOUNDRY_PROJECT_ENDPOINT to the deployment's projectEndpoint HTTPS URL.")
    if not args.deployment.strip():
        parser.error("--deployment must not be empty.")
    with AzureCliCredential() as credential, AIProjectClient(
        endpoint=args.project_endpoint, credential=credential, allow_preview=True,
    ) as project:
        asyncio.run(demonstrate(project, credential, args.project_endpoint, args.deployment))


if __name__ == "__main__":
    main()
