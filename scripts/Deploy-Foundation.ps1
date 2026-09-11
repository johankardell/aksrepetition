[CmdletBinding(SupportsShouldProcess)]
param([switch]$Apply)
. "$PSScriptRoot\Use-Lab.ps1"
if ($Lab.KubernetesVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Select an explicit supported regional Kubernetes patch in local.settings.json.'
}
$keyPath = Join-Path $Root 'rendered\keys\aks.pub'
if (-not (Test-Path $keyPath)) {
    throw 'Create rendered\keys\aks.pub using the README bootstrap SSH-key directive first.'
}
$sshPublicKey = (Get-Content $keyPath -Raw).Trim()
if ($sshPublicKey -notmatch '^ssh-rsa ') { throw 'Supply an RSA public key, not a private key.' }
$params = @(
    "prefix=$($Lab.Prefix)", "location=$($Lab.Location)",
    "kubernetesVersion=$($Lab.KubernetesVersion)", "vmSize=$($Lab.VmSize)",
    "adminGroupObjectId=$($Lab.AdminGroupObjectId)", "sshPublicKey=$sshPublicKey"
)
$template = Join-Path $Root 'infra\main.bicep'
az deployment group what-if -g $Lab.ResourceGroup -n foundation -f $template -p @params
if ($Apply -and $PSCmdlet.ShouldProcess($Lab.ResourceGroup, 'Deploy billable AKS foundation')) {
    az deployment group create -g $Lab.ResourceGroup -n foundation -f $template -p @params -o none
}
