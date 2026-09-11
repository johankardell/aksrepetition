[CmdletBinding()]
param(
    [string]$Directory = '.\advanced\regions\secondary',
    [Parameter(Mandatory)][ValidateRange(0,10)][int]$Api,
    [Parameter(Mandatory)][ValidateRange(0,10)][int]$Worker
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$path = Join-Path $Directory kustomization.yaml
$text = Get-Content $path -Raw
foreach ($entry in @{ 'order-api'=$Api; 'order-worker'=$Worker }.GetEnumerator()) {
    $pattern = '(?m)(  - name: ' + [regex]::Escape($entry.Key) + '\r?\n    count: )\d+'
    if ([regex]::Matches($text, $pattern).Count -ne 1) { throw "Expected exactly one replica entry for $($entry.Key)." }
    $replacement = '${1}' + $entry.Value
    $text = [regex]::Replace($text, $pattern, $replacement)
}
Set-Content $path $text
kubectl kustomize $Directory | Out-Null
Write-Host 'Replica source updated. Review, commit, push and reconcile before treating it as effective.'
