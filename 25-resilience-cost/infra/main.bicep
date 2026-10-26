targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
param accountName string = 'resilience-${uniqueString(resourceGroup().id)}'

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
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
  }
}
resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  parent: account
  name: 'gpt-4.1-mini'
  sku: { name: 'GlobalStandard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
}
var role = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
resource inference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, role)
  scope: account
  properties: { principalId: principalId, principalType: 'User', roleDefinitionId: role }
}
output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
