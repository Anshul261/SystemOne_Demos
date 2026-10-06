FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers \
    DEMO_UI_HOST=0.0.0.0 \
    DEMO_UI_PORT=7860 \
    GLINER_PORT=8010 \
    GLINER_URL=http://127.0.0.1:8010 \
    HF_HOME=/home/user/.cache/huggingface

RUN pip install --no-cache-dir uv==0.8.12
RUN useradd --create-home --uid 1000 user
WORKDIR /home/user/app

COPY --chown=user:user pyproject.toml uv.lock ./
COPY --chown=user:user services/gliner/pyproject.toml services/gliner/uv.lock ./services/gliner/

# GLiNER and Gradio require different versions of huggingface-hub.
USER user
RUN uv sync --frozen --no-dev
RUN uv sync --project services/gliner --frozen --no-dev

USER root
RUN .venv/bin/playwright install --with-deps chromium
USER user

COPY --chown=user:user . .
EXPOSE 7860
CMD [".venv/bin/python", "space.py"]
