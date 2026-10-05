# Benchmark results

Measured: `2026-10-05T10:44:02.361735+00:00`
Host: `aarch64` / `3.11.15`
Model: `fastino/GLiNER2.5-Decide` at `5a7adf72a23b4d311abae6ce050d7f0012bb3416`

## Raw GLiNER decisions

Sequential local HTTP requests after warmup:

| Requests | Throughput | Mean | p50 | p95 | Min | Max |
|---:|---:|---:|---:|---:|---:|---:|
| 50 | 52.81/s | 18.93 ms | 18.35 ms | 22.84 ms | 16.83 ms | 27.78 ms |

## Browser workflows

| Demo | Passed | End-to-end p50 | End-to-end p95 | Model calls | Model time | Cache hits | Site timer |
|---|---:|---:|---:|---:|---:|---:|---|
| `rpa` | 3/3 | 4.821 s | 6.527 s | 7.0 | 149.45 ms | 63.0 | n/a |
| `parabank` | 3/3 | 3.714 s | 3.89 s | 21.0 | 395.16 ms | 0.0 | n/a |
| `piko` | 3/3 | 3.685 s | 3.717 s | 16.0 | 357.08 ms | 0.0 | n/a |
| `piko-excel` | 3/3 | 7.132 s | 7.463 s | 9.0 | 180.9 ms | 81.0 | 00:04, 00:05, 00:05 |

## Reading the numbers

- Raw latency includes local HTTP and JSON overhead, not only GPU kernel time.
- End-to-end time includes third-party page loads, downloads, rendering, and browser actions.
- Field cache hits avoid repeated model calls after a label has already been classified.
- Three runs reveal obvious regressions but are not enough for capacity planning.
- ParaBank runs create synthetic users and sandbox payments; no real financial system is used.
