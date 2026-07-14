// Generic CPU container app: used for paddle-ocr, rapid-ocr, and the orchestrator —
// one module, three instantiations from main.bicep. No GPU containers in this
// system; entity extraction runs on the Foundry-hosted gpt-5-mini deployment
// instead of a self-hosted GPU model (serverless GPU isn't available in eastus2 —
// see the region table check that drove this design). orchestrator is the only one
// with external ingress, since it now also serves the frontend/ UI + /api/extract.
param location string
param environmentId string
param name string
param image string
param acrLoginServer string
param acrUsername string
@secure()
param acrPassword string
param cpu string = '2.0'
param memory string = '4Gi'
param targetPort int = 8000
param minReplicas int = 0
param maxReplicas int = 1
param env array = []
param secrets array = []
param external bool = false

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: external
        targetPort: targetPort
        transport: 'http'
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: concat([
        { name: 'acr-password', value: acrPassword }
      ], secrets)
    }
    template: {
      containers: [
        {
          name: name
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: env
          probes: [
            {
              type: 'startup'
              httpGet: { path: '/health', port: targetPort }
              initialDelaySeconds: 10
              periodSeconds: 10
              failureThreshold: 30
              timeoutSeconds: 5
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scale'
            http: { metadata: { concurrentRequests: '10' } }
          }
        ]
      }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
output name string = app.name
output principalId string = app.identity.principalId
