# Benchmark results

Measured: `2026-10-05T12:08:45.016124+00:00`
Command: `/home/alphadata-ai-team/Documents/SystemOne_Demos/.venv/bin/python3 benchmark.py --model gliner --demos clinic helpdesk --iterations 3 --micro-requests 20 --output-dir /tmp/opencode/gliner-jpt-compare-gliner`
Git commit: `739e791081f4e1454bf6a4599c8ba962cfd018c1`
Host: `Linux-6.17.0-1032-nvidia-aarch64-with-glibc2.39`
GPU: `NVIDIA GB10, 580.173.02, 12.1`
Python: `3.11.15` / Playwright: `1.55.0`
Model: `fastino/GLiNER2.5-Decide` at `5a7adf72a23b4d311abae6ce050d7f0012bb3416`

## Raw GLiNER decisions

Sequential local HTTP requests after warmup:

| Requests | Throughput | Mean | p50 | p95 | Min | Max |
|---:|---:|---:|---:|---:|---:|---:|
| 20 | 53.71/s | 18.61 ms | 17.98 ms | 21.83 ms | 17.27 ms | 22.62 ms |

## Browser workflows

| Demo | Passed | Mean ± SD | p50 | p95 | Min–max | Model calls | Model time | Cache hits | Site timer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `clinic` | 3/3 | 0.709 ± 0.027 s | 0.724 s | 0.725 s | 0.678–0.725 s | 5.0 | 110.95 ms | 0.0 | n/a |
| `helpdesk` | 3/3 | 0.548 ± 0.019 s | 0.559 s | 0.56 s | 0.526–0.56 s | 4.0 | 85.2 ms | 0.0 | n/a |

## Reading the numbers

- Raw latency includes local HTTP and JSON overhead, not only GPU kernel time.
- End-to-end time includes page loads, rendering, and browser actions; external workflows also include downloads and network delays.
- Field cache hits avoid repeated model calls after a label has already been classified.
- This report uses 3 runs per workflow; it measures reproducibility, not concurrent capacity.
