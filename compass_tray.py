"""Compass desktop launcher.

Click the desktop shortcut → server starts silently, Brave opens to the home
page, system-tray icon appears with Open / Quit menu.
"""
from __future__ import annotations

import atexit
import ctypes
import logging
import os
import socket
import subprocess
import sys
import time
import webbrowser
from ctypes import wintypes as _wt
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont
from pystray import Menu, MenuItem


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

HOST = "0.0.0.0"
PORT = 8000
LOCAL_URL = f"http://localhost:{PORT}"
LOG_PATH = ROOT / "compass.log"

BRAVE_CANDIDATES = [
    Path(os.environ.get("ProgramFiles", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    Path(os.environ.get("ProgramFiles(x86)", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
]


# ---- Logging (no console when launched via pythonw.exe, so write to file) ----

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("compass_tray")


# ---- Helpers ----

def find_brave() -> Path | None:
    for p in BRAVE_CANDIDATES:
        if p.is_file():
            return p
    return None


def open_in_brave(url: str = LOCAL_URL) -> None:
    """Open URL in Brave if installed, else fall back to default browser."""
    brave = find_brave()
    if brave:
        log.info("opening %s in Brave at %s", url, brave)
        try:
            subprocess.Popen([str(brave), url], close_fds=True)
            return
        except OSError as exc:
            log.warning("Brave launch failed (%s); falling back to default browser", exc)
    log.info("opening %s in default browser", url)
    webbrowser.open(url)


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def evict_stale_python_on_port(port: int) -> bool:
    """If a python.exe is listening on `port`, kill it. Returns True if a process
    was evicted. Prevents a stale Compass from before a code change holding the
    port and silently making relaunches no-op."""
    if sys.platform != "win32":
        return False
    ps_script = (
        f"$p = (Get-NetTCPConnection -LocalPort {port} -State Listen "
        f"-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess; "
        f"if ($p) {{ "
        f"  $proc = Get-Process -Id $p -ErrorAction SilentlyContinue; "
        f"  if ($proc -and $proc.ProcessName -in @('python','pythonw')) {{ "
        f"    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue; "
        f"    Write-Output \"killed $p\" "
        f"  }} "
        f"}}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        if "killed" in (result.stdout or ""):
            log.info("evicted stale python on port %d: %s", port, result.stdout.strip())
            time.sleep(1.0)  # give the OS a moment to free the socket
            return True
    except Exception:
        log.exception("eviction check failed")
    return False


# ---- Windows Job Object: any child of this process dies when this process exits ----
#
# uvicorn runs in its own subprocess. If this launcher is force-killed (Task Manager,
# system shutdown, crash) atexit doesn't fire, so without a Job Object the uvicorn
# child would orphan. Putting it in a "kill on job close" job means the OS itself
# kills it the moment our process handle closes, no matter how we exited.

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_JOB_HANDLE: int | None = None
if sys.platform == "win32":
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", _wt.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", _wt.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", _wt.DWORD),
            ("SchedulingClass", _wt.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _ensure_kill_on_close_job() -> int | None:
    """Create (once) a Job Object that auto-kills its members when its handle closes."""
    global _JOB_HANDLE
    if sys.platform != "win32" or _JOB_HANDLE is not None:
        return _JOB_HANDLE
    try:
        h = _k32.CreateJobObjectW(None, None)
        if not h:
            log.warning("CreateJobObjectW failed: %s", ctypes.get_last_error())
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _k32.SetInformationJobObject(
            h,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            log.warning("SetInformationJobObject failed: %s", ctypes.get_last_error())
            return None
        _JOB_HANDLE = h
        log.info("created kill-on-close job object")
    except OSError:
        log.exception("could not create job object; orphans possible if force-killed")
    return _JOB_HANDLE


def _attach_to_job(proc: subprocess.Popen) -> None:
    if sys.platform != "win32":
        return
    job = _ensure_kill_on_close_job()
    if not job:
        return
    handle = int(proc._handle) if hasattr(proc, "_handle") else None
    if not handle:
        return
    if not _k32.AssignProcessToJobObject(job, handle):
        log.warning("AssignProcessToJobObject failed: %s", ctypes.get_last_error())


def python_exe() -> Path:
    """Pick python.exe from the same venv pythonw.exe lives in."""
    here = Path(sys.executable)
    candidate = here.with_name("python.exe")
    return candidate if candidate.is_file() else here


def start_server() -> subprocess.Popen:
    cmd = [
        str(python_exe()),
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        HOST,
        "--port",
        str(PORT),
        "--no-access-log",
    ]
    log.info("spawning uvicorn: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    _attach_to_job(proc)
    return proc


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    log.info("terminating uvicorn (pid %s)", proc.pid)
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        log.exception("error stopping uvicorn")


# ---- Icon ----

def make_icon_image(size: int = 64) -> Image.Image:
    """Generate a 'C' tile in Compass blue."""
    img = Image.new("RGBA", (size, size), (91, 157, 255, 255))  # accent blue
    draw = ImageDraw.Draw(img)
    text = "C"
    font_size = int(size * 0.55)
    font: ImageFont.ImageFont
    for candidate in ("seguibl.ttf", "segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, font_size)
            break
        except OSError:
            continue
    else:
        font = ImageFont.load_default()
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (size - tw) // 2 - bbox[0]
        y = (size - th) // 2 - bbox[1] - 2
    except AttributeError:
        x, y = size // 4, size // 4
    draw.text((x, y), text, fill="white", font=font)
    return img


def make_ico_file(out: Path) -> None:
    """Write a multi-resolution .ico for the desktop shortcut."""
    base = make_icon_image(256)
    base.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


# ---- Server controller (wraps start/stop with state for the tray menu) ----

class ServerController:
    """Owns the uvicorn subprocess. The tray menu calls into this to stop,
    restart, or restart the server on demand without ever touching PowerShell."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status_text(self) -> str:
        if self.is_running():
            return f"Running on :{PORT}"
        return "Stopped"

    def reload_env(self) -> None:
        """Re-read .compass_secret_key, .xai_key, .xai_model, .admin_emails
        so a Restart picks up any edits made since the launcher started."""
        for fname, var in (
            (".compass_secret_key", "COMPASS_SECRET_KEY"),
            (".xai_key", "XAI_API_KEY"),
            (".xai_model", "XAI_MODEL"),
            (".admin_emails", "ADMIN_EMAILS"),
        ):
            f = ROOT / fname
            if f.is_file():
                v = f.read_text(encoding="utf-8").strip()
                if v:
                    os.environ[var] = v
                    log.info("reloaded %s from %s", var, fname)

    def start(self) -> bool:
        """Start uvicorn if it isn't already running. Returns True on success."""
        if self.is_running():
            return True
        # Evict anything else on :8000 (some other Compass instance, etc.)
        if port_in_use("127.0.0.1", PORT):
            evict_stale_python_on_port(PORT)
        self.reload_env()
        self.proc = start_server()
        # Wait for port to bind
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if self.proc.poll() is not None:
                log.error("uvicorn exited early with code %s", self.proc.returncode)
                self.proc = None
                return False
            if port_in_use("127.0.0.1", PORT):
                log.info("server listening on :%d", PORT)
                return True
            time.sleep(0.3)
        log.error("server did not become ready within 30s")
        self.stop()
        return False

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            stop_server(self.proc)
        self.proc = None

    def restart(self) -> bool:
        log.info("restart requested")
        self.stop()
        return self.start()


# ---- Tray menu actions ----

def make_menu(controller: ServerController) -> Menu:
    """Build the tray menu. Text and enabled state are functions so they
    re-evaluate every time the menu opens — no manual update_menu() needed."""

    def status_item_text(_item: MenuItem) -> str:
        return ("● " if controller.is_running() else "○ ") + controller.status_text()

    def toggle_text(_item: MenuItem) -> str:
        return "Stop server" if controller.is_running() else "Start server"

    def on_open(_icon: pystray.Icon, _item: MenuItem) -> None:
        if not controller.is_running():
            log.info("Open clicked while server stopped; starting first")
            if not controller.start():
                return
        open_in_brave()

    def on_restart(_icon: pystray.Icon, _item: MenuItem) -> None:
        controller.restart()

    def on_toggle(_icon: pystray.Icon, _item: MenuItem) -> None:
        if controller.is_running():
            controller.stop()
        else:
            controller.start()

    def on_quit(icon: pystray.Icon, _item: MenuItem) -> None:
        log.info("quit requested via tray menu")
        controller.stop()
        icon.stop()

    return Menu(
        MenuItem(status_item_text, None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Open Compass", on_open, default=True),
        Menu.SEPARATOR,
        MenuItem("Restart server", on_restart, enabled=lambda _i: controller.is_running()),
        MenuItem(toggle_text, on_toggle),
        Menu.SEPARATOR,
        MenuItem("Quit Compass", on_quit),
    )


# ---- Main ----

def main() -> int:
    log.info("Compass tray launcher starting (root=%s)", ROOT)

    # If port is taken, first try to evict any stale Compass on it so a relaunch
    # actually picks up new code. If something else (non-python) is on the port,
    # just open Brave to it as before.
    if port_in_use("127.0.0.1", PORT):
        evicted = evict_stale_python_on_port(PORT)
        if not evicted or port_in_use("127.0.0.1", PORT):
            log.info("port %d already in use (not ours); opening Brave at %s and exiting", PORT, LOCAL_URL)
            open_in_brave()
            return 0
        log.info("evicted stale Compass; starting fresh server")

    controller = ServerController()
    if not controller.start():
        log.error("initial server start failed; exiting")
        return 1
    atexit.register(controller.stop)

    open_in_brave()

    icon = pystray.Icon(
        "compass",
        icon=make_icon_image(64),
        title="Compass",
        menu=make_menu(controller),
    )
    try:
        icon.run()
    finally:
        controller.stop()
        log.info("Compass tray launcher exited")
    return 0


if __name__ == "__main__":
    sys.exit(main())
