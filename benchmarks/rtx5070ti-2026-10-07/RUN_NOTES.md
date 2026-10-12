# RTX 5070 Ti run notes

This run uses the original benchmark harness and browser workflow code.
GLiNER was started with `GLINER_DEVICE=cuda`; `/health` confirmed `cuda`.
PyTorch reported NVIDIA GeForce RTX 5070 Ti, version 2.14.1+cu130, CUDA 13.0.
The local Hugging Face cache resolves the model to revision `5a7adf72a23b4d311abae6ce050d7f0012bb3416`.

The local default font configuration caused Chromium to render text with zero height, including a minimal local HTML page. The supplied `fontconfig.xml` restricts font selection to installed DejaVu fonts and restores text rendering. It was applied only to the benchmark process using `FONTCONFIG_FILE`.

Reproduce from the repository root:

```bash
GLINER_DEVICE=cuda ./start-gliner.sh
FONTCONFIG_FILE="$PWD/benchmarks/rtx5070ti-2026-10-07/fontconfig.xml" .venv/bin/python benchmark.py --iterations 10 --micro-requests 500 --demos rpa parabank piko piko-excel --output-dir benchmarks/rtx5070ti-2026-10-07
```

Earlier exploratory attempts were interrupted after the font rendering failure. They do not contribute to this result set. Model latency uses five warmup calls and 500 sequential HTTP requests. Browser timings include network access and execution, and field caches reset for each workflow run.

LinkedIn media retains excerpts from earlier demo recordings; the overlays use this new benchmark, measured separately on the RTX 5070 Ti.
