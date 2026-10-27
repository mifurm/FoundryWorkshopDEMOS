"""Demonstrate the Magentic pattern with a manager and two specialist agents.

Magentic is a manager-driven orchestration: a model-based manager plans the work,
chooses which specialist to call next, and checks progress toward the user's
goal. Unlike a fixed sequential pipeline or round-robin group chat, the next
step depends on the manager's assessment of the conversation.

Here, a title writer and a checklist writer supply the pieces of a tiny workshop
card. The manager is instructed to involve both specialists and combine their
answers into a final response. Their descriptions advertise their capabilities;
their instructions define the narrow task each specialist should perform.

Agent Framework runs the coordination locally, while the supplied Foundry client
handles model inference. Explicit round, stall, and reset limits bound the work,
but the manager's exact choices and wording can vary between runs.

This module only builds the workflow. The demo runner supplies PROMPT, executes
the graph, and displays intermediate contributions and the final output.
"""

from agent_framework import Workflow
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import MagenticBuilder


PROMPT = (
    "Create a tiny card for a beginner Python workshop: one short title and a two-item "
    "bring-along checklist. Ask the title_writer for the title and checklist_writer for "
    "the checklist, then combine their answers. No research is needed."
)


def build(client: FoundryChatClient) -> Workflow:
    """Construct the manager-led workflow without running model inference.

    All three agents share the supplied client, but have separate roles. The two
    specialists are registered as participants available to the manager; the
    manager is supplied separately as the coordinator, not as another worker.

    Intermediate output is enabled for every participant so the demo can show
    their contributions as well as the workflow's final result. Coordination
    limits keep this small example bounded even if the manager stalls or resets
    its approach. The returned Workflow is ready for the caller to run.
    """
    # Descriptions help the manager choose a suitable specialist. Instructions
    # constrain what that specialist should return once it is selected.
    title = client.as_agent(
        name="title_writer", description="Writes short workshop titles.",
        instructions="Return one short beginner Python workshop title, nothing else.",
    )
    checklist = client.as_agent(
        name="checklist_writer", description="Writes bring-along checklists.",
        instructions="Return exactly two bring-along items for a Python workshop: laptop and charger.",
    )
    # The manager combines the specialists' answers rather than following a
    # hard-coded title-then-checklist sequence. PROMPT also requests both roles.
    manager = client.as_agent(
        name="manager",
        instructions="Coordinate both specialists, then give a short final workshop card. Do not invent facts.",
    )
    return MagenticBuilder(
        participants=[title, checklist], manager_agent=manager,
        # Expose specialist contributions for the teaching demo and cap the
        # manager's coordination loop at five rounds.
        intermediate_output_from="all", max_round_count=5,
        # Bound both stalled progress and attempts to reset the approach;
        # these safeguards do not prescribe a deterministic sequence of calls.
        max_stall_count=2, max_reset_count=1,
    ).build()
