#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
hostname=
parse_args "$@"
[[ $hostname =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || die 'Supply a DNS hostname, not a URL.'
directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/rendered/certs
mkdir -p "$directory"
umask 077
openssl req -x509 -newkey rsa:2048 -sha256 -noenc -days 14 \
    -subj "/CN=$hostname" -addext "subjectAltName=DNS:$hostname" \
    -addext 'basicConstraints=critical,CA:FALSE' \
    -keyout "$directory/tls.key" -out "$directory/tls.crt"
printf 'Self-signed lab certificate created under %s. Never use it for customer production.\n' "$directory"
