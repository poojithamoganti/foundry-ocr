param location string
param baseName string
param logAnalyticsWorkspaceId string

var storageAccountName = toLower('st${baseName}${uniqueString(resourceGroup().id)}')

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: take(storageAccountName, 24)
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

var containerNames = [
  'samples'
  'results'
  'corrections'
]

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = [for c in containerNames: {
  parent: blobServices
  name: c
  properties: { publicAccess: 'None' }
}]

// Audit trail: every blob read/write lands in the shared Log Analytics workspace.
resource diagSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${storageAccountName}'
  scope: blobServices
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      { category: 'StorageRead', enabled: true }
      { category: 'StorageWrite', enabled: true }
      { category: 'StorageDelete', enabled: true }
    ]
    metrics: [
      { category: 'Transaction', enabled: true }
    ]
  }
}

output storageAccountName string = storageAccount.name
output storageAccountId string = storageAccount.id
