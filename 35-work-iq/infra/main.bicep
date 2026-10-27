targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the developer signed in with az login.')
param principalId string
@description('Optional second, same-tenant user for the isolation exercise.')
param secondPrincipalId string = ''
param accountName string = 'foundry-workiq-${uniqueString(resourceGroup().id)}'
param projectName string = 'work-iq'
param deploymentName string = 'gpt-4.1-mini'
@minValue(1)
param capacity int = 10

var securityTags = {
  SecurityControl: 'Ignore'
  SecurityExemption: 'StorageAccountKeyAccess'
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  tags: securityTags
  name: accountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  parent: account
  name: deploymentName
  sku: { name: 'GlobalStandard', capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  tags: securityTags
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Work IQ - per-user Microsoft 365 context'
    description: 'Persistent native Work IQ agent with individual OAuth authorization.'
  }
  dependsOn: [model]
}

var userRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource developer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, userRole)
  scope: account
  properties: {
    roleDefinitionId: userRole
    principalId: principalId
    principalType: 'User'
  }
}

var managerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'eadc314b-1a2d-4efa-be10-5d325db5065e')
resource connectionManager 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, managerRole)
  scope: project
  properties: {
    roleDefinitionId: managerRole
    principalId: principalId
    principalType: 'User'
  }
}

resource secondUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(secondPrincipalId)) {
  name: guid(project.id, secondPrincipalId, userRole)
  scope: project
  properties: {
    roleDefinitionId: userRole
    principalId: secondPrincipalId
    principalType: 'User'
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output projectResourceId string = project.id
output tenantId string = tenant().tenantId
