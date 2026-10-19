targetScope = 'resourceGroup'

@minLength(4)
@maxLength(12)
param prefix string
param location string = resourceGroup().location
param vnetName string

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}
resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'AzureBastionSubnet'
}
resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${prefix}-bastion-pip'
  location: location
  tags: { purpose: 'aks-enterprise-refresher', environment: 'lab' }
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}
resource bastion 'Microsoft.Network/bastionHosts@2024-05-01' = {
  name: '${prefix}-bastion'
  location: location
  tags: { purpose: 'aks-enterprise-refresher', environment: 'lab' }
  sku: { name: 'Standard' }
  properties: {
    scaleUnits: 2
    enableTunneling: true
    ipConfigurations: [{
      name: 'primary'
      properties: {
        subnet: { id: subnet.id }
        publicIPAddress: { id: publicIp.id }
      }
    }]
  }
}

output bastionId string = bastion.id
output bastionName string = bastion.name
output publicIpId string = publicIp.id
