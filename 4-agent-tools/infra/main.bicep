targetScope = 'resourceGroup'

param location string = 'eastus2'

@description('Region for Function hosting and storage; independent of Foundry model availability.')
param functionLocation string = 'westus2'

@description('Object ID of the presenter signed in with az login.')
param principalId string

param accountName string = 'foundry-tools-${uniqueString(resourceGroup().id)}'
param projectName string = 'tools-demo'
param deploymentName string = 'gpt-4.1-mini'
param modelName string = 'gpt-4.1-mini'
param modelVersion string = '2025-04-14'

@minValue(1)
param capacity int = 10

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
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
  parent: account
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'Tools demo'
    description: 'A Foundry toolbox wrapping a read-only Azure Function.'
  }
  dependsOn: [
    model
  ]
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
      name: modelName
      version: modelVersion
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

resource storage 'Microsoft.Storage/storageAccounts@2025-01-01' = {
  name: 'tool${uniqueString(resourceGroup().id)}'
  location: functionLocation
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: 'tools-plan'
  location: functionLocation
  sku: {
    name: 'P0v3'
    tier: 'PremiumV3'
    capacity: 1
  }
}

resource functionApp 'Microsoft.Web/sites@2024-11-01' = {
  name: 'workshop-tools-${uniqueString(resourceGroup().id)}'
  location: functionLocation
  kind: 'functionapp'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      alwaysOn: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storage.name
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'node'
        }
        {
          name: 'WEBSITE_NODE_DEFAULT_VERSION'
          value: '~22'
        }
      ]
    }
  }
}

var blobOwnerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')

resource hostStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, blobOwnerRoleId)
  scope: storage
  properties: {
    roleDefinitionId: blobOwnerRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource priceFunction 'Microsoft.Web/sites/functions@2024-11-01' = {
  parent: functionApp
  name: 'workshop-price'
  properties: {
    config: {
      scriptFile: 'index.js'
      bindings: [
        {
          name: 'req'
          type: 'httpTrigger'
          direction: 'in'
          authLevel: 'anonymous'
          methods: [
            'get'
          ]
        }
        {
          name: 'res'
          type: 'http'
          direction: 'out'
        }
      ]
    }
    files: {
      'index.js': loadTextContent('../tools/workshop-price.js')
    }
  }
  dependsOn: [
    hostStorageRole
  ]
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output toolEndpoint string = 'https://${functionApp.properties.defaultHostName}/api'
