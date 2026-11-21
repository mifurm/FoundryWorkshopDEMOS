targetScope = 'resourceGroup'

param accountName string
param principalId string
@description('Built-in inference role definition GUID.')
param roleDefinitionGuid string = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}
var roleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionGuid)
resource uiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, roleId)
  scope: account
  properties: {
    roleDefinitionId: roleId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output roleAssignmentId string = uiRole.id
