#!/usr/bin/env bash
# Start the webhook server detached, so it outlives the terminal or agent
# session that launched it. Logs to frames/server.log (gitignored).
#
#   ./run-server.sh          start
#   ./run-server.sh stop     stop it and its ffmpeg recorders
#   ./run-server.sh status   is it up, and what do the buffers look like
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
mkdir -p frames

case "${1:-start}" in
  start)
    if curl -sf --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1; then
      echo "already running"; exit 0
    fi
    nohup uv run server.py >> frames/server.log 2>&1 < /dev/null &
    disown || true
    for _ in $(seq 1 40); do
      curl -sf --max-time 1 http://127.0.0.1:8080/health >/dev/null 2>&1 && break
      sleep 0.5
    done
    echo "started; log: frames/server.log"
    ;;
  stop)
    pkill -f '\.venv/bin/python3 server\.py' 2>/dev/null || true
    pkill -f 'rtsp_transport tcp -i rtsp' 2>/dev/null || true
    echo "stopped"
    ;;
  status)
    echo "health:"
    curl -s --max-time 5 http://127.0.0.1:8080/health || echo "not responding"
    echo
    echo "config:"
    curl -s --max-time 5 http://127.0.0.1:8080/config || echo "(no /config endpoint)"
    echo
    echo "events:"
    curl -s --max-time 5 http://127.0.0.1:8080/events || echo "(no /events endpoint)"
    ;;
  *) echo "usage: $0 [start|stop|status]" >&2; exit 2 ;;
esac
