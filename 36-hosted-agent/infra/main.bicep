targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
param accountName string = 'hosted-${uniqueString(resourceGroup().id)}'
param deploymentName string = 'gpt-5.4-mini'

var securityTags = {
  SecurityControl: 'Ignore'
  SecurityExemption: 'StorageAccountKeyAccess'
}

resource account 'Microsoft.CognitiveServices/accounts@2026-09-01' = {
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
resource model 'Microsoft.CognitiveServices/accounts/deployments@2026-09-01' = {
  tags: securityTags
  parent: account
  name: deploymentName
  sku: { name: 'GlobalStandard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-5.4-mini', version: '2026-03-17' }
  }
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2026-09-01' = {
  tags: securityTags
  parent: account
  name: 'hosted-agent'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Lab 36 - Custom code on Foundry' }
  dependsOn: [model]
}
var managerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'eadc314b-1a2d-4efa-be10-5d325db5065e')
resource presenterRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, managerRoleId)
  scope: account
  properties: { roleDefinitionId: managerRoleId, principalId: principalId, principalType: 'User' }
}
resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' = {
  tags: securityTags
  name: 'hosted${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false, publicNetworkAccess: 'Enabled' }
}
var pullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
resource imagePull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, project.id, pullRoleId)
  scope: registry
  properties: {
    roleDefinitionId: pullRoleId
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  tags: securityTags
  name: '${accountName}-logs'
  location: location
  properties: { retentionInDays: 30, sku: { name: 'PerGB2018' } }
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  tags: securityTags
  name: '${accountName}-insights'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableLocalAuth: true
  }
}
var publisherRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
resource tracePublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(insights.id, project.id, publisherRoleId)
  scope: insights
  properties: {
    roleDefinitionId: publisherRoleId
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
resource telemetryConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2026-09-01' = {
  parent: project
  name: 'application-insights'
  properties: {
    category: 'AppInsights'
    authType: 'ProjectManagedIdentity'
    target: insights.id
    isSharedToAll: false
    metadata: {
      ResourceId: insights.id
      ApplicationInsightsConnectionString: insights.properties.ConnectionString
    }
  }
  dependsOn: [tracePublisher]
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output projectResourceId string = project.id
output registryName string = registry.name
output registryServer string = registry.properties.loginServer
output applicationInsightsResourceId string = insights.id
output workspaceId string = workspace.properties.customerId
