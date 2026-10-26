targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Search region supporting agentic retrieval; independent of the model region.')
param searchLocation string = 'westus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'foundry-iq-${uniqueString(resourceGroup().id)}'
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
  properties: {
    customSubDomainName: accountName
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

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
