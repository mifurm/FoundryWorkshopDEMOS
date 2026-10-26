import json
import unittest
from unittest.mock import Mock

import demo


class BenchmarkTests(unittest.TestCase):
    def test_labels_all_conform(self):
        for case in demo.CASES:
            self.assertTrue(demo.score(json.dumps(case["expected"]), case["expected"])["correct"])

    def test_structure_is_not_correctness(self):
        result = demo.score('{"category":"billing","priority":"low"}',
                            {"category": "outage", "priority": "high"})
        self.assertTrue(result["schema_ok"])
        self.assertFalse(result["correct"])

    def test_missing_fields_extra_fields_and_invalid_json(self):
        for content in ("not json", "{}", '{"category":"access","priority":"low","extra":1}'):
            self.assertFalse(demo.score(content, {})["schema_ok"])

    def test_refusal_and_incomplete_never_scored_as_success(self):
        for kwargs in ({"refusal": "no"}, {"finish_reason": "length"},
                       {"finish_reason": "content_filter"}):
            self.assertFalse(demo.score('{"category":null,"priority":null}', {}, **kwargs)["correct"])

    def test_percentiles_and_cost_unknown(self):
        self.assertEqual(demo.percentile([4, 1, 3, 2], .95), 4)
        self.assertIsNone(demo.percentile([], .95))
        self.assertIsNone(demo.estimate_cost({"prompt_tokens": 10, "completion_tokens": 2}, None))
        self.assertAlmostEqual(demo.estimate_cost({"prompt_tokens": 100, "completion_tokens": 50}, [1, 2]), .0002)

    def test_transport_failure_is_preserved_and_counts_in_denominator(self):
        client = Mock()
        client.chat.completions.create.side_effect = TimeoutError("offline timeout fixture")
        record = demo.run_one(client, "alias", demo.CASES[0], [1, 2])
        self.assertIn("TimeoutError", record["transport_error"])
        summary = demo.summarize([record])
        self.assertEqual(summary["accuracy"], 0)
        self.assertEqual(summary["transport_failures"], 1)
        self.assertIsNone(summary["estimated_usd"])
        self.assertIsNone(summary["latency_successful_http_seconds"]["p50"])

    def test_missing_config_or_single_repeat_rejected(self):
        with self.assertRaises(SystemExit):
            demo.main(["--endpoint", "https://example.org", "--repeats", "1"])


if __name__ == "__main__":
    unittest.main()
