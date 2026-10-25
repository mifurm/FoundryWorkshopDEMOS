"""Analyze text with Azure AI Content Safety and apply a visible severity gate."""

import argparse
import os

from azure.ai.contentsafety import ContentSafetyClient
from azure.ai.contentsafety.models import AnalyzeTextOptions
from azure.identity import AzureCliCredential


CATEGORIES = {"Hate", "SelfHarm", "Sexual", "Violence"}


def analyze(client: ContentSafetyClient, text: str, threshold: int) -> bool:
    result = client.analyze_text(AnalyzeTextOptions(
        text=text, output_type="FourSeverityLevels",
    ))
    analyses = result.categories_analysis
    if not analyses or {item.category for item in analyses} != CATEGORIES:
        raise RuntimeError("Content Safety did not return all four harm categories.")
    for item in analyses:
        if item.severity not in (0, 2, 4, 6):
            raise RuntimeError(f"Invalid severity for {item.category}: {item.severity}")
        print(f"{item.category}: {item.severity}")
    blocked = any(item.severity >= threshold for item in analyses)
    print(f"Policy: {'BLOCK' if blocked else 'ALLOW'} (threshold >= {threshold})")
    return blocked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--text", default="Welcome to our friendly beginner AI workshop!")
    parser.add_argument("--threshold", type=int, choices=(2, 4, 6), default=2)
    args = parser.parse_args()
    if not args.endpoint or not args.endpoint.startswith("https://"):
        parser.error("Set AZURE_OPENAI_ENDPOINT to the Bicep endpoint HTTPS URL.")
    if not args.text.strip() or len(args.text) > 10000:
        parser.error("--text must contain 1-10000 characters.")
    with (
        AzureCliCredential() as credential,
        ContentSafetyClient(args.endpoint, credential) as client,
    ):
        analyze(client, args.text, args.threshold)


if __name__ == "__main__":
    main()
