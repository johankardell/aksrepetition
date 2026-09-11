[CmdletBinding()]
param()
. "$PSScriptRoot\Use-Lab.ps1"
$destination = Join-Path $Root 'rendered\base'
New-Item $destination -ItemType Directory -Force | Out-Null
$replacements = @{
    '__REGISTRY__' = $Lab.RegistryServer
    '__IMAGE_TAG__' = $Lab.ImageTag
    '__SERVICEBUS_NAMESPACE__' = "$($Lab.ServiceBusName).servicebus.windows.net"
    '__API_CLIENT_ID__' = $Outputs.apiClientId.value
    '__WORKER_CLIENT_ID__' = $Outputs.workerClientId.value
}
foreach ($file in Get-ChildItem (Join-Path $Root 'k8s\base') -Filter '*.yaml') {
    $text = Get-Content $file.FullName -Raw
    foreach ($key in $replacements.Keys) { $text = $text.Replace($key, $replacements[$key]) }
    if ($text -match '__[A-Z_]+__') { throw "Unresolved token in $($file.Name)" }
    Set-Content (Join-Path $destination $file.Name) -Value $text -Encoding utf8
}
kubectl kustomize $destination | Out-Null
Write-Host "Rendered base to $destination. Apply only before Flux takes ownership in lab 4."

