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


def probe(client, deployment, expected_status=None):
    """Require an exact HTTP status, or an explicit inference RBAC denial."""
    from openai import APIStatusError

    try:
        client.chat.completions.create(
            model=deployment, messages=[{"role": "user", "content": "Say hello."}],
            max_tokens=16,
        )
    except APIStatusError as exc:
        print(f"Actual HTTP {exc.status_code}: {exc.body}")
        if expected_status is None:
            body = exc.body
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                body = body["error"]
            message = body.get("message") if isinstance(body, dict) else None
            matched = (
                exc.status_code in (401, 403)
                and isinstance(body, dict) and body.get("code") == "PermissionDenied"
                and isinstance(message, str) and (
                    message == "Principal does not have access to API/Operation."
                    or (
                        "lacks the required data action" in message
                        and "Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action" in message
                    )
                )
            )
            expected = "an explicit inference RBAC PermissionDenied (HTTP 401 or 403)"
        else:
            matched = exc.status_code == expected_status
            expected = f"HTTP {expected_status}"
        if not matched:
            print(f"Expected {expected}; check authentication, RBAC propagation, and region.")
        return matched
    print("Probe succeeded unexpectedly: check inherited inference roles or deployment names.")
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "tutor-mini"))
    parser.add_argument("--denied-endpoint", default=os.getenv("WORKSHOP_DENIED_ENDPOINT"))
    parser.add_argument("--tenant-id", default=os.getenv("WORKSHOP_INFERENCE_TENANT_ID"))
    parser.add_argument("--client-id", default=os.getenv("WORKSHOP_INFERENCE_CLIENT_ID"))
    parser.add_argument("--certificate-path", default=os.getenv("WORKSHOP_INFERENCE_CERTIFICATE_PATH"))
    parser.add_argument("--skip-negative-probes", action="store_true")
    args = parser.parse_args(argv)
    if not args.endpoint:
        parser.error("Set AZURE_OPENAI_ENDPOINT or pass --endpoint.")
    identity = (args.tenant_id, args.client_id, args.certificate_path)
    if any(value is not None for value in identity) and not all(
        value and value.strip() for value in identity
    ):
        parser.error("Restricted inference requires --tenant-id, --client-id, and --certificate-path together.")

    from azure.identity import CertificateCredential, DefaultAzureCredential, get_bearer_token_provider
    from openai import OpenAI

    credential = (
        CertificateCredential(
            tenant_id=args.tenant_id, client_id=args.client_id, certificate_path=args.certificate_path,
        )
        if args.certificate_path else
        DefaultAzureCredential(exclude_interactive_browser_credential=True)
    )
    print("Inference identity:", f"certificate service principal {args.client_id}"
          if args.certificate_path else "DefaultAzureCredential (check inherited roles)")
    with credential:
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
                print("\nMissing inference permission (expected RBAC denial; HTTP 401 or 403):", target)
                with OpenAI(base_url=target.rstrip("/") + "/openai/v1/", api_key=token,
                            max_retries=0, timeout=60) as denied:
                    denied_ok = probe(denied, args.deployment)
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
