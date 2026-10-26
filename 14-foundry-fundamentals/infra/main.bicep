targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
param accountName string = 'fundamentals-${uniqueString(resourceGroup().id)}'
param projectName string = 'workshop-tutor'
param deploymentName string = 'tutor-mini'
@allowed(['User', 'ServicePrincipal', 'Group'])
param principalType string = 'User'
@minValue(1)
param capacity int = 10

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
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

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Workshop tutor'
    description: 'Project containers and keyless model inference.'
  }
  // Account child writes can conflict when project and deployment run concurrently.
  dependsOn: [model]
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: deploymentName
  sku: { name: 'GlobalStandard', capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
}

var roleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
resource inferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, roleId)
  scope: account
  properties: {
    roleDefinitionId: roleId
    principalId: principalId
    principalType: principalType
  }
}

// No scoped inference role here; the caller must also lack inherited data-plane roles.
resource deniedAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: '${accountName}-deny'
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${accountName}-deny'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource deniedModel 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: deniedAccount
  name: deploymentName
  sku: { name: 'GlobalStandard', capacity: capacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output deniedEndpoint string = 'https://${deniedAccount.name}.openai.azure.com/'
