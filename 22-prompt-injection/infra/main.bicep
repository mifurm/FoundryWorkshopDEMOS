targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
param accountName string = 'injection-${uniqueString(resourceGroup().id)}'
param safetyName string = 'shield-${uniqueString(resourceGroup().id)}'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
  }
}
resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'gpt-4.1-mini'
  sku: { name: 'GlobalStandard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
}
resource safety 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: safetyName
  location: location
  kind: 'ContentSafety'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: safetyName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}
var inferenceRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
var safetyRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
resource modelAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, inferenceRole)
  scope: account
  properties: { principalId: principalId, principalType: 'User', roleDefinitionId: inferenceRole }
}
resource safetyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(safety.id, principalId, safetyRole)
  scope: safety
  properties: { principalId: principalId, principalType: 'User', roleDefinitionId: safetyRole }
}
output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output contentSafetyEndpoint string = 'https://${safety.name}.cognitiveservices.azure.com/'
