"""Publish an Azure Function in Foundry Toolbox and let a local agent call it."""

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from agent_framework import AgentResponse
from agent_framework.foundry import FoundryChatClient, FoundryToolbox
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import (
    OpenApiAnonymousAuthDetails,
    OpenApiFunctionDefinition,
    OpenApiToolboxTool,
)
from azure.identity.aio import AzureCliCredential


def show_response(response: AgentResponse) -> None:
    results = [
        content
        for message in response.messages
        for content in message.contents
        if content.type == "function_result"
    ]
    if not results or not response.text.strip():
        raise RuntimeError("Expected a real tool result and an agent answer.")
    for result in results:
        if result.exception or result.status == "failed":
            raise RuntimeError(f"Tool call failed: {result.exception or result.result}")
        print(f"Tool result: {result.result}")
    if not any("workshop-price-tool-v1" in str(result.result) for result in results):
        raise RuntimeError("The tool did not return the deployed pricing function's source marker.")
    print(f"\nAgent answer:\n{response.text}")


async def run_agent(endpoint: str, deployment: str, tool_endpoint: str, attendees: int) -> None:
    spec = json.loads(Path(__file__).with_name("openapi.json").read_text())
    spec["servers"] = [{"url": tool_endpoint.rstrip("/")}]
    async with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as project,
    ):
        toolbox = await project.toolboxes.create_version(
            name=f"workshop-tools-{uuid4().hex[:8]}",
            description="Read-only workshop pricing hosted in Azure Functions.",
            tools=[OpenApiToolboxTool(openapi=OpenApiFunctionDefinition(
                name="workshop",
                description="Calculate the workshop price using the hosted API.",
                spec=spec,
                auth=OpenApiAnonymousAuthDetails(),
            ))],
        )
        url = (
            f"{endpoint.rstrip('/')}/toolboxes/{toolbox.name}"
            f"/versions/{toolbox.version}/mcp?api-version=v1"
        )
        print(f"Foundry Toolbox: {toolbox.name} / {toolbox.version}", flush=True)
        try:
            async with FoundryToolbox(credential, url=url) as tools:
                client = FoundryChatClient(project_client=project, model=deployment)
                agent = client.as_agent(
                    name="workshop-assistant",
                    instructions=(
                        "Always call the workshop pricing tool. Never calculate or guess prices "
                        "yourself. Report the tool's attendee count, unit price, total, and currency."
                    ),
                    tools=[tools],
                )
                result = await agent.run(
                    f"What is the workshop price for {attendees} attendees?",
                    options={"tool_choice": "required"},
                )
                show_response(result)
        finally:
            await project.toolboxes.delete_version(name=toolbox.name, version=toolbox.version)
            print("Temporary toolbox version deleted.")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--tool-endpoint", default=os.getenv("WORKSHOP_TOOL_ENDPOINT"))
    parser.add_argument("--attendees", type=int, default=12)
    args = parser.parse_args(argv)
    for value, name in (
        (args.project_endpoint, "FOUNDRY_PROJECT_ENDPOINT (projectEndpoint output)"),
        (args.tool_endpoint, "WORKSHOP_TOOL_ENDPOINT (toolEndpoint output)"),
    ):
        if not value or not value.startswith("https://"):
            parser.error(f"Set {name} to an HTTPS URL.")
    if not args.deployment.strip():
        parser.error("--deployment must not be empty.")
    if not 1 <= args.attendees <= 999:
        parser.error("--attendees must be between 1 and 999.")
    asyncio.run(run_agent(
        args.project_endpoint, args.deployment, args.tool_endpoint, args.attendees,
    ))


if __name__ == "__main__":
    main()
