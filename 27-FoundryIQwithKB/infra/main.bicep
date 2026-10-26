targetScope = 'resourceGroup'

param location string = 'eastus2'
param searchLocation string = 'westus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'surface-iq-${uniqueString(resourceGroup().id)}'
param searchName string = 'surface-iq-${uniqueString(resourceGroup().id)}'
param storageName string = 'surfaceiq${uniqueString(resourceGroup().id)}'
param deploymentName string = 'gpt-4.1-mini'
param embeddingDeploymentName string = 'text-embedding-3-small'

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
  identity: { type: 'SystemAssigned' }
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
  sku: { name: 'GlobalStandard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
}

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  parent: account
  dependsOn: [model]
  name: embeddingDeploymentName
  sku: { name: 'Standard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'text-embedding-3-small', version: '1' }
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  tags: securityTags
  parent: account
  // Account child writes must not overlap during initial provisioning.
  dependsOn: [embedding]
  name: 'surface-support'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Surface support - Foundry IQ knowledge base' }
}

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  tags: securityTags
  name: searchName
  location: searchLocation
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
    semanticSearch: 'free'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  tags: securityTags
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for container in ['support-cases', 'surface-kb']: {
  parent: blobService
  name: container
  properties: { publicAccess: 'None' }
}]

var presenterSearchRoles = [
  '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor
  '1407120a-92aa-4202-b7e9-c0e197c71c8f' // Search Index Data Reader
]
resource presenterSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in presenterSearchRoles: {
  name: guid(search.id, principalId, role)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
    principalId: principalId
    principalType: 'User'
  }
}]
var projectManagerRole = 'eadc314b-1a2d-4efa-be10-5d325db5065e'
resource presenterFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, projectManagerRole)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', projectManagerRole)
    principalId: principalId
    principalType: 'User'
  }
}
var storageContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
resource presenterStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, storageContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageContributor)
    principalId: principalId
    principalType: 'User'
  }
}
var storageReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
resource searchStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, search.id, storageReader)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageReader)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
var cognitiveUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
resource searchModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, search.id, cognitiveUser)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveUser)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
var searchReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
resource projectSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, project.id, searchReader)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchReader)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output embeddingDeployment string = embedding.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output projectResourceId string = project.id
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchResourceId string = search.id
output storageEndpoint string = storage.properties.primaryEndpoints.blob
output storageResourceId string = storage.id
