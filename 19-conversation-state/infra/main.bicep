targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the presenter signed in through Azure CLI.')
param principalId string
param accountName string = 'state-${uniqueString(resourceGroup().id)}'
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

var inferenceRole = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
resource presenterInference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, inferenceRole)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', inferenceRole)
    principalId: principalId
    principalType: 'User'
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
