param prefix string
param location string = resourceGroup().location
param spokeName string
param spokeAddress string = '10.40.0.0/16'
param nodesAddress string = '10.40.0.0/20'
param hubPrefix string = '10.50'
var firewallName = '${prefix}-fw'
var authorityHost = uri(environment().authentication.loginEndpoint, '/')
var loginFqdn = split(authorityHost, '/')[2]
var managementFqdn = split(environment().resourceManager, '/')[2]
resource hub 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${prefix}-hub'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['${hubPrefix}.0.0/16'] }
    subnets: [{ name: 'AzureFirewallSubnet', properties: { addressPrefix: '${hubPrefix}.0.0/26' } }]
  }
}
resource spoke 'Microsoft.Network/virtualNetworks@2024-05-01' existing = { name: spokeName }
resource peerSpoke 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: spoke
  name: 'to-hub'
  properties: { remoteVirtualNetwork: { id: hub.id }, allowVirtualNetworkAccess: true, allowForwardedTraffic: true }
}
resource peerHub 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: hub
  name: 'to-spoke'
  properties: { remoteVirtualNetwork: { id: spoke.id }, allowVirtualNetworkAccess: true, allowForwardedTraffic: true }
}
resource pip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${firewallName}-pip'
  location: location
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}
resource firewall 'Microsoft.Network/azureFirewalls@2024-05-01' = {
  name: firewallName
  location: location
  properties: {
    sku: { name: 'AZFW_VNet', tier: 'Standard' }
    threatIntelMode: 'Alert'
    additionalProperties: { 'Network.DNS.EnableProxy': 'true' }
    ipConfigurations: [
      {
        name: 'primary'
        properties: { subnet: { id: '${hub.id}/subnets/AzureFirewallSubnet' }, publicIPAddress: { id: pip.id } }
      }
    ]
    applicationRuleCollections: [
      {
        name: 'aks-required'
        properties: {
          priority: 100
          action: { type: 'Allow' }
          rules: [
            {
              name: 'aks-tag'
              sourceAddresses: [nodesAddress]
              protocols: [{ protocolType: 'Https', port: 443 }]
              fqdnTags: ['AzureKubernetesService']
            }
          ]
        }
      }
      {
        name: 'identity-gitops-observability'
        properties: {
          priority: 200
          action: { type: 'Allow' }
          rules: [
            {
              name: 'required-https'
              sourceAddresses: [nodesAddress]
              protocols: [{ protocolType: 'Https', port: 443 }]
              targetFqdns: [
                loginFqdn
                '*.${loginFqdn}'
                managementFqdn
                'github.com'
                'api.github.com'
                '*.githubusercontent.com'
                '*.monitor.azure.com'
                '*.monitoring.azure.com'
                '*.ods.opinsights.azure.com'
                '*.oms.opinsights.azure.com'
                '*.in.applicationinsights.azure.com'
                'dc.services.visualstudio.com'
                'global.handler.control.monitor.azure.com'
                'acs-mirror.azureedge.net'
              ]
            }
          ]
        }
      }
    ]
    networkRuleCollections: [
      {
        name: 'aks-network'
        properties: {
          priority: 100
          action: { type: 'Allow' }
          rules: [
            {
              name: 'control-tcp'
              sourceAddresses: [nodesAddress]
              destinationAddresses: ['AzureCloud.${location}']
              destinationPorts: ['9000']
              protocols: ['TCP']
            }
            {
              name: 'control-udp'
              sourceAddresses: [nodesAddress]
              destinationAddresses: ['AzureCloud.${location}']
              destinationPorts: ['1194']
              protocols: ['UDP']
            }
            {
              name: 'ntp'
              sourceAddresses: [nodesAddress]
              destinationFqdns: ['ntp.ubuntu.com']
              destinationPorts: ['123']
              protocols: ['UDP']
            }
          ]
        }
      }
    ]
  }
}
resource routes 'Microsoft.Network/routeTables@2024-05-01' = {
  name: '${prefix}-egress'
  location: location
  properties: {
    disableBgpRoutePropagation: false
    routes: [
      {
        name: 'default-to-firewall'
        properties: {
          addressPrefix: '0.0.0.0/0'
          nextHopType: 'VirtualAppliance'
          nextHopIpAddress: firewall.properties.ipConfigurations[0].properties.privateIPAddress
        }
      }
      { name: 'spoke-local', properties: { addressPrefix: spokeAddress, nextHopType: 'VnetLocal' } }
    ]
  }
}
output routeTableId string = routes.id
output firewallPrivateIp string = firewall.properties.ipConfigurations[0].properties.privateIPAddress
output firewallPublicIp string = pip.properties.ipAddress
