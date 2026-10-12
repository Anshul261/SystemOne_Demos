#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.extract.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "Extraction service is not managed by start-extract.sh."
  exit 0
fi

read -r PID <"$PID_FILE"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 40); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: Extraction service has not stopped yet; PID file retained."
    exit 1
  fi
fi
rm -f "$PID_FILE"
echo "Extraction service stopped."
