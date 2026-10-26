"""Compare a Foundry prompt agent with and without the Web Search tool."""

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


def show_response(
    response: Response, *, expect_web_search: bool = True, debug_response: bool = False,
) -> None:
    searches = [item for item in response.output if item.type == "web_search_call"]
    citations = [
        annotation
        for item in response.output if item.type == "message"
        for content in item.content if content.type == "output_text"
        for annotation in content.annotations if annotation.type == "url_citation"
    ]
    print(
        f"\nResponse status: {response.status}\n"
        f"Output types: {[item.type for item in response.output]}\n"
        f"Search calls: {len(searches)}\n"
        f"Search statuses: {[search.status for search in searches]}\n"
        f"URL citations: {len(citations)}",
        flush=True,
    )
    if debug_response:
        print(f"\nRaw response JSON:\n{response.model_dump_json(indent=2)}", flush=True)
    if response.status != "completed" or not response.output_text.strip():
        raise RuntimeError(f"Agent did not complete with text: {response.status}; {response.error}")
    if not expect_web_search:
        if searches or citations:
            raise RuntimeError(
                "The no-tool agent unexpectedly returned web-search calls or URL citations. "
                "Inspect the response diagnostics and agent configuration."
            )
        print(
            f"\n{response.output_text}\n\n"
            "No Web Search tool: this run did not retrieve web sources. "
            "Any plain-text URLs are not verified search citations.",
            flush=True,
        )
        return
    if not searches or not citations:
        missing = []
        if not searches:
            missing.append("web_search_call output")
        if not citations:
            missing.append("url_citation annotations")
        raise RuntimeError(
            f"Grounding evidence missing: {', '.join(missing)}. "
            "Inspect the response diagnostics and Foundry agent trace; "
            "plain-text URLs are not structured citations. "
            "Use --debug-response to inspect the raw response."
        )
    if any(search.status != "completed" for search in searches):
        raise RuntimeError(
            f"A web-search call failed or did not complete: {[search.status for search in searches]}."
        )
    print(f"\nWeb searches: {len(searches)}\n\n{response.output_text}\n\nSources:")
    for citation in citations:
        print(f"- {citation.title}: {citation.url}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini"))
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--debug-response", action="store_true",
        help="Print the full response JSON, including on validation failure; use only non-sensitive prompts.",
    )
    args = parser.parse_args(argv)
    if not args.project_endpoint or not args.project_endpoint.startswith("https://"):
        parser.error("Set FOUNDRY_PROJECT_ENDPOINT to the Bicep projectEndpoint HTTPS URL.")
    if not args.deployment.strip() or not args.prompt.strip():
        parser.error("--deployment and --prompt must not be empty.")
    with (
        AzureCliCredential() as credential,
        AIProjectClient(endpoint=args.project_endpoint, credential=credential) as project,
    ):
        agent_name = f"webiq-demo-{uuid4().hex[:8]}"
        instructions = "Search the public web before answering. Be concise and cite sources."
        for expect_web_search, definition in (
            (True, PromptAgentDefinition(
                model=args.deployment, instructions=instructions, tools=[WebSearchTool()],
            )),
            (False, PromptAgentDefinition(
                model=args.deployment, instructions=instructions, tools=[],
            )),
        ):
            label = "WITH WebSearchTool()" if expect_web_search else "WITHOUT WebSearchTool()"
            print(f"\n=== {label} ===", flush=True)
            agent = project.agents.create_version(
                agent_name=agent_name, definition=definition,
            )
            print(f"Temporary Foundry agent: {agent.name} / {agent.version}", flush=True)
            try:
                with project.get_openai_client(agent_name=agent.name) as client:
                    response = client.responses.create(
                        input=args.prompt,
                        tool_choice="required" if expect_web_search else "none",
                        timeout=120.0,
                    )
                    show_response(
                        response, expect_web_search=expect_web_search,
                        debug_response=args.debug_response,
                    )
            finally:
                project.agents.delete_version(agent_name=agent.name, agent_version=agent.version)
                print("Temporary agent version deleted.")
        print(
            "\nComparison: the same model, instructions, and prompt were used in independent requests. "
            "With the tool, the answer has completed searches and structured citations. "
            "Without it, instructions alone cannot enable browsing: the model may answer from "
            "prior knowledge or explain that it cannot search. "
            "This comparison demonstrates source retrieval, not which answer is more accurate.",
            flush=True,
        )


if __name__ == "__main__":
    main()
