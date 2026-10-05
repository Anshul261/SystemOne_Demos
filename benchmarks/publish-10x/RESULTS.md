# Benchmark results

Measured: `2026-10-05T10:58:54.575891+00:00`
Command: `/home/alphadata-ai-team/Documents/gliner-browser-demos/.venv/bin/python3 benchmark.py --iterations 10 --micro-requests 500 --output-dir benchmarks/publish-10x`
Git commit: `660e68f0558aa5aa90f266e517292354e456df53`
Host: `Linux-6.17.0-1032-nvidia-aarch64-with-glibc2.39`
GPU: `NVIDIA GB10, 580.173.02, 12.1`
Python: `3.11.15` / Playwright: `1.55.0`
Model: `fastino/GLiNER2.5-Decide` at `5a7adf72a23b4d311abae6ce050d7f0012bb3416`

## Raw GLiNER decisions

Sequential local HTTP requests after warmup:

| Requests | Throughput | Mean | p50 | p95 | Min | Max |
|---:|---:|---:|---:|---:|---:|---:|
| 500 | 49.45/s | 20.22 ms | 20.07 ms | 23.35 ms | 16.42 ms | 34.19 ms |

## Browser workflows

| Demo | Passed | Mean ± SD | p50 | p95 | Min–max | Model calls | Model time | Cache hits | Site timer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `rpa` | 10/10 | 4.892 ± 0.535 s | 4.736 s | 5.706 s | 4.019–5.89 s | 7.0 | 145.44 ms | 63.0 | n/a |
| `parabank` | 10/10 | 3.622 ± 0.271 s | 3.565 s | 4.054 s | 3.328–4.21 s | 21.0 | 434.57 ms | 0.0 | n/a |
| `piko` | 10/10 | 3.444 ± 0.275 s | 3.447 s | 3.736 s | 3.15–3.761 s | 16.0 | 340.27 ms | 0.0 | n/a |
| `piko-excel` | 10/10 | 8.34 ± 0.962 s | 8.372 s | 9.781 s | 7.231–10.157 s | 9.0 | 182.19 ms | 81.0 | 00:06, 00:05, 00:06, 00:05, 00:07, 00:07, 00:06, 00:06, 00:08, 00:05 |

## Reading the numbers

- Raw latency includes local HTTP and JSON overhead, not only GPU kernel time.
- End-to-end time includes third-party page loads, downloads, rendering, and browser actions.
- Field cache hits avoid repeated model calls after a label has already been classified.
- This report uses 10 runs per workflow; it measures reproducibility, not concurrent capacity.
- ParaBank runs create synthetic users and sandbox payments; no real financial system is used.
