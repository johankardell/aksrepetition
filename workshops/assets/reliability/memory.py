"""One-shot container-cgroup OOM probe, never a node-pressure generator."""

from pathlib import Path
import time


MIB = 1024 * 1024


def memory_limit():
    for filename in ("/sys/fs/cgroup/memory.max",
                     "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        path = Path(filename)
        if path.exists():
            value = path.read_text().strip()
            if value.isdigit():
                return int(value)
            raise RuntimeError("No finite container memory limit; refusing allocation")
    raise RuntimeError("Cannot verify a container memory cgroup; refusing allocation")


def touched_chunk(size):
    block = bytearray(size)
    block[::4096] = b"x" * len(range(0, size, 4096))
    return block


def run(limit=None, allocate=touched_chunk, sleep=time.sleep, clock=time.monotonic):
    limit = memory_limit() if limit is None else limit
    if not 0 < limit <= 64 * MIB:
        raise RuntimeError("Expected a container limit <=64 MiB; refusing allocation")
    held = []
    deadline = clock() + 8
    print(f"Verified container limit={limit}; allocating at most 96 MiB for 8 seconds",
          flush=True)
    for _ in range(96):
        if clock() >= deadline:
            break
        held.append(allocate(MIB))
        sleep(0.05)
    raise RuntimeError("Probe survived: OOM not demonstrated; do not increase the budget")


if __name__ == "__main__":
    run()
