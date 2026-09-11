[CmdletBinding()]
param([Parameter(Mandatory)][string]$RegistryServer)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$manifest = flux install --export
$images = @([regex]::Matches(($manifest -join "`n"), 'image:\s+(ghcr\.io/fluxcd/[^\s]+)') |
    ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
if ($images.Count -lt 4) { throw 'Expected the four standard Flux controller images.' }
foreach ($image in $images) {
    $target = $image.Replace('ghcr.io', $RegistryServer)
    docker pull $image
    docker tag $image $target
    docker push $target
}
