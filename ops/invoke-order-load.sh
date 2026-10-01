#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/../scripts/lib.sh"
base_uri='' count=100 concurrency=4 duration_seconds=120 timeout_seconds=10 delay_milliseconds=100
operation=PostOrders output_path=.artifacts/order-load.json
parse_args "$@"
[[ $base_uri =~ ^https?://[^/]+ ]] || die 'Use an HTTP(S) application URL.'
validate_range Count "$count" 1 2000
validate_range Concurrency "$concurrency" 1 20
validate_range DurationSeconds "$duration_seconds" 1 600
validate_range TimeoutSeconds "$timeout_seconds" 1 30
validate_range DelayMilliseconds "$delay_milliseconds" 0 10000
case $operation in PostOrders|Browse|Health|OrderLookup) ;; *) die 'Invalid --operation.' ;; esac
python3 - "$base_uri" "$count" "$concurrency" "$duration_seconds" "$timeout_seconds" "$delay_milliseconds" "$operation" "$output_path" <<'PY'
import concurrent.futures, datetime, json, math, pathlib, ssl, sys, time, urllib.error, urllib.request, uuid
root = sys.argv[1].rstrip("/")
count, concurrency, duration, timeout, delay = map(int, sys.argv[2:7])
operation, output = sys.argv[7:]
prefix = "load-" + uuid.uuid4().hex[:12]
start = time.monotonic()
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
deadline = start + duration
# curl and Python both use this explicitly approved lab trust bundle.
import os
context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or os.environ.get("CURL_CA_BUNDLE"))
def send(number):
    if time.monotonic() >= deadline:
        return None
    order_id = f"{prefix}-{number}"
    data = None
    path = {"PostOrders": "/orders", "Browse": "/", "Health": "/healthz", "OrderLookup": f"/orders/{order_id}"}[operation]
    if operation == "PostOrders":
        data = json.dumps({"id": order_id, "item": "synthetic-widget"}).encode()
    request = urllib.request.Request(root + path, data=data, headers={"Content-Type": "application/json"})
    begin = time.monotonic()
    status, failure = 0, None
    try:
        with urllib.request.urlopen(request, timeout=min(timeout, max(0.001, deadline - begin)), context=context) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failure = str(error)
    elapsed = (time.monotonic() - begin) * 1000
    time.sleep(min(delay / 1000, max(0, deadline - time.monotonic())))
    return {"id": order_id, "status": status, "milliseconds": elapsed, "error": failure}
with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
    results = [result for result in pool.map(send, range(1, count + 1)) if result is not None]
times = sorted(row["milliseconds"] for row in results)
success = sum(200 <= row["status"] < 400 for row in results)
summary = {"startedUtc": started, "elapsedSeconds": time.monotonic() - start, "requested": count,
           "sent": len(results), "successful": success, "availabilityPercent": 100 * success / len(results) if results else 0,
           "p95Milliseconds": times[max(0, math.ceil(len(times) * .95) - 1)] if times else None,
           "operation": operation, "requests": results}
path = pathlib.Path(output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({key: value for key, value in summary.items() if key != "requests"}, indent=2))
PY
