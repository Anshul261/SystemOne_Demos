# Benchmark results

Measured: `2026-10-05T12:08:59.998617+00:00`
Command: `/home/alphadata-ai-team/Documents/SystemOne_Demos/.venv/bin/python3 benchmark.py --model jpt --demos clinic helpdesk --iterations 3 --micro-requests 20 --output-dir /tmp/opencode/gliner-jpt-compare-jpt`
Git commit: `739e791081f4e1454bf6a4599c8ba962cfd018c1`
Host: `Linux-6.17.0-1032-nvidia-aarch64-with-glibc2.39`
GPU: `NVIDIA GB10, 580.173.02, 12.1`
Python: `3.11.15` / Playwright: `1.55.0`
Model: `kirp/jpt-0.8b` at `unreported`

## Raw GLiNER decisions

Sequential local HTTP requests after warmup:

| Requests | Throughput | Mean | p50 | p95 | Min | Max |
|---:|---:|---:|---:|---:|---:|---:|
| 20 | 22.65/s | 44.15 ms | 37.86 ms | 60.3 ms | 34.0 ms | 67.38 ms |

## Browser workflows

| Demo | Passed | Mean ± SD | p50 | p95 | Min–max | Model calls | Model time | Cache hits | Site timer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `clinic` | 3/3 | 1.806 ± 0.09 s | 1.825 s | 1.88 s | 1.708–1.886 s | 5.0 | 1230.87 ms | 0.0 | n/a |
| `helpdesk` | 3/3 | 0.826 ± 0.035 s | 0.813 s | 0.861 s | 0.799–0.866 s | 4.0 | 367.03 ms | 0.0 | n/a |

## Reading the numbers

- Raw latency includes local HTTP and JSON overhead, not only GPU kernel time.
- End-to-end time includes page loads, rendering, and browser actions; external workflows also include downloads and network delays.
- Field cache hits avoid repeated model calls after a label has already been classified.
- This report uses 3 runs per workflow; it measures reproducibility, not concurrent capacity.
