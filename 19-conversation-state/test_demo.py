from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import demo


class StateTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(f".test-state-{uuid4().hex}.sqlite3")
        self.store = demo.Store(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_persistence_isolation_reset_and_delete(self):
        history = [{"role": "user", "content": "private"}]
        self.store.save("alice", "same-thread", history)
        self.store.save("bob", "same-thread", [{"role": "user", "content": "other"}])
        resumed = demo.Store(self.path)
        self.assertEqual(resumed.load("alice", "same-thread"), history)
        self.assertEqual(resumed.load("alice", "other-thread"), [])
        self.assertEqual(resumed.load("mallory' OR 1=1 --", "same-thread"), [])
        self.assertEqual(resumed.delete("mallory", "same-thread"), 0)
        resumed.reset("bob", "same-thread")
        self.assertEqual(resumed.load("bob", "same-thread"), [])
        self.assertEqual(resumed.load("alice", "same-thread"), history)
        self.assertEqual(resumed.delete("alice", "same-thread"), 1)
        self.assertEqual(resumed.load("alice", "same-thread"), [])

    def test_budget_drops_whole_oldest_pairs_and_keeps_system(self):
        history = [
            {"role": "user", "content": "secret " * 20},
            {"role": "assistant", "content": "ack " * 20},
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "response"},
        ]
        messages, retained, removed = demo.bounded(history, "question", 500, 120)
        self.assertEqual(removed, 2)
        self.assertEqual(retained, history[2:])
        self.assertEqual(messages[0]["role"], "system")
        self.assertLessEqual(demo.cost(messages) + 120, 500)
        self.assertNotIn("secret", str(messages))
        self.assertEqual(demo.cost([{"content": "é"}]), 18)

    def test_oversized_prompt_and_bad_budget_fail_closed(self):
        for budget, reserve in ((200, 120), (100, 100), (100, 0)):
            with self.assertRaises(ValueError):
                demo.bounded([], "x" * 1000, budget, reserve)

    def test_real_turn_boundary_and_failed_response_do_not_change_state(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="A short answer."))])
        demo.turn(self.store, client, "deployed-model", "alice", "thread", "Question?")
        call = client.chat.completions.create.call_args.kwargs
        self.assertEqual(call["model"], "deployed-model")
        self.assertLessEqual(demo.cost(call["messages"]) + call["max_completion_tokens"], 800)
        saved = self.store.load("alice", "thread")
        self.assertEqual(len(saved), 2)
        self.assertEqual(self.store.load("bob", "thread"), [])
        client.chat.completions.create.return_value.choices[0].finish_reason = "length"
        with self.assertRaises(RuntimeError):
            demo.turn(self.store, client, "deployed-model", "alice", "thread", "Again?")
        self.assertEqual(self.store.load("alice", "thread"), saved)

    def test_default_lesson_resumes_truncates_and_cleans_both_scopes(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="A short answer."))])
        args = SimpleNamespace(deployment="test-model", budget=800, reserve=120)
        with patch("builtins.print"):
            demo.demonstration(self.store, client, args)
        self.assertEqual(client.chat.completions.create.call_count, 9)
        for call in client.chat.completions.create.call_args_list:
            self.assertLessEqual(demo.cost(call.kwargs["messages"]) + 120, 800)
        last_request = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertNotIn("MAPLE-742", str(last_request))
        with demo.closing(demo.sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
