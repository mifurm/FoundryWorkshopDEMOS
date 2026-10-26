targetScope = 'resourceGroup'

@description('A region supporting the selected model router version. East US 2 supports the default version.')
param location string = 'eastus2'

@description('Globally unique Foundry account name and custom subdomain.')
@minLength(2)
@maxLength(64)
param accountName string = 'foundry-router-${uniqueString(resourceGroup().id)}'

@description('Object ID of the signed-in presenter. Obtain with az ad signed-in-user show --query id -o tsv.')
param principalId string

@description('Name used by the Python client to address the model router deployment.')
param deploymentName string = 'model-router'

@description('Model router version. Verify availability in the selected region before changing.')
param modelVersion string = '2025-11-18'

@description('Deployment capacity units. Available quota and rate limits depend on the subscription and model.')
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
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource router 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = {
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
      name: 'model-router'
      version: modelVersion
    }
  }
}

var openAIUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')

resource inferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, openAIUserRoleId)
  scope: account
  properties: {
    roleDefinitionId: openAIUserRoleId
    principalId: principalId
    principalType: 'User'
  }
}

output endpoint string = 'https://${account.name}.openai.azure.com/'
output deployment string = router.name
output accountResourceId string = account.id
