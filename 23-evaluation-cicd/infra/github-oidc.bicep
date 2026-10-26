targetScope = 'resourceGroup'

param location string = resourceGroup().location
@description('Existing lab 23 Foundry account; no model or project is redeployed.')
param accountName string
param identityName string = 'foundry-evaluation-github'
@description('Exact environment-bound OIDC sub claim, including immutable IDs when enabled in GitHub.')
param githubSubject string

var securityTags = {
  SecurityControl: 'Ignore'
  SecurityExemption: 'StorageAccountKeyAccess'
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  tags: securityTags
  name: identityName
  location: location
}

resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: identity
  name: 'github-environment'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    audiences: ['api://AzureADTokenExchange']
    subject: githubSubject
  }
}

var roleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource evaluationAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, identity.id, roleDefinitionId)
  scope: account
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output clientId string = identity.properties.clientId
output principalId string = identity.properties.principalId
output tenantId string = tenant().tenantId
output subscriptionId string = subscription().subscriptionId
output identityResourceId string = identity.id
