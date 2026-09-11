[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('orders', 'orders-test')][string]$Namespace,
    [Parameter(Mandatory)][ValidatePattern('^sha256:[a-f0-9]{64}$')][string]$Digest
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$path = Join-Path $PSScriptRoot "..\gitops\clusters\primary\apps\$Namespace\kustomization.yaml"
$text = Get-Content $path -Raw
if ($text -notmatch '(?m)^\s+newTag:|^\s+digest:') { throw 'Expected exactly one image transform.' }
$matchesFound = [regex]::Matches($text, '(?m)^\s+(newTag|digest):[^\r\n]*').Count
if ($matchesFound -ne 1) { throw 'Ambiguous image transform; edit it explicitly.' }
$text = $text -replace '(?m)^(\s+)(newTag|digest):[^\r\n]*', "`${1}digest: $Digest"
Set-Content $path $text -Encoding utf8
kubectl kustomize (Split-Path $path) | Out-Null
