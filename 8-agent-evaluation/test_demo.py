import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import demo


class EvaluationTests(unittest.TestCase):
    def client(self, status="completed", errored=0):
        client = Mock()
        counts = SimpleNamespace(total=2, passed=2, failed=0, errored=errored)
        run = Mock(id="run-1", status=status, result_counts=counts)
        client.evals.create.return_value.id = "eval-1"
        client.evals.runs.create.return_value = run
        client.evals.runs.output_items.list.return_value = [
            Mock(status="pass"), Mock(status="pass"),
        ]
        return client

    def test_submits_stored_response_ids_to_cloud_evaluator(self):
        client = self.client()
        demo.evaluate(client, ["response-1", "response-2"], "judge")
        criterion = client.evals.create.call_args.kwargs["testing_criteria"][0]
        self.assertEqual(criterion["evaluator_name"], "builtin.coherence")
        self.assertEqual(criterion["initialization_parameters"], {"model": "judge"})
        source = client.evals.runs.create.call_args.kwargs["data_source"]
        self.assertEqual(source["type"], "azure_ai_responses")
        self.assertEqual(source["item_generation_params"]["source"]["content"], [
            {"item": {"resp_id": "response-1"}}, {"item": {"resp_id": "response-2"}},
        ])

    def test_failed_or_incomplete_results_are_errors(self):
        for client in (self.client(status="failed"), self.client(errored=1)):
            with self.subTest(client=client), self.assertRaises(RuntimeError):
                demo.evaluate(client, ["a", "b"], "judge")
        client = self.client()
        client.evals.runs.output_items.list.return_value = []
        with self.assertRaisesRegex(RuntimeError, "missing"):
            demo.evaluate(client, ["a", "b"], "judge")

    def test_polling_is_bounded(self):
        client = self.client(status="in_progress")
        with patch("demo.monotonic", side_effect=[0, 601]):
            with self.assertRaises(TimeoutError):
                demo.evaluate(client, ["a", "b"], "judge")

    def test_empty_response_ids_are_rejected(self):
        for ids in ([], [""]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                demo.evaluate(Mock(), ids, "judge")


if __name__ == "__main__":
    unittest.main()
