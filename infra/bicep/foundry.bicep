// Microsoft Foundry resource + project (current account-based model, not the older
// Microsoft.MachineLearningServices hub/project pattern) hosting the gpt-5-mini
// deployment that pipeline/agent_extract.py drives as an agent with function tools
// (layer-1/layer-2 OCR) and native vision (layer-3 escalation).
// ponytail: gpt-4o-mini was the original choice here but Azure marked the whole
// gpt-4o/4.1 family "Deprecating" for new deployments — swap this model block again
// if gpt-5-mini meets the same fate (`az cognitiveservices model list --location
// eastus2` shows current lifecycle status).
param location string
param baseName string
param agentModelDeploymentName string

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: 'aif-${baseName}'
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: 'aif-${baseName}'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: foundryAccount
  name: 'proj-${baseName}'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {}
}

resource gpt4oMiniDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: foundryAccount
  name: agentModelDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5-mini'
      version: '2025-08-07'
    }
  }
}

output accountName string = foundryAccount.name
output projectName string = project.name
// ponytail: format taken from the current function-calling quickstart
// (learn.microsoft.com/azure/foundry/agents/how-to/tools/function-calling); verify
// with `az cognitiveservices account project show` if agent calls 404.
output projectEndpoint string = 'https://${foundryAccount.properties.customSubDomainName}.ai.azure.com/api/projects/${project.name}'
