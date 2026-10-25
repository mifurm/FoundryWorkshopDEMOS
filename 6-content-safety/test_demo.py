import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import demo


class SafetyTests(unittest.TestCase):
    def client(self, severity):
        return Mock(analyze_text=Mock(return_value=SimpleNamespace(categories_analysis=[
            SimpleNamespace(category=category, severity=severity)
            for category in sorted(demo.CATEGORIES)
        ])))

    def test_allows_below_threshold_and_blocks_at_threshold(self):
        self.assertFalse(demo.analyze(self.client(0), "Hello", 2))
        self.assertFalse(demo.analyze(self.client(2), "Hello", 4))
        self.assertTrue(demo.analyze(self.client(4), "Hello", 4))
        self.assertTrue(demo.analyze(self.client(6), "Hello", 4))

    def test_requests_documented_severity_scale(self):
        client = self.client(0)
        demo.analyze(client, "Hello", 2)
        options = client.analyze_text.call_args.args[0]
        self.assertEqual(options.text, "Hello")
        self.assertEqual(options.output_type, "FourSeverityLevels")

    def test_missing_categories_or_invalid_scores_fail(self):
        for client in (self.client(None), self.client(3), Mock(
                analyze_text=Mock(return_value=SimpleNamespace(categories_analysis=[])))):
            with self.subTest(client=client), self.assertRaises(RuntimeError):
                demo.analyze(client, "Hello", 2)

    def test_service_errors_propagate(self):
        client = Mock(analyze_text=Mock(side_effect=RuntimeError("service unavailable")))
        with self.assertRaisesRegex(RuntimeError, "service unavailable"):
            demo.analyze(client, "Hello", 2)


if __name__ == "__main__":
    unittest.main()
