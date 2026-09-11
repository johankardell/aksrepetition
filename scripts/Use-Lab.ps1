# Dot-source from any directory; native command failures stop subsequent directives.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$Root = Split-Path $PSScriptRoot -Parent
$settingsPath = Join-Path $Root 'local.settings.json'
if (-not (Test-Path $settingsPath)) {
    throw 'Copy local.settings.example.json to local.settings.json and complete its values first.'
}
$Lab = Get-Content $settingsPath -Raw | ConvertFrom-Json
foreach ($name in 'SubscriptionId', 'TenantId', 'AdminGroupObjectId') {
    $parsed = [guid]::Empty
    if (-not [guid]::TryParse($Lab.$name, [ref]$parsed) -or $parsed -eq [guid]::Empty) {
        throw "Set a valid $name in local.settings.json."
    }
}
if ($Lab.Prefix -notmatch '^[a-z][a-z0-9]{3,11}$') {
    throw 'Prefix must be 4-12 lowercase letters/digits, starting with a letter.'
}
if ($Lab.Namespace -ne 'orders') {
    throw 'This cumulative curriculum requires Namespace=orders; manifests and federated subjects use that fixed namespace.'
}
if ($Lab.ImageTag -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$') {
    throw 'ImageTag must be a valid Docker image tag.'
}
$account = az account show -o json | ConvertFrom-Json
if ($account.id -ne $Lab.SubscriptionId -or $account.tenantId -ne $Lab.TenantId) {
    throw "Wrong Azure context. Run az account set --subscription $($Lab.SubscriptionId), then retry."
}
foreach ($entry in @{
    ClusterName = "$($Lab.Prefix)-aks"
    WorkspaceName = "$($Lab.Prefix)-logs"
    GrafanaName = "$($Lab.Prefix)-grafana"
}.GetEnumerator()) {
    $Lab | Add-Member -NotePropertyName $entry.Key -NotePropertyValue $entry.Value -Force
}
# Resource names containing the deployment-specific unique suffix come from ARM, not guessed names.
$deployments = az deployment group list -g $Lab.ResourceGroup --query '[].name' -o json | ConvertFrom-Json
if ($deployments -contains 'foundation') {
    $Outputs = az deployment group show -g $Lab.ResourceGroup -n foundation --query properties.outputs -o json | ConvertFrom-Json
    foreach ($entry in @{
        AcrName = 'acrName'
        RegistryServer = 'registryServer'
        KeyVaultName = 'keyVaultName'
        ServiceBusName = 'serviceBusName'
    }.GetEnumerator()) {
        $Lab | Add-Member -NotePropertyName $entry.Key -NotePropertyValue $Outputs.($entry.Value).value -Force
    }
}
