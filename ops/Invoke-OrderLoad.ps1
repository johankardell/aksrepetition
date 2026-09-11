[CmdletBinding()]
param(
    [Parameter(Mandatory)][uri]$BaseUri,
    [ValidateRange(1, 2000)][int]$Count = 100,
    [ValidateRange(1, 20)][int]$Concurrency = 4,
    [ValidateRange(1, 600)][int]$DurationSeconds = 120,
    [ValidateRange(1, 30)][int]$TimeoutSeconds = 10,
    [ValidateRange(0, 10000)][int]$DelayMilliseconds = 100,
    [ValidateSet('PostOrders', 'Browse', 'Health', 'OrderLookup')][string]$Operation = 'PostOrders',
    [string]$OutputPath = '.artifacts\order-load.json'
)
$ErrorActionPreference = 'Stop'
if ($BaseUri.Scheme -notin 'http', 'https') { throw 'Use an HTTP(S) application URL.' }
$root = $BaseUri.AbsoluteUri.TrimEnd('/')
$prefix = "load-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($DurationSeconds)
$start = [DateTimeOffset]::UtcNow
$results = @(1..$Count | ForEach-Object -Parallel {
    if ([DateTimeOffset]::UtcNow -ge $using:deadline) { return }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $id = "$using:prefix-$_"
    $status = 0
    $failure = $null
    try {
        $args = @{ TimeoutSec = $using:TimeoutSeconds; SkipHttpErrorCheck = $true }
        switch ($using:Operation) {
            'PostOrders' {
                $response = Invoke-WebRequest "$using:root/orders" @args -Method Post `
                    -ContentType 'application/json' -Body (@{ id = $id; item = 'synthetic-widget' } | ConvertTo-Json -Compress)
            }
            'Health' { $response = Invoke-WebRequest "$using:root/healthz" @args }
            'Browse' { $response = Invoke-WebRequest "$using:root/" @args }
            'OrderLookup' { $response = Invoke-WebRequest "$using:root/orders/$id" @args }
        }
        $status = [int]$response.StatusCode
    } catch {
        $failure = $_.Exception.Message
    }
    $watch.Stop()
    [pscustomobject]@{ id = $id; status = $status; milliseconds = $watch.Elapsed.TotalMilliseconds; error = $failure }
    Start-Sleep -Milliseconds $using:DelayMilliseconds
} -ThrottleLimit $Concurrency)
$sorted = @($results.milliseconds | Sort-Object)
$p95 = if ($sorted.Count) { $sorted[[Math]::Max(0, [Math]::Ceiling($sorted.Count * 0.95) - 1)] } else { $null }
$success = @($results | Where-Object { $_.status -ge 200 -and $_.status -lt 400 }).Count
$summary = [pscustomobject]@{
    startedUtc = $start
    elapsedSeconds = ([DateTimeOffset]::UtcNow - $start).TotalSeconds
    requested = $Count
    sent = $results.Count
    successful = $success
    availabilityPercent = if ($results.Count) { 100.0 * $success / $results.Count } else { 0 }
    p95Milliseconds = $p95
    operation = $Operation
    requests = $results
}
$directory = Split-Path $OutputPath
if ($directory) { New-Item -ItemType Directory -Force $directory | Out-Null }
$summary | ConvertTo-Json -Depth 6 | Set-Content $OutputPath -Encoding utf8
$summary | Select-Object -ExcludeProperty requests
