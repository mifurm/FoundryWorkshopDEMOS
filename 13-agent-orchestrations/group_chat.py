"""A planner and reviewer discuss a two-item workshop agenda for four turns."""

from agent_framework import Workflow
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState


PROMPT = "Plan a 30-minute beginner Python workshop with exactly two agenda items."


def select_speaker(state: GroupChatState) -> str:
    names = list(state.participants)
    return names[state.current_round % len(names)]


def build(client: FoundryChatClient) -> Workflow:
    planner = client.as_agent(
        name="planner", instructions="Propose or revise a two-item workshop agenda. Use at most 40 words.",
    )
    reviewer = client.as_agent(
        name="reviewer",
        instructions="Review the agenda for beginners and suggest one small improvement. Use at most 40 words.",
    )
    return GroupChatBuilder(
        participants=[planner, reviewer], selection_func=select_speaker,
        termination_condition=lambda messages: sum(message.role == "assistant" for message in messages) >= 4,
        max_rounds=5, output_from="all",
    ).build()
