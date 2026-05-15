#!/bin/bash
# Compass local server control for macOS/Linux — the cross-platform
# counterpart to compass_tray.py (Windows). Manages a detached uvicorn
# process by PID file + listening port so you can start / stop / restart
# the server without a terminal session staying open.
#
# Usage:  ./compass-ctl.sh [start|stop|restart|status]
# Or just double-click the "Compass *.command" files in Finder.

ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT=8000
PY="$ROOT/.venv/bin/python"
PIDFILE="$ROOT/.compass.pid"
LOG="$ROOT/compass.log"
URL="http://localhost:$PORT"

cd "$ROOT"

# PIDs currently LISTENing on $PORT (works even for a server we didn't start).
listeners() { lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true; }

is_up() { [ -n "$(listeners)" ]; }

stop_server() {
    local pids
    pids="$(listeners)"
    if [ -z "$pids" ] && [ -f "$PIDFILE" ]; then
        pids="$(cat "$PIDFILE" 2>/dev/null || true)"
    fi
    if [ -z "$pids" ]; then
        echo "• Compass is not running."
        rm -f "$PIDFILE"
        return 0
    fi
    echo "• Stopping Compass (PID: $pids)…"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        is_up || break
        sleep 0.3
    done
    if is_up; then
        echo "  …didn't stop gracefully, forcing."
        # shellcheck disable=SC2086
        kill -9 $(listeners) 2>/dev/null || true
        sleep 0.5
    fi
    rm -f "$PIDFILE"
    is_up && echo "✗ Failed to stop." || echo "✓ Stopped."
}

start_server() {
    if is_up; then
        echo "• Compass is already running at $URL (PID: $(listeners))."
        return 0
    fi
    if [ ! -x "$PY" ]; then
        echo "✗ Can't find the venv Python at $PY"
        echo "  (expected a .venv in the project — nothing else to install)."
        return 1
    fi
    echo "• Starting Compass…"
    nohup "$PY" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" \
        --no-access-log >> "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    disown 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        is_up && break
        sleep 0.4
    done
    if is_up; then
        echo "✓ Compass is up at $URL"
    else
        echo "✗ Server didn't come up — last log lines:"
        tail -n 15 "$LOG" 2>/dev/null
        return 1
    fi
}

case "${1:-status}" in
    start)   start_server ;;
    stop)    stop_server ;;
    restart) stop_server; sleep 0.5; start_server ;;
    status)
        if is_up; then
            echo "✓ Compass is RUNNING at $URL (PID: $(listeners))"
        else
            echo "• Compass is STOPPED."
        fi
        ;;
    *)
        echo "Usage: $0 [start|stop|restart|status]"
        exit 2
        ;;
esac
