targetScope = 'resourceGroup'

param location string = resourceGroup().location
param webAppName string
param authClientId string
param authIdentityId string
param authIdentityClientId string
param allowedUserId string
param projectEndpoint string
param foundryAccountName string
param foundryProjectName string = 'surface-support'
param skillProjectEndpoint string = ''
param skillDeployment string = 'gpt-4.1-mini'
param discoveryProjectEndpoint string = ''
param discoveryDeployment string = 'gpt-4.1-mini'
param releaseProjectEndpoint string = ''
param releaseDeployment string = 'gpt-4.1-mini'
param evaluationProjectEndpoint string = ''
param evaluationDeployment string = 'gpt-4.1-mini'
param cicdProjectEndpoint string = ''
param cicdDeployment string = 'gpt-4.1-mini'
param basicsEndpoint string = ''
param basicsChat string = 'basics-chat'
param basicsReasoning string = 'basics-reasoning'
param basicsBatch string = 'basics-batch'
param routerEndpoint string = ''
param routerDeployment string = 'model-router'
param workflowProjectEndpoint string = ''
param workflowDeployment string = 'gpt-4.1-mini'
param webSearchProjectEndpoint string = ''
param webSearchDeployment string = 'gpt-5-mini'
param toolsProjectEndpoint string = ''
param toolsFunctionEndpoint string = ''
param toolsDeployment string = 'gpt-4.1-mini'
param orchestrationProjectEndpoint string = ''
param orchestrationDeployment string = 'gpt-4.1-mini'
param fundamentalsEndpoint string = ''
param fundamentalsDeniedEndpoint string = ''
param fundamentalsDeployment string = 'tutor-mini'
param approvalEndpoint string = ''
param approvalDeployment string = 'gpt-4.1-mini'
param analystProjectEndpoint string = ''
param analystDeployment string = 'gpt-4.1-mini'
param hostedAgentProjectEndpoint string = ''
param ragEndpoint string = ''
param ragSearchEndpoint string = ''
param ragDeployment string = 'rag-chat'
param ragEmbeddingDeployment string = 'rag-embedding'
param cuEndpoint string = ''
param cuDeployment string = 'cu-ga-completion'
param cuEmbeddingDeployment string = 'cu-embedding'
param monitoringProjectEndpoint string = ''
param monitoringConfiguration string = ''
param monitoringWorkspaceId string = ''
param cacheEndpoint string = ''
param cacheDeployment string = 'gpt-4.1-mini'
param lifecycleProjectEndpoint string = ''
param lifecycleProjectResourceId string = ''
param lifecycleDeployment string = 'gpt-4.1-mini'
param fineTuningEndpoint string = ''
param guardrailsEndpoint string = ''
param guardrailsAccountId string = ''
param guardrailsProjectEndpoint string = ''
param guardrailsDeployment string = 'gpt-4.1-mini'
param guardrailsAgentDeployment string = 'agent-baseline'
param documentProjectEndpoint string = ''
param voiceProjectEndpoint string = ''
@allowed(['gpt-realtime', 'gpt-realtime-2.1'])
param voiceModel string = 'gpt-realtime'
param fleetConfiguration string = ''
param toolSearchProjectEndpoint string = ''
param evaluatorEndpoint string = ''
param evaluatorProjectEndpoint string = ''
param evaluatorDeployment string = 'gpt-4.1-mini'
param memoryProjectEndpoint string = ''
param memoryDeployment string = 'gpt-4.1-mini'
param memoryEmbedding string = 'text-embedding-3-small'
param iqSearchEndpoint string = ''
param iqModelEndpoint string = ''
param iqDeployment string = 'gpt-4.1-mini'
param decisionEndpoint string = ''
param decisionDeployment string = 'decision'
param safetyEndpoint string = ''
param structuredEndpoint string = ''
param benchmarkEndpoint string = ''
param benchmarkMini string = 'benchmark-mini'
param benchmarkFull string = 'benchmark-full'
param benchmarkRouter string = 'model-router'
param structuredDeployment string = 'ticket-extractor'
param tracingEndpoint string = ''
param tracingConnectionString string = ''
param tracingDeployment string = 'gpt-4.1-mini'
param conversationEndpoint string = ''
param conversationDeployment string = 'gpt-4.1-mini'
param injectionEndpoint string = ''
param injectionDeployment string = 'gpt-4.1-mini'
param injectionSafetyEndpoint string = ''
param resilienceEndpoint string = ''
param resilienceDeployment string = 'gpt-4.1-mini'
param visionEndpoint string = ''
param visionDeployment string = 'receipt-vision'
param publicAccess bool = false

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundry
  name: foundryProjectName
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${webAppName}-plan'
  location: location
  kind: 'linux'
  sku: { name: 'F1', tier: 'Free', capacity: 1 }
  properties: { reserved: true }
}

