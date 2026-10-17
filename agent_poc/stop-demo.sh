#!/usr/bin/env bash
# Stop the demo console. With --all, stop the d1 service too.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .demo.pid ]; then
  PID=$(cat .demo.pid)
  if kill -0 "$PID" 2>/dev/null; then
    pkill -TERM -P "$PID" 2>/dev/null || true  # uvicorn runs as a child of uv
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
  fi
  rm -f .demo.pid
  echo "Demo console stopped."
else
  echo "The demo console is not running (no .demo.pid)."
fi
if [ "${1:-}" = "--all" ]; then
  ../stop-d1.sh
fi
