# SystemOne Demos

Six browser automations that demonstrate SystemOne-style decisions by
treating forms and incoming requests as classification problems.
They use `fastino/GLiNER2.5-Decide` by default and can also use `kirp/jpt-0.8b`.
The models select source fields, business actions, and routing from explicit
allowlists. Playwright executes the choice; neither model generates values or selectors.

## Demos

| Command | Site | Capability |
|---|---|---|
| `python run.py rpa` | RPA Challenge | Maps shuffled form fields to source columns and enters ten policyholder-style records. |
| `python run.py parabank` | ParaBank | Classifies an insurance claim action, creates a synthetic test customer, and issues a sandbox claim payment. |
| `python run.py piko` | Piko Healing Agent | Maps a three-step insured form and classifies recovery from randomized IDs and a verification modal. |
| `python run.py piko-excel` | Piko Automation Challenge | Downloads the participant workbook, starts the timer, classifies shuffled fields, and submits ten records. |
| `python run.py clinic` | Local clinic intake board | Routes five synthetic patient messages by request type, urgency, human review, and topic tags. Urgent cases go to clinical review; no care is booked or prescribed. |
| `python run.py helpdesk` | Local employee intake board | Assigns four employee requests to benefits, IT, facilities, or expenses with priority and review status. |

All input data is synthetic. ParaBank is a public testing application, not a
real bank. The two new intake boards run locally and save routing decisions in
the browser for verification; they do not connect to a clinic or employer.

## Setup

Start the GLiNER service from the adjacent vLLM workspace:

```bash
cd ../vllm/dgx_spark/deployments/gliner2_5_decide
./04-start-server.sh
./05-test-server.sh
```

Install this project's dependencies and Chromium:

```bash
uv sync --extra test
uv run playwright install chromium
```

## Run

```bash
uv run python run.py rpa --headed
uv run python run.py parabank --headed
uv run python run.py piko --headed
uv run python run.py piko-excel --headed
uv run python run.py clinic --headed
uv run python run.py helpdesk --headed
```

Omit `--headed` for headless execution. Run all demos with:

```bash
uv run python run.py all
```

## Compare with JPT-0.8B

The [JPT-0.8B model card](https://huggingface.co/kirp/jpt-0.8b) describes a
different API: typed `choice`, `score`, and `noul` questions through
[`llm2jev`](https://github.com/tic-top/llm2jev). Serve it on a separate port
from GLiNER. For example, with vLLM and `llm2jev` installed in their own
environment:

```bash
vllm serve kirp/jpt-0.8b --max-logprobs 256 --return-tokens-as-token-ids --enable-scale-out --port 8001
llm2jev --model kirp/jpt-0.8b --backend vllm --url http://127.0.0.1:8001 --port 8085 --temperature 1.140
```

In this project's environment:

```bash
uv run python run.py clinic --model jpt --jpt-url http://127.0.0.1:8085
uv run python run.py helpdesk --model jpt --jpt-url http://127.0.0.1:8085
uv run python compare_options.py --models gliner jpt --sizes 8 32 128 255 --iterations 3
uv run python benchmark.py --model jpt --demos clinic helpdesk --iterations 3 --micro-requests 50
```

The UI has a model picker and reads `JPT_URL` if the decision service uses a
different port. `compare_options.py` uses the same IT-access request and correct
route at each size, adding department/request distractors up to 255 choices. It
reports accuracy and mean latency; server errors are shown for unsupported
sizes. JPT evaluates the clinic topic tags as separate yes/no questions, while
GLiNER uses a multi-label head. Their reported probabilities are not calibrated
against each other. Benchmarks save JPT results under `benchmarks/jpt/` by default;
choose separate `--output-dir` values to keep comparison runs.

Measured comparison on the local GB10: [clinic, help-desk, and 255-option
results](benchmarks/COMPARISON.md).

## Demo UI

Start the local visual control room:

```bash
./start-ui.sh
```

Open `http://127.0.0.1:7863`. The UI streams browser screenshots and shows each
model's field, business, and browser-action classifications with their confidence.
Runs are serialized so two browser sessions cannot alter the shared sandbox
sites at the same time. Override the port with `DEMO_UI_PORT`.

Stop it with `./stop-ui.sh`. Logs are written to `gradio.log`.

Set `GLINER_URL` when the classifier is not listening at
`http://127.0.0.1:8010`.

Each form-field decision uses its own request. The intake demos send one request
per message with several decision heads. Choices are written as JSON Lines under
`artifacts/`; an unknown or low-confidence class stops the workflow instead of
silently falling back to a hard-coded route. The intake demos use
caller-supplied label descriptions and, for the clinic, multi-label topic tags.
See the [GLiNER2.5-Decide model card](https://huggingface.co/fastino/GLiNER2.5-Decide)
for the request, ticket-routing, described-label, and multi-decision examples.

## Test

Unit tests do not need a running model:

```bash
uv run pytest
```

Run the model decision checks with:

```bash
RUN_LIVE_GLINER_TESTS=1 uv run pytest tests/test_live_decisions.py
```

The live browser demos are the end-to-end tests because the target sites are
third-party sandboxes and can change independently of this repository.

## Performance benchmarks

Run sequential model latency checks and repeat every browser workflow three
times:

```bash
uv run python benchmark.py --iterations 3 --micro-requests 50
```

The command writes `benchmarks/results.json` with every run and
`benchmarks/RESULTS.md` with a compact summary. Measurements include raw model
p50/p95 latency, throughput, end-to-end browser time, model calls, model time,
and field-classification cache hits. Results depend on the network and the
availability of the third-party sandbox sites.

For a publication-oriented run with raw latency samples, ten workflow
iterations, CSV output, environment provenance, and SHA-256 checksums:

```bash
uv run python benchmark.py \
  --iterations 10 \
  --micro-requests 500 \
  --output-dir benchmarks/publish-10x
```

Current publication report: [`benchmarks/publish-10x/RESULTS.md`](benchmarks/publish-10x/RESULTS.md).
