"""An Agent Framework tutor calls Foundry using a real Entra Agent ID."""

import argparse
import asyncio
import os
from uuid import UUID

import httpx
from agent_framework.openai import OpenAIChatClient
from openai import AsyncOpenAI


def validate_identity(tenant: str, blueprint: str, agent: str, secret: str) -> None:
    for value in (tenant, blueprint, agent):
        UUID(value)
    if not secret.strip():
        raise ValueError("ENTRA_BLUEPRINT_SECRET must not be empty.")
    if blueprint == agent:
        raise ValueError("The blueprint client ID and child agent ID must be different.")


async def agent_token(
    client: httpx.AsyncClient, tenant: str, blueprint: str, agent: str, secret: str,
) -> str:
    validate_identity(tenant, blueprint, agent, secret)
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    first = await client.post(token_url, data={
        "client_id": blueprint, "client_secret": secret,
        "grant_type": "client_credentials", "scope": "api://AzureADTokenExchange/.default",
        "fmi_path": agent,
    })
    first.raise_for_status()
    exchange = first.json().get("access_token")
    if not isinstance(exchange, str) or not exchange.strip():
        raise RuntimeError("Entra returned no blueprint exchange token.")
    second = await client.post(token_url, data={
        "client_id": agent, "grant_type": "client_credentials",
        "scope": "https://cognitiveservices.azure.com/.default",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": exchange,
    })
    second.raise_for_status()
    token = second.json().get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("Entra returned no agent resource token.")
    return token


async def run_agent(endpoint: str, deployment: str) -> None:
    tenant = os.environ["ENTRA_TENANT_ID"]
    blueprint = os.environ["ENTRA_BLUEPRINT_CLIENT_ID"]
    identity = os.environ["ENTRA_AGENT_ID"]
    secret = os.environ["ENTRA_BLUEPRINT_SECRET"]
    async with httpx.AsyncClient(timeout=30) as http:
        token = await agent_token(http, tenant, blueprint, identity, secret)
    print(f"Blueprint client ID: {blueprint}\nAgent identity: {identity}\n"
          "The model call uses the child agent's token, not the presenter's Azure CLI identity.",
          flush=True)
    async with AsyncOpenAI(
        base_url=endpoint.rstrip("/") + "/openai/v1/", api_key=token, timeout=60,
    ) as openai:
        client = OpenAIChatClient(model=deployment, async_client=openai)
        tutor = client.as_agent(
            name="identity_tutor",
            instructions="You are an AI workshop tutor. Answer in one short sentence.",
        )
        response = await tutor.run("What is an AI agent?")
        if not response.text.strip():
            raise RuntimeError("The agent returned no text.")
        print(f"\nAuthenticated Foundry response:\n{response.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"))
    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"))
    args = parser.parse_args()
    if not args.endpoint or not args.endpoint.startswith("https://") or not args.deployment.strip():
        parser.error("Set the Bicep endpoint HTTPS URL and a nonempty deployment name.")
    names = ("ENTRA_TENANT_ID", "ENTRA_BLUEPRINT_CLIENT_ID", "ENTRA_AGENT_ID", "ENTRA_BLUEPRINT_SECRET")
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        parser.error(f"Run setup_identity.ps1 first. Missing: {', '.join(missing)}")
    validate_identity(*(os.environ[name] for name in names))
    asyncio.run(run_agent(args.endpoint, args.deployment))


if __name__ == "__main__":
    main()
