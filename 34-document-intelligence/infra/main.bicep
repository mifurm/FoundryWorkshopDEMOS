targetScope = 'resourceGroup'

param location string = 'eastus2'
param functionLocation string = 'westus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'foundry-docs-${uniqueString(resourceGroup().id)}'
param projectName string = 'document-intelligence'
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

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  tags: securityTags
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Document Intelligence invoice agent'
    description: 'Persistent agent with a cloud-hosted, key-protected invoice extraction tool.'
  }
  dependsOn: [model]
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

resource documents 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  tags: securityTags
  name: 'doc-intel-${uniqueString(resourceGroup().id)}'
  location: location
  kind: 'FormRecognizer'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'doc-intel-${uniqueString(resourceGroup().id)}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2025-01-01' = {
  tags: securityTags
  name: 'docs${uniqueString(resourceGroup().id)}'
  location: functionLocation
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-11-01' = {
  tags: securityTags
  name: 'document-tool-plan'
  location: functionLocation
  sku: {
    name: 'P0v3'
    tier: 'PremiumV3'
    capacity: 1
  }
}

resource functionApp 'Microsoft.Web/sites@2024-11-01' = {
  tags: securityTags
  name: 'document-tool-${uniqueString(resourceGroup().id)}'
  location: functionLocation
  kind: 'functionapp'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      alwaysOn: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'node' }
        { name: 'WEBSITE_NODE_DEFAULT_VERSION', value: '~22' }
        { name: 'AzureFunctionsJobHost__logging__fileLoggingMode', value: 'always' }
        { name: 'AzureFunctionsJobHost__logging__logLevel__default', value: 'Information' }
        { name: 'DOCUMENT_INTELLIGENCE_ENDPOINT', value: documents.properties.endpoint }
      ]
    }
  }
}

var blobRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
resource hostStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, blobRoleId)
  scope: storage
  properties: {
    roleDefinitionId: blobRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

var documentRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
resource documentRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(documents.id, functionApp.id, documentRoleId)
  scope: documents
  properties: {
    roleDefinitionId: documentRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

var samplePdfs = {
  clear: loadFileAsBase64('../samples/clear.pdf')
  'missing-total': loadFileAsBase64('../samples/missing-total.pdf')
  'office-supplies': loadFileAsBase64('../samples/office-supplies.pdf')
  'training-materials': loadFileAsBase64('../samples/training-materials.pdf')
  catering: loadFileAsBase64('../samples/catering.pdf')
  equipment: loadFileAsBase64('../samples/equipment.pdf')
  printing: loadFileAsBase64('../samples/printing.pdf')
  'zero-total': loadFileAsBase64('../samples/zero-total.pdf')
  'large-total': loadFileAsBase64('../samples/large-total.pdf')
  'eur-invoice': loadFileAsBase64('../samples/eur-invoice.pdf')
  'missing-total-office': loadFileAsBase64('../samples/missing-total-office.pdf')
  'missing-total-catering': loadFileAsBase64('../samples/missing-total-catering.pdf')
}

resource extractFunction 'Microsoft.Web/sites/functions@2024-11-01' = {
  parent: functionApp
  name: 'extract-invoice'
  properties: {
    config: {
      scriptFile: 'index.js'
      bindings: [
        {
          name: 'req'
          type: 'httpTrigger'
          direction: 'in'
          authLevel: 'function'
          methods: ['get']
          route: 'invoices/{document_id}'
        }
        { name: 'res', type: 'http', direction: 'out' }
      ]
    }
    files: {
      'index.js': loadTextContent('../tools/extract-invoice.js')
      'documents.json': string(samplePdfs)
    }
  }
  dependsOn: [hostStorageRole, documentRole]
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output projectResourceId string = project.id
output functionResourceId string = functionApp.id
output toolEndpoint string = 'https://${functionApp.properties.defaultHostName}/api'
output documentIntelligenceEndpoint string = documents.properties.endpoint
