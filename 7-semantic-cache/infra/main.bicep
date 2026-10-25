targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'foundry-cache-${uniqueString(resourceGroup().id)}'
param deploymentName string = 'gpt-4.1-mini'
param embeddingDeploymentName string = 'text-embedding-3-small'
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

resource embeddings 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: embeddingDeploymentName
  dependsOn: [
    model
  ]
  sku: {
    name: 'Standard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
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

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output embeddingDeployment string = embeddings.name
