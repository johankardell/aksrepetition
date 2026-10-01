#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)/lib.sh"

base_uri='' seconds=600 output_path='./.artifacts/advanced/traffic.json'
parse_args "$@"
[[ -n $base_uri ]] || die '--base-uri is required.'
[[ $seconds =~ ^\+?0*([0-9]{1,4})$ ]] || die '--seconds must be an integer from 1 to 7200.'
seconds=$((10#${BASH_REMATCH[1]}))
(( seconds >= 1 && seconds <= 7200 )) || die '--seconds must be an integer from 1 to 7200.'
python3 - "$base_uri" "$seconds" "$output_path" <<'PY'
import datetime
import http.client
import json
import os
import pathlib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

base_uri, seconds, output_path = sys.argv[1:]
uri = urllib.parse.urlsplit(base_uri)
if uri.scheme not in ("http", "https") or not uri.netloc:
    sys.exit("--base-uri must be an absolute HTTP(S) URI.")
context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or os.environ.get("CURL_CA_BUNDLE"))
path = pathlib.Path(output_path)
path.parent.mkdir(parents=True, exist_ok=True)
observations = []
end = time.monotonic() + int(seconds)
while time.monotonic() < end:
    start = time.monotonic()
    status, failure = 0, ""
    try:
        try:
            response = urllib.request.urlopen(base_uri.rstrip("/") + "/readyz", timeout=10, context=context)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            response.read()
            status = response.status
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        failure = str(error)
    observations.append({
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": status,
        "milliseconds": int((time.monotonic() - start) * 1000),
        "error": failure,
    })
    time.sleep(1)
path.write_text(json.dumps(observations, indent=2) + "\n")
successes = sum(sample["status"] == 200 for sample in observations)
print(json.dumps({
    "Samples": len(observations),
    "Successes": successes,
    "AvailabilityPercent": round(100 * successes / len(observations), 3),
    "MaximumLatencyMs": max(sample["milliseconds"] for sample in observations),
    "EvidencePath": output_path,
}, indent=2))
PY
