targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the presenter/backend user signed in through the default Azure CLI session.')
param principalId string
param accountName string = 'foundry-memory-${uniqueString(resourceGroup().id)}'
param projectName string = 'native-agent-memory'
param deploymentName string = 'gpt-4.1-mini'
param embeddingDeploymentName string = 'text-embedding-3-small'
@minValue(1)
param capacity int = 10
@minValue(1)
param embeddingCapacity int = 10

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

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  parent: account
  name: embeddingDeploymentName
  sku: {
    name: 'Standard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
  }
  // Account child writes are serialized to avoid Azure RequestConflict.
  dependsOn: [model]
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
    displayName: 'Native agent memory demo'
    description: 'Native extraction, scoped memory search and forgetting for two authenticated users.'
  }
  dependsOn: [embedding]
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

resource projectRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, project.id, userRoleId)
  scope: account
  properties: {
    roleDefinitionId: userRoleId
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output embeddingDeployment string = embedding.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
