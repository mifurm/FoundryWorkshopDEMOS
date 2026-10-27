targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Search region supporting agentic retrieval; independent of the model region.')
param searchLocation string = 'westus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'foundry-iq-${uniqueString(resourceGroup().id)}'
param projectName string = 'foundry-iq-demo'
param searchName string = 'iq-search-${uniqueString(resourceGroup().id)}'
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
    displayName: 'Foundry IQ workshop'
    description: 'Project for the Search-backed knowledge retrieval workshop.'
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  // Serialize account child writes to avoid project/model RequestConflict errors.
  dependsOn: [
    project
  ]
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

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  tags: securityTags
  name: searchName
  location: searchLocation
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
    semanticSearch: 'free'
  }
}

var searchRoles = [
  '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
  '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  '1407120a-92aa-4202-b7e9-c0e197c71c8f'
]
resource presenterSearchRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for roleId in searchRoles: {
  name: guid(search.id, principalId, roleId)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
    principalId: principalId
    principalType: 'User'
  }
}]

var inferenceRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
resource searchInferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, search.id, inferenceRole)
  scope: account
  properties: {
    roleDefinitionId: inferenceRole
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

var foundryUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource presenterProjectRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, foundryUserRole)
  scope: project
  properties: {
    roleDefinitionId: foundryUserRole
    principalId: principalId
    principalType: 'User'
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output projectResourceId string = project.id
output deployment string = model.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
