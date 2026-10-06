"""Start the model service and Gradio UI inside a Docker Space."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


def main() -> None:
    processes: list[subprocess.Popen] = []

    def stop():
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def shutdown(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        model = subprocess.Popen(
            [str(ROOT / "services/gliner/.venv/bin/python"), str(ROOT / "services/gliner/server.py")],
            cwd=ROOT,
        )
        processes.append(model)
        health_url = os.getenv("GLINER_URL", "http://127.0.0.1:8010").rstrip("/") + "/health"
        deadline = time.monotonic() + 1800
        print("Loading GLiNER before starting the demo UI…", flush=True)
        while True:
            if model.poll() is not None:
                raise RuntimeError(f"GLiNER exited with status {model.returncode}")
            try:
                with urlopen(health_url, timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("GLiNER did not become ready within 30 minutes")
            time.sleep(1)
        ui = subprocess.Popen([sys.executable, str(ROOT / "ui.py")], cwd=ROOT)
        processes.append(ui)
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise RuntimeError("A demo service exited. See the preceding service logs.")
    finally:
        stop()


if __name__ == "__main__":
    main()
