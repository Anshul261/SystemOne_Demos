# RTX 5070 Ti repeatability benchmark

Measured from `2026-10-07T09:14:22.413134+00:00` to `2026-10-07T09:33:28.803308+00:00`.

GPU: `NVIDIA GeForce RTX 5070 Ti, 617.14, 12.0`. Model: `fastino/GLiNER2.5-Decide`, CUDA.
Model cache revision: `5a7adf72a23b4d311abae6ce050d7f0012bb3416`.

Five rounds on one host and one continuously loaded model service. Each round uses 50 warmup requests, 500 measured requests, and ten runs of each of four browser workflows. Workflow order rotates between rounds. The earlier pilot run is excluded.

## Decision latency

| Requests | Mean | Median | p95 | Sequential throughput |
|---:|---:|---:|---:|---:|
| 2500 | 29.57 ms | 28.71 ms | 31.65 ms | 33.82/s |

95% t interval for the mean across five round means: **29.18–29.95 ms**. Round mean range: 29.19–29.96 ms.

| Round | Workflow order | Mean | Median | p95 | Requests/s |
|---:|---|---:|---:|---:|---:|
| 1 | piko → rpa → piko-excel → parabank | 29.19 ms | 28.44 ms | 30.35 ms | 34.25 |
| 2 | rpa → piko-excel → parabank → piko | 29.96 ms | 28.97 ms | 32.89 ms | 33.37 |
| 3 | piko-excel → parabank → piko → rpa | 29.34 ms | 28.57 ms | 30.35 ms | 34.08 |
| 4 | parabank → piko → rpa → piko-excel | 29.74 ms | 28.95 ms | 31.25 ms | 33.62 |
| 5 | piko → rpa → piko-excel → parabank | 29.60 ms | 28.72 ms | 31.23 ms | 33.78 |

## Browser workflows

Time summaries describe successful attempts. All attempted runs, including failures, remain in the raw data and success denominators.

| Workflow | Passed | Mean ± run SD | Median | p95 | 95% t interval for mean across rounds |
|---|---:|---:|---:|---:|---:|
| piko | 50/50 | 3.53 ± 0.33 s | 3.49 s | 3.62 s | 3.40–3.65 s |
| rpa | 50/50 | 4.69 ± 0.47 s | 4.56 s | 5.56 s | 4.51–4.87 s |
| piko-excel | 50/50 | 9.07 ± 1.18 s | 9.11 s | 10.94 s | 8.44–9.69 s |
| parabank | 50/50 | 3.84 ± 0.32 s | 3.83 s | 4.38 s | 3.71–3.97 s |

## Interpretation and limits

- Raw samples are pooled before calculating median and p95; round percentiles are not averaged. Throughput divides total requests by total measured wall time.
- Latency includes HTTP and JSON overhead. Browser timings include network access, page loads and execution. Field caches reset per attempt; cache hits remain part of workflow timings.
- Confidence intervals use the five round means, not 2,500 independent-request assumptions. Formula: mean ± t(0.975, df=4) × round SD / sqrt(5). These are approximate intervals conditional on roughly independent, approximately normal round means; five rounds cannot establish those assumptions strongly. Shared host state, temperature and network conditions can induce dependence.
- Median and p95 are descriptive pooled estimates. Their confidence intervals are not estimated. Run SD describes spread and is not a confidence interval.
- Repeated synthetic fixtures measure repeatability of these specific workflows. Passing every attempt does not establish 100% accuracy on unseen tasks or a general browser agent.
- This is single-host, sequential, warmed-service testing. It does not measure cold starts, concurrent capacity, cross-machine reproducibility, or production reliability.
- Default Chromium fonts failed to render locally. The saved fontconfig.xml is scoped to benchmark processes and uses installed DejaVu fonts. Browser workflow source code is unchanged.
- Source hashes and environment details are in protocol.json. Each round contains raw latencies, browser runs, logs and telemetry. No measured outliers are removed.
- The LinkedIn footage is from earlier recordings; only the benchmark overlays are regenerated from this experiment.

Method reference: [NIST confidence limits for the mean](https://www.itl.nist.gov/div898/handbook/eda/section3/eda352.htm).

## Reproduce

```bash
GLINER_DEVICE=cuda ./start-gliner.sh
.venv/bin/python benchmarks/run_repeatability.py
```
