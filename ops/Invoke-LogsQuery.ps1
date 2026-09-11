[CmdletBinding()]
param([Parameter(Mandatory)][string]$Query)
. "$PSScriptRoot\..\scripts\Use-Lab.ps1"
$workspace = az monitor log-analytics workspace show -g $Lab.ResourceGroup -n $Lab.WorkspaceName --query customerId -o tsv
$body = @{ query = $Query; timespan = 'PT1H' } | ConvertTo-Json -Compress
az rest --method post --url "https://api.loganalytics.azure.com/v1/workspaces/$workspace/query" `
    --resource 'https://api.loganalytics.io' --body $body
