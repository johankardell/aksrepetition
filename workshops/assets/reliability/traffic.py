"""Bounded, sequential service traffic; reports observations, not an SLO claim."""

import json
import time
import urllib.error
import urllib.request


def sample(url, opener=urllib.request.urlopen, clock=time.monotonic):
    start = clock()
    result = {"status": None}
    try:
        with opener(url, timeout=2) as response:
            payload = json.loads(response.read())
            if not isinstance(payload, dict):
                raise ValueError("Expected diagnostic JSON object")
            result["status"] = response.status
            result.update({key: payload[key] for key in ("pod", "revision") if key in payload})
    except urllib.error.HTTPError as error:
        result["status"] = error.code
        error.close()
    except (OSError, ValueError) as error:
        result["status"] = None
        result["error"] = type(error).__name__
    result["elapsed_ms"] = round((clock() - start) * 1000, 1)
    return result


def run(url="http://diagnostic:8080/work", seconds=120,
        take_sample=sample, clock=time.monotonic, sleep=time.sleep):
    if not 1 <= seconds <= 120:
        raise ValueError("Traffic duration must be 1..120 seconds")
    deadline = clock() + seconds
    counts = {"requests": 0, "ok": 0, "failed": 0}
    while clock() < deadline:
        result = take_sample(url)
        counts["requests"] += 1
        counts["ok" if result["status"] == 200 else "failed"] += 1
        print(json.dumps({"time": time.time(), **result}), flush=True)
        sleep(0.5)
    print(json.dumps({"summary": counts}), flush=True)
    return 0 if counts["requests"] and not counts["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
