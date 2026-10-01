#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd)/lib.sh"

base_uri='' ledger_path='./.artifacts/advanced/dr-accepted.json'
timeout_seconds=180 report_path='./.artifacts/advanced/order-verification.json'
parse_args "$@"
[[ -n $base_uri ]] || die '--base-uri is required.'
[[ $timeout_seconds =~ ^\+?0*([0-9]{1,4})$ ]] || die '--timeout-seconds must be an integer from 1 to 3600.'
timeout_seconds=$((10#${BASH_REMATCH[1]}))
(( timeout_seconds >= 1 && timeout_seconds <= 3600 )) || die '--timeout-seconds must be an integer from 1 to 3600.'
python3 - "$base_uri" "$ledger_path" "$timeout_seconds" "$report_path" <<'PY'
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

base_uri, ledger_path, timeout_seconds, report_path = sys.argv[1:]
uri = urllib.parse.urlsplit(base_uri)
if uri.scheme not in ("http", "https") or not uri.netloc:
    sys.exit("--base-uri must be an absolute HTTP(S) URI.")
context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or os.environ.get("CURL_CA_BUNDLE"))
orders = json.loads(pathlib.Path(ledger_path).read_text(encoding="utf-8-sig"))
# Accept legacy ledgers containing a single object as well as JSON arrays.
if isinstance(orders, dict):
    orders = [orders]
if not isinstance(orders, list) or not orders:
    sys.exit("The accepted order ledger is empty or is not an array/object.")
if any(not isinstance(order, dict) or not isinstance(order.get("id"), str)
       or not isinstance(order.get("item"), str) for order in orders):
    sys.exit("Every accepted order must contain string id and item fields.")
deadline = time.monotonic() + int(timeout_seconds)
while True:
    results = []
    for order in orders:
        verified, error_text = False, ""
        try:
            order_id = urllib.parse.quote(order["id"], safe="")
            with urllib.request.urlopen(base_uri.rstrip("/") + "/orders/" + order_id, timeout=10, context=context) as response:
                actual = json.load(response)
            verified = (isinstance(actual, dict) and actual.get("id") == order["id"]
                        and actual.get("item") == order["item"])
            if not verified:
                error_text = "ID/item mismatch."
        except urllib.error.HTTPError as error:
            error_text = str(error)
            error.close()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as error:
            error_text = str(error)
        results.append({"id": order["id"], "item": order["item"],
                        "verified": verified, "error": error_text})
    missing = sum(not result["verified"] for result in results)
    if missing == 0 or time.monotonic() >= deadline:
        break
    time.sleep(2)
path = pathlib.Path(report_path)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "expected": len(orders),
    "unverified": missing,
    "results": results,
}, indent=2) + "\n")
if missing:
    sys.exit(f"{missing} orders are missing or mismatched. See {report_path}.")
print(f"Verified all {len(orders)} accepted IDs and items. SQL uniqueness must also be checked.")
PY
