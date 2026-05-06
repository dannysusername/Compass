# `upload.js` — drag-and-drop PDFs and the dark mode toggle

This file does two unrelated jobs in one place: the PDF drop zone (used
by the *Add a syllabus* popup) and the dark/light theme toggle button
in the header. They live together because they both attach to elements
that show up on every page, and the file is small enough that splitting
it would be more work than it's worth.

File: `static/upload.js`. Loaded from `base.html` so it runs on every
page.

## The plain-English story

Two completely separate features, both wired up by this one file at
page load:

**1. The drop zone.** When the user opens *Add a syllabus*, they see a
big dotted box that says "Drop a PDF here." This file watches that box
for two things:

- The user dragged a file over it (highlight the box).
- The user dropped a file on it (grab the file and pretend the user
  picked it through the regular *Choose file* dialog).

Then a little card appears showing the filename, size, and a *Remove*
button. The user clicks Submit, the form sends the PDF to the server,
and the rest of the syllabus parsing pipeline takes over.

**2. The theme toggle.** The *Dark/Light* button in the page header.
Click it, the page flips colors, and the choice is saved so the next
time the user loads any page it picks up where they left off.

The two halves don't talk to each other — the file just bundles them.
Think of it as one shipping container with two sealed boxes inside.

## The wrapper and "use strict"

```js
(function () {
    "use strict";
    ...
})();
```

Same sealed-box pattern as `modal.js`. The new addition is `"use
strict";` — a directive that turns on stricter JavaScript rules:
mistakes that would silently misbehave (typoing a variable name and
accidentally creating a new global) become loud errors instead.

## Three small helper functions

These are utilities used by the drop-zone code below. They're defined
once at the top so the main code is easier to read.

### `escapeHTML` — make user text safe to insert as HTML

```js
function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
}
```

**Plain English:** "Take a string the user gave us (like a filename)
and replace any character that has a special meaning in HTML with its
safe escape code, so we can drop it into the page without breaking the
markup or letting someone sneak in a `<script>` tag."

When you write `picked.innerHTML = '...' + filename + '...'`, you're
asking the browser to interpret the result as HTML. If a malicious or
weird filename contained `<script>` or `"` or `&`, that could break
the page or be unsafe. This function rewrites the dangerous five
characters as their HTML escape codes so the browser displays them as
plain text.

**Line by line:**

