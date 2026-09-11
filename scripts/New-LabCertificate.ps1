param([Parameter(Mandatory)][string]$Hostname)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Hostname -notmatch '^[a-zA-Z0-9.-]+$') { throw 'Supply a DNS hostname, not a URL.' }
$directory = Join-Path (Split-Path $PSScriptRoot -Parent) 'rendered\certs'
New-Item $directory -ItemType Directory -Force | Out-Null
$key = [System.Security.Cryptography.RSA]::Create(2048)
try {
    $request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        "CN=$Hostname", $key, [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $san = [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
    $san.AddDnsName($Hostname)
    $request.CertificateExtensions.Add($san.Build())
    $request.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true))
    $certificate = $request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-5), [DateTimeOffset]::UtcNow.AddDays(14))
    try {
        Set-Content (Join-Path $directory 'tls.crt') $certificate.ExportCertificatePem() -Encoding ascii
        Set-Content (Join-Path $directory 'tls.key') $key.ExportPkcs8PrivateKeyPem() -Encoding ascii
    } finally { $certificate.Dispose() }
} finally { $key.Dispose() }
Write-Host 'Self-signed lab certificate created under rendered\certs. Never use it for customer production.'
