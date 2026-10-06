#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.gliner.pid"
LOG_FILE="$SCRIPT_DIR/gliner.log"
PORT="${GLINER_PORT:-8010}"

if [ -f "$PID_FILE" ]; then
  read -r EXISTING_PID <"$PID_FILE"
  if kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "GLiNER is already running with PID $EXISTING_PID."
    exit 0
  fi
fi

if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "ERROR: A service is already listening on port $PORT."
  exit 1
fi

cd "$SCRIPT_DIR"
if command -v setsid >/dev/null 2>&1; then
  nohup setsid ./run-gliner.sh >"$LOG_FILE" 2>&1 </dev/null &
else
  nohup ./run-gliner.sh >"$LOG_FILE" 2>&1 </dev/null &
fi
PID=$!
echo "$PID" >"$PID_FILE"

# First launch can download packages and model weights; inspect gliner.log for progress.
for _ in $(seq 1 600); do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "ERROR: GLiNER exited during startup."
    tail -n 50 "$LOG_FILE"
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "GLiNER is ready at http://127.0.0.1:$PORT. Logs: $LOG_FILE"
    exit 0
  fi
  sleep 1
done

echo "ERROR: GLiNER is still starting after 600 seconds. Check $LOG_FILE or run ./stop-gliner.sh."
exit 1
