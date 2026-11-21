targetScope = 'resourceGroup'

param accountName string
param projectName string = 'lifecycle'
param principalId string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

resource publisherRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'lifecycle-ui-publisher')
  properties: {
    roleName: 'Lifecycle UI publisher ${uniqueString(resourceGroup().id)}'
    description: 'Publish and update applications only in the lifecycle project.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: [
          'Microsoft.CognitiveServices/accounts/projects/applications/read'
          'Microsoft.CognitiveServices/accounts/projects/applications/write'
          'Microsoft.CognitiveServices/accounts/projects/applications/agentdeployments/read'
          'Microsoft.CognitiveServices/accounts/projects/applications/agentdeployments/write'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

resource activityRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'lifecycle-ui-activity-reader')
  properties: {
    roleName: 'Lifecycle UI activity reader ${uniqueString(resourceGroup().id)}'
    description: 'Read Activity Log events for the lifecycle resource group.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: ['Microsoft.Insights/eventtypes/values/read']
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

resource foundryAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, 'lifecycle-ui-foundry')
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
resource publisherAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, publisherRole.id)
  scope: project
  properties: {
    roleDefinitionId: publisherRole.id
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
resource activityAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, principalId, activityRole.id)
  properties: {
    roleDefinitionId: activityRole.id
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output publisherRoleId string = publisherRole.id
output activityRoleId string = activityRole.id
