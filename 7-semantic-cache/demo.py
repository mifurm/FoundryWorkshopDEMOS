"""Cache Foundry answers locally by embedding similarity, not by exact text."""

import argparse
from math import isfinite, sqrt
import os
from time import perf_counter

from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import OpenAI


PROMPTS = [
    "Explain what Microsoft Foundry is in one short sentence.",
    "In one brief sentence, explain what Microsoft Foundry is.",
    "Explain what Microsoft Foundry is in one short sentence.",
]


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Embedding vectors must be nonempty and have matching dimensions.")
    if not all(isfinite(value) for value in [*left, *right]):
        raise ValueError("Embedding values must be finite.")
    denominator = sqrt(sum(x * x for x in left)) * sqrt(sum(x * x for x in right))
    if denominator == 0:
        raise ValueError("Embedding vectors must have a nonzero norm.")
    return sum(a * b for a, b in zip(left, right)) / denominator


def answer(
    client: OpenAI, deployment: str, embeddings: str, prompt: str,
    cache: list[tuple[list[float], str]], threshold: float,
) -> tuple[str, bool, float]:
    if not isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("Similarity threshold must be in (0, 1].")
    vector = client.embeddings.create(model=embeddings, input=prompt).data[0].embedding
    # Validate even the first vector before it can enter the cache.
    cosine(vector, vector)
    best = max(((cosine(vector, saved), text) for saved, text in cache), default=None)
    if best is not None and best[0] >= threshold:
        return best[1], True, best[0]
    response = client.chat.completions.create(
        model=deployment, messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=120, timeout=60,
    )
    choice = response.choices[0]
    text = choice.message.content
    if choice.finish_reason != "stop" or not text or not text.strip():
        raise RuntimeError("Model did not return a complete, nonempty answer.")
    cache.append((vector, text))
    return text, False, best[0] if best is not None else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    parser.add_argument("--embeddings", default=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
                                                       "text-embedding-3-small"))
    parser.add_argument("--threshold", type=float, default=0.85)
    args = parser.parse_args()
    if not args.endpoint or not args.endpoint.startswith("https://"):
        parser.error("Set AZURE_OPENAI_ENDPOINT to the Bicep endpoint HTTPS URL.")
    if not isfinite(args.threshold) or not 0 < args.threshold <= 1:
        parser.error("--threshold must be in (0, 1].")
    if not args.deployment.strip() or not args.embeddings.strip():
        parser.error("Deployment names must not be empty.")
    cache: list[tuple[list[float], str]] = []
    hits = 0
    with AzureCliCredential() as credential:
        with OpenAI(
            base_url=args.endpoint.rstrip("/") + "/openai/v1/",
            api_key=get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default"),
        ) as client:
            for prompt in PROMPTS:
                start = perf_counter()
                text, hit, score = answer(
                    client, args.deployment, args.embeddings, prompt, cache, args.threshold,
                )
                hits += hit
                print(f"\n{'HIT' if hit else 'MISS'}; best similarity={score:.4f}; "
                      f"{perf_counter() - start:.2f}s\n{prompt}\n{text}")
    print(f"\nGeneration requests: {len(PROMPTS) - hits}; cache hits: {hits}; "
          f"embedding requests: {len(PROMPTS)}.")
    if not hits:
        raise RuntimeError("No cache hits observed; check embeddings and similarity threshold.")


if __name__ == "__main__":
    main()
