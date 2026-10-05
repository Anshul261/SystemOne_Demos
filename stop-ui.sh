#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.ui.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "GLiNER Browser Lab is not running."
  exit 0
fi

read -r PID <"$PID_FILE"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 20); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
  done
fi
rm -f "$PID_FILE"
echo "GLiNER Browser Lab stopped."
