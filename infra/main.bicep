targetScope = 'resourceGroup'

@minLength(4)
@maxLength(12)
param prefix string
param location string = resourceGroup().location
param kubernetesVersion string
param adminGroupObjectId string
@description('Public RSA key for private-node break-glass access; never supply a private key.')
param sshPublicKey string
param nodeAdminUsername string = 'aksoperator'
param vmSize string = 'Standard_D4ds_v5'
param zones array = ['1', '2', '3']
@description('First two octets of the spoke VNet. Use a different value for the secondary region.')
param networkPrefix string = '10.40'

var suffix = uniqueString(resourceGroup().id)
var tags = { purpose: 'aks-enterprise-refresher', environment: 'lab' }
var networkContributor = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4d97b98b-1d4f-4787-a291-c67834d212e7'
)

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${prefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['${networkPrefix}.0.0/16'] }
    subnets: [
      { name: 'nodes', properties: { addressPrefix: '${networkPrefix}.0.0/20' } }
      { name: 'management', properties: { addressPrefix: '${networkPrefix}.16.0/24' } }
      {
        name: 'endpoints'
        properties: { addressPrefix: '${networkPrefix}.17.0/24', privateEndpointNetworkPolicies: 'Disabled' }
      }
      { name: 'gateway', properties: { addressPrefix: '${networkPrefix}.18.0/24' } }
      { name: 'AzureBastionSubnet', properties: { addressPrefix: '${networkPrefix}.19.0/26' } }
    ]
  }
}
resource clusterIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-cluster'
  location: location
}
resource kubeletIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-kubelet'
  location: location
}
resource networkRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vnet.id, clusterIdentity.id, networkContributor)
  scope: vnet
  properties: {
    principalId: clusterIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: networkContributor
  }
}
resource identityRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kubeletIdentity.id, clusterIdentity.id, 'identity-operator')
  scope: kubeletIdentity
  properties: {
    principalId: clusterIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'f1a07417-d97a-45cb-824c-7a7467783830'
    )
  }
}
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${prefix}${suffix}'
  location: location
  tags: tags
  sku: { name: 'Premium' }
  properties: { adminUserEnabled: false, publicNetworkAccess: 'Enabled' }
}
resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, kubeletIdentity.id, 'pull')
  scope: acr
  properties: {
    principalId: kubeletIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}
resource aks 'Microsoft.ContainerService/managedClusters@2025-07-01' = {
  name: '${prefix}-aks'
  location: location
  tags: tags
  sku: { name: 'Base', tier: 'Standard' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${clusterIdentity.id}': {} } }
  properties: {
    dnsPrefix: prefix
    kubernetesVersion: kubernetesVersion
    enableRBAC: true
    disableLocalAccounts: true
    linuxProfile: { adminUsername: nodeAdminUsername, ssh: { publicKeys: [{ keyData: sshPublicKey }] } }
    aadProfile: {
      managed: true
      enableAzureRBAC: true
      adminGroupObjectIDs: [adminGroupObjectId]
      tenantID: tenant().tenantId
    }
    apiServerAccessProfile: {
      enablePrivateCluster: true
      enablePrivateClusterPublicFQDN: false
      privateDNSZone: 'system'
    }
    oidcIssuerProfile: { enabled: true }
    securityProfile: { workloadIdentity: { enabled: true } }
    identityProfile: {
      kubeletidentity: {
        resourceId: kubeletIdentity.id
        clientId: kubeletIdentity.properties.clientId
        objectId: kubeletIdentity.properties.principalId
      }
    }
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkDataplane: 'cilium'
      podCidr: '192.168.0.0/16'
      serviceCidr: '172.20.0.0/16'
      dnsServiceIP: '172.20.0.10'
      loadBalancerSku: 'standard'
      outboundType: 'loadBalancer'
      loadBalancerProfile: { managedOutboundIPs: { count: 1 } }
    }
    agentPoolProfiles: [
      {
        name: 'system'
        mode: 'System'
        type: 'VirtualMachineScaleSets'
        count: 3
        vmSize: vmSize
        osType: 'Linux'
        osSKU: 'AzureLinux3'
        osDiskType: 'Managed'
        availabilityZones: zones
        vnetSubnetID: '${vnet.id}/subnets/nodes'
        nodeTaints: ['CriticalAddonsOnly=true:NoSchedule']
        upgradeSettings: { maxSurge: '1' }
      }
      {
        name: 'apps'
        mode: 'User'
        type: 'VirtualMachineScaleSets'
        count: 3
        vmSize: vmSize
        osType: 'Linux'
        osSKU: 'AzureLinux3'
        osDiskType: 'Managed'
        availabilityZones: zones
        vnetSubnetID: '${vnet.id}/subnets/nodes'
        upgradeSettings: { maxSurge: '1' }
      }
    ]
    addonProfiles: {
      omsagent: { enabled: true, config: { logAnalyticsWorkspaceResourceID: workspace.id, useAADAuth: 'true' } }
      azureKeyvaultSecretsProvider: {
        enabled: true
        config: { enableSecretRotation: 'true', rotationPollInterval: '2m' }
      }
    }
  }
  dependsOn: [networkRole, identityRole, pullRole]
}
resource adminRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aks.id, adminGroupObjectId, 'lab-admin')
  scope: aks
  properties: {
    principalId: adminGroupObjectId
    principalType: 'Group'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'b1ff04bb-8a4e-4dc4-8eb5-8693973ce19b'
    )
  }
}
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-${take(suffix, 8)}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { name: 'standard', family: 'A' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}
resource bus 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: '${prefix}-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Premium', tier: 'Premium', capacity: 1 }
  properties: { disableLocalAuth: true, minimumTlsVersion: '1.2', publicNetworkAccess: 'Enabled', premiumMessagingPartitions: 1 }
}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: bus
  name: 'orders'
  properties: { maxDeliveryCount: 5, lockDuration: 'PT1M', deadLetteringOnMessageExpiration: true }
}
module workload 'workload-identities.bicep' = {
  name: 'workload-identities'
  params: {
    prefix: prefix
    location: location
    issuer: aks.properties.oidcIssuerProfile.issuerURL
    keyVaultName: kv.name
    serviceBusName: bus.name
  }
  dependsOn: [queue]
}
output clusterName string = aks.name
output clusterId string = aks.id
output vnetId string = vnet.id
output nodesSubnetId string = '${vnet.id}/subnets/nodes'
output endpointsSubnetId string = '${vnet.id}/subnets/endpoints'
output acrName string = acr.name
output acrId string = acr.id
output registryServer string = acr.properties.loginServer
output keyVaultName string = kv.name
output keyVaultId string = kv.id
output serviceBusName string = bus.name
output serviceBusId string = bus.id
output workspaceId string = workspace.id
output apiClientId string = workload.outputs.apiClientId
output apiPrincipalId string = workload.outputs.apiPrincipalId
output workerClientId string = workload.outputs.workerClientId
output workerPrincipalId string = workload.outputs.workerPrincipalId
output clusterPrincipalId string = clusterIdentity.properties.principalId
output oidcIssuer string = aks.properties.oidcIssuerProfile.issuerURL
