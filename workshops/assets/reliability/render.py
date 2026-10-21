"""Render only the isolated fixture with an explicitly selected private ACR digest."""

import argparse
from pathlib import Path
import re
import subprocess
import uuid


SOURCE = Path(__file__).resolve().parent


def render(registry, image, destination):
    if not re.fullmatch(r"[a-z0-9]+\.azurecr\.io", registry):
        raise ValueError("Use the approved existing foundation ACR login server")
    if not re.fullmatch(re.escape(registry) + r"/order-app@sha256:[0-9a-f]{64}", image):
        raise ValueError("Image must be this ACR's immutable order-app sha256 digest")
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Render destination exists; inspect it instead of overwriting")
    destination.mkdir(parents=True)
    run_id = uuid.uuid4().hex
    (destination / "run-id").write_text(run_id + "\n")
    for source in SOURCE.iterdir():
        if source.suffix == ".yaml" or source.name in ("diagnostic.py", "memory.py", "traffic.py"):
            text = source.read_text().replace("__WORKSHOP_IMAGE__", image).replace(
                "__WORKSHOP_RUN__", run_id,
            )
            if "__WORKSHOP_" in text:
                raise ValueError(f"Unresolved token in {source.name}")
            (destination / source.name).write_text(text)
    try:
        result = subprocess.run(
            ["kubectl", "kustomize", str(destination)], check=True,
            capture_output=True, text=True, timeout=30,
        )
        (destination / "bundle.yaml").write_text(result.stdout)
    except (OSError, subprocess.SubprocessError):
        # Keep the failed render for diagnosis; never apply a partial directory.
        raise RuntimeError("Offline Kustomize validation failed; inspect render destination") from None
    return result.stdout


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    render(args.registry, args.image, args.output)
    print(f"Offline render validated: {args.output}; nothing applied")
