targetScope = 'subscription'

@description('Azure region — locked to East US 2 for this system')
@allowed(['eastus2'])
param location string = 'eastus2'

@description('Resource group name')
param resourceGroupName string = 'rg-foundry-ocr'

@description('Base name for resources')
param baseName string = 'foundryocr'

@description('Container image tags, built and pushed by scripts/build_and_push.ps1')
param paddleImageTag string = 'latest'
param rapidOcrImageTag string = 'latest'
param orchestratorImageTag string = 'latest'

@description('Azure OpenAI model deployment used by the Foundry agent (see agent/setup_agent.py)')
param agentModelDeploymentName string = 'gpt-5-mini'

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
}

// Belt-and-suspenders region lock: the @allowed param blocks a wrong `location` at
// deploy time, this policy blocks *any* resource in the RG from landing outside
// East US 2 even if a future module forgets to thread the param through.
// (Extension resources like policyAssignment need a module to target a scope other
// than this file's — a bare `resource ... scope: rg` isn't deployable here.)
module policy 'policy.bicep' = {
  name: 'policy'
  scope: rg
  params: { location: location }
}

// ACR's name is reconstructed here (matching shared.bicep's naming) instead of read
// from shared.outputs.acrId — listCredentials() requires an argument that's known at
// the start of the deployment, and a module output isn't available until that module
// has actually run.
// resourceId() needs subscriptionId spelled out here: at subscription deployment
// scope there's no "current resource group" to default a single extra prefix arg
// to, so a lone resourceGroupName arg gets misread as subscriptionId instead.
var acrResourceId = resourceId(subscription().subscriptionId, resourceGroupName, 'Microsoft.ContainerRegistry/registries', toLower('acr${baseName}${uniqueString(resourceId('Microsoft.Resources/resourceGroups', resourceGroupName))}'))

module shared 'shared.bicep' = {
  name: 'shared'
  scope: rg
  params: { location: location, baseName: baseName }
}

module storage 'storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    baseName: baseName
    logAnalyticsWorkspaceId: shared.outputs.logAnalyticsWorkspaceId
  }
}

module servicebus 'servicebus.bicep' = {
  name: 'servicebus'
  scope: rg
  params: {
    location: location
    baseName: baseName
    logAnalyticsWorkspaceId: shared.outputs.logAnalyticsWorkspaceId
  }
}

module foundry 'foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    location: location
    baseName: baseName
    agentModelDeploymentName: agentModelDeploymentName
  }
}

module containerAppEnv 'containerapp-env.bicep' = {
  name: 'containerapp-env'
  scope: rg
  params: {
    location: location
    baseName: baseName
    logAnalyticsWorkspaceName: 'log-${baseName}'
  }
  dependsOn: [shared]
}

var sharedAppEnv = [
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: shared.outputs.appInsightsConnectionString }
]

// Layer 2 OCR tool — CPU-only, no GPU workload profile needed (and serverless GPU
// isn't available in eastus2 anyway; see infra/bicep/containerapp-env.bicep).
module paddleOcr 'containerapp.bicep' = {
  name: 'paddle-ocr'
  scope: rg
  params: {
    location: location
    environmentId: containerAppEnv.outputs.environmentId
    name: 'paddle-ocr'
    image: '${shared.outputs.acrLoginServer}/paddle-ocr:${paddleImageTag}'
    acrLoginServer: shared.outputs.acrLoginServer
    acrUsername: shared.outputs.acrName
    acrPassword: listCredentials(acrResourceId, '2023-07-01').passwords[0].value
    cpu: '2.0'
    memory: '4Gi'
    targetPort: 8080
    env: sharedAppEnv
  }
}

// Layer 3 OCR tool — RapidOCR at its higher-accuracy model size, still CPU-only.
// Escalation target when Paddle's signals indicate a complex layout (see
// pipeline/agent_tools.py's run_paddle_ocr tool description).
module rapidOcr 'containerapp.bicep' = {
  name: 'rapid-ocr'
  scope: rg
  params: {
    location: location
    environmentId: containerAppEnv.outputs.environmentId
    name: 'rapid-ocr'
    image: '${shared.outputs.acrLoginServer}/rapid-ocr:${rapidOcrImageTag}'
    acrLoginServer: shared.outputs.acrLoginServer
    acrUsername: shared.outputs.acrName
    acrPassword: listCredentials(acrResourceId, '2023-07-01').passwords[0].value
    cpu: '2.0'
    memory: '4Gi'
    targetPort: 8080
    env: sharedAppEnv
  }
}

// Runs the Service Bus consumer loop, drives the Foundry agent (gpt-5-mini) that
// does layer-1/layer-2/layer-3 tool calls and entity extraction, AND serves the
// frontend/ upload UI + /api/extract for interactive use — see
// pipeline/agent_extract.py, pipeline/orchestrator.py, and agent/setup_agent.py.
// External ingress since this is the one app a browser needs to reach directly; no
// auth in front of it yet (see README) — add Container Apps built-in auth (Easy Auth)
// before pointing this at anything but a trusted demo audience.
module orchestrator 'containerapp.bicep' = {
  name: 'orchestrator'
  scope: rg
  params: {
    location: location
    environmentId: containerAppEnv.outputs.environmentId
    name: 'orchestrator'
    image: '${shared.outputs.acrLoginServer}/orchestrator:${orchestratorImageTag}'
    acrLoginServer: shared.outputs.acrLoginServer
    acrUsername: shared.outputs.acrName
    acrPassword: listCredentials(acrResourceId, '2023-07-01').passwords[0].value
    cpu: '1.0'
    memory: '2Gi'
    targetPort: 8080
    minReplicas: 1
    external: true
    env: concat(sharedAppEnv, [
      { name: 'SERVICEBUS_NAMESPACE', value: servicebus.outputs.namespaceName }
      { name: 'STORAGE_ACCOUNT', value: storage.outputs.storageAccountName }
      { name: 'PADDLE_OCR_URL', value: 'http://paddle-ocr' }
      { name: 'RAPID_OCR_URL', value: 'http://rapid-ocr' }
      { name: 'CONTENT_SAFETY_ENDPOINT', value: shared.outputs.contentSafetyEndpoint }
      { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundry.outputs.projectEndpoint }
      { name: 'AGENT_MODEL_DEPLOYMENT', value: agentModelDeploymentName }
    ])
  }
}

module rbac 'rbac.bicep' = {
  name: 'rbac'
  scope: rg
  params: { principalId: orchestrator.outputs.principalId }
}

output storageAccountName string = storage.outputs.storageAccountName
output serviceBusNamespace string = servicebus.outputs.namespaceName
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output acrLoginServer string = shared.outputs.acrLoginServer
output frontendUrl string = 'https://${orchestrator.outputs.fqdn}'
