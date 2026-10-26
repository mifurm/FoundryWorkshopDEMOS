"""Keyless first response, streaming usage, and real negative probes."""
import argparse
import os
import sys
from urllib.parse import urlsplit


def denied_endpoint(endpoint):
    parsed = urlsplit(endpoint)
    suffix = ".openai.azure.com"
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(suffix):
        raise ValueError("Pass --denied-endpoint for a nonstandard inference endpoint.")
    return f"https://{parsed.hostname[:-len(suffix)]}-deny{suffix}/"


def stream_response(chunks, write):
    usage = None
    for chunk in chunks:
        if chunk.choices:
            write(chunk.choices[0].delta.content or "")
        if chunk.usage is not None:
            usage = chunk.usage.model_dump()
    return usage


def probe(client, deployment, expected_status):
    """Print the service error without pretending an unexpected result is success."""
    from openai import APIStatusError

    try:
        client.chat.completions.create(
            model=deployment, messages=[{"role": "user", "content": "Say hello."}],
            max_tokens=16,
        )
    except APIStatusError as exc:
        print(f"Actual HTTP {exc.status_code}: {exc.body}")
        if exc.status_code != expected_status:
            print(f"Expected {expected_status}; check authentication, RBAC propagation, and region.")
        return exc.status_code == expected_status
    print("Probe succeeded unexpectedly: check inherited inference roles or deployment names.")
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "tutor-mini"))
    parser.add_argument("--denied-endpoint", default=os.getenv("WORKSHOP_DENIED_ENDPOINT"))
    parser.add_argument("--skip-negative-probes", action="store_true")
    args = parser.parse_args(argv)
    if not args.endpoint:
        parser.error("Set AZURE_OPENAI_ENDPOINT or pass --endpoint.")

    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import OpenAI

    with DefaultAzureCredential(exclude_interactive_browser_credential=True) as credential:
        token = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
        with OpenAI(base_url=args.endpoint.rstrip("/") + "/openai/v1/", api_key=token,
                    max_retries=0, timeout=60) as client:
            print("Project:", os.getenv("FOUNDRY_PROJECT_ENDPOINT", "(not supplied)"))
            print("Inference:", args.endpoint, "| Deployment:", args.deployment)
            print("A project organizes assets; inference uses /openai/v1/.")
            print("The deployment name is a user-chosen alias, not the catalog model name.")
            messages = [
                {"role": "system", "content": "You are a workshop tutor. Answer in two short sentences."},
                {"role": "user", "content": "What is the difference between a project and a model deployment?"},
            ]
            response = client.chat.completions.create(
                model=args.deployment, messages=messages, max_tokens=160,
            )
            print("First response:", response.choices[0].message.content)
            print("Serving model:", response.model, "Usage:", response.usage.model_dump() if response.usage else None)
            print("Stream: ", end="", flush=True)
            with client.chat.completions.create(
                model=args.deployment, messages=messages, max_tokens=160,
                stream=True, stream_options={"include_usage": True},
            ) as chunks:
                usage = stream_response(chunks, lambda text: print(text, end="", flush=True))
            print("\nStream usage:", usage or "Not returned by the service")
            if not args.skip_negative_probes:
                print("\nWrong deployment (expected HTTP 404):")
                wrong_ok = probe(client, "deliberately-missing-deployment", 404)
                print("Fix: use the Bicep deployment output, not gpt-4.1-mini.")
                target = args.denied_endpoint or denied_endpoint(args.endpoint)
                print("\nMissing inference permission (expected HTTP 403):", target)
                with OpenAI(base_url=target.rstrip("/") + "/openai/v1/", api_key=token,
                            max_retries=0, timeout=60) as denied:
                    denied_ok = probe(denied, args.deployment, 403)
                print("Fix: assign Cognitive Services OpenAI User at the target account; allow RBAC propagation.")
                if not (wrong_ok and denied_ok):
                    print("Negative demonstration not reproduced; inherited roles can bypass the denial.")
                    return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
