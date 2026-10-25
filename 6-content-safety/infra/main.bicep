targetScope = 'resourceGroup'

param location string = 'eastus2'
@description('Object ID of the presenter signed in with az login.')
param principalId string
param accountName string = 'safety-${uniqueString(resourceGroup().id)}'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'ContentSafety'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

var userRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
resource presenterRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, userRoleId)
  scope: account
  properties: {
    roleDefinitionId: userRoleId
    principalId: principalId
    principalType: 'User'
  }
}

output endpoint string = 'https://${account.name}.cognitiveservices.azure.com/'
