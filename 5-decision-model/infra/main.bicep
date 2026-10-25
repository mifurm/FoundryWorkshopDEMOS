targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'foundry-decision-${uniqueString(resourceGroup().id)}'
param deploymentName string = 'decision'
@minValue(1)
param capacity int = 1

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
      format: 'Microsoft'
      name: 'Microsoft-Decision-1'
      version: '1'
    }
  }
}

var userRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource presenterRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, userRoleId)
  scope: account
  properties: {
    roleDefinitionId: userRoleId
    principalId: principalId
    principalType: 'User'
  }
}

output endpoint string = 'https://${account.name}.services.ai.azure.com'
output deployment string = model.name
