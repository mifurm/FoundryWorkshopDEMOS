"""Demonstrate the concurrent (fan-out/fan-in) orchestration pattern.

The workflow fans out the same workshop description to two independent agents:
one suggests a title, while the other suggests a vegetarian snack. Their model
calls can overlap because neither agent needs the other's answer. Unlike a
sequential workflow, there is no handoff of one agent's output to the next.

The framework coordinates the workflow locally, while Foundry handles model
inference. At fan-in, the workflow exposes both participant responses rather
than asking another agent to combine them into a single recommendation.
The demo runner builds this graph, runs it with PROMPT, and prints the outputs.
"""

from agent_framework import Workflow
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import ConcurrentBuilder


PROMPT = "We are planning a friendly beginner Python workshop."


def build(client: FoundryChatClient) -> Workflow:
    """Build, but do not execute, a two-participant concurrent workflow.

    Args:
        client: Foundry chat client configured by the caller. Both agents use
            this client for inference but have distinct names and instructions.

    Returns:
        A workflow that sends its input to both agents independently and exposes
        both responses. The caller supplies the input when running the workflow.
    """
    # Give each branch a separate responsibility; both interpret the original
    # workshop description rather than consuming another participant's result.
    title = client.as_agent(name="title", instructions="Suggest exactly one short workshop title.")
    snack = client.as_agent(name="snack", instructions="Suggest exactly one simple vegetarian snack.")
    # Keep both answers as workflow outputs. This fan-in collects responses;
    # it does not add a summarizer or make one participant depend on the other.
    return ConcurrentBuilder(participants=[title, snack], output_from="all").build()
