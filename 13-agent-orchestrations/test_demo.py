import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from agent_framework import AgentResponse, BaseChatClient, ChatResponse, Message

import concurrent_sample
import demo
import group_chat
import magentic
import sequential


class LocalChatClient(BaseChatClient):
    """Offline responses for executing the actual framework graphs."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.specialists = 0

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls.append(list(messages))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        instructions = options.get("instructions", "")
        last = messages[-1].text
        if "is_request_satisfied" in last:
            completed = self.specialists >= 2
            speaker = "title_writer" if self.specialists == 0 else "checklist_writer"
            text = json.dumps({
                "is_request_satisfied": {"reason": "test", "answer": completed},
                "is_in_loop": {"reason": "test", "answer": False},
                "is_progress_being_made": {"reason": "test", "answer": True},
                "next_speaker": {"reason": "test", "answer": speaker},
                "instruction_or_question": {"reason": "test", "answer": "Please provide your contribution."},
            })
        elif "Return one short beginner" in instructions or "Return exactly two bring-along" in instructions:
            self.specialists += 1
            text = "Python Together" if self.specialists == 1 else "Laptop and charger"
        else:
            text = "A short workshop answer."
        return ChatResponse(messages=[Message(role="assistant", contents=[text])], finish_reason="stop")


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_passes_writer_answer_to_editor(self):
        client = LocalChatClient()
        result = await sequential.build(client).run(sequential.PROMPT)
        self.assertEqual(len(result.get_outputs()), 2)
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(any(message.role == "assistant" for message in client.calls[1]))
        demo.show(result.get_outputs(), "sequential")

    async def test_concurrent_runs_independently_with_overlapping_calls(self):
        client = LocalChatClient()
        result = await concurrent_sample.build(client).run(concurrent_sample.PROMPT)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.max_active, 2)
        self.assertTrue(all(not any(message.role == "assistant" for message in messages)
                            for messages in client.calls))
        demo.show(result.get_outputs(), "concurrent")

    async def test_group_chat_is_bounded_and_shares_conversation(self):
        client = LocalChatClient()
        result = await group_chat.build(client).run(group_chat.PROMPT)
        self.assertEqual(len(client.calls), 4)
        self.assertTrue(any(message.role == "assistant" for message in client.calls[-1]))
        demo.show(result.get_outputs(), "group-chat")

    async def test_magentic_manager_coordinates_both_specialists(self):
        client = LocalChatClient()
        result = await asyncio.wait_for(magentic.build(client).run(magentic.PROMPT), timeout=10)
        self.assertEqual(client.specialists, 2)
        self.assertTrue(result.get_intermediate_outputs())
        demo.show(result.get_intermediate_outputs(), "magentic")
        demo.show(result.get_outputs(), "magentic")

    def test_round_robin_and_empty_output_validation(self):
        state = Mock(current_round=3, participants={"planner": "", "reviewer": ""})
        self.assertEqual(group_chat.select_speaker(state), "reviewer")
        for outputs in ([], [object()], [AgentResponse()]):
            with self.subTest(outputs=outputs), self.assertRaises(RuntimeError):
                demo.show(outputs, "test")

    def test_manager_limits_are_explicit(self):
        client = Mock()
        with patch("magentic.MagenticBuilder") as builder:
            magentic.build(client)
        self.assertEqual(builder.call_args.kwargs["max_round_count"], 5)
        self.assertEqual(builder.call_args.kwargs["max_reset_count"], 1)
        self.assertEqual(len(builder.call_args.kwargs["participants"]), 2)


if __name__ == "__main__":
    unittest.main()
