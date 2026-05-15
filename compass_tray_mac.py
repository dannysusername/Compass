#!/usr/bin/env python3
"""Compass macOS menu-bar app — the macOS counterpart to compass_tray.py.

Puts a 🧭 icon in the system menu bar (top-right). Click it for
Open / Start / Stop / Restart / Quit. Manages a detached uvicorn process
tracked by the listening port + a .compass.pid file, so it also controls
a server that was started some other way. Launched via Compass.app.
"""

import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

PORT = 8000
URL = f"http://localhost:{PORT}"
PY = ROOT / ".venv" / "bin" / "python"
PIDFILE = ROOT / ".compass.pid"
LOG = ROOT / "compass.log"


def _listeners() -> list[int]:
    """PIDs LISTENing on PORT (whoever started them)."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{PORT}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    return [int(p) for p in out.split() if p.strip().isdigit()]


def is_up() -> bool:
    return bool(_listeners())


def start_server() -> str:
    if is_up():
        return "already running"
    if not PY.exists():
        return f"missing venv python at {PY}"
    with open(LOG, "a") as logf:
        proc = subprocess.Popen(
            [str(PY), "-m", "uvicorn", "main:app", "--host", "0.0.0.0",
             "--port", str(PORT), "--no-access-log"],
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True, cwd=str(ROOT),
        )
    PIDFILE.write_text(str(proc.pid))
    for _ in range(40):  # ~10s
        if is_up():
            return "started"
        time.sleep(0.25)
    return "did not come up — check compass.log"


def stop_server() -> str:
    pids = _listeners()
    if not pids and PIDFILE.exists():
        try:
            pids = [int(PIDFILE.read_text().strip())]
        except Exception:
            pids = []
    if not pids:
        PIDFILE.unlink(missing_ok=True)
        return "not running"
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(20):  # ~6s grace
        if not is_up():
            break
        time.sleep(0.3)
    for p in _listeners():  # force any stragglers
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass
    PIDFILE.unlink(missing_ok=True)
    return "stopped"


# Headless validation hook — exercises the control logic without a GUI.
if "--selftest" in sys.argv:
    print("python :", sys.executable)
    print("root   :", ROOT)
    print("up?    :", is_up(), "listeners:", _listeners())
    sys.exit(0)


import rumps  # noqa: E402  (after --selftest so validation needs no GUI libs)


def _notify(title: str, msg: str) -> None:
    try:
        rumps.notification("Compass", title, msg)
    except Exception:
        pass  # notifications need a bundle id; never fatal


class CompassApp(rumps.App):
    def __init__(self):
        super().__init__("Compass", title="🧭", quit_button=None)
        self.status_item = rumps.MenuItem("● …")  # no callback ⇒ greyed
        self.menu = [
            rumps.MenuItem("Open Compass in browser", callback=self.open_browser),
            None,
            rumps.MenuItem("Start", callback=self.do_start),
            rumps.MenuItem("Stop", callback=self.do_stop),
            rumps.MenuItem("Restart  (after code changes)", callback=self.do_restart),
            None,
            self.status_item,
            None,
            rumps.MenuItem("Quit Compass  (stops the server)", callback=self.do_quit),
        ]
        # Match the Windows tray: launching the app brings the server up.
        start_server()
        self._refresh(None)
        rumps.Timer(self._refresh, 4).start()

    def _refresh(self, _):
        up = is_up()
        self.title = "🧭" if up else "🧭⏸"
        self.status_item.title = "● Server running" if up else "○ Server stopped"

    def open_browser(self, _):
        webbrowser.open(URL)

    def do_start(self, _):
        _notify("Start", start_server())
        self._refresh(None)

    def do_stop(self, _):
        _notify("Stop", stop_server())
        self._refresh(None)

    def do_restart(self, _):
        stop_server()
        time.sleep(0.5)
        _notify("Restart", start_server())
        self._refresh(None)

    def do_quit(self, _):
        stop_server()
        rumps.quit_application()


if __name__ == "__main__":
    CompassApp().run()
