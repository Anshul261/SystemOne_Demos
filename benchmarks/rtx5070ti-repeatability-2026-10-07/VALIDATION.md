# Independent validation

Share with caveats. Raw counts, round files, source hashes, pooled mean/quantiles, weighted throughput, success denominators and workflow confidence intervals reconcile. Student t critical value was independently solved from the analytic df=4 CDF.

## Within-round latency diagnostics

| Round | Lag-1 correlation | First-half mean | Second-half mean | Difference |
|---:|---:|---:|---:|---:|
| 1 | 0.051 | 29.19 ms | 29.19 ms | +0.00 ms |
| 2 | 0.080 | 29.49 ms | 30.43 ms | +0.94 ms |
| 3 | 0.047 | 29.49 ms | 29.18 ms | -0.32 ms |
| 4 | 0.049 | 29.40 ms | 30.09 ms | +0.68 ms |
| 5 | 0.068 | 29.75 ms | 29.45 ms | -0.30 ms |

These are descriptive checks, not a stationarity or independence test. Round means share one warmed service and one workstation; approximate mean confidence intervals should not be read as general-task accuracy or cross-machine reliability.

Media and post should use pooled median/p95, approximate mean confidence intervals, all attempted run counts, and explicit repeated-fixture scope. Footage and measurements come from separate runs.
