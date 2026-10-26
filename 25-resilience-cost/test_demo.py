from datetime import datetime, timezone
from contextlib import redirect_stdout
import io
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import demo


class ResilienceTests(unittest.TestCase):
    def budget(self, **kwargs):
        return demo.Budget(deadline=100, **kwargs)

    def test_throttle_respects_retry_after_then_recovers(self):
        operation = Mock(side_effect=[demo.synthetic_status(429, "2"), "ok"])
        sleep = Mock()
        budget = self.budget()
        self.assertEqual(demo.bounded(operation, budget, "model", sleep=sleep, clock=lambda: 0), "ok")
        sleep.assert_called_once_with(2)
        self.assertEqual(budget.model_requests, 2)

    def test_permanent_failure_never_retries(self):
        operation = Mock(side_effect=demo.synthetic_status(403))
        with self.assertRaises(demo.APIStatusError):
            demo.bounded(operation, self.budget(), "model", sleep=Mock(), clock=lambda: 0)
        operation.assert_called_once()

    def test_request_budget_and_deadline_stop_before_call(self):
        operation = Mock(side_effect=TimeoutError)
        with self.assertRaises(demo.BudgetExceeded):
            demo.bounded(operation, self.budget(model_limit=1), "model",
                         sleep=Mock(), clock=lambda: 0)
        operation.assert_called_once()
        operation.reset_mock()
        with self.assertRaises(demo.BudgetExceeded):
            demo.bounded(operation, self.budget(), "model", clock=lambda: 101)
        operation.assert_not_called()

    def test_retry_after_cannot_exceed_remaining_deadline(self):
        operation = Mock(side_effect=demo.synthetic_status(429, "200"))
        with self.assertRaises(demo.BudgetExceeded):
            demo.bounded(operation, self.budget(), "model", sleep=Mock(), clock=lambda: 0)
        operation.assert_called_once()

    def test_http_date_and_invalid_retry_after(self):
        now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
        error = demo.synthetic_status(429, "Sun, 25 Oct 2026 12:00:03 GMT")
        self.assertEqual(demo.retry_delay(error, 0, now), 3)
        with self.assertRaises(ValueError):
            demo.retry_delay(demo.synthetic_status(429, "nan"), 0)

    def test_retried_committed_write_does_not_duplicate(self):
        tool = demo.SyntheticBookingTool()
        result = demo.bounded(lambda timeout: tool.book("one-key", timeout),
                              self.budget(), "tool", sleep=Mock(), clock=lambda: 0)
        self.assertEqual(result, "synthetic-booking-1")
        self.assertEqual(len(tool.bookings), 1)

    def test_attempt_cap_propagates_outage(self):
        operation = Mock(side_effect=ConnectionError("offline"))
        with self.assertRaises(ConnectionError):
            demo.bounded(operation, self.budget(), "tool", attempts=2,
                         sleep=Mock(), clock=lambda: 0)
        self.assertEqual(operation.call_count, 2)

    def test_default_demo_completes_recovery_and_expected_failure_paths(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop",
                                     message=SimpleNamespace(content="A short workshop."))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
        )
        output = io.StringIO()
        with (
            patch("demo.OpenAI", return_value=client),
            patch("demo.AzureCliCredential"),
            patch("demo.get_bearer_token_provider", return_value=lambda: "unused-test-token"),
            patch("sys.argv", ["demo.py", "--endpoint", "https://model.example/"]),
            redirect_stdout(output),
        ):
            demo.main()
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertIn("bookings=1", output.getvalue())
        self.assertIn("EXPECTED PERMANENT FAILURE", output.getvalue())
        self.assertIn("EXPECTED DEADLINE STOP", output.getvalue())
        self.assertIn("model=5/6; tool=4/4", output.getvalue())
        self.assertIn("input=24; output=10", output.getvalue())


if __name__ == "__main__":
    unittest.main()
