"""Send different prompts to one Microsoft Foundry model router deployment."""

import argparse
import os
from collections.abc import Sequence
from time import perf_counter
from urllib.parse import urlparse

from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import AzureOpenAI


PROMPTS = (
    ("Simple", "What is the capital of France? Answer in one sentence."),
    (
        "Writing",
        "Write a friendly, three-sentence invitation to a workshop about "
        "Microsoft Foundry. The audience is Python developers new to AI.",
    ),
    (
        "Reasoning",
        "A workshop has 120 attendees. Each lab needs one mentor per started "
        "group of 8 attendees, and there are 11 mentors. Attendees may join "
        "either of two consecutive lab slots. Find an allocation that minimizes "
        "the maximum number of mentors needed in either slot. Explain why it "
        "is optimal, including rounding to whole groups.",
    ),
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AZURE_OPENAI_ENDPOINT"),
        help="Foundry OpenAI endpoint (or set AZURE_OPENAI_ENDPOINT).",
    )
    parser.add_argument(
        "--deployment",
        default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "model-router"),
        help="Model router deployment name (default: model-router).",
    )
    parser.add_argument("--prompt", help="Send one custom prompt instead of the demo set.")
    args = parser.parse_args(argv)
    if not args.endpoint:
        parser.error("Set AZURE_OPENAI_ENDPOINT or pass --endpoint from the Bicep output.")
    endpoint = urlparse(args.endpoint)
    if endpoint.scheme != "https" or not endpoint.hostname:
        parser.error("--endpoint must be an absolute HTTPS URL.")
    if not args.deployment.strip():
        parser.error("--deployment must not be empty.")
    if args.prompt is not None and not args.prompt.strip():
        parser.error("--prompt must not be empty.")

    prompts = (("Custom", args.prompt),) if args.prompt is not None else PROMPTS
    with AzureCliCredential() as credential:
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        with AzureOpenAI(
            azure_endpoint=args.endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2024-10-21",
            timeout=120.0,
            max_retries=0,
        ) as client:
            for label, prompt in prompts:
                print(f"\n=== {label} ===\nPrompt: {prompt}", flush=True)
                started = perf_counter()
                response = client.chat.completions.create(
                    model=args.deployment,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant. Keep answers concise.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                )
                elapsed = perf_counter() - started
                print(f"Serving model: {response.model}")
                print(f"Elapsed: {elapsed:.2f}s (client-observed, including routing/network)")
                if response.usage is not None:
                    print(
                        f"Tokens: input={response.usage.prompt_tokens}, "
                        f"output={response.usage.completion_tokens}, "
                        f"total={response.usage.total_tokens}"
                    )
                if not response.choices:
                    raise RuntimeError("The service returned no completion choices.")
                choice = response.choices[0]
                if choice.message.refusal:
                    raise RuntimeError(f"The model refused the prompt: {choice.message.refusal}")
                if not choice.message.content:
                    raise RuntimeError(
                        f"The service returned no text (finish_reason={choice.finish_reason})."
                    )
                print(f"Finish reason: {choice.finish_reason}")
                print(f"Answer:\n{choice.message.content}")


if __name__ == "__main__":
    main()
