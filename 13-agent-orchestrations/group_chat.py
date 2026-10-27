"""Demonstrate the group-chat orchestration pattern with a planner and reviewer.

Participants take turns contributing to a shared conversation, so each agent can
respond to earlier suggestions rather than working on an isolated copy of the
prompt. The planner proposes or revises a two-item workshop agenda; the reviewer
checks its suitability for beginners and suggests an improvement.

A deterministic round-robin selector chooses the next speaker, rather than a
model-driven manager. The discussion stops after four assistant messages, with
a separate five-round safety cap to bound execution. Orchestration runs locally
in Agent Framework; the supplied Foundry client handles model inference.

This module only builds the workflow. The demo runner supplies PROMPT, executes
the graph, and displays the output from both participants.
"""

from agent_framework import Workflow
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState


PROMPT = "Plan a 30-minute beginner Python workshop with exactly two agenda items."


def select_speaker(state: GroupChatState) -> str:
    """Return the participant name for the current round in registration order.

    The builder registers the planner before the reviewer. Taking the current
    round modulo the participant count cycles through those names repeatedly,
    giving each agent a turn without an additional model call for selection.
    The workflow supplies the state and a nonempty collection of participants.
    """
    names = list(state.participants)
    return names[state.current_round % len(names)]


def build(client: FoundryChatClient) -> Workflow:
    """Construct the shared-discussion workflow without running model inference.

    Both agents use the supplied client, but have distinct instructions: one
    develops the agenda and the other critiques it. Their short-response limits
    keep the exchange easy to follow during a live workshop demonstration.

    The builder wires speaker selection, termination, and output collection into
    an executable Workflow, which the caller can run with PROMPT.
    """
    planner = client.as_agent(
        name="planner", instructions="Propose or revise a two-item workshop agenda. Use at most 40 words.",
    )
    reviewer = client.as_agent(
        name="reviewer",
        instructions="Review the agenda for beginners and suggest one small improvement. Use at most 40 words.",
    )
    return GroupChatBuilder(
        participants=[planner, reviewer], selection_func=select_speaker,
        # Count assistant contributions, not the initial user prompt or other
        # message roles, so the normal stopping point is four agent replies.
        termination_condition=lambda messages: sum(message.role == "assistant" for message in messages) >= 4,
        # The round cap is an independent guard; include both agents' output
        # rather than exposing only one participant's contribution.
        max_rounds=5, output_from="all",
    ).build()
