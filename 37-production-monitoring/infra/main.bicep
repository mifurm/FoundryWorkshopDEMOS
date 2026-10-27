targetScope = 'resourceGroup'

param location string = 'eastus2'
param principalId string
param accountName string = 'monitor-${uniqueString(resourceGroup().id)}'
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
  name: 'production-monitoring'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Lab 37 - Live agent quality and recovery' }
  dependsOn: [model]
}
var userRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource presenterRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, userRoleId)
  scope: account
  properties: { roleDefinitionId: userRoleId, principalId: principalId, principalType: 'User' }
}
resource evaluationRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, project.id, userRoleId)
  scope: account
  properties: {
    roleDefinitionId: userRoleId
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
var readerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
resource traceReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(insights.id, project.id, readerRoleId)
  scope: insights
  properties: {
    roleDefinitionId: readerRoleId
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
var logsReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')
resource evaluationLogsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, project.id, logsReaderRoleId)
  scope: workspace
  properties: {
    roleDefinitionId: logsReaderRoleId
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
  dependsOn: [tracePublisher, traceReader, evaluationLogsReader]
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = model.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output projectResourceId string = project.id
output applicationInsightsResourceId string = insights.id
output workspaceId string = workspace.properties.customerId
