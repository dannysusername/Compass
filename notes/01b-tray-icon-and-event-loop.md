# Deep Dive: The tail of `main()` — tray icon + event loop

Picks up at `compass_tray.py` line 425, right after `open_in_brave()`
returns. This is the last chunk of the babysitter's startup, and the part
that "stays alive" while you use the app.

## The code

```python
    open_in_brave()                              # already covered

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
```

## Step 1 — Build the tray icon object

```python
icon = pystray.Icon(
    "compass",
    icon=make_icon_image(64),
    title="Compass",
    menu=make_menu(controller),
)
```

`pystray.Icon(...)` is a constructor — like Java's `new Icon(...)`. We are
creating an icon object, not yet showing it.

| Argument                           | Meaning                                                   |
|------------------------------------|-----------------------------------------------------------|
| `"compass"`                      | Internal name Windows uses to identify the icon. Hidden.  |
| `icon=make_icon_image(64)`         | The actual image. Generated on the fly via PIL (line 256).|
| `title="Compass"`                | Tooltip when you hover the tray icon.                     |
| `menu=make_menu(controller)`       | The right-click menu. Wires items to `controller` methods.|

The image isn't a shipped `.png` file — `make_icon_image()` draws it from
scratch every launch (a 64×64 blue tile with "SF" centered).

`make_menu(controller)` (line 360) returns a `pystray.Menu` whose items each
hold a callback like "when clicked, call `controller.restart()`."

After this line: the icon **exists as an object in memory**, but isn't yet
showing in your tray.

## Step 2 — Run the icon (the event loop)

```python
try:
    icon.run()
finally:
    controller.stop()
    log.info("Compass tray launcher exited")
```

`icon.run()` does two things at once:

1. **Adds the icon to your system tray** — the SF tile appears.
2. **Starts an event loop that blocks here forever**, waiting for menu
   clicks.

This is the equivalent of Swing's `EventQueue` in Java: the program just
sits and listens for events.

Execution **stops on this line** until something tells the icon to exit.
That something is `icon.stop()` — called by `on_quit()` when you click
"Quit Compass" in the menu.

The `try / finally` is the same idea as Java:

```java
try {
    icon.run();
} finally {
    controller.stop();
    log.info("Compass tray launcher exited");
}
```

The `finally` block **always runs**, no matter how `icon.run()` exits —
clean quit, crash, anything. Safety net: before `main()` returns, kill the
web server. Prevents leaving a zombie web server behind.

## Step 3 — Return

```python
return 0
```

`main()` is done. `0` means "everything went fine." Java equivalent:
`System.exit(0)`.

At the bottom of the file:

```python
if __name__ == "__main__":
    sys.exit(main())
```

`sys.exit(main())` takes that `0` and exits the Python process. **The
babysitter is gone.**

## What this means in real time

The user-facing reality:

- You see the tray icon appear.
- You can right-click it for the menu.
- You can use the app via Brave.
- The babysitter program is invisible — but alive in memory, just waiting
  on `icon.run()`.

The babysitter does literally nothing else for the rest of the session
except handle menu clicks. All the actual *app* work happens in:
- The web server (uvicorn + main.py)
- Brave

## Where the babysitter lives the rest of its life

```
main() runs to here:

    open_in_brave()                    ✓ done
    icon = pystray.Icon(...)            ✓ done
    icon.run() ────── BLOCKED HERE ────── waiting for menu clicks
                          │
                          │  (you click Quit)
                          ▼
                    icon.stop() called
                          │
                          ▼
                    icon.run() returns
                          │
                          ▼
                    finally: controller.stop()
                                       (kills the web server)
                          │
                          ▼
                    return 0
                          │
                          ▼
                    sys.exit(0)
                          │
                          ▼
                    babysitter process gone
```

## End of `compass_tray.py`

The babysitter file is now fully traced. Next file: `main.py`, starting at
line 786 — the `home()` function. That's the door Brave's first request
walks through on the web server side.
