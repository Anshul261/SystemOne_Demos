# GLiNER versus JPT on request routing

Measured on 5 October 2026, NVIDIA GB10. GLiNER was served from the local
quantized `fastino/GLiNER2.5-Decide` endpoint, revision
`5a7adf72a23b4d311abae6ce050d7f0012bb3416`. JPT was
`kirp/jpt-0.8b` through `llm2jev 0.6.1` with the Transformers GPU backend and
temperature 1.140. Calls were sequential, so the two models did not compete
for GPU time during a run.

## Increasing the choices

One IT-access request with `it_access` as the correct route. The six help-desk
queues stayed in every option set; additional department/request pairs brought
the total to 8, 32, 128, and 255. Three calls per size and model after serving
startup. Mean client-observed time includes HTTP.

The [option-sweep CSV](comparison-2026-10-05/option-sweep.csv) and the
[GLiNER](comparison-2026-10-05/gliner/) and
[JPT](comparison-2026-10-05/jpt/) benchmark directories contain the measured
runs. Each benchmark directory includes the raw latency samples, browser-run
CSV, generated report, and checksums.

| Options | GLiNER correct | GLiNER mean | JPT correct | JPT mean |
|---:|---:|---:|---:|---:|
| 8 | 3/3 | 24.3 ms | 3/3 | 56.7 ms |
| 32 | 3/3 | 42.7 ms | 3/3 | 117.3 ms |
| 128 | 3/3 | 638.2 ms | 3/3 | 314.4 ms |
| 255 | 3/3 | 2325.6 ms | 3/3 | 707.5 ms |

JPT was 3.3 times faster at 255 options on this request. GLiNER also accepted
255 labels, so capacity alone was not the difference. The extra options are
synthetic but distinct staff routes, not a representative 255-class accuracy
benchmark. Repeat with real labelled cases before drawing accuracy conclusions.

## Browser workflows

Each run routed the same five clinic messages or four help-desk tickets using a
fresh browser. Three runs per model and workflow. These are p50 end-to-end
times; both models completed all runs. The p50 model-call count equals the
number of messages, since each request carries several decision heads.

| Workflow | GLiNER p50 | JPT p50 | Model calls |
|---|---:|---:|---:|
| Clinic | 0.724 s | 1.825 s | 5 |
| Help desk | 0.559 s | 0.813 s | 4 |

The two models picked the same main queue for each of the nine messages, but
sometimes chose different priorities or review flags. Clinic topics use one
multi-label head in GLiNER and five yes/no questions in JPT. The Transformers
backend scores those JPT questions serially; an SGLang or vLLM backend with
prefix caching may change these times. Reported probabilities have different
calibration and are not directly comparable.

JPT also completed the RPA Challenge, Piko Excel Challenge, and ParaBank
workflows on a single live run each. On Piko Resilient Intake, JPT selected
`go_back` at the final filled step instead of `submit`, so the workflow stopped;
GLiNER completed that run. These extra browser runs are checks, not repeated
timing measurements.

Reproduce the option sweep with
`uv run python compare_options.py --models gliner jpt --sizes 8 32 128 255 --iterations 3`.
For browser results, run `uv run python benchmark.py --model gliner --demos clinic helpdesk --iterations 3 --micro-requests 20`
and repeat with `--model jpt`. See the root README for service setup.
