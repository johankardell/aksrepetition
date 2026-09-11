param prefix string
param location string = resourceGroup().location
param vnetId string
param subnetId string
param acrId string
param keyVaultId string
param serviceBusId string
var services = [
  { name: 'acr', id: acrId, group: 'registry', zone: 'privatelink.azurecr.io' }
  { name: 'vault', id: keyVaultId, group: 'vault', zone: 'privatelink.vaultcore.azure.net' }
  { name: 'bus', id: serviceBusId, group: 'namespace', zone: 'privatelink.servicebus.windows.net' }
]
resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [
  for svc in services: {
    name: svc.zone
    location: 'global'
  }
]
resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [
  for (svc, i) in services: {
    parent: zones[i]
    name: '${prefix}-link'
    location: 'global'
    properties: { virtualNetwork: { id: vnetId }, registrationEnabled: false }
  }
]
resource endpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [
  for svc in services: {
    name: '${prefix}-${svc.name}-pe'
    location: location
    properties: {
      subnet: { id: subnetId }
      privateLinkServiceConnections: [
        { name: svc.name, properties: { privateLinkServiceId: svc.id, groupIds: [svc.group] } }
      ]
    }
  }
]
resource groups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [
  for (svc, i) in services: {
    parent: endpoints[i]
    name: 'default'
    properties: { privateDnsZoneConfigs: [{ name: svc.name, properties: { privateDnsZoneId: zones[i].id } }] }
  }
]
