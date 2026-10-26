targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
param accountName string = 'structured-${uniqueString(resourceGroup().id)}'
param deploymentName string = 'ticket-extractor'
@allowed(['User', 'ServicePrincipal', 'Group'])
param principalType string = 'User'
@minValue(1)
param capacity int = 10

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
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

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: deploymentName
  sku: { name: 'GlobalStandard', capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
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
output deployment string = model.name
