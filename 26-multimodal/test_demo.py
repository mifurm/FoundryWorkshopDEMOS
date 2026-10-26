import base64
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from PIL import Image

import demo


class MultimodalTests(unittest.TestCase):
    def test_supplied_images_are_real_png_and_match_truth_manifest(self):
        directory = Path(__file__).with_name("images")
        self.assertEqual(json.loads((directory / "truth.json").read_text()), demo.TRUTH)
        for name in demo.TRUTH:
            with Image.open(directory / name) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (640, 400))
            payload = demo.image_url(directory / name).split(",", 1)[1]
            self.assertEqual(base64.b64decode(payload), (directory / name).read_bytes())

    def test_mask_really_obscures_total_and_currency_is_absent(self):
        with Image.open(Path(__file__).with_name("images") / "ambiguous.png") as image:
            self.assertEqual(image.getpixel((300, 265)), (0, 0, 0))
            self.assertEqual(image.getpixel((40, 190)), (255, 255, 255))

    def test_explicit_missing_truth_validates(self):
        for truth in demo.TRUTH.values():
            result = demo.evaluate(json.dumps(truth), truth)
            self.assertTrue(result["correct"])

    def test_schema_valid_guess_is_visually_wrong(self):
        guessed = {"merchant": "WORKSHOP CAFE", "currency": "USD", "total": 12.5, "uncertainty": []}
        result = demo.evaluate(json.dumps(guessed), demo.TRUTH["ambiguous.png"])
        self.assertTrue(result["schema_ok"])
        self.assertFalse(result["correct"])

    def test_refusal_truncation_and_schema_failures(self):
        truth = demo.TRUTH["clear.png"]
        self.assertEqual(demo.evaluate("", truth, refusal="No")["status"], "refusal")
        self.assertEqual(demo.evaluate(json.dumps(truth), truth, finish_reason="length")["status"], "incomplete")
        self.assertEqual(demo.evaluate("{}", truth)["status"], "invalid")
        self.assertEqual(demo.evaluate("broken", truth)["status"], "invalid")

    def test_timeout_is_not_a_prediction(self):
        client = Mock()
        client.chat.completions.create.side_effect = TimeoutError("offline fixture")
        path = Path(__file__).with_name("images") / "clear.png"
        result = demo.extract(client, "alias", path, demo.TRUTH["clear.png"])
        self.assertEqual(result["status"], "request_error")
        self.assertIsNone(result["usage"])

    def test_unknown_usage_is_not_zero_cost(self):
        self.assertIsNone(demo.estimated_cost(None, .4, 1.6))
        self.assertAlmostEqual(demo.estimated_cost({"prompt_tokens": 1000, "completion_tokens": 100}, .4, 1.6), .00056)


if __name__ == "__main__":
    unittest.main()
