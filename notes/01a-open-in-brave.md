# Deep Dive: `open_in_brave()`

A close look at `compass_tray.py` line 59 — the function that actually opens
Brave once the web server is ready.

## The function

```python
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
```

## The signature, in Java terms

```python
def open_in_brave(url: str = LOCAL_URL) -> None:
```

Roughly equivalent to:

```java
void openInBrave(String url = LOCAL_URL) {
```

- `url: str = LOCAL_URL` → parameter is a String, defaults to `LOCAL_URL`
  if no value is passed. Python lets you put defaults right in the signature;
  Java would use overloads.
- `-> None` → returns nothing (Java's `void`).

`LOCAL_URL` is defined at the top of the file (line 30):

```python
LOCAL_URL = f"http://localhost:{PORT}"
```

So unless you pass something else, this opens `http://localhost:8000`.

## Step 1 — Find Brave

```python
brave = find_brave()
```

Helper at line 52:

```python
def find_brave() -> Path | None:
    for p in BRAVE_CANDIDATES:
        if p.is_file():
            return p
    return None
```

- `Path | None` → "Path or None." Java: `Optional<Path>`.
- Walks `BRAVE_CANDIDATES`, returns the first path that's a real file on disk.
- Returns `None` if Brave isn't installed in any expected location.

`BRAVE_CANDIDATES` (line 33):

```python
BRAVE_CANDIDATES = [
    Path(os.environ.get("ProgramFiles", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    Path(os.environ.get("ProgramFiles(x86)", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
]
```

- `os.environ.get("ProgramFiles", "")` reads the `ProgramFiles` env var
  (e.g. `C:\Program Files`). Falls back to empty string if not set.
- `Path("...") / "folder" / "file.exe"` joins paths — like `File.separator`
  in Java, just prettier.

## Step 2 — Launch Brave (happy path)

```python
if brave:
    log.info("opening %s in Brave at %s", url, brave)
    try:
        subprocess.Popen([str(brave), url], close_fds=True)
        return
```

- `if brave:` → Python treats `None` as falsy. So "if we found Brave."
- `subprocess.Popen([str(brave), url], close_fds=True)` is the same kind of
  call we use for the web server — it launches a separate program.

The equivalent terminal command:

```
"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" http://localhost:8000
```

Brave's `.exe` accepts a URL as a command-line argument. On launch, it opens
a tab pointed at that URL.

`close_fds=True` → don't share open file descriptors with the new process
(clean handoff, no accidental ties to the babysitter's open files).

## Step 3 — The fallback (no Brave)

```python
log.info("opening %s in default browser", url)
webbrowser.open(url)
```

If Brave wasn't found, we fall through to here. `webbrowser` is a built-in
Python module that asks Windows "what's the user's default browser?" and
opens the URL in it.

## Step 4 — The try/except safety net

```python
try:
    subprocess.Popen([str(brave), url], close_fds=True)
    return
except OSError as exc:
    log.warning("Brave launch failed (%s); falling back to default browser", exc)
```

If `Popen` itself blows up (corrupted .exe, permission issue, etc.), catch
the error, log it, and fall through to the default-browser fallback.

Java equivalent: `try { ... } catch (IOException exc) { ... }`.

## Flow

```
open_in_brave(url) called
        │
        ▼
find_brave() — check 3 install paths
        │
   ┌────┴────────────┐
   ▼                 ▼
 found            not found
   │                 │
   ▼                 │
subprocess.Popen     │
[brave.exe, url]     │
   │                 │
   ▼ (failed?)       │
   │   ┌─────────────┘
   ▼   ▼
webbrowser.open(url)  ← default browser fallback
```

## What "Brave is now open" actually means

A new process exists in Windows: `brave.exe`. It is:

- Running independently from the babysitter (Popen returned immediately).
- Visible to you — a new window appears.
- About to send `GET http://localhost:8000` to the web server.

The babysitter is done involving itself with Brave. From here on, Brave talks
directly to the web server for the rest of its life. The bridge into
`main.py` happens the moment Brave fires that first request.
