from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shlex
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright

from gliner_client import Decision, GlinerClient
from run import DEMOS, PIKO_CHALLENGE_DESCRIPTIONS


class TimedGlinerClient(GlinerClient):
    def __init__(self, base_url: str):
        super().__init__(base_url)
        self.latencies_ms: list[float] = []
        self.field_requests = 0
        self.field_cache_hits = 0

    def classify(
        self,
        text: str,
        task: str,
        candidates: dict[str, str],
        prompt: str,
    ) -> Decision:
        started = time.perf_counter()
        try:
            return super().classify(text, task, candidates, prompt)
        finally:
            self.latencies_ms.append((time.perf_counter() - started) * 1000)

    def classify_field(self, label: str, candidates: dict[str, str]) -> Decision:
        self.field_requests += 1
        cache_key = (label.casefold(), tuple(candidates))
        if cache_key in self._field_cache:
            self.field_cache_hits += 1
        return super().classify_field(label, candidates)


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def latency_stats(values: list[float]) -> dict[str, float]:
    return {
        "mean_ms": round(statistics.fmean(values), 2),
        "p50_ms": round(percentile(values, 50), 2),
        "p95_ms": round(percentile(values, 95), 2),
        "min_ms": round(min(values), 2),
        "max_ms": round(max(values), 2),
    }


def distribution_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "mean": 0.0,
            "standard_deviation": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "minimum": 0.0,
            "maximum": 0.0,
        }
    return {
        "mean": round(statistics.fmean(values), 3),
        "standard_deviation": round(statistics.stdev(values), 3)
        if len(values) > 1
        else 0.0,
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "minimum": round(min(values), 3),
        "maximum": round(max(values), 3),
    }


def micro_benchmark(base_url: str, requests: int, warmup: int = 5) -> dict[str, Any]:
    labels = list(PIKO_CHALLENGE_DESCRIPTIONS)
    client = TimedGlinerClient(base_url)

    def classify(index: int) -> None:
        label = labels[index % len(labels)].replace("_", " ").title()
        client.classify(
            text=f"Form field label: {label}",
            task="source_field",
            candidates=PIKO_CHALLENGE_DESCRIPTIONS,
            prompt=f"Which source value should be entered into the form field labeled '{label}'?",
        )

    for index in range(warmup):
        classify(index)
    client.latencies_ms.clear()

    started = time.perf_counter()
    for index in range(requests):
        classify(index)
    elapsed = time.perf_counter() - started
    return {
        "requests": requests,
        "warmup_requests": warmup,
        "wall_seconds": round(elapsed, 3),
        "requests_per_second": round(requests / elapsed, 2),
        "latency": latency_stats(client.latencies_ms),
        "samples_ms": [round(value, 3) for value in client.latencies_ms],
    }


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def site_timer(events: list[dict[str, Any]]) -> str | None:
    completed = next(
        (event for event in reversed(events) if event["event"] == "completed"), None
    )
    if not completed:
        return None
    match = re.search(r"(\d{2}:\d{2}) Total time", completed.get("result", ""))
    return match.group(1) if match else None


def run_browser_benchmarks(
    base_url: str,
    demos: list[str],
    iterations: int,
) -> list[dict[str, Any]]:
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for demo in demos:
                for iteration in range(1, iterations + 1):
                    print(f"[{demo}] run {iteration}/{iterations}", flush=True)
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    client = TimedGlinerClient(base_url)
                    started = time.perf_counter()
                    result: dict[str, Any] = {
                        "demo": demo,
                        "iteration": iteration,
                        "success": False,
                    }
                    try:
                        artifact = DEMOS[demo](page, client)
                        events = read_events(artifact)
                        result.update(
                            success=True,
                            artifact=str(artifact),
                            decision_events=sum(
                                event["event"]
                                in {
                                    "field_classification",
                                    "business_decision",
                                    "browser_action",
                                }
                                for event in events
                            ),
                            field_requests=client.field_requests,
                            field_cache_hits=client.field_cache_hits,
                            site_timer=site_timer(events),
                        )
                    except Exception as exc:  # noqa: BLE001 - preserve failed benchmark runs
                        result["error"] = str(exc)
                    finally:
                        result["end_to_end_seconds"] = round(
                            time.perf_counter() - started, 3
                        )
                        result["model_calls"] = len(client.latencies_ms)
                        result["model_total_ms"] = round(sum(client.latencies_ms), 2)
                        result["model_latency"] = (
                            latency_stats(client.latencies_ms)
                            if client.latencies_ms
                            else None
                        )
                        result["model_latency_samples_ms"] = [
                            round(value, 3) for value in client.latencies_ms
                        ]
                        results.append(result)
                        context.close()
        finally:
            browser.close()
    return results


def summarize_browser_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
    for demo in dict.fromkeys(run["demo"] for run in runs):
        demo_runs = [run for run in runs if run["demo"] == demo]
        passed = [run for run in demo_runs if run["success"]]
        durations = [run["end_to_end_seconds"] for run in passed]
        model_latencies = [
            run["model_latency"]["p50_ms"] for run in passed if run["model_latency"]
        ]
        summary[demo] = {
            "runs": len(demo_runs),
            "passed": len(passed),
            "success_rate": round(len(passed) / len(demo_runs), 3),
            "end_to_end_distribution_seconds": distribution_stats(durations),
            "end_to_end_p50_seconds": round(
                percentile([run["end_to_end_seconds"] for run in passed], 50), 3
            ),
            "end_to_end_p95_seconds": round(
                percentile([run["end_to_end_seconds"] for run in passed], 95), 3
            ),
            "model_calls_p50": round(
                percentile([run["model_calls"] for run in passed], 50), 1
            ),
            "model_total_p50_ms": round(
                percentile([run["model_total_ms"] for run in passed], 50), 2
            ),
            "model_call_p50_ms": round(percentile(model_latencies, 50), 2),
            "field_cache_hits_p50": round(
                percentile([run.get("field_cache_hits", 0) for run in passed], 50), 1
            ),
            "site_timers": [
                run["site_timer"] for run in passed if run.get("site_timer")
            ],
        }
    return summary


