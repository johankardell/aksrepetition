targetScope = 'resourceGroup'
param profileName string
param primaryHost string
param secondaryHost string
param primaryPlsId string
param secondaryPlsId string
param primaryLocation string
param secondaryLocation string

resource profile 'Microsoft.Cdn/profiles@2024-02-01' = {
  name: profileName
  location: 'global'
  sku: { name: 'Premium_AzureFrontDoor' }
  properties: { originResponseTimeoutSeconds: 60 }
}
resource endpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-02-01' = {
  parent: profile
  name: '${profileName}-edge'
  location: 'global'
  properties: { enabledState: 'Enabled' }
}
resource group 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: profile
  name: 'orders'
  properties: {
    healthProbeSettings: {
      probePath: '/readyz'
      probeProtocol: 'Https'
      probeRequestType: 'GET'
      probeIntervalInSeconds: 30
    }
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
      additionalLatencyInMilliseconds: 50
    }
    sessionAffinityState: 'Disabled'
  }
}
resource primary 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: group
  name: 'primary'
  properties: {
    hostName: primaryHost
    originHostHeader: primaryHost
    httpsPort: 443
    httpPort: 80
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
    sharedPrivateLinkResource: {
      privateLink: { id: primaryPlsId }
      privateLinkLocation: primaryLocation
      requestMessage: 'Approved AKS lab origin'
    }
  }
}
resource secondary 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: group
  name: 'secondary'
  properties: {
    hostName: secondaryHost
    originHostHeader: secondaryHost
    httpsPort: 443
    httpPort: 80
    priority: 2
    weight: 1000
    enabledState: 'Disabled'
    enforceCertificateNameCheck: true
    sharedPrivateLinkResource: {
      privateLink: { id: secondaryPlsId }
      privateLinkLocation: secondaryLocation
      requestMessage: 'Approved AKS lab origin'
    }
  }
}
resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01' = {
  parent: endpoint
  name: 'orders'
  properties: {
    originGroup: { id: group.id }
    patternsToMatch: ['/*']
    supportedProtocols: ['Http', 'Https']
    httpsRedirect: 'Enabled'
    forwardingProtocol: 'HttpsOnly'
    linkToDefaultDomain: 'Enabled'
    enabledState: 'Enabled'
  }
  dependsOn: [primary, secondary]
}
resource waf 'Microsoft.Network/frontDoorWebApplicationFirewallPolicies@2024-02-01' = {
  name: replace('${profileName}waf', '-', '')
  location: 'global'
  sku: { name: 'Premium_AzureFrontDoor' }
  properties: {
    policySettings: { enabledState: 'Enabled', mode: 'Prevention' }
    managedRules: {
      managedRuleSets: [
        { ruleSetType: 'Microsoft_DefaultRuleSet', ruleSetVersion: '2.1' }
        { ruleSetType: 'Microsoft_BotManagerRuleSet', ruleSetVersion: '1.1' }
      ]
    }
    customRules: {
      rules: [
        {
          name: 'ControlledWafTest'
          priority: 10
          ruleType: 'MatchRule'
          action: 'Block'
          enabledState: 'Enabled'
          matchConditions: [
            {
              matchVariable: 'RequestHeader'
              selector: 'X-Lab-Waf-Test'
              operator: 'Equal'
              matchValue: ['block']
              negateCondition: false
              transforms: []
            }
          ]
        }
      ]
    }
  }
}
resource security 'Microsoft.Cdn/profiles/securityPolicies@2024-02-01' = {
  parent: profile
  name: 'orders-waf'
  properties: {
    parameters: {
      type: 'WebApplicationFirewall'
      wafPolicy: { id: waf.id }
      associations: [
        { domains: [{ id: endpoint.id }], patternsToMatch: ['/*'] }
      ]
    }
  }
}
output endpointHost string = endpoint.properties.hostName
output frontDoorId string = profile.properties.frontDoorId
output wafId string = waf.id
