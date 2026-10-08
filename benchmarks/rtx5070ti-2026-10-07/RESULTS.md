# Benchmark results

Measured: `2026-10-07T09:07:02.573527+00:00`
Command: `/home/anshul/projects/SystemOne_Demos/.venv/bin/python benchmark.py --iterations 10 --micro-requests 500 --demos rpa parabank piko piko-excel --output-dir benchmarks/rtx5070ti-2026-10-07`
Git commit: `fa7e14a446fca34cd9dace33a1fdd8d5a5369dab`
Host: `Linux-5.15.167.4-microsoft-standard-WSL2-x86_64-with-glibc2.39`
GPU: `NVIDIA GeForce RTX 5070 Ti, 617.14, 12.0`
Python: `3.13.7` / Playwright: `1.55.0`
Model: `fastino/GLiNER2.5-Decide` at `unreported`

## Raw model decisions

Sequential local HTTP requests after warmup:

| Requests | Throughput | Mean | p50 | p95 | Min | Max |
|---:|---:|---:|---:|---:|---:|---:|
| 500 | 33.92/s | 29.48 ms | 28.63 ms | 31.23 ms | 27.57 ms | 60.59 ms |

## Browser workflows

| Demo | Passed | Mean ± SD | p50 | p95 | Min–max | Model calls | Model time | Cache hits | Site timer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `rpa` | 10/10 | 4.964 ± 0.777 s | 4.625 s | 6.036 s | 4.033–6.082 s | 7.0 | 218.03 ms | 63.0 | n/a |
| `parabank` | 10/10 | 3.86 ± 0.391 s | 3.81 s | 4.535 s | 3.444–4.572 s | 21.0 | 645.63 ms | 0.0 | n/a |
| `piko` | 10/10 | 3.546 ± 0.096 s | 3.522 s | 3.682 s | 3.378–3.683 s | 16.0 | 539.68 ms | 0.0 | n/a |
| `piko-excel` | 10/10 | 8.71 ± 1.02 s | 8.523 s | 10.334 s | 7.503–10.648 s | 9.0 | 279.63 ms | 81.0 | 00:05, 00:06, 00:06, 00:07, 00:06, 00:08, 00:06, 00:07, 00:08, 00:05 |

## Reading the numbers

- Raw latency includes local HTTP and JSON overhead, not only GPU kernel time.
- End-to-end time includes page loads, rendering, and browser actions; external workflows also include downloads and network delays.
- Field cache hits avoid repeated model calls after a label has already been classified.
- This report uses 10 runs per workflow; it measures reproducibility, not concurrent capacity.
- ParaBank runs create synthetic users and sandbox payments; no real financial system is used.
