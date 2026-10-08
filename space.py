"""Start the d1 and extraction model services, then the Gradio UI, inside a Docker Space."""

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
        services = {
            "d1": ("services/d1", os.getenv("D1_URL", "http://127.0.0.1:8020")),
            "extraction": ("services/extract", os.getenv("EXTRACT_URL", "http://127.0.0.1:8030")),
        }
        started = {}
        for name, (folder, _) in services.items():
            started[name] = subprocess.Popen(
                [str(ROOT / folder / ".venv/bin/python"), str(ROOT / folder / "server.py")], cwd=ROOT / folder)
            processes.append(started[name])
        deadline = time.monotonic() + 1800
        print("Loading the d1 and extraction models before starting the demo UI…", flush=True)
        for name, (_, url) in services.items():
            while True:
                if started[name].poll() is not None:
                    raise RuntimeError(f"The {name} service exited with status {started[name].returncode}")
                try:
                    with urlopen(url.rstrip("/") + "/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"The {name} service did not become ready within 30 minutes")
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
