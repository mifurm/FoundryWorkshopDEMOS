"""A manager asks a title writer and checklist writer to assemble a workshop card."""

from agent_framework import Workflow
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import MagenticBuilder


PROMPT = (
    "Create a tiny card for a beginner Python workshop: one short title and a two-item "
    "bring-along checklist. Ask the title_writer for the title and checklist_writer for "
    "the checklist, then combine their answers. No research is needed."
)


def build(client: FoundryChatClient) -> Workflow:
    title = client.as_agent(
        name="title_writer", description="Writes short workshop titles.",
        instructions="Return one short beginner Python workshop title, nothing else.",
    )
    checklist = client.as_agent(
        name="checklist_writer", description="Writes bring-along checklists.",
        instructions="Return exactly two bring-along items for a Python workshop: laptop and charger.",
    )
    manager = client.as_agent(
        name="manager",
        instructions="Coordinate both specialists, then give a short final workshop card. Do not invent facts.",
    )
    return MagenticBuilder(
        participants=[title, checklist], manager_agent=manager,
        intermediate_output_from="all", max_round_count=5,
        max_stall_count=2, max_reset_count=1,
    ).build()
