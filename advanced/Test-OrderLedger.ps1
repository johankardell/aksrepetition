[CmdletBinding()]
param(
    [Parameter(Mandatory)][uri]$BaseUri,
    [string]$LedgerPath = '.\.artifacts\advanced\dr-accepted.json',
    [ValidateRange(1,3600)][int]$TimeoutSeconds = 180,
    [string]$ReportPath = '.\.artifacts\advanced\order-verification.json'
)
$ErrorActionPreference = 'Stop'
$orders = @(Get-Content $LedgerPath -Raw | ConvertFrom-Json)
if ($orders.Count -eq 0) { throw 'The accepted order ledger is empty.' }
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$results = @()
do {
    $results = foreach ($order in $orders) {
        $errorText = ''
        $verified = $false
        try {
            $id = [uri]::EscapeDataString($order.id)
            $actual = Invoke-RestMethod "$($BaseUri.AbsoluteUri.TrimEnd('/'))/orders/$id" -TimeoutSec 10
            $verified = $actual.id -ceq $order.id -and $actual.item -ceq $order.item
            if (-not $verified) { $errorText = 'ID/item mismatch.' }
        } catch { $errorText = $_.Exception.Message }
        [pscustomobject]@{ id=$order.id; item=$order.item; verified=$verified; error=$errorText }
    }
    $missing = @($results | Where-Object { -not $_.verified })
    if ($missing.Count -eq 0 -or [DateTime]::UtcNow -ge $deadline) { break }
    Start-Sleep 2
} while ($true)
New-Item (Split-Path $ReportPath) -ItemType Directory -Force | Out-Null
$report = [pscustomobject]@{
    utc = [DateTime]::UtcNow.ToString('o')
    expected = $orders.Count
    unverified = $missing.Count
    results = $results
}
$report | ConvertTo-Json -Depth 8 | Set-Content $ReportPath
if ($missing.Count) { throw "$($missing.Count) orders are missing or mismatched. See $ReportPath." }
Write-Host "Verified all $($orders.Count) accepted IDs and items. SQL uniqueness must also be checked."
