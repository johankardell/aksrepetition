[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ResourceGroup,
    [Parameter(Mandatory)][string]$Location,
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$ResourceId,
    [Parameter(Mandatory)][string]$GroupId,
    [Parameter(Mandatory)][string]$SubnetId,
    [Parameter(Mandatory)][string]$VnetId,
    [Parameter(Mandatory)][string]$ZoneName
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
az network private-dns zone create -g $ResourceGroup -n $ZoneName -o none
$zoneId = az network private-dns zone show -g $ResourceGroup -n $ZoneName --query id -o tsv
$link = "$Name-link"
$links = az network private-dns link vnet list -g $ResourceGroup -z $ZoneName -o json | ConvertFrom-Json
if (-not @($links | Where-Object { $_.virtualNetwork.id -eq $VnetId }).Count) {
    az network private-dns link vnet create -g $ResourceGroup -z $ZoneName -n $link --virtual-network $VnetId --registration-enabled false -o none
}
az network private-endpoint create -g $ResourceGroup -n $Name -l $Location --subnet $SubnetId --private-connection-resource-id $ResourceId --group-ids $GroupId --connection-name $Name -o none
az network private-endpoint dns-zone-group create -g $ResourceGroup --endpoint-name $Name -n default --private-dns-zone $zoneId --zone-name $GroupId -o none
az network private-endpoint show -g $ResourceGroup -n $Name --query '{id:id,state:privateLinkServiceConnections[0].privateLinkServiceConnectionState.status}' -o json
