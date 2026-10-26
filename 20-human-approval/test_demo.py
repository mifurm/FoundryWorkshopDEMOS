import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

import demo


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = demo.connect(Path(self.directory.name) / "bookings.db")
        self.addCleanup(self.db.close)
        self.action = {"workshop": "foundry-intro", "attendees": 4}

    def proposal(self):
        return demo.propose(self.db, "tenant/alice", self.action, 100)

    def test_approval_and_replay_create_exactly_one_booking(self):
        proposal, digest = self.proposal()
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookings").fetchone()[0], 0)
        self.assertIn("APPROVED", demo.decide(self.db, "tenant/alice", proposal, digest, True, 101))
        self.assertIn("REPLAY", demo.decide(self.db, "tenant/alice", proposal, digest, True, 102))
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookings").fetchone()[0], 1)

    def test_rejected_proposal_cannot_execute(self):
        proposal, digest = self.proposal()
        self.assertIn("REJECTED", demo.decide(self.db, "tenant/alice", proposal, digest, False, 101))
        with self.assertRaises(PermissionError):
            demo.decide(self.db, "tenant/alice", proposal, digest, True, 102)
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookings").fetchone()[0], 0)

    def test_proposal_and_replay_survive_a_new_database_connection(self):
        proposal, digest = self.proposal()
        reopened = demo.connect(Path(self.directory.name) / "bookings.db")
        self.addCleanup(reopened.close)
        self.assertIn("APPROVED", demo.decide(
            reopened, "tenant/alice", proposal, digest, True, 101,
        ))
        self.assertIn("REPLAY", demo.decide(
            self.db, "tenant/alice", proposal, digest, True, 102,
        ))
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookings").fetchone()[0], 1)

    def test_wrong_actor_digest_expiry_and_changed_payload_block(self):
        proposal, digest = self.proposal()
        for actor, supplied, now in (("tenant/bob", digest, 101),
                                     ("tenant/alice", "wrong", 101),
                                     ("tenant/alice", digest, 1001)):
            with self.subTest(actor=actor, now=now), self.assertRaises(PermissionError):
                demo.decide(self.db, actor, proposal, supplied, True, now)
        with self.db:
            self.db.execute("UPDATE proposals SET payload=? WHERE id=?",
                            (json.dumps({**self.action, "attendees": 8}), proposal))
        with self.assertRaises(PermissionError):
            demo.decide(self.db, "tenant/alice", proposal, digest, True, 101)

    def test_application_policy_rejects_invalid_arguments(self):
        for action in ({**self.action, "attendees": True},
                       {**self.action, "attendees": 21},
                       {**self.action, "workshop": "real-booking"},
                       {**self.action, "execute": True}):
            with self.subTest(action=action), self.assertRaises(ValueError):
                demo.validate(action)

    def test_actual_model_tool_arguments_are_validated(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(tool_calls=[
                SimpleNamespace(type="function", function=SimpleNamespace(
                    name="propose_booking", arguments=json.dumps(self.action),
                )),
            ])),
        ])
        self.assertEqual(demo.model_action(client, "model", 4), self.action)
        with self.assertRaisesRegex(RuntimeError, "differs"):
            demo.model_action(client, "model", 5)

    def test_live_stop_finish_reason_with_a_valid_tool_call_is_accepted(self):
        client = Mock()
        choice = SimpleNamespace(finish_reason="stop", message=SimpleNamespace(tool_calls=[
            SimpleNamespace(type="function", function=SimpleNamespace(
                name="propose_booking", arguments=json.dumps(self.action),
            )),
        ]))
        client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
        self.assertEqual(demo.model_action(client, "model", 4), self.action)
        choice.finish_reason = "length"
        with self.assertRaisesRegex(RuntimeError, "complete tool"):
            demo.model_action(client, "model", 4)
        choice.finish_reason = "stop"
        choice.message.refusal = "Cannot comply."
        with self.assertRaisesRegex(RuntimeError, "complete tool"):
            demo.model_action(client, "model", 4)
        client.chat.completions.create.return_value.choices = []
        with self.assertRaisesRegex(RuntimeError, "one model choice"):
            demo.model_action(client, "model", 4)


if __name__ == "__main__":
    unittest.main()
