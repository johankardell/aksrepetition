#!/usr/bin/env bash
set -euo pipefail
exec bash "$(cd -- "$(dirname -- "$0")/../ops" && pwd)/publish-reviewed-change.sh" "$@"