def command_output(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def environment_metadata() -> dict[str, Any]:
    memory_kib = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                memory_kib = int(line.split()[1])
                break
    except OSError:
        pass
    return {
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
        "host_memory_gib": round(memory_kib / 1024 / 1024, 2),
        "gpu": command_output(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,compute_cap",
                "--format=csv,noheader",
            ]
        ),
        "git_commit": command_output(["git", "rev-parse", "HEAD"]),
        "packages": {
            package: importlib.metadata.version(package)
            for package in ("httpx", "openpyxl", "playwright")
        },
    }


def write_runs_csv(path: Path, runs: list[dict[str, Any]]) -> None:
    fields = [
        "demo",
        "iteration",
        "success",
        "end_to_end_seconds",
        "decision_events",
        "field_requests",
        "field_cache_hits",
        "model_calls",
        "model_total_ms",
        "site_timer",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(runs)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def markdown_report(results: dict[str, Any]) -> str:
    micro = results["micro"]
    lines = [
        "# Benchmark results",
        "",
        f"Measured: `{results['measured_at']}`",
        f"Command: `{results['command']}`",
        f"Git commit: `{results['system']['git_commit']}`",
        f"Host: `{results['system']['platform']}`",
        f"GPU: `{results['system']['gpu']}`",
        f"Python: `{results['system']['python']}` / Playwright: `{results['system']['packages']['playwright']}`",
        f"Model: `{results['model']['model']}` at `{results['model']['revision']}`",
        "",
        "## Raw GLiNER decisions",
        "",
        "Sequential local HTTP requests after warmup:",
        "",
        "| Requests | Throughput | Mean | p50 | p95 | Min | Max |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {micro['requests']} | {micro['requests_per_second']}/s | "
            f"{micro['latency']['mean_ms']} ms | {micro['latency']['p50_ms']} ms | "
            f"{micro['latency']['p95_ms']} ms | {micro['latency']['min_ms']} ms | "
            f"{micro['latency']['max_ms']} ms |"
        ),
        "",
        "## Browser workflows",
        "",
        "| Demo | Passed | Mean ± SD | p50 | p95 | Min–max | Model calls | Model time | Cache hits | Site timer |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for demo, item in results["browser_summary"].items():
        timers = ", ".join(item["site_timers"]) or "n/a"
        distribution = item["end_to_end_distribution_seconds"]
        lines.append(
            f"| `{demo}` | {item['passed']}/{item['runs']} | "
            f"{distribution['mean']} ± {distribution['standard_deviation']} s | "
            f"{item['end_to_end_p50_seconds']} s | {item['end_to_end_p95_seconds']} s | "
            f"{distribution['minimum']}–{distribution['maximum']} s | "
            f"{item['model_calls_p50']} | {item['model_total_p50_ms']} ms | "
            f"{item['field_cache_hits_p50']} | {timers} |"
        )
    lines.extend(
        [
            "",
            "## Reading the numbers",
            "",
            "- Raw latency includes local HTTP and JSON overhead, not only GPU kernel time.",
            "- End-to-end time includes third-party page loads, downloads, rendering, and browser actions.",
            "- Field cache hits avoid repeated model calls after a label has already been classified.",
            f"- This report uses {results['configuration']['iterations']} runs per workflow; it measures reproducibility, not concurrent capacity.",
            "- ParaBank runs create synthetic users and sandbox payments; no real financial system is used.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--micro-requests", type=int, default=50)
    parser.add_argument("--demos", nargs="+", choices=list(DEMOS), default=list(DEMOS))
    parser.add_argument("--gliner-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", default="benchmarks")
    args = parser.parse_args()

    started_at = datetime.now(UTC)
    model = httpx.get(f"{args.gliner_url}/health", timeout=10).json()
    micro = micro_benchmark(args.gliner_url, args.micro_requests)
    browser_runs = run_browser_benchmarks(args.gliner_url, args.demos, args.iterations)
    results = {
        "measured_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "command": " ".join(shlex.quote(part) for part in [sys.executable, *sys.argv]),
        "configuration": {
            "iterations": args.iterations,
            "micro_requests": args.micro_requests,
            "demos": args.demos,
            "sequential": True,
        },
        "system": environment_metadata(),
        "model": model,
        "micro": micro,
        "browser_runs": browser_runs,
        "browser_summary": summarize_browser_runs(browser_runs),
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.json"
    report_path = output_dir / "RESULTS.md"
    csv_path = output_dir / "runs.csv"
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    report_path.write_text(markdown_report(results), encoding="utf-8")
    write_runs_csv(csv_path, browser_runs)
    checksum_lines = [
        f"{sha256(path)}  {path.name}" for path in (report_path, results_path, csv_path)
    ]
    (output_dir / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )
    print(markdown_report(results))


if __name__ == "__main__":
    main()
