param spokeName string
param hubName string

resource spoke 'Microsoft.Network/virtualNetworks@2024-05-01' existing = { name: spokeName }
resource hub 'Microsoft.Network/virtualNetworks@2024-05-01' existing = { name: hubName }
resource peerSpoke 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: spoke
  name: 'to-hub'
  properties: {
    remoteVirtualNetwork: { id: hub.id }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
  }
}
resource peerHub 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: hub
  name: 'to-spoke'
  properties: {
    remoteVirtualNetwork: { id: spoke.id }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
  }
}
