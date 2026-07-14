param location string
param baseName string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${baseName}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${baseName}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: toLower('acr${baseName}${uniqueString(resourceGroup().id)}')
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: true
  }
}

// Guardrails: Azure AI Content Safety, called by pipeline/guardrails.py around every
// OCR/KIE model input+output since the self-hosted OSS models don't get Foundry's
// native content-filter (that's only wired to Foundry-catalog serverless deployments).
resource contentSafety 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: 'cs-${baseName}'
  location: location
  kind: 'ContentSafety'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'cs-${baseName}'
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsId string = appInsights.id
output acrLoginServer string = acr.properties.loginServer
output acrId string = acr.id
output acrName string = acr.name
output contentSafetyEndpoint string = contentSafety.properties.endpoint
output contentSafetyId string = contentSafety.id
