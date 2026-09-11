[CmdletBinding()]
param([Parameter(Mandatory)][string]$Message)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
if ((git branch --show-current) -ne 'main') {
    throw 'Start on the updated main branch. Finish any existing review before publishing another lab change.'
}
$staged = @(git diff --cached --name-only)
if (-not $staged.Count) { throw 'Stage only the intended lab files with git add before calling this helper.' }
git fetch origin main
if ((git rev-parse HEAD) -ne (git rev-parse origin/main)) {
    throw 'Local main is not current. Preserve your staged changes, update from origin/main, and review the diff before retrying.'
}
$branch = 'lab-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
git switch -c $branch
git commit -m $Message
git push -u origin $branch
$url = gh pr create --base main --head $branch --fill
Write-Host "Review through your normal protected-branch process: $url"
do {
    $answer = Read-Host 'After the approved PR is merged, press Enter to continue; type stop to leave it pending'
    if ($answer -eq 'stop') { throw "Review left pending at $url. Do not reconcile or promote data until the change is merged." }
    $state = gh pr view $url --json state --jq .state
    if ($state -eq 'CLOSED') { throw 'The PR was closed without merging. Stop this lab change and resolve the review.' }
    if ($state -ne 'MERGED') { Write-Host 'The PR is not merged yet; no reconciliation or promotion is authorized.' }
} while ($state -ne 'MERGED')
git switch main
git pull --ff-only
Write-Host "Reviewed change is on main at $(git rev-parse HEAD)."

