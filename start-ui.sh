#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.ui.pid"
LOG_FILE="$SCRIPT_DIR/gradio.log"
PORT="${DEMO_UI_PORT:-7863}"

if [ -f "$PID_FILE" ]; then
  read -r EXISTING_PID <"$PID_FILE"
  if kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "GLiNER Browser Lab is already running with PID $EXISTING_PID."
    exit 0
  fi
fi

cd "$SCRIPT_DIR"
nohup ./run-ui.sh >"$LOG_FILE" 2>&1 &
echo "$!" >"$PID_FILE"

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    echo "GLiNER Browser Lab is ready at http://127.0.0.1:$PORT."
    exit 0
  fi
  read -r UI_PID <"$PID_FILE"
  if ! kill -0 "$UI_PID" 2>/dev/null; then
    echo "ERROR: UI exited during startup."
    tail -n 100 "$LOG_FILE"
    exit 1
  fi
  sleep 1
done

echo "ERROR: UI did not become ready within 60 seconds."
exit 1
