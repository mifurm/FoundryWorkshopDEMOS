"""Configure the lab's OAuth connection using an administrator-approved Entra app."""

import os
from uuid import UUID

from azure.identity import AzureCliCredential
import httpx

from demo import ARM, CONNECTION, TARGET, load_config


def connection_body(tenant: str, client_id: str, secret: str) -> dict:
    tenant, client_id = str(UUID(tenant)), str(UUID(client_id))
    if not secret.strip():
        raise ValueError("WORK_IQ_CLIENT_SECRET must not be empty.")
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return {"properties": {
        "authType": "OAuth2", "group": "ServicesAndApps", "category": "RemoteA2A",
        "target": TARGET, "isSharedToAll": True,
        "AuthorizationUrl": base + "/authorize",
        "TokenUrl": base + "/token", "RefreshUrl": base + "/token",
        "Scopes": ["api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask", "offline_access"],
        "Credentials": {"ClientId": client_id, "ClientSecret": secret},
        "metadata": {"ApiType": "Azure"},
    }}


def main() -> None:
    config = load_config()
    client_id = os.environ.get("WORK_IQ_CLIENT_ID", "")
    secret = os.environ.get("WORK_IQ_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise ValueError("Set WORK_IQ_CLIENT_ID and WORK_IQ_CLIENT_SECRET from your approved Entra app.")
    body = connection_body(config["tenantId"], client_id, secret)
    with AzureCliCredential(tenant_id=config["tenantId"]) as credential, httpx.Client(timeout=60) as http:
        response = http.put(
            ARM + config["projectResourceId"] + "/connections/" + CONNECTION,
            params={"api-version": "2025-04-01-preview"}, json=body,
            headers={"Authorization": "Bearer " + credential.get_token(ARM + "/.default").token},
        )
        if response.status_code not in (200, 201):
            # Do not log request/response bodies that might echo connection credentials.
            raise RuntimeError(f"Connection creation failed: HTTP {response.status_code}; "
                               f"request ID {response.headers.get('x-ms-request-id', 'unavailable')}.")
        redirect = response.json().get("properties", {}).get("oauthRedirectUrl")
        if not isinstance(redirect, str) or not redirect.startswith("https://"):
            raise RuntimeError("Connection created but no OAuth redirect URL returned; inspect it in Foundry.")
        print("OAuth2 connection created. Add this exact Web redirect URI to the Entra app:")
        print(redirect)
        print("Each caller must still authorize their OWN Microsoft 365 account when using the agent.")


if __name__ == "__main__":
    main()
