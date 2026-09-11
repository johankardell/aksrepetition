[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9.-]*\.yaml$')][string]$Name,
    [ValidateSet('orders', 'orders-test')][string]$Namespace = 'orders'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$root = Join-Path $PSScriptRoot "..\gitops\clusters\primary\apps\$Namespace"
$path = Join-Path $root 'kustomization.yaml'
$text = Get-Content $path -Raw
$pattern = '(?m)^  - (?:path: )?' + [regex]::Escape($Name) + '[ \t]*\r?\n'
$text = [regex]::Replace($text, $pattern, '')
Set-Content $path $text -Encoding utf8
$target = Join-Path $root $Name
if (Test-Path $target) { Remove-Item $target }
kubectl kustomize $root | Out-Null
