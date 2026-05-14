// Inline "+ New tag" mini-form attached to a tag <select>. When the user
// picks "__new__", a small {name, color, Create} row appears below the
// select; on success the new tag is added to every tag-select on the page,
// selected here, and the form hides again.

import { api, NotAuthenticated } from "../api.js";
import { state } from "../state.js";
import { showLogin } from "../nav.js";

// Idempotent — call after every populate of the tag select. Guarded by a
// dataset flag so we only attach the form once per <select>.
export function bindInlineNewTag(sel) {
    if (!sel) return;
    if (sel.dataset.inlineTagBound === "1") return;
    sel.dataset.inlineTagBound = "1";

    const wrap = document.createElement("div");
    wrap.className = "inline-new-tag hidden";
    wrap.innerHTML = `
        <div class="new-tag-row">
            <input type="text" class="inline-new-tag-name" placeholder="Tag name" maxlength="60">
            <input type="color" class="inline-new-tag-color" value="#A04528">
            <button type="button" class="primary inline-new-tag-create">Create</button>
        </div>
        <div class="status inline-new-tag-status" hidden></div>
    `;
    // Insert after the wrapping <label> so the form sits in the natural
    // form flow. Falls back to inserting after the select itself if the
    // structure is different.
    const labelEl = sel.closest("label");
    const anchor = labelEl || sel;
    if (anchor.parentElement) {
        anchor.parentElement.insertBefore(wrap, anchor.nextSibling);
    }

    const nameInput = wrap.querySelector(".inline-new-tag-name");
    const colorInput = wrap.querySelector(".inline-new-tag-color");
    const createBtn = wrap.querySelector(".inline-new-tag-create");
    const statusEl = wrap.querySelector(".inline-new-tag-status");

    sel.addEventListener("change", () => {
        if (sel.value === "__new__") {
            wrap.classList.remove("hidden");
            nameInput.value = "";
            colorInput.value = "#A04528";
            statusEl.hidden = true;
            nameInput.focus();
        } else {
            wrap.classList.add("hidden");
        }
    });

    createBtn.addEventListener("click", async () => {
        const name = (nameInput.value || "").trim();
        if (!name) {
            statusEl.textContent = "Tag name required.";
            statusEl.className = "status error";
            statusEl.hidden = false;
            nameInput.focus();
            return;
        }
        statusEl.textContent = "Creating…";
        statusEl.className = "status pending";
        statusEl.hidden = false;
        try {
            const tag = await api.createTag({ name, color: colorInput.value });
            // Bust the cache so subsequent fillTagSelect calls re-fetch.
            state.tagsPromise = null;
            const fresh = await api.tags();
            state.tagsPromise = Promise.resolve(fresh);
            // Refill every tag select on the page so the new tag is
            // immediately pickable elsewhere.
            const { fillTagSelect } = await import("../lookups.js");
            document.querySelectorAll("select[name='tag_id']").forEach((s) => {
                fillTagSelect(s, fresh);
            });
            sel.value = String(tag.id);
            wrap.classList.add("hidden");
            statusEl.hidden = true;
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            statusEl.textContent = err.message || "Couldn't create.";
            statusEl.className = "status error";
        }
    });
}