- `String(s)` — coerce to a string (in case it's a number, etc.).
- `.replace(/[&<>"']/g, fn)` — find every occurrence of any of those
  five characters and replace it with whatever `fn` returns.
- The function takes the matched character `c` and looks it up in a
  small lookup table to get the escape code (`<` becomes `&lt;`, etc.).

### `fmtSize` — pretty-print a byte count

```js
function fmtSize(b) {
    if (b < 1024) return b + " B";
    var k = b / 1024;
    if (k < 1024) return Math.round(k) + " KB";
    return (k / 1024).toFixed(1) + " MB";
}
```

**Plain English:** "Given a number of bytes, print it the way a human
would read it: 850 B, or 23 KB, or 4.2 MB."

**Line by line:**

- Less than 1024 bytes → just bytes.
- Between 1 KB and 1 MB → kilobytes, rounded to a whole number.
- Anything bigger → megabytes with one decimal place (`.toFixed(1)`).

That's it — used to display file size in the picked-file card.

### `isAccepted` — does this file match the form's `accept` attribute?

```js
function isAccepted(accept, file) {
    if (!accept || accept === "*/*") return true;
    var name = file.name.toLowerCase();
    var type = (file.type || "").toLowerCase();
    return accept.split(",").some(function (token) {
        token = token.trim().toLowerCase();
        if (!token) return false;
        if (token.charAt(0) === ".") return name.endsWith(token);
        if (token.endsWith("/*")) return type.startsWith(token.slice(0, -1));
        return type === token;
    });
}
```

**Plain English:** "The form's file input has an `accept` attribute
saying what kinds of files are allowed (`application/pdf,.pdf`). Given
a dropped file, check whether it matches *any* of those rules."

The native file picker enforces `accept` automatically — but drag-and-drop
doesn't, so we have to check it ourselves.

**Line by line:**

- `if (!accept || accept === "*/*") return true` — no restriction, accept anything.
- `accept.split(",")` — the attribute is a comma-separated list. Split it.
- `.some(fn)` — return true if *any* token matches.
- Inside the loop, three forms of token are handled:
  - Starts with `.` → file extension match (`.pdf` → does the filename end with `.pdf`?).
  - Ends with `/*` → MIME family match (`image/*` → does the file's type start with `image/`?).
  - Otherwise → exact MIME match (`application/pdf` → does the file's type equal that?).

## `wireZone` — set up one drop zone

This is the meat of the drop-zone code. Called once per drop zone on
the page (the home page has one — inside the *Add a syllabus* popup).

```js
function wireZone(zone) {
    var input = zone.querySelector('input[type="file"]');
    if (!input) return;
    var picked = zone.querySelector(".drop-picked");
    var accept = input.accept || "";
    ...
}
```

**Plain English:** "We just got handed one drop zone element. Find the
hidden file input inside it (that's where we'll stash any dropped
file), find the little 'picked file' card, and remember the `accept`
list so we can check dropped files against it."

`zone.querySelector(...)` searches *only inside* `zone` — every drop
zone is self-contained.

### `update()` — sync the visible picked-file card to the input's state

```js
function update() {
    var f = input.files && input.files[0];
    if (f) {
        zone.classList.add("has-file");
        if (picked) {
            picked.innerHTML =
                '<span class="name">' + escapeHTML(f.name) + "</span>" +
                '<span class="size">' + fmtSize(f.size) + "</span>" +
                '<button type="button" class="clear-file" aria-label="Remove">' +
                "Remove</button>";
        }
    } else {
        zone.classList.remove("has-file");
        if (picked) picked.innerHTML = "";
    }
}
```

**Plain English:** "Look at the file input. If there's a file, show
the picked-file card with its name, size, and a Remove button. If
there's no file, hide the card and remove the 'has-file' marker
class."

This function is called whenever the file changes (after a drop, after
a click-and-pick, after Remove). It's the single source of truth for
"what does the zone look like right now?"

The CSS uses the `has-file` class to swap the look of the zone — for
example, dimming the "Drop a PDF here" prompt when a file is selected.

`input.files` is a FileList (zero or more files). For our case it has
either zero or one. `&&` is a short-circuit: if `input.files` is null
or undefined, `f` ends up falsy and we go to the else branch.

### `setFile(file)` — accept a file from a drop

```js
function setFile(file) {
    if (!file) return;
    if (!isAccepted(accept, file)) {
        zone.classList.add("reject");
        setTimeout(function () { zone.classList.remove("reject"); }, 800);
        return;
    }
    try {
        var dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
    } catch (e) {
        return;
    }
    update();
}
```

**Plain English:** "We just got a file from a drop. If it's not the
right kind of file, briefly flash the zone red and stop. Otherwise,
shove it into the file input as if the user had picked it through the
*Choose file* dialog, then refresh the visible card."

Why the dance with `DataTransfer`? You can't just write `input.files
= [file]` — `input.files` is read-only by default. The trick is to
build a fake clipboard object (`DataTransfer`), put the file on it,
and assign its files list to the input. Modern browsers allow this;
older Safari doesn't, hence the `try/catch` fallback (a silent no-op
— the user can still use the *Choose file* button).

`zone.classList.add("reject")` + `setTimeout(..., 800)` — add a
"reject" class, wait 0.8 seconds, remove it. CSS uses that class to
flash the zone red. A self-cleaning visual blip.

### Wiring the listeners — input change, drag, drop, remove

```js
input.addEventListener("change", update);
```

**Plain English:** "If the user uses the regular *Choose file*
dialog, fire `update` to refresh the card."

```js
["dragenter", "dragover"].forEach(function (ev) {
    zone.addEventListener(ev, function (e) {
        e.preventDefault();
        zone.classList.add("drag");
    });
});
```

**Plain English:** "When the user drags a file over the zone, add a
'drag' class so the CSS can highlight it."

`e.preventDefault()` is critical here. By default, the browser's
behavior when you drop a file on the page is to *navigate to that
file* (you'd suddenly find your browser opening the PDF directly).
`preventDefault` cancels that default — we need to cancel it on
`dragover` (and `dragenter`) too, not just on the drop, otherwise the
browser treats the page as a non-drop-target and the drop never fires.

```js
zone.addEventListener("dragleave", function (e) {
    if (!zone.contains(e.relatedTarget)) zone.classList.remove("drag");
});
```

**Plain English:** "When the cursor leaves the zone, remove the
highlight — but only if it's *really* leaving, not just moving from
the outer zone div onto a child element inside the zone."

`dragleave` fires every time the cursor moves from a parent to a
child (because each is technically a separate element). `e.relatedTarget`
is what the cursor moved *onto*. If that destination is still inside
`zone`, we haven't really left, so don't drop the highlight.

```js
zone.addEventListener("drop", function (e) {
    e.preventDefault();
    zone.classList.remove("drag");
    var files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) setFile(files[0]);
});
```

**Plain English:** "When the user drops something, cancel the default
'open this file' behavior, remove the highlight, grab the first
dropped file, and try to attach it to the input."

We only ever take `files[0]` — even if the user dropped multiple, we
keep the first one. The form is for a single syllabus.

```js
if (picked) {
    picked.addEventListener("click", function (e) {
        if (e.target.classList && e.target.classList.contains("clear-file")) {
            e.stopPropagation();
            e.preventDefault();
            try {
                var dt = new DataTransfer();
                input.files = dt.files;
            } catch (err) {
                input.value = "";
            }
            update();
        }
    });
}
```

**Plain English:** "If the user clicks the *Remove* button on the
picked-file card, clear the file from the input and refresh the
card."

`e.stopPropagation()` and `e.preventDefault()` keep the click from
also bubbling up to the file input (which would re-open the picker).

To clear: build an empty `DataTransfer`, assign its (empty) files
list to the input. The fallback for older browsers is `input.value =
""`, which works for single-file inputs.

## `wireThemeToggle` — the Dark/Light button

```js
function wireThemeToggle() {
    var btn = document.querySelector("[data-theme-toggle]");
    if (!btn) return;
    var html = document.documentElement;

    function setLabel() {
        btn.textContent = html.classList.contains("dark") ? "Light" : "Dark";
    }
    setLabel();

    btn.addEventListener("click", function () {
        var goingDark = !html.classList.contains("dark");
        html.classList.toggle("dark", goingDark);
        try {
            localStorage.setItem("compass-theme", goingDark ? "dark" : "light");
        } catch (e) {
            // localStorage may be unavailable in private mode
        }
        setLabel();
    });
}
```

**Plain English:** "Find the Dark/Light button. Set its label based
on the current theme (says 'Light' when we're in dark mode, since
clicking would switch *to* light, and vice versa). On click, flip the
theme and remember the choice."

How does the theme actually work? CSS rules look at `<html
class="dark">` and apply a different palette when that class is
present. Adding/removing the class flips the page's whole color
scheme. `document.documentElement` is the `<html>` element.

The choice is saved in `localStorage` — a tiny per-site key/value
store that survives page reloads. Recall the inline script we covered
in `04-home-template.md` that sat in `base.html`'s `<head>`:

```js
var t = localStorage.getItem("compass-theme");
if (t === "dark") document.documentElement.classList.add("dark");
```

That's the *reading* half of the same setup — it runs before the page
paints so dark mode is applied immediately, with no flash of light
mode. This file is the *writing* half: when the user clicks the
toggle, save the choice for next time.

`localStorage.setItem` can throw in private/incognito mode where
storage is disabled. The `try/catch` makes the toggle still work for
the current session, just without persistence.

## `init` — wire everything up at page load

```js
function init() {
    document.querySelectorAll("[data-upload-zone]").forEach(wireZone);
    wireThemeToggle();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
```

**Plain English:** "When the page is ready, find every drop zone and
wire it up, and wire up the theme toggle. If the page isn't ready
yet, wait for it; if it already is, go ahead now."

`document.readyState` tells us where the browser is in loading the
page:

- `"loading"` — still parsing HTML.
- `"interactive"` or `"complete"` — DOM is built and we can poke at it.

The `<script defer>` attribute in `base.html` already delays the
script until after parsing, so `readyState` will usually be past
`"loading"` by the time this runs and the `else` branch fires
immediately. The `if` branch is a safety net.

## How it fits with the rest of the page

```
Page loads. base.html includes <script src="upload.js" defer>.
        │
        ▼
Browser parses HTML, then runs upload.js
        │
        ▼
init() runs:
   • For each <... data-upload-zone>, wireZone() sets up listeners.
   • wireThemeToggle() sets up the Dark/Light button.
        │
        ▼
   ── User clicks + (Add a syllabus) ──
   modal.js opens the popup, focus lands on the file input.
        │
        ▼
   User drags a PDF over the drop zone:
      dragover → highlight class added
      drop     → setFile() puts the file on the input, picked card appears
        │
        ▼
   User clicks Submit → browser sends the form (POST /syllabus) the
   ordinary way, with the file payload.
        │
        ▼
   Server handles the syllabus upload (different file, different note).

   ── Or: User clicks Dark/Light ──
   wireThemeToggle handler runs:
      flips the .dark class on <html>
      saves the choice in localStorage
      updates the button label
        │
        ▼
   Next page load, the inline <head> script reads localStorage and
   applies the saved theme before paint.
```

## Why this file is loaded on every page

`upload.js` is included in `base.html`, not in any specific page
template. That means it runs on the home page, the class detail page,
the today/week views — everywhere.

- The theme toggle is in the header on every page, so it has to be
  wired up everywhere.
- The drop zone only exists on pages that include the syllabus modal
  (currently just the home page), but `wireZone` is safe to run on
  pages with no drop zones — `querySelectorAll(...)` returns an empty
  list and `forEach` does nothing.

Same script, no harm done either way. The next note covers `todo.js`
— the biggest of the three, in charge of toggling/editing/dragging
tasks in the today list.
