"""Dependency-free workshop runtime; never imports or starts the orders application."""

import argparse
import json
import os
from pathlib import Path
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    def __init__(self, directory, startup_seconds=12, clock=time.monotonic):
        self.directory = Path(directory)
        self.clock = clock
        self.started = clock()
        self.startup_seconds = startup_seconds
        self.draining = False

    def fault(self, probe, seconds):
        if probe not in ("ready", "live") or not 1 <= seconds <= 30:
            raise ValueError("Only ready/live faults lasting 1..30 seconds are allowed")
        (self.directory / probe).write_text(str(self.clock() + seconds))

    def failing(self, probe):
        try:
            return self.clock() < float((self.directory / probe).read_text())
        except FileNotFoundError:
            return False

    def status(self, path):
        started = self.clock() - self.started >= self.startup_seconds
        draining = self.draining or (self.directory / "drain").exists()
        if path == "/startupz":
            return 200 if started else 503
        if path == "/livez":
            return 503 if self.failing("live") else 200
        if path in ("/readyz", "/work"):
            return 200 if started and not draining and not self.failing("ready") else 503
        return 404


def event(name, **fields):
    print(json.dumps({"time": time.time(), "event": name, **fields}), flush=True)


def handler_for(state, revision, sleep=time.sleep):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def do_GET(self):
            status = state.status(self.path)
            if self.path == "/work" and status == 200:
                # A short, bounded in-flight request makes drain behavior observable.
                sleep(0.2)
            body = json.dumps({"status": status, "revision": revision,
                               "pod": os.environ.get("HOSTNAME", "offline")}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                event("client_disconnected")

        def log_message(self, *_args):
            pass

    return Handler


def serve(directory):
    directory = Path(directory)
    directory.mkdir(exist_ok=True)
    (directory / "drain").unlink(missing_ok=True)
    state = State(directory)
    revision = os.environ.get("REVISION", "v1")
    server = ThreadingHTTPServer(("0.0.0.0", 8080), handler_for(state, revision))
    server.daemon_threads = False
    stopping = threading.Event()

    def terminate(_signum, _frame):
        if stopping.is_set():
            return
        stopping.set()
        state.draining = True
        event("sigterm", revision=revision)

        def finish():
            time.sleep(3)
            server.shutdown()

        threading.Thread(target=finish).start()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    event("started", revision=revision)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        event("stopped", revision=revision)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("serve", "fault", "drain"))
    parser.add_argument("probe", nargs="?", choices=("ready", "live"))
    parser.add_argument("seconds", nargs="?", type=int)
    args = parser.parse_args()
    directory = Path(os.environ.get("STATE_DIR", "/state"))
    if args.operation == "serve":
        serve(directory)
    elif args.operation == "drain":
        (directory / "drain").write_text("draining\n")
        event("prestop_drain")
        time.sleep(3)
    elif args.probe is None or args.seconds is None:
        parser.error("fault requires a probe and duration")
    else:
        State(directory).fault(args.probe, args.seconds)


if __name__ == "__main__":
    main()
