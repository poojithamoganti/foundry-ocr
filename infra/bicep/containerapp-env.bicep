param location string
param baseName string
param logAnalyticsWorkspaceName string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${baseName}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    // Consumption-only: serverless GPU workload profiles aren't available in eastus2
    // (checked against the current Container Apps supported-regions list), and this
    // system no longer needs GPU anyway — entity extraction and layer-3 vision run on
    // the Foundry-hosted gpt-5-mini deployment instead (see foundry.bicep).
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
  }
}

output environmentId string = env.id
output environmentName string = env.name
output defaultDomain string = env.properties.defaultDomain
