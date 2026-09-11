targetScope = 'resourceGroup'
param location string = resourceGroup().location
param serverName string
param adminObjectId string
param adminLogin string
param tenantId string = tenant().tenantId
param skuName string = 'Standard_D2ds_v5'
param postgresVersion string = '16'

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  sku: { name: skuName, tier: 'GeneralPurpose' }
  properties: {
    version: postgresVersion
    createMode: 'Default'
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenantId
    }
    storage: { storageSizeGB: 128, autoGrow: 'Enabled' }
    backup: { backupRetentionDays: 14, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Disabled' }
  }
}
resource admin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2024-08-01' = {
  parent: server
  name: adminObjectId
  properties: {
    principalName: adminLogin
    principalType: 'User'
    tenantId: tenantId
  }
}
resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: 'ordersdb'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}
output serverId string = server.id
output hostname string = server.properties.fullyQualifiedDomainName
