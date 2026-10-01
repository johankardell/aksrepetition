#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/../scripts/lib.sh"
query=
parse_args "$@"
require_value query "$query"
source "$(dirname -- "$0")/../scripts/use-lab.sh"
workspace=$(az monitor log-analytics workspace show -g "$(lab_value ResourceGroup)" -n "$(lab_value WorkspaceName)" --query customerId -o tsv)
body=$(jq -n --arg query "$query" '{query:$query,timespan:"PT1H"}')
az rest --method post --url "https://api.loganalytics.azure.com/v1/workspaces/$workspace/query" \
    --resource https://api.loganalytics.io --body "$body"
