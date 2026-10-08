---
title: SystemOne Decision Lab
sdk: docker
app_port: 7860
colorFrom: green
colorTo: gray
fullWidth: true
startup_duration_timeout: 1h
models:
  - fastino/GLiNER2.5-Decide
---

# SystemOne Demos

Six demos that treat forms and incoming requests as classification problems.
Four automate websites. Clinic Requests and Employee Help Desk use interactive
message-to-ticket simulations in the UI.
They use `fastino/GLiNER2.5-Decide` by default and can also use `kirp/jpt-0.8b`.
The models select source fields, business actions, and routing from explicit
allowlists. Playwright executes browser choices; Python rules produce simulated
intake tickets. Neither model generates values or selectors.

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

The GLiNER service is bundled in `services/gliner` with its own uv environment
and lockfile. Start it from this repository:

```bash
./start-gliner.sh
curl -fsS http://127.0.0.1:8010/health
```

The first launch installs inference dependencies and downloads
`fastino/GLiNER2.5-Decide` to the Hugging Face cache. Existing cached weights
are reused. The service selects CUDA when available and otherwise uses CPU.
Weights and virtual environments are not committed. No JPT model is installed.
To download the weights ahead of time with the Hugging Face CLI:

```bash
hf download fastino/GLiNER2.5-Decide
```

Logs are written to `gliner.log`. Stop with `./stop-gliner.sh`, or run in the
foreground with `./run-gliner.sh`. Set `GLINER_DEVICE=cpu` to force CPU,
`GLINER_MODEL` to select another compatible checkpoint, or `GLINER_PORT` to
change the port (also set `GLINER_URL` for the demo client).

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

Open `http://127.0.0.1:7863`. The UI uses Gradio 6.29.1 `gr.Server` with a custom
frontend and the Gradio JavaScript client for queued, streamed results.

For Clinic Requests and Employee Help Desk, choose an example or write a request.
Send it to watch the message, model predictions, routing rules, and final ticket
appear in order. Play the sample inbox to process all five clinic messages or
all four employee messages. Select a ledger entry to inspect its results or
download the run as JSON. Guided playback adds pauses for reading; model time
measures the classification call itself. Ticket delivery and acknowledgments
are simulated, and acknowledgment text comes from a template.

The four website demos stream browser screenshots and execution events. Runs
share one queue so browser sessions cannot alter the sandbox sites at the same
time. Override the port with `DEMO_UI_PORT`.

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

## Document stamp check (d1 vision)

