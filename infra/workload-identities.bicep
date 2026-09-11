param prefix string
param location string
param issuer string
param keyVaultName string
param serviceBusName string

resource api 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-api'
  location: location
}
resource worker 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-worker'
  location: location
}
resource apiFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: api
  name: 'orders-api'
  properties: {
    issuer: issuer
    subject: 'system:serviceaccount:orders:order-api'
    audiences: ['api://AzureADTokenExchange']
  }
}
resource workerFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: worker
  name: 'orders-worker'
  properties: {
    issuer: issuer
    subject: 'system:serviceaccount:orders:order-worker'
    audiences: ['api://AzureADTokenExchange']
  }
}
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: keyVaultName }
resource bus 'Microsoft.ServiceBus/namespaces@2024-01-01' existing = { name: serviceBusName }
resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' existing = { parent: bus, name: 'orders' }
resource send 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(queue.id, api.id, 'send')
  scope: queue
  properties: {
    principalId: api.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
    )
  }
}
resource receive 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(queue.id, worker.id, 'receive')
  scope: queue
  properties: {
    principalId: worker.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4f6a3b9b-b2e8-4167-8ed5-3e4263470b02'
    )
  }
}
resource secrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, api.id, 'secrets')
  scope: kv
  properties: {
    principalId: api.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6'
    )
  }
}
output apiClientId string = api.properties.clientId
output apiPrincipalId string = api.properties.principalId
output workerClientId string = worker.properties.clientId
output workerPrincipalId string = worker.properties.principalId
