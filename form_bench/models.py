"""The two models and how they are called: System One (Jev, or d1 on the local service) and System Two (an LLM).

System One is TypeSafe's Jev when TYPESAFE_API_KEY is set, and otherwise d1 on the local service: the request
shape is the same. Every call goes through jev-ultrafast's post_json (its retries and errors), metered here, so
the bench can count what each agent spent.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

from jev_ultrafast import model

TYPESAFE = "https://api.typesafe.ai/v1/systemone"
SYSTEM_ONE = "jev" if os.getenv("TYPESAFE_API_KEY") else "d1"
D1_URL = os.getenv("D1_URL", "http://127.0.0.1:8020")
D1_MODEL = os.getenv("D1_MODEL", "3b")
if SYSTEM_ONE == "d1":
    os.environ["TYPESAFE_API_KEY"] = "local-d1"  # jev-ultrafast reads it; the local service ignores it
LLM_URL = os.getenv("TEXT_MODEL_BASE_URL", "https://api.cerebras.ai/v1").rstrip("/")
LLM_MODEL = os.getenv("TEXT_MODEL", "qwen-3.8-27b")
os.environ.setdefault("TEXT_MODEL_BASE_URL", LLM_URL)
os.environ.setdefault("TEXT_MODEL", LLM_MODEL)
LLM_REASONING = os.getenv("LLM_REASONING", "low")  # "low" beat "none"; medium and high did no better
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
MAX_QUESTIONS = int(os.getenv("MAX_QUESTIONS", "16"))  # per System One request; smaller requests run in parallel


@dataclass
class Meter:
    started: float = field(default_factory=time.perf_counter)
    system_one_calls: int = 0
    system_one_ms: float = 0
    system_one_tokens: int = 0
    llm_calls: int = 0
    llm_ms: float = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cached_tokens: int = 0
    actions: int = 0
    flagged: list = field(default_factory=list)
    error: str | None = None

    def report(self) -> dict:
        return {k: v for k, v in vars(self).items() if k != "started"} | {
            "wall_ms": round((time.perf_counter() - self.started) * 1000)}


meter: Meter | None = None
_post_json = model.post_json


def post_json(url: str, key: str, body: dict) -> dict:
    """jev-ultrafast's HTTP call: routes System One to local d1 if needed, retries a dropped connection once,
    and meters the call. jev-ultrafast's own calls come through here too."""
    if url == TYPESAFE and SYSTEM_ONE == "d1":
        url, body = f"{D1_URL}/system_one", {**body, "model": D1_MODEL}
    if "api.cerebras.ai" in url:  # Cerebras rejects jev-ultrafast's reasoning field; every LLM call shares settings
        body = {k: v for k, v in body.items() if k != "reasoning"} | {"reasoning_effort": LLM_REASONING,
                                                                     "temperature": LLM_TEMPERATURE}
    started = time.perf_counter()
    try:
        result = _post_json(url, key, body)
    except RuntimeError as error:  # one retry for a dropped connection, never for a refused request
        if "connection failed" not in str(error):
            raise
        result = _post_json(url, key, body)
    elapsed = (time.perf_counter() - started) * 1000
    if meter and url.endswith("/chat/completions"):
        usage = result.get("usage", {})
        meter.llm_calls += 1
        meter.llm_ms += elapsed
        meter.llm_input_tokens += usage.get("prompt_tokens", 0)
        meter.llm_output_tokens += usage.get("completion_tokens", 0)
        meter.llm_cached_tokens += (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    elif meter:
        meter.system_one_calls += 1
        meter.system_one_ms += elapsed
        meter.system_one_tokens += result.get("usage", {}).get("input_tokens", 0)
    return result


model.post_json = post_json


@contextmanager
def metered():
    global meter
    meter = Meter()
    try:
        yield meter
    finally:
        meter = None


def system_one(note: str, questions: dict) -> dict:
    body = {"model": os.getenv("TYPESAFE_MODEL", "jev-latest"), "state": {"note": note}, "questions": questions}
    return post_json(TYPESAFE, os.environ["TYPESAFE_API_KEY"], body)["answers"]


def ask_all(note: str, questions: dict) -> dict:
    """Many System One questions, sent in parallel requests of at most MAX_QUESTIONS."""
    keys = list(questions)
    chunks = [{k: questions[k] for k in keys[n:n + MAX_QUESTIONS]} for n in range(0, len(keys), MAX_QUESTIONS)]
    with ThreadPoolExecutor(max(len(chunks), 1)) as pool:
        return {k: v for answers in pool.map(lambda chunk: system_one(note, chunk), chunks) for k, v in answers.items()}


def llm_json(system: str, user: dict) -> dict:
    body = {"model": LLM_MODEL, "max_tokens": 8192, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user)}]}
    result = post_json(f"{LLM_URL}/chat/completions", os.environ["TEXT_MODEL_API_KEY"], body)
    return json.loads(result["choices"][0]["message"]["content"])
