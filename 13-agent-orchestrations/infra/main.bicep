targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'foundry-patterns-${uniqueString(resourceGroup().id)}'
param projectName string = 'orchestration-demo'
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
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
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
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'Four simple orchestration patterns'
    description: 'Local Sequential, Concurrent, Group Chat, and Magentic examples.'
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
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

var userRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource presenterRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, userRole)
  scope: account
  properties: {
    roleDefinitionId: userRole
    principalId: principalId
    principalType: 'User'
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
