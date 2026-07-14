param location string

resource locationPolicy 'Microsoft.Authorization/policyAssignments@2022-06-01' = {
  name: 'allow-eastus2-only'
  properties: {
    policyDefinitionId: '/providers/Microsoft.Authorization/policyDefinitions/e56962a6-4747-49cd-b67b-bf8b01975c4c'
    parameters: {
      listOfAllowedLocations: { value: [location] }
    }
  }
}
