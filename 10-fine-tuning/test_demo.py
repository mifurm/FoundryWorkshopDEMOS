import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import demo


class FineTuningTests(unittest.TestCase):
    def test_training_data_has_twelve_balanced_valid_examples(self):
        self.assertEqual(demo.validate_data(demo.DATA), 12)
        labels = [json.loads(line)["messages"][-1]["content"]
                  for line in demo.DATA.read_text().splitlines()]
        self.assertEqual({label: labels.count(label) for label in set(labels)},
                         {"BILLING": 4, "TECHNICAL": 4, "ACCOUNT": 4})

    def test_too_few_or_malformed_examples_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "training.jsonl"
            data.write_text("{}\n" * 9)
            with self.assertRaisesRegex(ValueError, "10 training"):
                demo.validate_data(data)
            data.write_text("{}\n" * 10)
            with self.assertRaisesRegex(ValueError, "system, user"):
                demo.validate_data(data)

    def test_submits_single_epoch_supervised_global_training(self):
        client = Mock()
        client.files.create.return_value.id = "file-1"
        demo.submit(client, demo.MODEL)
        client.files.wait_for_processing.assert_called_once_with("file-1")
        kwargs = client.fine_tuning.jobs.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-4.1-mini-2025-04-14")
        self.assertEqual(kwargs["training_file"], "file-1")
        self.assertEqual(kwargs["method"]["type"], "supervised")
        self.assertEqual(kwargs["method"]["supervised"]["hyperparameters"]["n_epochs"], 1)
        self.assertEqual(kwargs["extra_body"], {"trainingType": "GlobalStandard"})

    def test_failed_cancelled_and_missing_model_jobs_are_not_success(self):
        for status in ("failed", "cancelled", "succeeded"):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                demo.show_job(Mock(id="job-1", status=status, fine_tuned_model=None))

    def test_resume_does_not_submit_and_retains_trained_model_id(self):
        client = MagicMock()
        job = Mock(id="job-1", status="succeeded", fine_tuned_model="trained-model-1")
        job.model_dump.return_value = {
            "id": "job-1", "status": "succeeded", "fine_tuned_model": "trained-model-1",
        }
        client.fine_tuning.jobs.retrieve.return_value = job
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "evidence" / "job.json"
            with (
                patch("sys.argv", ["demo.py", "--endpoint", "https://account.openai.azure.com",
                                   "--job-id", "job-1", "--wait", "--report", str(report)]),
                patch("demo.AzureCliCredential"),
                patch("demo.get_bearer_token_provider"),
                patch("demo.OpenAI") as factory,
                patch("demo.submit") as submit,
            ):
                factory.return_value.__enter__.return_value = client
                demo.main()
            submit.assert_not_called()
            client.fine_tuning.jobs.retrieve.assert_called_once_with("job-1")
            self.assertEqual(json.loads(report.read_text()), {
                "endpoint": "https://account.openai.azure.com",
                "job": job.model_dump.return_value,
            })

    def test_polling_updates_report_without_resubmitting(self):
        client = MagicMock()
        pending = Mock(id="job-1", status="pending")
        pending.model_dump.return_value = {"id": "job-1", "status": "pending", "fine_tuned_model": None}
        complete = Mock(id="job-1", status="succeeded", fine_tuned_model="trained-model-1")
        complete.model_dump.return_value = {
            "id": "job-1", "status": "succeeded", "fine_tuned_model": "trained-model-1",
        }
        client.fine_tuning.jobs.retrieve.side_effect = [pending, complete]
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "job.json"
            def during_wait(seconds):
                self.assertEqual(seconds, 15)
                self.assertEqual(json.loads(report.read_text())["job"], pending.model_dump.return_value)
            with (
                patch("sys.argv", ["demo.py", "--endpoint", "https://account.openai.azure.com",
                                   "--job-id", "job-1", "--wait", "--report", str(report)]),
                patch("demo.AzureCliCredential"),
                patch("demo.get_bearer_token_provider"),
                patch("demo.OpenAI") as factory,
                patch("demo.submit") as submit,
                patch("demo.sleep", side_effect=during_wait),
            ):
                factory.return_value.__enter__.return_value = client
                demo.main()
            submit.assert_not_called()
            self.assertEqual(client.fine_tuning.jobs.retrieve.call_count, 2)
            self.assertEqual(json.loads(report.read_text())["job"], complete.model_dump.return_value)

    def test_timeout_retains_pending_job_for_resume(self):
        client = MagicMock()
        job = Mock(id="job-1", status="pending")
        job.model_dump.return_value = {"id": "job-1", "status": "pending", "fine_tuned_model": None}
        client.fine_tuning.jobs.retrieve.return_value = job
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "job.json"
            with (
                patch("sys.argv", ["demo.py", "--endpoint", "https://account.openai.azure.com",
                                   "--job-id", "job-1", "--wait", "--report", str(report)]),
                patch("demo.AzureCliCredential"),
                patch("demo.get_bearer_token_provider"),
                patch("demo.OpenAI") as factory,
                patch("demo.monotonic", side_effect=[0, 1801]),
                patch("demo.submit") as submit,
            ):
                factory.return_value.__enter__.return_value = client
                with self.assertRaisesRegex(TimeoutError, "job-1"):
                    demo.main()
            submit.assert_not_called()
            self.assertEqual(json.loads(report.read_text())["job"], job.model_dump.return_value)


if __name__ == "__main__":
    unittest.main()
