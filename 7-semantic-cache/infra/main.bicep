targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'foundry-cache-${uniqueString(resourceGroup().id)}'
param deploymentName string = 'gpt-4.1-mini'
param embeddingDeploymentName string = 'text-embedding-3-small'
param gatewayName string = 'apim-cache-${uniqueString(resourceGroup().id)}'
param redisName string = 'redis-cache-${uniqueString(resourceGroup().id)}'
@description('Workshop Balanced-tier Redis SKU. Larger sizes increase standing costs; availability is region/subscription dependent.')
@allowed([
  'Balanced_B0'
  'Balanced_B1'
  'Balanced_B3'
  'Balanced_B5'
])
param redisSku string = 'Balanced_B0'
@description('Cosine distance, not similarity: lower values require a closer match.')
@allowed([
  '0.05'
  '0.10'
  '0.15'
  '0.20'
])
param scoreThreshold string = '0.15'
@minValue(1)
@maxValue(3600)
param cacheDuration int = 300
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

resource embeddings 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  tags: securityTags
  parent: account
  name: embeddingDeploymentName
  dependsOn: [
    model
  ]
  sku: {
    name: 'GlobalStandard'
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

resource gateway 'Microsoft.ApiManagement/service@2024-05-01' = {
  tags: securityTags
  name: gatewayName
  location: location
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherName: 'Foundry workshop'
    publisherEmail: 'workshop@example.com'
  }
}

var inferenceRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a001fd3d-188f-4b5d-821b-7da978bf7442')
resource gatewayRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, gateway.id, inferenceRoleId)
  scope: account
  properties: {
    roleDefinitionId: inferenceRoleId
    principalId: gateway.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource redis 'Microsoft.Cache/redisEnterprise@2025-07-01' = {
  tags: securityTags
  name: redisName
  location: location
  sku: {
    name: redisSku
  }
  properties: {
    highAvailability: 'Disabled'
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource database 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = {
  parent: redis
  name: 'default'
  properties: {
    accessKeysAuthentication: 'Enabled'
    clientProtocol: 'Encrypted'
    port: 10000
    clusteringPolicy: 'EnterpriseCluster'
    evictionPolicy: 'NoEviction'
    modules: [
      {
        name: 'RediSearch'
      }
    ]
  }
}

resource externalCache 'Microsoft.ApiManagement/service/caches@2024-05-01' = {
  parent: gateway
  name: 'default'
  properties: {
    connectionString: '${redis.properties.hostName}:10000,password=${database.listKeys().primaryKey},ssl=True,abortConnect=False'
    resourceId: uri(environment().resourceManager, redis.id)
    useFromLocation: 'default'
  }
}

resource embeddingsBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: gateway
  name: 'workshop-embeddings'
  properties: {
    protocol: 'http'
    url: 'https://${account.name}.openai.azure.com/openai/deployments/${embeddings.name}/embeddings'
  }
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: gateway
  name: 'workshop-chat'
  properties: {
    displayName: 'Foundry chat with gateway semantic caching'
    path: 'ai'
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    serviceUrl: 'https://${account.name}.openai.azure.com'
  }
}

resource chat 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'chat-completions'
  properties: {
    displayName: 'Chat completions'
    method: 'POST'
    urlTemplate: '/openai/deployments/${model.name}/chat/completions'
    responses: []
  }
}

var identityPolicy = replace(replace(loadTextContent('policy.xml'), '__TENANT_ID__', tenant().tenantId), '__PRINCIPAL_ID__', principalId)
var lookupPolicy = replace(identityPolicy, '__SCORE_THRESHOLD__', scoreThreshold)

resource policy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = {
  parent: chat
  name: 'policy'
  dependsOn: [
    gatewayRole
    externalCache
    embeddingsBackend
  ]
  properties: {
    format: 'rawxml'
    value: replace(replace(lookupPolicy, '__CACHE_DURATION__', string(cacheDuration)), '__CHAT_DEPLOYMENT__', model.name)
  }
}

output endpoint string = '${gateway.properties.gatewayUrl}/ai'
output deployment string = model.name
output embeddingDeployment string = embeddings.name
