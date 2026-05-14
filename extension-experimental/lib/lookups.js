// Class + tag dropdown population, shared by add-task and edit-task forms.
// Uses cached promises in state.js so we don't re-fetch on every form open;
// callers bust the cache (state.classesPromise = null, state.tagsPromise = null)
// after any mutation that would change the lists.

import { api } from "./api.js";
import { state } from "./state.js";
import { bindInlineNewTag } from "./behaviors/tag-inline.js";

// Pull classes + tags (cached), then refill every dropdown in the document.
// Idempotent — call after any class/tag mutation to keep all selects in sync.
//
// `force: true` busts both caches before fetching — used when a form opens
// to catch the case where another tab created/deleted a class while the
// panel was sitting idle.
export async function ensureLookups({ force = false } = {}) {
    if (force) {
        state.classesPromise = null;
        state.tagsPromise = null;
    }
    if (!state.classesPromise) state.classesPromise = api.classes().catch(() => []);
    if (!state.tagsPromise) state.tagsPromise = api.tags().catch(() => []);
    const [classes, tags] = await Promise.all([state.classesPromise, state.tagsPromise]);
    document.querySelectorAll("select[name='class_id']").forEach((sel) => {
        fillClassSelect(sel, classes);
    });
    document.querySelectorAll("select[name='tag_id']").forEach((sel) => {
        fillTagSelect(sel, tags);
        bindInlineNewTag(sel);
    });
    return { classes, tags };
}

// Refill class dropdown. Keeps the leading "Personal" option (its value
// differs by form: "0" in editor, "" in add-task — both interpreted as
// Personal by the server's edit_task / _create_task_for_user).
export function fillClassSelect(sel, classes) {
    while (sel.options.length > 1) sel.remove(1);
    classes.forEach((c) => {
        const o = document.createElement("option");
        o.value = String(c.id);
        o.textContent = `${c.code} — ${c.name}`;
        sel.appendChild(o);
    });
}

// Refill tag dropdown — system tags grouped above user tags, "+ New tag…"
// sentinel last. Mirrors the website's add-task modal.
export function fillTagSelect(sel, tags) {
    while (sel.options.length > 1) sel.remove(1);
    const sys = tags.filter((t) => t.is_system);
    const own = tags.filter((t) => !t.is_system);
    if (sys.length) {
        const g = document.createElement("optgroup");
        g.label = "System";
        sys.forEach((t) => g.appendChild(tagOption(t)));
        sel.appendChild(g);
    }
    if (own.length) {
        const g = document.createElement("optgroup");
        g.label = "Yours";
        own.forEach((t) => g.appendChild(tagOption(t)));
        sel.appendChild(g);
    }
    const newOpt = document.createElement("option");
    newOpt.value = "__new__";
    newOpt.textContent = "+ New tag…";
    sel.appendChild(newOpt);
}

function tagOption(t) {
    const o = document.createElement("option");
    o.value = String(t.id);
    o.textContent = t.name;
    if (t.color) o.dataset.color = t.color;
    return o;
}

// First-class id from the cached classes list — used as Add-task's
// default class_id (matches the website's `default_class_id` from
// templates). Returns "" when the user has no classes (Personal).
export async function defaultClassId() {
    if (!state.classesPromise) state.classesPromise = api.classes().catch(() => []);
    const classes = await state.classesPromise;
    return classes && classes.length ? String(classes[0].id) : "";
}
