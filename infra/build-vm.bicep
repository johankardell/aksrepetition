param prefix string
param location string = resourceGroup().location
param vmSize string = 'Standard_B2s'
param sshPublicKey string
param kubernetesVersion string
param managementSubnetId string
param bastionSubnetAddress string
@description('The bootstrap configures rootless tooling for this account.')
param username string = 'labbuilder'
var tags = { purpose: 'aks-enterprise-refresher', environment: 'lab' }
var cloudInit = replace(
  replace(loadTextContent('build-vm-cloud-init.yaml'), '__BUILD_CONFIG__', base64(string({ kubernetesVersion: kubernetesVersion, username: username }))),
  '__BOOTSTRAP_SCRIPT__',
  base64(loadTextContent('build-vm-init.sh'))
)

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${prefix}-build-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'ssh-from-bastion-only'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: bastionSubnetAddress
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'deny-other-inbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}
resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${prefix}-build-nic'
  location: location
  tags: tags
  properties: {
    networkSecurityGroup: { id: nsg.id }
    ipConfigurations: [{
      name: 'private'
      properties: {
        privateIPAllocationMethod: 'Dynamic'
        subnet: { id: managementSubnetId }
      }
    }]
  }
}
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: '${prefix}-build'
  location: location
  tags: tags
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 64
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Delete'
      }
    }
    osProfile: {
      computerName: '${prefix}-build'
      adminUsername: username
      customData: base64(cloudInit)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: { publicKeys: [{ path: '/home/${username}/.ssh/authorized_keys', keyData: sshPublicKey }] }
      }
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
    }
    networkProfile: { networkInterfaces: [{ id: nic.id, properties: { primary: true, deleteOption: 'Delete' } }] }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}
resource ready 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'build-tools-ready'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Extensions'
    type: 'CustomScript'
    typeHandlerVersion: '2.1'
    autoUpgradeMinorVersion: true
    settings: { commandToExecute: 'bash -c "cloud-init status --wait && test -f /var/lib/aks-lab/build-ready"' }
  }
}
output vmId string = vm.id
output vmName string = vm.name
output username string = username
output privateIp string = nic.properties.ipConfigurations[0].properties.privateIPAddress
