# `modal.js` — opening and closing dialogs

The page is now in Brave. The HTML is on screen, the styles are
applied, and three small JavaScript files (`upload.js`, `todo.js`,
`modal.js`) start running in order. This note covers `modal.js` — 42
lines, the simplest of the three.

File: `static/modal.js`.

## The plain-English story

Think of `modal.js` as a **dumb light switch**. It doesn't know what's
in any room, it doesn't care what you do once the light is on. It only
knows two things:

- "Someone clicked a button labeled *open the X room*. OK, let me find
  the X room and turn its light on."
- "Someone clicked outside the lit room, or pressed Escape. OK, let me
  turn the light off."

The "rooms" here are the popup dialogs — the *Add a syllabus* popup,
the *Add a task* popup, the *Edit task* popup. They all live on the
page from the start, but they're invisible until something switches
them on.

Why is this its own file? Because every page in the app uses popups,
and every popup needs the same open/close behavior. Putting it in one
small file means we don't repeat the same logic three or four times.

What this file does **not** do: it doesn't know what an "Add task"
popup contains, doesn't submit any forms, doesn't send anything to the
server. That work belongs to other files (`todo.js`, `upload.js`).
This one is just the switchboard.

## What it does, three behaviors

1. Click a button marked `data-open-modal="<id>"` → unhide the popup
   with that id.
2. Click on the dimmed background or any "close" button → hide.
3. Press Escape → hide every open popup.

That's it. No library, no framework, no animation — just toggling a
property on/off.

## The wrapper around everything

```js
(function () {
    ...
})();
```

**Plain English:** this whole pattern means "run the code below right
now, but keep its variables in a sealed box so they can't bump into
variables in other JS files." The other two files (`todo.js`,
`upload.js`) wrap themselves the same way. Each is its own sealed box.

**Technical:** an **IIFE** (Immediately-Invoked Function Expression).
A function literal followed by `()` calls it immediately. Anything
declared inside (`function open`, `function close`) lives only inside
the function, so different files can each have their own `open`
function without overwriting each other. A modern alternative would be
ES modules (`<script type="module">`); IIFEs work everywhere with no
build step.

## The two helpers — open and close

```js
function open(modal) {
    modal.hidden = false;
    document.body.classList.add('modal-open');
    const focusable = modal.querySelector(
        'input, select, textarea, button:not([data-close-modal])'
    );
    if (focusable) focusable.focus();
}
```

**Plain English:** "To open a popup: make it visible, tell the page
behind it to stop scrolling, then put the cursor in the first usable
field so the user can start typing right away."

**Line by line:**

- `modal.hidden = false` — flips the popup's `hidden` flag off. Every
  popup template starts with `<div ... hidden>`, which acts like
  invisible by default. Setting `hidden = false` makes it appear.
- `document.body.classList.add('modal-open')` — adds a marker class
  to the page's body. The CSS uses this marker to lock scrolling on
  the page underneath, so the user can't scroll the back content while
  the popup is up.
- `modal.querySelector('input, select, textarea, button:not([data-close-modal])')`
  — searches *inside the popup* for the first thing that can take
  focus: any input, select, textarea, or button. The exception
  `:not([data-close-modal])` skips the X close button — we don't want
  the user opening a popup and immediately landing on "close."
- `if (focusable) focusable.focus()` — if we found something, give it
  focus (cursor lands there).

Why bother with the focus part? Two reasons: keyboard users don't
have to mouse-hunt for the input, and screen readers announce what
just opened.

```js
function close(modal) {
    modal.hidden = true;
    document.body.classList.remove('modal-open');
}
```

**Plain English:** "To close: hide the popup, let the page scroll
again." The mirror of `open` — no focus restore (a future improvement
might be to send focus back to the button that opened the popup).

## Wiring up the open buttons

```js
document.querySelectorAll('[data-open-modal]').forEach((btn) => {
    btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-open-modal');
        const modal = document.getElementById(id);
        if (modal) open(modal);
    });
});
```

**Plain English:** "Find every button on the page that says *open the
X popup*. For each one, set up a rule: when this button is clicked,
look up popup X and open it."

