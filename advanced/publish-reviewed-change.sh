#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)/lib.sh"

message=''
parse_args "$@"
[[ -n $message ]] || die '--message is required.'
current_branch=$(git branch --show-current)
[[ $current_branch == main ]] \
    || die 'Start on the updated main branch. Finish any existing review before publishing another lab change.'
staged=$(git diff --cached --name-only)
[[ -n $staged ]] || die 'Stage only the intended lab files with git add before calling this helper.'
git fetch origin main
head=$(git rev-parse HEAD)
origin_head=$(git rev-parse origin/main)
[[ $head == "$origin_head" ]] \
    || die 'Local main is not current. Preserve your staged changes, update from origin/main, and review the diff before retrying.'
branch="lab-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
git switch -c "$branch"
git commit -m "$message"
git push -u origin "$branch"
url=$(gh pr create --base main --head "$branch" --fill)
printf 'Review through your normal protected-branch process: %s\n' "$url"
while true; do
    printf '%s' 'After the approved PR is merged, press Enter to continue; type stop to leave it pending: '
    if ! IFS= read -r answer || [[ ${answer,,} == stop ]]; then
        die "Review left pending at $url. Do not reconcile or promote data until the change is merged."
    fi
    state=$(gh pr view "$url" --json state --jq .state)
    [[ $state != CLOSED ]] || die 'The PR was closed without merging. Stop this lab change and resolve the review.'
    [[ $state == MERGED ]] && break
    printf '%s\n' 'The PR is not merged yet; no reconciliation or promotion is authorized.'
done
git switch main
git pull --ff-only
head=$(git rev-parse HEAD)
printf 'Reviewed change is on main at %s.\n' "$head"
