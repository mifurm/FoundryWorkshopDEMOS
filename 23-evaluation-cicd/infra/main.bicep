targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
@description('Set ServicePrincipal for GitHub Actions OIDC.')
@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'
param accountName string = 'eval-ci-${uniqueString(resourceGroup().id)}'
param deploymentName string = 'gpt-4.1-mini'

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
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  tags: securityTags
  parent: account
  // The account rejects concurrent model deployment and project writes.
  dependsOn: [model]
  name: 'evaluation-ci'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Evaluation regression gate' }
}
resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  parent: account
  name: deploymentName
  sku: { name: 'GlobalStandard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
}
var userRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource callerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, userRole)
  scope: account
  properties: { roleDefinitionId: userRole, principalId: principalId, principalType: principalType }
}
resource evaluatorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, project.id, userRole)
  scope: account
  properties: {
    roleDefinitionId: userRole
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
