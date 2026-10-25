"""Two agents independently suggest a workshop title and a snack."""

from agent_framework import Workflow
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import ConcurrentBuilder


PROMPT = "We are planning a friendly beginner Python workshop."


def build(client: FoundryChatClient) -> Workflow:
    title = client.as_agent(name="title", instructions="Suggest exactly one short workshop title.")
    snack = client.as_agent(name="snack", instructions="Suggest exactly one simple vegetarian snack.")
    return ConcurrentBuilder(participants=[title, snack], output_from="all").build()
