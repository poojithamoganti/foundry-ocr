param location string
param baseName string
param logAnalyticsWorkspaceId string

var namespaceName = 'sb-${baseName}'

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: namespaceName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
}

// The 3-layer OCR routing + entity extraction all happen inside one agent call per
// document (pipeline/agent_extract.py) — no per-layer queues, just ingest in and
// dead-letter for anything a guardrail blocks or that errors out.
var queueNames = [
  'ingest_queue'
  'dead_letter_queue'
]

resource queues 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = [for q in queueNames: {
  parent: serviceBusNamespace
  name: q
  properties: {
    enablePartitioning: false
    maxDeliveryCount: 10
  }
}]

resource diagSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${namespaceName}'
  scope: serviceBusNamespace
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      { category: 'OperationalLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

output namespaceName string = serviceBusNamespace.name
output namespaceId string = serviceBusNamespace.id
