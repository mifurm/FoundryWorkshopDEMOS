"""Use a version-pinned Foundry skill through explicit application-managed injection."""

import argparse
from collections.abc import Sequence
from io import BytesIO
import json
import os
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import CreateSkillVersionFromFilesBody, PromptAgentDefinition
from azure.identity import AzureCliCredential
from openai.types.responses import Response


SKILL_PATH = Path(__file__).parent / "skills" / "workshop-brief" / "SKILL.md"
BASE_INSTRUCTIONS = "You are a concise workshop assistant."


def skill_package(name: str) -> tuple[bytes, bytes]:
    text = SKILL_PATH.read_text(encoding="utf-8")
    # Keep the frontmatter name aligned with the unique project resource name.
    text = text.replace("name: workshop-brief\n", f"name: {name}\n", 1)
    content = text.encode("utf-8")
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("SKILL.md", content)
    return stream.getvalue(), content


def downloaded_instructions(package: bytes, expected: bytes) -> str:
    with ZipFile(BytesIO(package)) as archive:
        files = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(files) != 1 or Path(files[0].filename).name != "SKILL.md":
            raise RuntimeError("Downloaded skill must contain exactly one SKILL.md file.")
        content = archive.read(files[0])
    if content != expected:
        raise RuntimeError("Downloaded skill content differs from the uploaded version.")
    return content.decode("utf-8")


def matches_contract(text: str, title: str) -> bool:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return False
    return value == {
        "title": title,
        "before": "Sign in with Azure CLI.",
        "during": "Use synthetic data only.",
        "after": "Delete the demo resource group.",
        "skill_code": "WORKSHOP-BRIEF-V1",
    }


def show_response(
    response: Response, title: str, *, require_skill: bool, debug_response: bool,
) -> None:
    print(f"\nResponse ID: {response.id}; status: {response.status}", flush=True)
    if debug_response:
        print(response.model_dump_json(indent=2), flush=True)
    if response.status != "completed" or not response.output_text.strip():
        raise RuntimeError(f"Agent did not complete with text: {response.status}; {response.error}")
    print(response.output_text, flush=True)
    if response.usage is not None:
        print(f"Token usage: {response.usage.model_dump_json()}", flush=True)
    matched = matches_contract(response.output_text, title)
    print(f"Exact skill contract match: {matched}", flush=True)
    if require_skill and not matched:
        raise RuntimeError("Skill-enabled answer did not satisfy the exact workshop-brief contract.")


def demonstrate(
    project: AIProjectClient, deployment: str, title: str, *, debug_response: bool = False,
) -> None:
    suffix = uuid4().hex[:12]
    name = f"workshop-brief-{suffix}"
    package, expected = skill_package(name)
    with BytesIO(package) as upload:
        skill = project.beta.skills.create_from_files(
            name=name,
            content=CreateSkillVersionFromFilesBody(files=[("workshop-brief.zip", upload)]),
        )
    print(f"Created Foundry skill: {skill.name} / {skill.version}", flush=True)
    try:
        downloaded = b"".join(project.beta.skills.download_version(name, skill.version))
        instructions = downloaded_instructions(downloaded, expected)
        print(
            f"Downloaded and byte-verified pinned skill version {skill.version}.\n"
            "Direct injection by this application; no automatic discovery or script execution.",
            flush=True,
        )
        for use_skill in (False, True):
            print(f"\n=== {'WITH' if use_skill else 'WITHOUT'} workshop-brief skill ===", flush=True)
            agent = project.agents.create_version(
                agent_name=f"skill-demo-{suffix}",
                definition=PromptAgentDefinition(
                    model=deployment,
                    instructions=(
                        BASE_INSTRUCTIONS + "\n\n" + instructions if use_skill else BASE_INSTRUCTIONS
                    ),
                    tools=[],
                ),
            )
            print(f"Temporary Foundry agent: {agent.name} / {agent.version}", flush=True)
            try:
                with project.get_openai_client(agent_name=agent.name) as client:
                    response = client.responses.create(
                        input=f"Create a short workshop briefing for this title: {json.dumps(title)}",
                        tool_choice="none",
                        timeout=120.0,
                    )
                    show_response(
                        response, title, require_skill=use_skill, debug_response=debug_response,
                    )
            finally:
                project.agents.delete_version(agent_name=agent.name, agent_version=agent.version)
                print("Temporary agent version deleted.", flush=True)
        print(
            "\nBoth requests used the same model and input in independent conversations. "
            "The skill supplied the checklist and output contract, not a new executable tool. "
            "This is instruction following, not proof of general agent quality.",
            flush=True,
        )
    finally:
        project.beta.skills.delete(name=name)
        print(f"Temporary skill deleted: {name}", flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--title", default="Introduction to Microsoft Foundry")
    parser.add_argument("--debug-response", action="store_true", help="Print raw responses; use synthetic titles.")
    args = parser.parse_args(argv)
    if not args.project_endpoint or not args.project_endpoint.startswith("https://"):
        parser.error("Set FOUNDRY_PROJECT_ENDPOINT to the Bicep projectEndpoint HTTPS URL.")
    if not args.deployment.strip() or not args.title.strip():
        parser.error("--deployment and --title must not be empty.")
    with (
        AzureCliCredential() as credential,
        AIProjectClient(
            endpoint=args.project_endpoint, credential=credential, allow_preview=True,
        ) as project,
    ):
        demonstrate(project, args.deployment, args.title, debug_response=args.debug_response)


if __name__ == "__main__":
    main()
