param location string = resourceGroup().location
param prefix string
param clusterName string
param workspaceId string
param grafanaName string
@description('Object ID of a group or user allowed to view Grafana, not a client ID.')
param viewerObjectId string
@allowed(['Group', 'User'])
param viewerPrincipalType string = 'Group'
@description('Approved lab alert destination. No email is sent during authoring.')
param alertEmail string

resource aks 'Microsoft.ContainerService/managedClusters@2025-04-01' existing = {
  name: clusterName
}
resource metrics 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: '${prefix}-metrics'
  location: location
  properties: {
    publicNetworkAccess: 'Enabled'
  }
}
resource grafana 'Microsoft.Dashboard/grafana@2023-09-01' = {
  name: grafanaName
  location: location
  sku: { name: 'Standard' }
  identity: { type: 'SystemAssigned' }
  properties: {
    apiKey: 'Disabled'
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
    grafanaIntegrations: {
      azureMonitorWorkspaceIntegrations: [{ azureMonitorWorkspaceResourceId: metrics.id }]
    }
  }
}
resource metricsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(metrics.id, grafana.id, 'MonitoringReader')
  scope: metrics
  properties: {
    principalId: grafana.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '43d0d8ad-25c7-4714-9337-8ba259a9fe05')
  }
}
resource grafanaViewer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(grafana.id, viewerObjectId, 'GrafanaViewer')
  scope: grafana
  properties: {
    principalId: viewerObjectId
    principalType: viewerPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '60921a7e-fef1-4a43-9b16-a26c52ad4769')
  }
}
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appinsights'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspaceId
    RetentionInDays: 30
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'control-plane'
  scope: aks
  properties: {
    workspaceId: workspaceId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'kube-audit-admin', enabled: true }
      { category: 'kube-apiserver', enabled: true }
      { category: 'kube-controller-manager', enabled: true }
      { category: 'kube-scheduler', enabled: true }
    ]
  }
}
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${prefix}-orders-alerts'
  location: 'global'
  properties: {
    groupShortName: 'orders-lab'
    enabled: true
    emailReceivers: [{ name: 'lab-operator', emailAddress: alertEmail, useCommonAlertSchema: true }]
  }
}
resource sloRules 'Microsoft.AlertsManagement/prometheusRuleGroups@2023-03-01' = {
  name: '${prefix}-orders-slo'
  location: location
  properties: {
    enabled: true
    interval: 'PT1M'
    scopes: [metrics.id]
    rules: [
      {
        alert: 'OrdersHighErrorRatio'
        expression: '(sum(rate(orders_http_requests_total{namespace="orders",status=~"5.."}[5m])) / clamp_min(sum(rate(orders_http_requests_total{namespace="orders"}[5m])), 0.001)) > 0.01'
        for: 'PT2M'
        severity: 2
        enabled: true
        annotations: { summary: 'Orders HTTP 5xx exceeds 1% over five minutes. Check external probe and traces.' }
        actions: [{ actionGroupId: actionGroup.id }]
      }
      {
        alert: 'OrdersHighLatency'
        expression: 'histogram_quantile(0.95, sum by (le) (rate(orders_http_duration_seconds_bucket{namespace="orders"}[5m]))) > 1'
        for: 'PT2M'
        severity: 2
        enabled: true
        annotations: { summary: 'Orders measured p95 server latency exceeds one second.' }
        actions: [{ actionGroupId: actionGroup.id }]
      }
      {
        alert: 'OrdersMetricsMissing'
        expression: 'absent_over_time(orders_http_duration_seconds_count{namespace="orders"}[5m])'
        for: 'PT2M'
        severity: 2
        enabled: true
        annotations: { summary: 'No orders latency series: investigate scraping separately from application health.' }
        actions: [{ actionGroupId: actionGroup.id }]
      }
    ]
  }
}
output metricsId string = metrics.id
output grafanaId string = grafana.id
output grafanaEndpoint string = grafana.properties.endpoint
output appInsightsId string = appInsights.id
