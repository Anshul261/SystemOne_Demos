"""Compare routing accuracy and latency as the candidate list grows."""

from __future__ import annotations

import argparse
import statistics
import time

from intake import HELPDESK_QUEUES
from decision_clients import MODELS, make_client

DEPARTMENTS = (
    "sales", "legal", "compliance", "communications", "procurement",
    "training", "security", "shipping", "inventory", "marketing",
    "operations", "research", "recruiting", "quality", "planning",
    "analytics", "customer success", "design", "partnerships", "events",
    "production", "records", "strategy", "vendor management",
)
REQUESTS = (
    "a status update", "a policy question", "an approval", "a correction",
    "a delivery", "a change of owner", "an incident", "a missing document",
    "a renewal", "a deadline", "a report", "a follow-up",
)
MESSAGE = (
    "[subject] Cannot sign in to work account\n"
    "[body] My work account says access denied after the password reset. "
    "I need IT to restore my login before my shift tomorrow."
)


def routing_options(size: int) -> dict[str, str]:
    if not 6 <= size <= 255:
        raise ValueError("Choose 6 through 255 options")
    options = dict(HELPDESK_QUEUES)
    for department in DEPARTMENTS:
        for request in REQUESTS:
            if len(options) == size:
                return options
            key = f"route_{len(options):03d}"
            options[key] = f"Route to {department} for {request}"
    return options


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", choices=sorted(MODELS), default=["omni", "3b", "multi"])
    parser.add_argument("--sizes", nargs="+", type=int, default=[8, 32, 128, 255])
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    for size in args.sizes:
        routing_options(size)
    print("model  options  correct  mean_ms  selected / error", flush=True)
    failed = False
    for model in args.models:
        client = make_client(model)
        client.timeout = 180
        for size in args.sizes:
            options = routing_options(size)
            times = []
            correct = 0
            try:
                for _ in range(args.iterations):
                    start = time.perf_counter()
                    decision = client.classify(
                        MESSAGE, "queue", options,
                        "Which staff queue should handle this employee request?",
                    )
                    times.append((time.perf_counter() - start) * 1000)
                    correct += decision.label == "it_access"
                print(
                    f"{model:6} {size:7}  {correct}/{args.iterations}"
                    f"    {statistics.fmean(times):7.1f}  {decision.label}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - report unsupported sizes and unavailable services
                failed = True
                print(f"{model:6} {size:7}  error    -        {exc}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
