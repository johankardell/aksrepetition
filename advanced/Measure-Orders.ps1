[CmdletBinding()]
param(
    [Parameter(Mandatory)][uri]$BaseUri,
    [ValidateRange(1,7200)][int]$Seconds = 600,
    [string]$OutputPath = '.\.artifacts\advanced\traffic.json'
)
$ErrorActionPreference = 'Stop'
New-Item (Split-Path $OutputPath) -ItemType Directory -Force | Out-Null
$observations = [System.Collections.Generic.List[object]]::new()
$end = [DateTime]::UtcNow.AddSeconds($Seconds)
while ([DateTime]::UtcNow -lt $end) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $status = 0
    $failure = ''
    try {
        $response = Invoke-WebRequest -Uri "$($BaseUri.AbsoluteUri.TrimEnd('/'))/readyz" -TimeoutSec 10 -SkipHttpErrorCheck
        $status = [int]$response.StatusCode
    } catch { $failure = $_.Exception.Message }
    $observations.Add([pscustomobject]@{
        utc = [DateTime]::UtcNow.ToString('o')
        status = $status
        milliseconds = $clock.ElapsedMilliseconds
        error = $failure
    })
    Start-Sleep -Seconds 1
}
$observations | ConvertTo-Json -Depth 4 | Set-Content $OutputPath -Encoding utf8
$ok = @($observations | Where-Object status -EQ 200).Count
[pscustomobject]@{
    Samples = $observations.Count
    Successes = $ok
    AvailabilityPercent = [Math]::Round(100.0 * $ok / $observations.Count, 3)
    MaximumLatencyMs = ($observations.milliseconds | Measure-Object -Maximum).Maximum
    EvidencePath = $OutputPath
}
