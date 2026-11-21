"""Run four tiny Microsoft Agent Framework orchestration examples on Foundry."""

import argparse
import asyncio
import os

from agent_framework import AgentResponse
from agent_framework.foundry import FoundryChatClient
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

if __package__:
    from . import concurrent_sample, group_chat, magentic, sequential
else:
    import concurrent_sample
    import group_chat
    import magentic
    import sequential


SAMPLES = {
    "sequential": sequential, "concurrent": concurrent_sample,
    "group-chat": group_chat, "magentic": magentic,
}


def show(outputs: list[object], label: str) -> list[dict[str, str]]:
    if not outputs:
        raise RuntimeError(f"{label}: no workflow output.")
    result = []
    for output in outputs:
        if not isinstance(output, AgentResponse) or not output.text.strip():
            raise RuntimeError(f"{label}: expected a nonempty AgentResponse.")
        names = ", ".join(dict.fromkeys(message.author_name or "agent" for message in output.messages))
        print(f"\n[{names}]\n{output.text}")
        result.append({"authors": names, "text": output.text})
    return result


async def run_samples(
    endpoint: str, deployment: str, pattern: str,
    credential: AzureCliCredential | ManagedIdentityCredential | None = None,
    timeout: float = 300,
) -> list[dict]:
    async with (
        (credential if credential is not None else AzureCliCredential()) as active_credential,
        AIProjectClient(endpoint=endpoint, credential=active_credential) as project,
    ):
        client = FoundryChatClient(project_client=project, model=deployment)
        samples = SAMPLES if pattern == "all" else {pattern: SAMPLES[pattern]}
        results = []
        for name, sample in samples.items():
            print(f"\n=== {name.upper()} ===\nTask: {sample.PROMPT}", flush=True)
            workflow = sample.build(client)
            result = await asyncio.wait_for(workflow.run(sample.PROMPT), timeout=timeout)
            intermediate = result.get_intermediate_outputs()
            updates = []
            if intermediate:
                print("\nParticipant updates:")
                updates = show(intermediate, name)
            print("\nWorkflow output:")
            results.append({"pattern": name, "prompt": sample.PROMPT,
                            "intermediate": updates, "outputs": show(result.get_outputs(), name)})
        return results


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
