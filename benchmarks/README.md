# Benchmarks

The publication-oriented result is [`publish-10x/RESULTS.md`](publish-10x/RESULTS.md).
It contains ten sequential runs of each browser workflow and 500 warmed GLiNER
requests. The exact harness commit and command are recorded in the report.

Files in `publish-10x/`:

- `RESULTS.md`: readable methodology and summary
- `results.json`: environment, 500 raw latency samples, all run records, and aggregates
- `runs.csv`: one row for each of the 40 browser runs
- `SHA256SUMS`: hashes for the three result files

Verify the published files with:

```bash
cd benchmarks/publish-10x
sha256sum -c SHA256SUMS
```

The files directly under `benchmarks/` are the earlier three-run exploratory
measurement and are retained for comparison.
