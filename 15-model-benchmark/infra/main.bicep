targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
param accountName string = 'benchmark-${uniqueString(resourceGroup().id)}'
@allowed(['User', 'ServicePrincipal', 'Group'])
param principalType string = 'User'
@minValue(1)
param capacity int = 10
param routerVersion string = '2025-11-18'

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
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource mini 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  parent: account
  name: 'benchmark-mini'
  sku: { name: 'GlobalStandard', capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
}

resource full 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  parent: account
  name: 'benchmark-full'
  sku: { name: 'GlobalStandard', capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1', version: '2025-04-14' }
  }
  dependsOn: [mini]
}

resource router 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
  tags: securityTags
  parent: account
  name: 'model-router'
  sku: { name: 'GlobalStandard', capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: 'model-router', version: routerVersion }
  }
  dependsOn: [full]
}

var roleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
resource inferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, roleId)
  scope: account
  properties: {
    roleDefinitionId: roleId
    principalId: principalId
    principalType: principalType
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = mini.name
output alternateDeployment string = full.name
output routerDeployment string = router.name