Recall the templates:

- `<button data-open-modal="syllabus-modal">+</button>` (the + syllabus button)
- `<button data-open-modal="add-task-modal">+ Add task</button>`

This block runs once when the page loads. It finds those buttons,
attaches a click handler to each, and that handler reads the
attribute to know *which* popup to open.

**Line by line:**

- `document.querySelectorAll('[data-open-modal]')` — find all elements
  with a `data-open-modal` attribute (the brackets are how CSS targets
  attributes). Returns a list of DOM elements.
- `.forEach((btn) => { ... })` — for each button in the list, run the
  block.
- `btn.addEventListener('click', () => { ... })` — attach a click
  listener to the button.
- Inside the click handler: read the attribute (`syllabus-modal`),
  find the matching element (`document.getElementById(id)`), and if
  it exists, call `open` on it.

**One caveat:** this only wires up buttons that exist *when the
script first runs*. If `todo.js` later creates a new button on the
fly, that button won't auto-open a popup. In this app every trigger
button is in the initial HTML, so it's fine.

## Closing on background click or close button

```js
document.addEventListener('click', (e) => {
    const overlay = e.target.closest('.modal-overlay');
    if (!overlay) return;
    if (e.target === overlay) close(overlay);
    if (e.target.closest('[data-close-modal]')) close(overlay);
});
```

**Plain English:** "Listen to *every* click on the page. If the click
happened inside a popup's dimmed area, check what was clicked: if it
was the dim background itself (clicking *outside* the dialog box), or
if it was any 'close' button — close the popup."

This is one listener that handles every popup, instead of one
listener per popup. The pattern is called **event delegation** —
useful when there are many similar things to listen to.

**Line by line:**

- `e.target` — the deepest element the click landed on (e.g., the X
  button, or the dim border, or some text inside the dialog).
- `e.target.closest('.modal-overlay')` — walk *upward* from the
  clicked element until we find an ancestor with class
  `modal-overlay`, or `null` if there's no such ancestor. So this is
  asking "was this click anywhere inside a popup overlay?"
- `if (!overlay) return` — click was outside any popup, ignore it.
- `if (e.target === overlay) close(overlay)` — the user clicked
  *exactly* on the dim border, not on the dialog box inside it. That's
  the "click outside to close" gesture.
- `if (e.target.closest('[data-close-modal]')) close(overlay)` —
  click was on (or inside) anything marked as a close button (the X,
  the Cancel button). Close.

Both checks can match the same click harmlessly — `close` setting
`hidden = true` twice has no extra effect.

## Closing on Escape

```js
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('.modal-overlay:not([hidden])').forEach(close);
});
```

**Plain English:** "Listen for keypresses. If the key is Escape, find
every popup that's currently visible and close it."

**Line by line:**

- `document.addEventListener('keydown', ...)` — listen for any key
  press anywhere on the page.
- `if (e.key !== 'Escape') return` — bail unless it's Escape.
- `'.modal-overlay:not([hidden])'` — CSS selector for "every element
  with class `modal-overlay` that does NOT have the `hidden`
  attribute." That's our currently-open popups.
- `.forEach(close)` — pass each open overlay through `close`.

## How it fits with the rest of the page

```
User clicks [+ Add task]
        │
        ▼
Browser fires a 'click' event on the button
        │
        ▼
modal.js click listener runs
   • reads data-open-modal="add-task-modal"
   • finds the <div id="add-task-modal" hidden>
   • calls open(modal)
        │
        ▼
Popup becomes visible, page lock-scrolls, cursor lands in the title input
        │
        ▼
User types a task, clicks Submit
        │
        ▼
todo.js takes over — sends the form to the server, adds the new row,
                    and calls close(modal) on success
        │
        ▼
(or the user hits Escape / clicks ×, and modal.js closes it directly)
```

The point: `modal.js` doesn't know what any specific popup *does*. It
only knows how to show and hide them. The actual content logic
(submitting the add-task form, populating the edit-task form's
fields) lives in the next file we'll cover — `todo.js`.
