param location string = resourceGroup().location
param prefix string
param acrName string
@description('Existing GitHub repository, owner/name. No repository is created by this template.')
param githubRepository string
param serviceBusName string
param oidcIssuer string

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}
resource publisher 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-image-publisher'
  location: location
}
resource publishFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: publisher
  name: 'github-acr-publish'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:environment:acr-publish'
    audiences: ['api://AzureADTokenExchange']
  }
}
resource pushRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, publisher.id, 'AcrPush')
  scope: acr
  properties: {
    principalId: publisher.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8311e382-0749-4cb8-b61a-304f252e45ec')
  }
}
resource bus 'Microsoft.ServiceBus/namespaces@2024-01-01' existing = {
  name: serviceBusName
}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: bus
  name: 'orders-test'
  properties: {
    lockDuration: 'PT1M'
    maxDeliveryCount: 5
    defaultMessageTimeToLive: 'PT1H'
    deadLetteringOnMessageExpiration: true
  }
}
resource testIdentities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for role in ['api', 'worker']: {
  name: '${prefix}-test-${role}'
  location: location
}]
resource testFederations 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = [for (role, i) in ['api', 'worker']: {
  parent: testIdentities[i]
  name: 'orders-test-${role}'
  properties: {
    issuer: oidcIssuer
    subject: 'system:serviceaccount:orders-test:order-${role}'
    audiences: ['api://AzureADTokenExchange']
  }
}]
var dataRoles = [
  '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  '4f6a3b9b-b2e8-4167-8ed5-3e4263470b02'
]
resource testRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (role, i) in dataRoles: {
  name: guid(queue.id, testIdentities[i].id, role)
  scope: queue
  properties: {
    principalId: testIdentities[i].properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]
output publisherClientId string = publisher.properties.clientId
output testApiClientId string = testIdentities[0].properties.clientId
output testWorkerClientId string = testIdentities[1].properties.clientId
