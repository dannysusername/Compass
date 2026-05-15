// Settings surface. Footer ⚙ opens this. Sections: Account (email +
// Logout), Timezone (display), xAI key (set/clear), Calendar subscription
// (webcal link + URL + regenerate), Manage tags (rename/recolor/delete +
// add). Source of truth = state.me; refreshed after any settings mutation.

import { api, NotAuthenticated } from "../api.js";
import { state, clearForLogout, resetCaches } from "../state.js";
import { showLogin, showSecondary, returnToList } from "../nav.js";

const $ = (sel) => document.querySelector(sel);

export function showSettings() {
    showSecondary("#settings-view");
    populateSettings();
}

function hideSettings() { returnToList(); }

function populateSettings() {
    if (!state.me) return;
    $("#settings-email").textContent = state.me.email || "";
    $("#settings-tz").textContent = state.me.timezone || "—";
    const me = state.me;
    const usage = $("#settings-parse-usage");
    const xaiSet = $("#settings-xai-status");
    if (me.xai_api_key_set) {
        usage.textContent = "Using your own xAI key — unlimited syllabus parses.";
        xaiSet.textContent = "Key set: " + (me.xai_api_key_masked || "");
    } else if (me.free_parses_remaining === null) {
        // No own key, but uncapped → admin granted unlimited.
        usage.textContent = "Unlimited syllabus parses — granted by an admin.";
        xaiSet.textContent = "No personal key needed.";
    } else if (me.server_key_available) {
        const used = me.free_parses_used || 0;
        const limit = me.free_parse_limit || 0;
        const left = me.free_parses_remaining || 0;
        usage.textContent =
            `Free syllabus parses: ${used} of ${limit} used · ${left} left` +
            (left <= 0 ? " — add your own xAI key below for unlimited." : ".");
        xaiSet.textContent = "No personal key set (using the free pool).";
    } else {
        usage.textContent =
            "Free parsing isn't configured on this server — add your own xAI key to parse syllabi.";
        xaiSet.textContent = "No key set. Syllabus upload requires one.";
    }
    const urls = state.me.calendar_urls || {};
    const webcal = $("#settings-cal-webcal");
    webcal.href = urls.webcal_url || "#";
    $("#settings-cal-url").textContent = urls.https_url || "";
    populateManageTags();
}

export function setXaiStatus(text, kind) {
    const el = $("#settings-xai-status-line");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

function setManageTagsStatus(text, kind) {
    const el = $("#settings-tags-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

async function populateManageTags() {
    const ul = $("#settings-tags-list");
    ul.innerHTML = "";
    let tags;
    try {
        state.tagsPromise = api.tags();
        tags = await state.tagsPromise;
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        return;
    }
    tags.forEach((t) => ul.appendChild(renderManageTagRow(t)));
}

function renderManageTagRow(t) {
    const li = document.createElement("li");
    li.dataset.tagId = String(t.id);
    const swatch = document.createElement("input");
    swatch.type = "color";
    swatch.className = "manage-tag-swatch";
    swatch.value = t.color || "#A04528";
    swatch.title = "Click to change color";
    swatch.addEventListener("input", async () => {
        try {
            await api.editTag(t.id, { name: t.name, color: swatch.value });
            t.color = swatch.value;
            state.tagsPromise = null;
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't recolor: " + err.message);
        }
    });
    if (t.is_system) {
        const sys = document.createElement("span");
        sys.className = "manage-tag-system";
        sys.textContent = "sys";
        li.appendChild(sys);
    }
    const name = document.createElement("input");
    name.type = "text";
    name.className = "manage-tag-name";
    name.value = t.name;
    name.addEventListener("blur", async () => {
        const newName = (name.value || "").trim();
        if (!newName || newName === t.name) {
            name.value = t.name;
            return;
        }
        try {
            await api.editTag(t.id, { name: newName, color: swatch.value });
            t.name = newName;
            state.tagsPromise = null;
        } catch (err) {
            name.value = t.name;
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't rename: " + err.message);
        }
    });
    name.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); name.blur(); }
        if (e.key === "Escape") { name.value = t.name; name.blur(); }
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "manage-tag-del" + (t.is_system ? " is-system" : "");
    del.setAttribute("aria-label", `Delete ${t.name}`);
    del.textContent = "×";
    del.addEventListener("click", async () => {
        if (t.is_system) return;
        if (!confirm(`Delete tag "${t.name}"? Tasks using it will lose the tag.`)) return;
        try {
            await api.deleteTag(t.id);
            li.remove();
            state.tagsPromise = null;
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't delete: " + err.message);
        }
    });
    li.appendChild(swatch);
    li.appendChild(name);
    li.appendChild(del);
    return li;
}

export function bindSettings() {
    $("#settings-back").addEventListener("click", hideSettings);
    $("#open-settings").addEventListener("click", showSettings);

    $("#settings-logout").addEventListener("click", async () => {
        try { await api.logout(); } catch (_) { /* clear local state regardless */ }
        clearForLogout();
        hideSettings();
        showLogin();
    });

    const xaiForm = $("#settings-xai-form");
    xaiForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const key = (xaiForm.xai_api_key.value || "").trim();
        setXaiStatus("Saving…", "pending");
        try {
            const r = await api.saveXaiKey(key);
            if (state.me) {
                state.me.xai_api_key_set = !!(r && r.xai_api_key_set);
                state.me.xai_api_key_masked = r && r.xai_api_key_masked;
            }
            xaiForm.reset();
            setXaiStatus("Saved ✓", "success");
            setTimeout(() => setXaiStatus("", ""), 800);
            populateSettings();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            setXaiStatus(err.message || "Couldn't save.", "error");
        }
    });

    $("#settings-xai-clear").addEventListener("click", async () => {
        setXaiStatus("Clearing…", "pending");
        try {
            const r = await api.saveXaiKey("");
            if (state.me) {
                state.me.xai_api_key_set = !!(r && r.xai_api_key_set);
                state.me.xai_api_key_masked = null;
            }
            xaiForm.reset();
            setXaiStatus("Cleared", "success");
            setTimeout(() => setXaiStatus("", ""), 800);
            populateSettings();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            setXaiStatus(err.message || "Couldn't clear.", "error");
        }
    });

    $("#settings-cal-regen").addEventListener("click", async () => {
        if (!confirm("Regenerate your calendar token? Existing subscriptions will stop working until you re-subscribe with the new URL.")) return;
        try {
            const r = await api.regenerateCalendarToken();
            if (state.me) {
                state.me.calendar_token = r.calendar_token;
                state.me.calendar_urls = r.calendar_urls;
            }
            populateSettings();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't regenerate: " + err.message);
        }
    });

    const newTagForm = $("#settings-new-tag-form");
    newTagForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = (newTagForm.name.value || "").trim();
        const color = newTagForm.color.value || "#A04528";
        if (!name) return;
        setManageTagsStatus("Creating…", "pending");
        try {
            await api.createTag({ name, color });
            newTagForm.reset();
            newTagForm.color.value = "#A04528";
            resetCaches();
            setManageTagsStatus("Added ✓", "success");
            setTimeout(() => setManageTagsStatus("", ""), 800);
            populateManageTags();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            setManageTagsStatus(err.message || "Couldn't add.", "error");
        }
    });
}
