param principalId string

// Orchestrator authenticates to Storage, Service Bus, Content Safety, and the Foundry
// project via managed identity (DefaultAzureCredential) — no connection strings/keys
// to leak or rotate.
var roles = {
  'storage-blob-data-contributor': 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  'servicebus-data-owner': '090c5cfd-751d-490a-894a-3ce6f1109419'
  'cognitive-services-user': 'a97b65f3-24c7-4388-baec-2e87135dc908'
  // Foundry User (renamed from Azure AI User; GUID unchanged)
  'azure-ai-user': '53ca6127-db72-4b80-b1b0-d745d6d5456d'
}

resource orchestratorRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in items(roles): {
  name: guid(resourceGroup().id, 'orchestrator', role.key)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.value)
  }
}]
