"""Demonstrate the sequential orchestration pattern with a writer and an editor.

A sequential workflow passes work through agents in a fixed order. Here, the
writer receives the workshop request and drafts a friendly two-line invitation.
The editor then receives the preceding conversation, including the writer's
answer, and is instructed to shorten that invitation to a single sentence
without changing the facts.

This is a dependent handoff, not two independent calls with the same prompt:
the editor works from the draft produced by the writer. Unlike a manager-driven
orchestration, no model decides which agent runs next; participant order defines
the pipeline.

Agent Framework orchestrates the handoff locally, while the supplied Foundry
client handles model inference. This module only builds the workflow; the demo
runner supplies PROMPT, executes the graph, and displays both agents' output so
the draft and revision can be compared.
"""

from agent_framework import Workflow
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder


PROMPT = "Invite beginners to a free, 30-minute Python workshop on Friday."


def build(client: FoundryChatClient) -> Workflow:
    """Construct the two-stage invitation workflow without running inference.

    Both agents share the supplied client, but have distinct instructions: the
    writer creates the draft and the editor revises it. Registering them in that
    order establishes the dependency and lets the editor see the writer's reply.

    Output from both stages is exposed for the teaching demo rather than only
    the final edited invitation. The returned Workflow is ready for the caller
    to run with the workshop request.
    """
    writer = client.as_agent(name="writer", instructions="Write a friendly two-line invitation.")
    # Keep editing separate from drafting so the second stage can transform
    # the first stage's answer while retaining the original workshop details.
    editor = client.as_agent(
        name="editor",
        instructions="Shorten the writer's invitation to one sentence. Preserve facts; invent nothing.",
    )
    # Participant order defines the handoff; "all" retains both contributions
    # in the workflow output, not just the editor's final sentence.
    return SequentialBuilder(participants=[writer, editor], output_from="all").build()
