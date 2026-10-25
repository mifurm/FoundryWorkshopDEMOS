"""Run a writer -> editor Agent Framework workflow against Microsoft Foundry."""

import argparse
import asyncio
import os
from collections.abc import Sequence

from agent_framework import AgentResponse
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import AzureCliCredential


DEFAULT_PROMPT = (
    "Invite Python developers to a free, 45-minute introductory Microsoft Foundry "
    "workshop on Friday. No prior AI experience is needed."
)


async def run_workflow(endpoint: str, deployment: str, prompt: str) -> None:
    async with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as project,
    ):
        client = FoundryChatClient(project_client=project, model=deployment)
        writer = client.as_agent(
            name="writer",
            instructions="Draft a friendly workshop invitation in at most 100 words.",
        )
        editor = client.as_agent(
            name="editor",
            instructions=(
                "Edit the writer's draft into a final invitation of at most 60 words. "
                "Preserve all supplied facts, invent no details, and return only the invitation."
            ),
        )
        workflow = SequentialBuilder(
            participants=[writer, editor], output_from="all"
        ).build()
        print("Workflow: writer -> editor", flush=True)
        events = await workflow.run(prompt)
        outputs = events.get_outputs()
        if len(outputs) != 2:
            raise RuntimeError(f"Expected both workflow stages; received {len(outputs)}.")
        for label, output in zip(("Writer draft", "Editor final"), outputs):
            if not isinstance(output, AgentResponse) or not output.text.strip():
                raise RuntimeError(f"{label} returned no agent text.")
            print(f"\n=== {label} ===\n{output.text}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args(argv)
    if not args.project_endpoint or not args.project_endpoint.startswith("https://"):
        parser.error("Set FOUNDRY_PROJECT_ENDPOINT to the Bicep projectEndpoint HTTPS URL.")
    if not args.deployment.strip() or not args.prompt.strip():
        parser.error("--deployment and --prompt must not be empty.")
    asyncio.run(run_workflow(args.project_endpoint, args.deployment, args.prompt))


if __name__ == "__main__":
    main()
