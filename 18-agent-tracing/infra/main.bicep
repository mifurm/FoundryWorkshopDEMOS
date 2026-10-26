targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the caller; use ServicePrincipal for federated CI.')
param principalId string
@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'
param accountName string = 'trace-${uniqueString(resourceGroup().id)}'
param deploymentName string = 'gpt-4.1-mini'

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
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  tags: securityTags
  parent: account
  // The account rejects concurrent model deployment and project writes.
  dependsOn: [model]
  name: 'tracing'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Synthetic agent tracing' }
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
var userRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource callerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, userRole)
  scope: account
  properties: { roleDefinitionId: userRole, principalId: principalId, principalType: principalType }
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
var publisherRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
resource telemetryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(insights.id, principalId, publisherRole)
  scope: insights
  properties: { roleDefinitionId: publisherRole, principalId: principalId, principalType: principalType }
}
output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output applicationInsightsConnectionString string = insights.properties.ConnectionString
output applicationInsightsResourceId string = insights.id
