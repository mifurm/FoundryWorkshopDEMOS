targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
@description('Real Entra Agent ID, created by setup_identity.ps1; its object and client IDs are identical.')
@minLength(36)
@maxLength(36)
param agentIdentityId string
param accountName string = 'foundry-agentid-${uniqueString(resourceGroup().id)}'
param deploymentName string = 'gpt-4.1-mini'
@minValue(1)
param capacity int = 10

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: deploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
  }
}

var inferenceRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
resource agentInferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, agentIdentityId, inferenceRole)
  scope: account
  properties: {
    roleDefinitionId: inferenceRole
    principalId: agentIdentityId
    principalType: 'ServicePrincipal'
  }
}

var readerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
resource presenterReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, readerRole)
  scope: account
  properties: {
    roleDefinitionId: readerRole
    principalId: principalId
    principalType: 'User'
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output agentIdentityId string = agentIdentityId
