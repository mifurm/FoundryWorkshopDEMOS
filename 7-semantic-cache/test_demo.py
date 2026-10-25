import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import demo


class CacheTests(unittest.TestCase):
    def client(self, vectors):
        client = Mock()
        client.embeddings.create.side_effect = [
            SimpleNamespace(data=[SimpleNamespace(embedding=vector)]) for vector in vectors
        ]
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="A workshop answer."))
        ])
        return client

    def test_semantic_hit_avoids_generation_for_different_text(self):
        client = self.client([[1.0, 0.0], [0.9, 0.1]])
        cache = []
        first, hit, _ = demo.answer(client, "chat", "embedding", "Explain Foundry", cache, 0.85)
        self.assertFalse(hit)
        second, hit, similarity = demo.answer(
            client, "chat", "embedding", "What is Foundry?", cache, 0.85,
        )
        self.assertTrue(hit)
        self.assertGreaterEqual(similarity, 0.85)
        self.assertEqual(first, second)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual(client.embeddings.create.call_count, 2)

    def test_unrelated_prompt_misses_and_threshold_is_inclusive(self):
        client = self.client([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])
        cache = []
        for prompt in ("first", "unrelated"):
            self.assertFalse(demo.answer(client, "chat", "embedding", prompt, cache, 1.0)[1])
        self.assertTrue(demo.answer(client, "chat", "embedding", "first again", cache, 1.0)[1])
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_invalid_vectors_and_thresholds_are_rejected(self):
        for left, right in (([], []), ([0.0], [1.0]), ([1.0], [1.0, 2.0]),
                            ([float("nan")], [1.0])):
            with self.subTest(left=left), self.assertRaises(ValueError):
                demo.cosine(left, right)
        for threshold in (0, -1, 1.1, float("nan")):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                demo.answer(Mock(), "chat", "embedding", "hi", [], threshold)

    def test_incomplete_answer_is_not_cached(self):
        client = self.client([[1.0]])
        client.chat.completions.create.return_value.choices[0].finish_reason = "length"
        cache = []
        with self.assertRaises(RuntimeError):
            demo.answer(client, "chat", "embedding", "hi", cache, 0.85)
        self.assertEqual(cache, [])


if __name__ == "__main__":
    unittest.main()
