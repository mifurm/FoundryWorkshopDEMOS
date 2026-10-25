"""Run four tiny Microsoft Agent Framework orchestration examples on Foundry."""

import argparse
import asyncio
import os

from agent_framework import AgentResponse
from agent_framework.foundry import FoundryChatClient
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import AzureCliCredential

import concurrent_sample
import group_chat
import magentic
import sequential


SAMPLES = {
    "sequential": sequential, "concurrent": concurrent_sample,
    "group-chat": group_chat, "magentic": magentic,
}


def show(outputs: list[object], label: str) -> None:
    if not outputs:
        raise RuntimeError(f"{label}: no workflow output.")
    for output in outputs:
        if not isinstance(output, AgentResponse) or not output.text.strip():
            raise RuntimeError(f"{label}: expected a nonempty AgentResponse.")
        names = ", ".join(dict.fromkeys(message.author_name or "agent" for message in output.messages))
        print(f"\n[{names}]\n{output.text}")


async def run_samples(endpoint: str, deployment: str, pattern: str) -> None:
    async with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as project,
    ):
        client = FoundryChatClient(project_client=project, model=deployment)
        samples = SAMPLES if pattern == "all" else {pattern: SAMPLES[pattern]}
        for name, sample in samples.items():
            print(f"\n=== {name.upper()} ===\nTask: {sample.PROMPT}", flush=True)
            workflow = sample.build(client)
            result = await asyncio.wait_for(workflow.run(sample.PROMPT), timeout=300)
            intermediate = result.get_intermediate_outputs()
            if intermediate:
                print("\nParticipant updates:")
                show(intermediate, name)
            print("\nWorkflow output:")
            show(result.get_outputs(), name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--pattern", choices=["all", *SAMPLES], default="all")
    args = parser.parse_args()
    if not args.project_endpoint or not args.project_endpoint.startswith("https://"):
        parser.error("Set FOUNDRY_PROJECT_ENDPOINT to the Bicep projectEndpoint HTTPS URL.")
    if not args.deployment.strip():
        parser.error("Deployment must not be empty.")
    asyncio.run(run_samples(args.project_endpoint, args.deployment, args.pattern))


if __name__ == "__main__":
    main()
