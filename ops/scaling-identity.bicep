param scalerIdentityName string
param location string = resourceGroup().location
param serviceBusName string
param oidcIssuer string

resource scaler 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: scalerIdentityName
  location: location
}
resource bus 'Microsoft.ServiceBus/namespaces@2024-01-01' existing = {
  name: serviceBusName
}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' existing = {
  parent: bus
  name: 'orders'
}
resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: scaler
  name: 'keda-operator'
  properties: {
    issuer: oidcIssuer
    subject: 'system:serviceaccount:kube-system:keda-operator'
    audiences: ['api://AzureADTokenExchange']
  }
}
// The documented KEDA Service Bus flow reads queue runtime/management properties.
// Deliberately queue-scoped, never namespace/subscription-scoped.
resource scalerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(queue.id, scaler.id, 'keda-runtime-properties')
  scope: queue
  properties: {
    principalId: scaler.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '090c5cfd-751d-490a-894a-3ce6f1109419')
  }
}
output scalerRoleId string = scalerRole.id
output scalerClientId string = scaler.properties.clientId
output scalerName string = scaler.name
