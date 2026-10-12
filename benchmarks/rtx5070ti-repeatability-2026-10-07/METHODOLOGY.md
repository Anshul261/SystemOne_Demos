# Measurement definitions

The experiment asks how repeatable latency and four existing browser workflows are on this workstation. It does not estimate general model accuracy.

- Five fresh measurement rounds use one continuously running GLiNER service on CUDA. The service is not restarted between rounds.
- Each round excludes 50 warmup calls, then records 500 sequential field-mapping requests using the existing harness. The microbenchmark rotates through the nine source-field labels in `PIKO_CHALLENGE_DESCRIPTIONS`; it does not sample 2,500 different natural-language tasks.
- Each round runs ten attempts per browser workflow, with new browser contexts and fresh field caches per attempt. Workflow order rotates across rounds; the first four rounds put every workflow in each position once, and the fifth repeats the first order. This reduces fixed-order confounding but does not eliminate shared-state effects.
- Raw latency includes local HTTP and JSON overhead. It is not a GPU kernel timer. Throughput is sequential requests divided by their total measured wall time, not maximum concurrent capacity.
- End-to-end browser time includes network and page loads, rendering, downloads, model calls, browser execution and verification. Time distributions are reported for successful attempts, and all failures remain in the attempt denominator.
- Pooled latency quantiles are calculated from concatenated samples using linear interpolation, equivalent to a position of `(n-1)*q`. They are not averages of round quantiles.
- For mean confidence intervals, one observation is a round mean. The interval is the unweighted mean of five round means plus or minus `2.7764451051977987 * sample_SD / sqrt(5)`. The critical value is the 97.5th percentile of Student t with four degrees of freedom. With equal counts this center also equals the pooled mean; unequal successful counts could make them differ.
- These intervals describe approximate uncertainty in the mean conditional on independent, roughly normal round means. The rounds share a host, loaded model, temperature history and external websites, so dependence and drift remain possible. Five rounds are insufficient to demonstrate those assumptions strongly. No confidence interval is claimed for the median or p95.
- No outlier trimming, selective reruns, or failed-attempt deletion is used. The earlier ten-run pilot is kept separately and excluded from this experiment.
- Synthetic fixture repeats measure within-workflow repeatability. Even a perfect observed success count cannot establish 100% generalization accuracy or production reliability.

Formula reference: [NIST confidence limits for the mean](https://www.itl.nist.gov/div898/handbook/eda/section3/eda352.htm).
