FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers \
    DEMO_UI_HOST=0.0.0.0 \
    DEMO_UI_PORT=7860 \
    D1_URL=http://127.0.0.1:8020 \
    EXTRACT_URL=http://127.0.0.1:8030 \
    HF_HOME=/home/user/.cache/huggingface

RUN pip install --no-cache-dir uv==0.8.12
RUN useradd --create-home --uid 1000 user
WORKDIR /home/user/app

COPY --chown=user:user pyproject.toml uv.lock ./
COPY --chown=user:user services/d1/pyproject.toml services/d1/uv.lock ./services/d1/
COPY --chown=user:user services/extract/pyproject.toml services/extract/uv.lock ./services/extract/

# The UI, d1 and extraction services each pin their own transformers / huggingface-hub versions.
USER user
RUN uv sync --frozen --no-dev
RUN uv sync --project services/d1 --frozen --no-dev
RUN uv sync --project services/extract --frozen --no-dev

USER root
RUN .venv/bin/playwright install --with-deps chromium
USER user

COPY --chown=user:user . .
EXPOSE 7860
CMD [".venv/bin/python", "space.py"]
