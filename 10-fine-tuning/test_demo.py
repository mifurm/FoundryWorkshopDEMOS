import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

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
        self.assertEqual(kwargs["extra_body"], {"trainingType": "Global"})

    def test_failed_cancelled_and_missing_model_jobs_are_not_success(self):
        for status in ("failed", "cancelled", "succeeded"):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                demo.show_job(Mock(id="job-1", status=status, fine_tuned_model=None))


if __name__ == "__main__":
    unittest.main()
