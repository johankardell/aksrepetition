[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Source,
    [ValidateSet('Resource', 'Patch')][string]$Kind = 'Resource',
    [ValidateSet('orders', 'orders-test')][string]$Namespace = 'orders'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$root = Join-Path $PSScriptRoot "..\gitops\clusters\primary\apps\$Namespace"
$name = Split-Path $Source -Leaf
Copy-Item $Source (Join-Path $root $name) -Force
$path = Join-Path $root 'kustomization.yaml'
$text = Get-Content $path -Raw
if ($text -notmatch [regex]::Escape($name)) {
    if ($Kind -eq 'Resource') {
        if ($text -notmatch '(?m)^resources:') { throw 'Missing resources list.' }
        $text = $text -replace '(?m)^resources:', "resources:`n  - $name"
    } elseif ($text -match '(?m)^patches:') {
        $text = $text -replace '(?m)^patches:', "patches:`n  - path: $name"
    } else {
        $text += "`npatches:`n  - path: $name`n"
    }
    Set-Content $path $text -Encoding utf8
}
kubectl kustomize $root | Out-Null
