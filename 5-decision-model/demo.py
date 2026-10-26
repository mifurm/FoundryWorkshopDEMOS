"""Route three support tickets using Microsoft's typed Decision API, not chat."""

import argparse
import os
from time import perf_counter, sleep

import httpx
from azure.identity import AzureCliCredential


TEAMS = {
    "billing": "Charges, invoices, and refunds",
    "technical": "Software errors, bugs, and outages",
    "account": "Sign-in, password, and account access",
}
EXAMPLES = [
    ("I was charged twice for my subscription.", "billing"),
    ("The API returns an error on every request.", "technical"),
    ("I cannot sign in after resetting my password.", "account"),
]


def classify(client: httpx.Client, deployment: str, ticket: str) -> str:
    request = {
        "model": deployment,
        "state": ticket,
        "questions": {"team": {
            "type": "choice",
            "instructions": "Which team should handle this support ticket?",
            "criteria": TEAMS,
        }},
    }
    for attempt in range(3):
        response = client.post("providers/microsoft/v1/systemone", json=request)
        if response.status_code != 429 or attempt == 2:
            break
        milliseconds = response.headers.get("retry-after-ms")
        seconds = response.headers.get("Retry-After")
        if milliseconds is None and seconds is None:
            response.raise_for_status()
        delay = float(milliseconds) / 1000 if milliseconds is not None else float(seconds)
        if not 0 <= delay <= 65:
            response.raise_for_status()
        print(f"Decision API throttled (HTTP 429); retrying in {delay:.2f}s "
              f"(retry {attempt + 1}/2).", flush=True)
        sleep(delay)
    response.raise_for_status()
    payload = response.json()
    answer = payload.get("answers", {}).get("team", {})
    if answer.get("type") != "choice" or answer.get("choice") not in TEAMS:
        raise RuntimeError(f"Decision API returned an invalid team answer: {payload}")
    return answer["choice"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "decision"))
    parser.add_argument("--ticket", help="Classify a custom, non-sensitive support ticket.")
    args = parser.parse_args()
    if not args.endpoint or not args.endpoint.startswith("https://"):
        parser.error("Set AZURE_OPENAI_ENDPOINT to the Bicep endpoint HTTPS URL.")
    if not args.deployment.strip() or (args.ticket is not None and not args.ticket.strip()):
        parser.error("Deployment and ticket must not be empty.")
    examples = [(args.ticket, None)] if args.ticket is not None else EXAMPLES
    correct = 0
    with AzureCliCredential() as credential:
        token = credential.get_token("https://cognitiveservices.azure.com/.default").token
        with httpx.Client(
            base_url=args.endpoint.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"}, timeout=60,
        ) as client:
            for ticket, expected in examples:
                start = perf_counter()
                team = classify(client, args.deployment, ticket)
                correct += team == expected
                print(f"{ticket}\n  Team: {team}; expected: {expected}; "
                      f"latency: {perf_counter() - start:.2f}s")
    if args.ticket is None:
        print(f"Accuracy on toy examples: {correct}/{len(examples)} (not a benchmark).")


if __name__ == "__main__":
    main()
