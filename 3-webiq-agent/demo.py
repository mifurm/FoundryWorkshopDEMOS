"""Ask a Foundry prompt agent to search the web and display its source citations."""

import argparse
import os
from collections.abc import Sequence
from uuid import uuid4

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, WebSearchTool
from azure.identity import AzureCliCredential
from openai.types.responses import Response


DEFAULT_PROMPT = (
    "Search Microsoft Learn for Microsoft Foundry model router. "
    "Explain what it does in three short bullets and cite the source URLs."
)


def show_response(response: Response) -> None:
    if response.status != "completed" or not response.output_text.strip():
        raise RuntimeError(f"Agent did not complete with text: {response.status}; {response.error}")
    searches = [item for item in response.output if item.type == "web_search_call"]
    citations = [
        annotation
        for item in response.output if item.type == "message"
        for content in item.content if content.type == "output_text"
        for annotation in content.annotations if annotation.type == "url_citation"
    ]
    if not searches or not citations:
        raise RuntimeError("Expected an actual web-search call and URL citations, not an ungrounded answer.")
    if any(search.status != "completed" for search in searches):
        raise RuntimeError("A web-search call failed or did not complete.")
    print(f"\nWeb searches: {len(searches)}\n\n{response.output_text}\n\nSources:")
    for citation in citations:
        print(f"- {citation.title}: {citation.url}")


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
    with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=args.project_endpoint, credential=credential) as project,
    ):
        agent = project.agents.create_version(
            agent_name=f"webiq-demo-{uuid4().hex[:8]}",
            definition=PromptAgentDefinition(
                model=args.deployment,
                instructions="Search the public web before answering. Be concise and cite sources.",
                tools=[WebSearchTool()],
            ),
        )
        print(f"Temporary Foundry agent: {agent.name} / {agent.version}", flush=True)
        try:
            with project.get_openai_client(agent_name=agent.name) as client:
                response = client.responses.create(
                    input=args.prompt, tool_choice="required", timeout=120.0,
                )
                show_response(response)
        finally:
            project.agents.delete_version(agent_name=agent.name, agent_version=agent.version)
            print("Temporary agent version deleted.")


if __name__ == "__main__":
    main()
