#!/usr/bin/env bash
# Start the demo console on http://127.0.0.1:8040 (d1 too, if it isn't running). Stop with ./stop-demo.sh.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .demo.pid ] && kill -0 "$(cat .demo.pid)" 2>/dev/null; then
  echo "The demo console is already running (PID $(cat .demo.pid)) on http://127.0.0.1:${PORT:-8040}."
  exit 0
fi
curl -fsS http://127.0.0.1:8020/health >/dev/null 2>&1 || (cd .. && ./start-d1.sh)
# The server warms d1 with real router questions on startup (graph.warm_up).
export HF_TOKEN="${HF_TOKEN:-$(hf auth token 2>/dev/null | tail -1)}"
echo $$ > .demo.pid  # exec keeps this PID
exec uv run --env-file .env uvicorn server:app --host 127.0.0.1 --port "${PORT:-8040}"
