#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/../scripts/lib.sh"
query=
timespan=PT1H
parse_args "$@"
require_value query "$query"
[[ $timespan =~ ^P([1-9][0-9]*D|T[1-9][0-9]*[HMS])$ ]] \
    || die '--timespan must be a positive whole-day, hour, minute or second ISO 8601 duration (for example P1D or PT1H).'
source "$(dirname -- "$0")/../scripts/use-lab.sh"
workspace=$(az monitor log-analytics workspace show -g "$(lab_value ResourceGroup)" -n "$(lab_value WorkspaceName)" --query customerId -o tsv)
body=$(jq -n --arg query "$query" --arg timespan "$timespan" '{query:$query,timespan:$timespan}')
az rest --method post --url "https://api.loganalytics.azure.com/v1/workspaces/$workspace/query" \
    --resource https://api.loganalytics.io --body "$body"
