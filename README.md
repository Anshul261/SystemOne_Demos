# GLiNER browser demos

Three small browser automations that treat forms as classification problems.
They use the local `fastino/GLiNER2.5-Decide` service to select source fields,
business actions, and recovery actions from explicit allowlists. Playwright
executes the selected action. GLiNER never generates values or selectors.

## Demos

| Command | Site | Capability |
|---|---|---|
| `python run.py rpa` | RPA Challenge | Maps shuffled form fields to source columns and enters ten policyholder-style records. |
| `python run.py parabank` | ParaBank | Classifies an insurance claim action, creates a synthetic test customer, and issues a sandbox claim payment. |
| `python run.py piko` | Piko Healing Agent | Maps a three-step insured form and classifies recovery from randomized IDs and a verification modal. |

All input data is synthetic. ParaBank is a public testing application, not a
real bank. The runner restricts navigation to the three named demo workflows.

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
```

Omit `--headed` for headless execution. Run all demos with:

```bash
uv run python run.py all
```

Set `GLINER_URL` when the classifier is not listening at
`http://127.0.0.1:8010`.

Every model decision is an independent request. Successful and failed choices
are written as JSON Lines under `artifacts/`. A wrong or low-confidence class
stops the workflow instead of silently falling back to hard-coded field maps.

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