resource app 'Microsoft.Web/sites@2023-12-01' = {
  name: webAppName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: { '${authIdentityId}': {} }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: publicAccess ? 'Enabled' : 'Disabled'
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'python 27-FoundryIQwithKB/ui.py'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'UI_AZURE_HOSTED', value: '1' }
        { name: 'FOUNDRY_PROJECT_ENDPOINT', value: projectEndpoint }
        { name: 'UI_SKILLS_PROJECT_ENDPOINT', value: skillProjectEndpoint }
        { name: 'UI_SKILLS_DEPLOYMENT', value: skillDeployment }
        { name: 'UI_DISCOVERY_PROJECT_ENDPOINT', value: discoveryProjectEndpoint }
        { name: 'UI_DISCOVERY_DEPLOYMENT', value: discoveryDeployment }
        { name: 'UI_RELEASE_PROJECT_ENDPOINT', value: releaseProjectEndpoint }
        { name: 'UI_RELEASE_DEPLOYMENT', value: releaseDeployment }
        { name: 'UI_EVALUATION_PROJECT_ENDPOINT', value: evaluationProjectEndpoint }
        { name: 'UI_EVALUATION_DEPLOYMENT', value: evaluationDeployment }
        { name: 'UI_CICD_PROJECT_ENDPOINT', value: cicdProjectEndpoint }
        { name: 'UI_CICD_DEPLOYMENT', value: cicdDeployment }
        { name: 'UI_JOB_STATE_DIR', value: '/home/data/workshop-jobs' }
        { name: 'UI_BASICS_ENDPOINT', value: basicsEndpoint }
        { name: 'UI_BASICS_CHAT', value: basicsChat }
        { name: 'UI_BASICS_REASONING', value: basicsReasoning }
        { name: 'UI_BASICS_BATCH', value: basicsBatch }
        { name: 'UI_BATCH_STATE_DIR', value: '/home/data/model-basics-ui' }
        { name: 'UI_ROUTER_ENDPOINT', value: routerEndpoint }
        { name: 'UI_ROUTER_DEPLOYMENT', value: routerDeployment }
        { name: 'UI_WORKFLOW_PROJECT_ENDPOINT', value: workflowProjectEndpoint }
        { name: 'UI_WORKFLOW_DEPLOYMENT', value: workflowDeployment }
        { name: 'UI_WEB_SEARCH_PROJECT_ENDPOINT', value: webSearchProjectEndpoint }
        { name: 'UI_WEB_SEARCH_DEPLOYMENT', value: webSearchDeployment }
        { name: 'UI_TOOLS_PROJECT_ENDPOINT', value: toolsProjectEndpoint }
        { name: 'UI_TOOLS_FUNCTION_ENDPOINT', value: toolsFunctionEndpoint }
        { name: 'UI_TOOLS_DEPLOYMENT', value: toolsDeployment }
        { name: 'UI_ORCHESTRATION_PROJECT_ENDPOINT', value: orchestrationProjectEndpoint }
        { name: 'UI_ORCHESTRATION_DEPLOYMENT', value: orchestrationDeployment }
        { name: 'UI_FUNDAMENTALS_ENDPOINT', value: fundamentalsEndpoint }
        { name: 'UI_FUNDAMENTALS_DENIED_ENDPOINT', value: fundamentalsDeniedEndpoint }
        { name: 'UI_FUNDAMENTALS_DEPLOYMENT', value: fundamentalsDeployment }
        { name: 'UI_APPROVAL_ENDPOINT', value: approvalEndpoint }
        { name: 'UI_APPROVAL_DEPLOYMENT', value: approvalDeployment }
        { name: 'UI_APPROVAL_STATE_PATH', value: '/home/data/human-approval-ui/bookings.db' }
        { name: 'UI_PRESENTER_TENANT_ID', value: tenant().tenantId }
        { name: 'UI_PRESENTER_OBJECT_ID', value: allowedUserId }
        { name: 'UI_ANALYST_PROJECT_ENDPOINT', value: analystProjectEndpoint }
        { name: 'UI_ANALYST_DEPLOYMENT', value: analystDeployment }
        { name: 'UI_HOSTED_AGENT_PROJECT_ENDPOINT', value: hostedAgentProjectEndpoint }
        { name: 'UI_RAG_ENDPOINT', value: ragEndpoint }
        { name: 'UI_RAG_SEARCH_ENDPOINT', value: ragSearchEndpoint }
        { name: 'UI_RAG_DEPLOYMENT', value: ragDeployment }
        { name: 'UI_RAG_EMBEDDING_DEPLOYMENT', value: ragEmbeddingDeployment }
        { name: 'UI_CU_ENDPOINT', value: cuEndpoint }
        { name: 'UI_CU_DEPLOYMENT', value: cuDeployment }
        { name: 'UI_CU_EMBEDDING_DEPLOYMENT', value: cuEmbeddingDeployment }
        { name: 'UI_MONITORING_PROJECT_ENDPOINT', value: monitoringProjectEndpoint }
        { name: 'UI_MONITORING_CONFIGURATION', value: monitoringConfiguration }
        { name: 'UI_MONITORING_WORKSPACE_ID', value: monitoringWorkspaceId }
        { name: 'UI_CACHE_ENDPOINT', value: cacheEndpoint }
        { name: 'UI_CACHE_DEPLOYMENT', value: cacheDeployment }
        { name: 'UI_LIFECYCLE_PROJECT_ENDPOINT', value: lifecycleProjectEndpoint }
        { name: 'UI_LIFECYCLE_PROJECT_RESOURCE_ID', value: lifecycleProjectResourceId }
        { name: 'UI_LIFECYCLE_DEPLOYMENT', value: lifecycleDeployment }
        { name: 'UI_FINE_TUNING_ENDPOINT', value: fineTuningEndpoint }
        { name: 'UI_GUARDRAILS_ENDPOINT', value: guardrailsEndpoint }
        { name: 'UI_GUARDRAILS_ACCOUNT_ID', value: guardrailsAccountId }
        { name: 'UI_GUARDRAILS_PROJECT_ENDPOINT', value: guardrailsProjectEndpoint }
        { name: 'UI_GUARDRAILS_DEPLOYMENT', value: guardrailsDeployment }
        { name: 'UI_GUARDRAILS_AGENT_DEPLOYMENT', value: guardrailsAgentDeployment }
        { name: 'UI_DOCUMENT_PROJECT_ENDPOINT', value: documentProjectEndpoint }
        { name: 'UI_VOICE_PROJECT_ENDPOINT', value: voiceProjectEndpoint }
        { name: 'UI_VOICE_MODEL', value: voiceModel }
        { name: 'UI_FLEET_CONFIGURATION', value: fleetConfiguration }
        { name: 'UI_TOOL_SEARCH_PROJECT_ENDPOINT', value: toolSearchProjectEndpoint }
        { name: 'UI_EVALUATOR_ENDPOINT', value: evaluatorEndpoint }
        { name: 'UI_EVALUATOR_PROJECT_ENDPOINT', value: evaluatorProjectEndpoint }
        { name: 'UI_EVALUATOR_DEPLOYMENT', value: evaluatorDeployment }
        { name: 'UI_MEMORY_PROJECT_ENDPOINT', value: memoryProjectEndpoint }
        { name: 'UI_MEMORY_DEPLOYMENT', value: memoryDeployment }
        { name: 'UI_MEMORY_EMBEDDING', value: memoryEmbedding }
        { name: 'UI_IQ_SEARCH_ENDPOINT', value: iqSearchEndpoint }
        { name: 'UI_IQ_MODEL_ENDPOINT', value: iqModelEndpoint }
        { name: 'UI_IQ_DEPLOYMENT', value: iqDeployment }
        { name: 'UI_DECISION_ENDPOINT', value: decisionEndpoint }
        { name: 'UI_DECISION_DEPLOYMENT', value: decisionDeployment }
        { name: 'UI_SAFETY_ENDPOINT', value: safetyEndpoint }
        { name: 'UI_STRUCTURED_ENDPOINT', value: structuredEndpoint }
        { name: 'UI_BENCHMARK_ENDPOINT', value: benchmarkEndpoint }
        { name: 'UI_BENCHMARK_MINI', value: benchmarkMini }
        { name: 'UI_BENCHMARK_FULL', value: benchmarkFull }
        { name: 'UI_BENCHMARK_ROUTER', value: benchmarkRouter }
        { name: 'UI_STRUCTURED_DEPLOYMENT', value: structuredDeployment }
        { name: 'UI_TRACING_ENDPOINT', value: tracingEndpoint }
        { name: 'UI_TRACING_CONNECTION_STRING', value: tracingConnectionString }
        { name: 'UI_TRACING_DEPLOYMENT', value: tracingDeployment }
        { name: 'UI_CONVERSATION_ENDPOINT', value: conversationEndpoint }
        { name: 'UI_CONVERSATION_DEPLOYMENT', value: conversationDeployment }
        { name: 'UI_CONVERSATION_STATE_PATH', value: '/home/data/conversation-state-ui/state.sqlite3' }
        { name: 'UI_INJECTION_ENDPOINT', value: injectionEndpoint }
        { name: 'UI_INJECTION_DEPLOYMENT', value: injectionDeployment }
        { name: 'UI_INJECTION_SAFETY_ENDPOINT', value: injectionSafetyEndpoint }
        { name: 'UI_RESILIENCE_ENDPOINT', value: resilienceEndpoint }
        { name: 'UI_RESILIENCE_DEPLOYMENT', value: resilienceDeployment }
        { name: 'UI_VISION_ENDPOINT', value: visionEndpoint }
        { name: 'UI_VISION_DEPLOYMENT', value: visionDeployment }
        { name: 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID', value: authIdentityClientId }
      ]
    }
  }
}

resource auth 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: app
  name: 'authsettingsV2'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureActiveDirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: authClientId
          clientSecretSettingName: 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
        validation: {
          defaultAuthorizationPolicy: {
            allowedPrincipals: { identities: [allowedUserId] }
          }
        }
      }
    }
    login: { tokenStore: { enabled: true } }
    httpSettings: { requireHttps: true }
  }
}

resource appFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, app.id, 'foundry-user')
  scope: project
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output url string = 'https://${app.properties.defaultHostName}'
