param(
    [Parameter(Mandatory = $true)]
    [guid] $TenantId,
    [string] $DisplayName = "Workshop identity tutor"
)

$ErrorActionPreference = "Stop"
$statePath = Join-Path $PSScriptRoot ".azure/identity.json"
if (Test-Path $statePath) {
    throw "Identity state already exists at $statePath. Reuse the existing identity or clean it up first."
}

Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
Connect-MgGraph -TenantId $TenantId -ContextScope Process -NoWelcome -Scopes @(
    "User.Read",
    "AgentIdentityBlueprint.Create",
    "AgentIdentityBlueprint.AddRemoveCreds.All",
    "AgentIdentityBlueprint.UpdateAuthProperties.All",
    "AgentIdentityBlueprintPrincipal.Create",
    "AgentIdentity.Create.All"
)

$headers = @{ "OData-Version" = "4.0" }
function Invoke-AgentGraph {
    param([string] $Method, [string] $Path, [hashtable] $Body)
    Invoke-MgGraphRequest -Method $Method -Uri "https://graph.microsoft.com/$Path" `
        -Headers $headers -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 10)
}

$user = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/me"
$sponsor = "https://graph.microsoft.com/v1.0/users/$($user.id)"
$blueprint = Invoke-AgentGraph POST "v1.0/applications/microsoft.graph.agentIdentityBlueprint" @{
    displayName = "$DisplayName blueprint"
    "sponsors@odata.bind" = @($sponsor)
    "owners@odata.bind" = @($sponsor)
}

# Record each created object immediately, so a later failure can still be cleaned up.
New-Item -ItemType Directory -Path (Split-Path $statePath) -Force | Out-Null
$state = @{
    tenantId = "$TenantId"
    blueprintObjectId = $blueprint.id
    blueprintClientId = $blueprint.appId
}
$state | ConvertTo-Json | Set-Content $statePath

Invoke-AgentGraph PATCH "v1.0/applications/$($blueprint.id)" @{
    identifierUris = @("api://$($blueprint.appId)")
    api = @{ oauth2PermissionScopes = @(@{
        id = [guid]::NewGuid().ToString()
        value = "access_agent"
        type = "Admin"
        isEnabled = $true
        adminConsentDisplayName = "Access the workshop agent"
        adminConsentDescription = "Access the workshop identity tutor."
    }) }
} | Out-Null

$principal = Invoke-AgentGraph POST "v1.0/serviceprincipals/microsoft.graph.agentIdentityBlueprintPrincipal" @{
    appId = $blueprint.appId
}
$state.blueprintPrincipalId = $principal.id
$state | ConvertTo-Json | Set-Content $statePath

$agent = Invoke-AgentGraph POST "beta/serviceprincipals/Microsoft.Graph.AgentIdentity" @{
    displayName = $DisplayName
    agentIdentityBlueprintId = $blueprint.appId
    "sponsors@odata.bind" = @($sponsor)
}
$state.agentIdentityId = $agent.id
$state | ConvertTo-Json | Set-Content $statePath

$password = Invoke-AgentGraph POST "v1.0/applications/$($blueprint.id)/addPassword" @{
    passwordCredential = @{
        displayName = "One-day workshop only"
        endDateTime = [DateTime]::UtcNow.AddDays(1).ToString("o")
    }
}
$env:ENTRA_TENANT_ID = "$TenantId"
$env:ENTRA_BLUEPRINT_CLIENT_ID = $blueprint.appId
$env:ENTRA_AGENT_ID = $agent.id
$env:ENTRA_BLUEPRINT_SECRET = $password.secretText
$password = $null
Write-Host "Agent ID: $($agent.id)"
Write-Host "Blueprint client ID: $($blueprint.appId)"
Write-Host "IDs saved to $statePath. The one-day secret is only in this process environment."
Write-Host "Continue deployment and run Python in this PowerShell session. Do not enable tracing or transcription."
