[CmdletBinding()]
param()
. "$PSScriptRoot\..\scripts\Use-Lab.ps1"
$destination = Join-Path $Root 'gitops\clusters\primary'
if (Test-Path (Join-Path $destination 'apps\orders\kustomization.yaml')) {
    throw 'GitOps is already initialized. Edit its sources; do not recopy rendered manifests.'
}
$source = Join-Path $Root 'rendered\base'
foreach ($required in 'kustomization.yaml', 'secret-provider.yaml', 'identity-patch.yaml', 'policies.yaml') {
    if (-not (Test-Path (Join-Path $source $required))) {
        throw "Missing accumulated rendered base file $required. Complete labs 2 and 3; do not rerender the baseline."
    }
}
kubectl kustomize $source | Out-Null
$delivery = az deployment group show -g $Lab.ResourceGroup -n delivery --query properties.outputs -o json | ConvertFrom-Json
foreach ($namespace in 'orders', 'orders-test') {
    $path = Join-Path $destination "apps\$namespace"
    New-Item $path -ItemType Directory -Force | Out-Null
    Copy-Item (Join-Path $source '*') $path -Recurse
    foreach ($file in Get-ChildItem $path -Filter '*.yaml' -Recurse) {
        $text = Get-Content $file.FullName -Raw
        if ($file.Name -eq 'kustomization.yaml') {
            # The copied Namespace snapshot is retained, but its reconciliation stays platform-owned.
            $text = $text -replace '(?m)^[ \t]*-[ \t]*namespace\.yaml[ \t]*\r?\n', ''
        }
        if ($namespace -eq 'orders-test') {
            if ($file.Name -in 'secret-provider.yaml', 'identity-patch.yaml') {
                Remove-Item $file.FullName
                continue
            }
            if ($file.Name -eq 'kustomization.yaml') {
                $text = $text -replace '(?m)^[ \t]*-[ \t]*(?:path:[ \t]*)?(?:secret-provider|identity-patch)\.yaml[ \t]*\r?\n', ''
            }
            $text = $text -replace 'namespace: orders\b', 'namespace: orders-test'
            $text = $text.Replace('QUEUE_NAME=orders', 'QUEUE_NAME=orders-test')
            $text = $text.Replace($Outputs.apiClientId.value, $delivery.testApiClientId.value)
            $text = $text.Replace($Outputs.workerClientId.value, $delivery.testWorkerClientId.value)
        }
        if ($text -match '__[A-Z_]+__') { throw "Unresolved token in $($file.Name)." }
        Set-Content $file.FullName $text -Encoding utf8
    }
    kubectl kustomize $path | Out-Null
}
Write-Host 'Initialized application sources once. Review the diff before committing.'
