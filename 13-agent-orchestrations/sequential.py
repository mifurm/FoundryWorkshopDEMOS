"""A writer drafts a two-line invitation; an editor shortens it."""

from agent_framework import Workflow
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder


PROMPT = "Invite beginners to a free, 30-minute Python workshop on Friday."


def build(client: FoundryChatClient) -> Workflow:
    writer = client.as_agent(name="writer", instructions="Write a friendly two-line invitation.")
    editor = client.as_agent(
        name="editor",
        instructions="Shorten the writer's invitation to one sentence. Preserve facts; invent nothing.",
    )
    return SequentialBuilder(participants=[writer, editor], output_from="all").build()