The **Document Stamp Check** workflow in the UI verifies that an insurance
certificate carries its required official stamp: round, blue ink, issued by
Springfield Mutual Insurance, reading APPROVED and the issue date, pressed in
ink inside the "Official stamp" box. It uses LiquidAI's
[d1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M) or
[d1-3B](https://huggingface.co/LiquidAI/d1-3B) decision models on the image.
Neither model generates text. Each answers narrow visual questions about the
stamp region in one forward pass, and `stamp_check.py` turns those answers into
accept, reject with a reason, or human review.

- Questions and thresholds were chosen on a separate calibration split and
  frozen in `fixtures/stamp_calibration.json`.
- A check is trusted only when its calibration AUROC is at least 0.95. The model
  sends untrusted checks to a person instead of deciding them.
- Answers within 0.75 log-odds of their threshold also go to review.
- Photos that show a desk around the page are cropped to the paper first.

Start the model service (it loads both models, about 7.5 GB of GPU memory),
then the UI:

```bash
./start-d1.sh
curl -fsS http://127.0.0.1:8020/health
./start-ui.sh
```

Pick a sample, play all 22 samples, or upload your own scan or photo. You can
print `fixtures/stamps/certificate_template.png`, stamp it, and photograph it.
Set `D1_URL` if the service runs elsewhere and `D1_MODELS=omni` to load only
the small model.

Measured on an RTX 5070 Ti, using 110 held-out test certificates (11 classes:
valid plus 10 defect types) captured as clean renders, flatbed scans, and phone
photos:

| | d1-omni-600M | d1-3B |
|---|---:|---:|
| Correct verdict and reason | 29/110 | 100/110 |
| Bad stamps accepted | 0/100 | 8/100 (all misspelled insurer names) |
| Valid stamps rejected | 0/10 | 0/10 |
| Sent to human review | 75/110 | 4/110 |
| Median model time per document (live samples) | 21 ms | 72 ms |

d1-omni-600M is trusted only for "stamp in the box" and "centre word"; it cannot
tell ink colours apart (it called a red ring blue at 0.94) and cannot read the
issuer, so it routes most documents to review. d1-3B separates every visual
check perfectly on the test split (AUROC 1.00) except spelling: it reads
"SPRINGFEILD" as "SPRINGFIELD", so a misspelled issuer is a known blind spot.
The real-ink check is the most sensitive to capture quality. Faint stamps on
re-compressed or blurred photos land near its threshold and go to review.

All certificates are synthetic. To regenerate the data, calibration, and
evaluation, run the scripts in `experiments/d1/` (`make_stamps.py`,
`calibrate.py`, `evaluate_flow.py`, `export_samples.py`) in that directory's
own environment.

## Long-span extraction on real PDFs

The **Long-Span Extraction** workflow runs Fastino's GLiNER2.5 boundary models
([multi-v1](https://huggingface.co/fastino/gliner2.5-multi-v1), 287M, and
[base-v1](https://huggingface.co/fastino/gliner2.5-base-v1), 194M) over real PDF pages.
Each span is drawn on the lines it covers, so a 60-word clause is highlighted line by line.

- **Text and boxes:** `services/extract/document.py` reads every character and its box with
  pdfium. It orders lines by layout (recursive XY-cut), joins words hyphenated across lines,
  and maps each span back to one rectangle per text line.
- **Reading order:** layout order matters on real files. Flattened form values are written at
  the end of the content stream, and two-column pages need column order.
- **Splitting:** by default the page is split where a numbered item or section starts, so a
  span cannot run into the next item. On the flood policy's definitions page, whole-page
  extraction returned a 177-word "definition" that covered five of them; with the split, no
  span crosses an item boundary.
- **The 8-word cap:** the toggle marks where a fixed-width span head (8 words) would have
  stopped each long span.
- **Labels and uploads:** you can edit the labels or upload your own text-layer PDF. Label
  examples in the bundled schemas are paraphrased and never copied from the document.

Samples (in `fixtures/documents/`):

| Document | Source |
|---|---|
| FEMA Standard Flood Insurance Policy, Dwelling Form (16 pages) | 44 CFR 61 App. A(1), 2024, from [GovInfo](https://www.govinfo.gov/content/pkg/CFR-2024-title44-vol1/pdf/CFR-2024-title44-vol1-part61-appA1.pdf) |
| Medicare Advance Beneficiary Notice, English and Spanish | CMS's official fillable CMS-R-131, filled with synthetic data by `experiments/d1/make_abn.py` |
| Scanned haematology report (1 photographed page) | A real hospital report shared as a sample, with patient details redacted; read with OCR |

Start the service (it loads both models, about 2 GB of GPU memory) and the UI:

```bash
./start-extract.sh
./start-ui.sh
```

Measured on an RTX 5070 Ti, with the default schema and numbered-item splitting:

| | Whole policy (16 pages) |
|---|---:|
| Spans | 569 |
| Spans longer than 8 words | 34 |
| Model time | 4.0 s (250 ms per page; about 110 ms without splitting) |

**Scans and photos:** upload a JPEG, PNG, or WebP page and the service reads it with
[docTR](https://github.com/mindee/doctr) OCR on the GPU (about 300 ms per page once warm). It
reads scanned tables row by row and writes " | " between cells that share a row, so a test
name, its result, and its range stay separate. On one real haematology report, GLiNER2.5
Base found 10 test names, 13 values, and 14 of 15 reference ranges. Multi did far worse on
that English OCR text. Each document can name its own default model.

**Documents that stay on your machine:** list them in `fixtures/documents/local/samples.json`,
with their files in the same folder. The folder is git-ignored. The UI shows those documents
only when their files are present.

Known limits:

- A scanned PDF is read from its text layer; upload the page as an image to use OCR.
- OCR mistakes reach the extractor: "fL" can be read as "L", and "27.0" as "27,0".
- Long sentences inside table cells or checkbox options, such as the ABN's three options,
  come back as short spans.
- On the Spanish ABN, English labels find the tests, costs, and phone numbers, but fewer
  fields than on the English form.

The service pins `gliner2` to a GitHub commit: release 2.0.0 cannot load these
checkpoints' tokenizers with `transformers` 4.57.

## Lab report reader (OCR + d1)

The **Lab Report Reader** workflow turns a photographed lab report into checked results: each test
with its value, unit, reference range, and a low/high flag. The models only make judgements;
plain code handles what has one right answer (`lab_report.py`):

1. **OCR** (docTR, about 0.5 s) finds each cell on the page and its box.
2. **d1 picks the columns.** It decides which title names the test, result, unit, and range columns,
   so any wording works ("TEST DESCRIPTION", "Investigation", "Biological Ref. Interval"). Each
   cell then joins the column under the nearest title.
3. **d1 reads the test column.** It decides whether each line is a test, a method note, or a
   section heading. It sees the cell's crop as well as its text, so bold underlined headings count.
4. **Code pairs the cells.** Each test pairs with the number on its line. Units and ranges are
   lined up with the tests in order (an order-preserving alignment with skips), because many
   reports print them a line below their test. Pairing by row would give the next test's range.
   Ranges are read by pattern, including ranges where OCR dropped the dash or read it as a "1".
5. **d1 normalises units.** It maps each printed unit to a standard one, OCR damage included
   ("L" next to MCV becomes fL).
6. **Flags and the lab's marks.** Code flags values outside their range. d1 vision checks whether
   the lab printed the value in a shaded box, and printed markers (`*`, `H`, `L`) come from the
   text. The demo compares our flags with the lab's own marks.

Start `./start-extract.sh` (OCR) and `./start-d1.sh`. The d1 service now has a
`/system_one_batch` endpoint, so each step is one batched call.

Results with d1-3B on an RTX 5070 Ti (`experiments/d1/eval_lab.py`):

| Document | Tests found | Values | Units | Ranges | Flags | Lab's boxes |
|---|---:|---:|---:|---:|---:|---:|
| Real haematology report (the one the reader was built on) | 15/15 | 15/15 | 15/15 | 15/15 | 15/15 | 15/15 |
| Synthetic batch 3: 6 reports, scored once after development | 62/62 | 62/62 | 62/62 | 62/62 | 62/62 | 62/62 |

On the real report all three written findings are read too. The flags match the three values
the lab boxed (PCV low, both ESR values high), and nothing else is flagged.

How the evaluation was run:

- The synthetic reports (`experiments/d1/make_lab_reports.py`) use three other labs' layouts,
  header wordings, panels, and units. They are scanned or photographed with the stamp set's
  capture model.
- Batches 1 and 2 informed the fixes. Batch 3 was generated afterwards and scored once.
- All three batches share the same three page designs, so batch 3 shows robustness to new
  values and captures, not to new layouts.

d1-omni-600M runs the same reader about 2.5 times faster. On the real report it gets the
values, ranges, and flags right, but only 10 of 15 units, no findings, and 8 spurious records.
On the synthetic layouts it finds about half the tests, so d1-3B is the default.

Known limits:

- d1 vision cannot tell bold from regular type (3 of 20 bold values found). A lab that marks
  abnormal values only in bold shows no marks to compare.
- OCR errors in test names stay as printed ("ESR I Hr." for ESR II Hr).
- d1-3B takes about 3 to 8 s per page, mostly because it reads each test-column line with its image.

## Host all six demos on Hugging Face Spaces

Create a **Docker Space** and push this repository's files to it. The root
`README.md` configures port 7860, and `Dockerfile` installs Chromium and both
Python environments. `space.py` starts the bundled GLiNER service, waits for
its health check, and starts the UI. The model downloads on the first start.
The service uses CUDA when available and otherwise CPU.

The UI and model service have separate environments because Gradio 6.29.1 needs
`huggingface-hub>=1.16`, while GLiNER's Transformers dependency requires a version
below 1.0. The model service stays on loopback port 8010; only the UI port is
exposed. The four browser demos need outbound access to their sandbox sites.

Test the container locally:

```bash
docker build -t systemone-demos .
docker run --rm -p 7860:7860 systemone-demos
```

To upload only tracked repository files to an existing Space:

```bash
hf auth login
mkdir -p /tmp/opencode/systemone-space
git archive HEAD | tar -x -C /tmp/opencode/systemone-space
hf upload YOUR_USERNAME/YOUR_SPACE /tmp/opencode/systemone-space --type space
```

JPT appears in the Space's model picker only when `JPT_URL` is configured in its
settings. Its server must be reachable from the Space. GLiNER is bundled.
Local audit files are ephemeral on Spaces; download a run's JSON to keep it.

The implementation follows Gradio's [server-mode guide](https://www.gradio.app/guides/server-mode)
and Hugging Face's [Docker Spaces guide](https://huggingface.co/docs/hub/spaces-sdks-docker).

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
