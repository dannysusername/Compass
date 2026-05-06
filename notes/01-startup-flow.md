# Compass Startup Flow

What happens when I double-click the SF icon, click menu items, and quit.

## Double-click → app running

```
┌─────────────────────────────────────────────────────────┐
│  YOU DOUBLE-CLICK THE SF ICON                           │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Windows starts Python → runs compass_tray.py         │
│  Python finds the entry point at the bottom and         │
│  calls main()                                           │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 1 — main() begins                                 │
│  • Write "starting" to compass.log                    │
│  • Check if port 8000 is busy → it's free, move on      │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 2 — Create the ServerController                   │
│  Think of it as a remote control for the web server.    │
│  Right now it owns nothing (no server running yet).     │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 3 — controller.start()                            │
│                                                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 3a. Read .xai_key, .xai_model, .compass_token   │  │
│  │     from disk into environment variables          │  │
│  └─────────────────────┬─────────────────────────────┘  │
│                        ▼                                │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 3b. Launch the web server as a SEPARATE PROGRAM   │  │
│  │     (uvicorn running main.py)                     │  │
│  │                                                   │  │
│  │     ┌─────────────────────┐                       │  │
│  │     │ Babysitter (this)   │                       │  │
│  │     │   ↓ spawns          │                       │  │
│  │     │ Web Server (uvicorn)│ ← now running too     │  │
│  │     └─────────────────────┘                       │  │
│  └─────────────────────┬─────────────────────────────┘  │
│                        ▼                                │
│  ┌───────────────────────────────────────────────────┐  │
│  │ 3c. Wait until the web server is ready            │  │
│  │     (poll port 8000 every 0.3s, up to 30s)        │  │
│  │                                                   │  │
│  │     "Are you ready?" → no → wait                  │  │
│  │     "Are you ready?" → no → wait                  │  │
│  │     "Are you ready?" → YES! → continue            │  │
│  └─────────────────────┬─────────────────────────────┘  │
└────────────────────────┼────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 4 — Register cleanup hook                         │
│  "If I (the babysitter) ever exit, stop the web server" │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 5 — Open Brave                                    │
│  Launches Brave as another separate program, pointed    │
│  at http://localhost:8000                               │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 6 — Build the tray icon + right-click menu        │
│  Each menu item is wired to a controller method:        │
│    • Open Compass → opens Brave                       │
│    • Restart server → controller.restart()              │
│    • Stop / Start    → controller.stop() / .start()     │
│    • Quit Compass  → stops server, ends program       │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 7 — icon.run() blocks here, waiting               │
│  The babysitter is now idle. It just sits and listens   │
│  for menu clicks until you Quit.                        │
└─────────────────────────────────────────────────────────┘
```

## Three programs are now running

```
┌────────────────────┐    ┌────────────────────┐    ┌────────────┐
│ Babysitter         │    │ Web Server         │    │ Brave      │
│ compass_tray.py  │───▶│ uvicorn + main.py  │◀──▶│            │
│                    │    │ port 8000          │    │            │
│ • Owns tray icon   │    │ • Serves web pages │    │ • Shows    │
│ • Watches server   │    │ • Talks to DB      │    │   the site │
│ • Has the menu     │    │                    │    │            │
└────────────────────┘    └────────────────────┘    └────────────┘
```

The babysitter and Brave both talk to the web server, but they don't talk
to each other — Brave doesn't know the babysitter exists.

## Clicking a menu item (Restart)

```
You right-click the tray icon
        │
        ▼
Menu pops up. You click "Restart server"
        │
        ▼
on_restart() runs
        │
        ▼
controller.restart()
        │
        ├──▶ controller.stop()  → kills the web server process
        │
        └──▶ controller.start() → spawns a fresh one (Step 3 again)
```

## Quitting

```
You click "Quit Compass"
        │
        ▼
on_quit() runs
        │
        ├──▶ controller.stop() → kills the web server
        │
        └──▶ icon.stop()       → ends the tray icon
                │
                ▼
        icon.run() returns, main() finishes
                │
                ▼
        Python process exits. All three programs gone.
```

## Where to find each piece in code

All in `compass_tray.py`:

| Action               | Function          | Approx. line |
|----------------------|-------------------|--------------|
| Entry point          | `main()`          | 405          |
| Start server         | `controller.start()` | 321       |
| Stop server          | `controller.stop()`  | 345       |
| Restart server       | `controller.restart()` | 352     |
| Spawn uvicorn        | `start_server()`  | 215          |
| Build menu           | `make_menu()`     | 360          |
| Open / Restart / Stop / Quit handlers | `on_open`, `on_restart`, `on_toggle`, `on_quit` | 370–389 |
