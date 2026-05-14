// Classes tab + class-detail drill-down. The list is a vertical card
// stack; each card opens the detail surface (syllabus iframe, documents,
// tasks, events, delete-class). "+ Add class" and "+ Upload syllabus"
// sit above the list and stay visible on the empty state so a fresh
// user can take action immediately.

import { api, NotAuthenticated } from "../api.js";
import { state, resetCaches } from "../state.js";
import { showLogin, showSecondary, returnToList, setFabHidden } from "../nav.js";
import { renderRow } from "./row.js";
import { ensureLookups } from "../lookups.js";
import { showAddClass } from "../forms/add-class.js";
import { showSyllabusUpload } from "../forms/syllabus.js";
import { showSettings, setXaiStatus } from "../forms/settings.js";
import { load } from "./index.js";

const $ = (sel) => document.querySelector(sel);

export async function loadClasses() {
    const target = $("#content");
    target.innerHTML = '<p class="muted loading">Loading…</p>';
    try {
        const classes = await api.classes();
        $("#today-date").textContent = "Classes";
        renderClassesList(target, classes);
    } catch (err) {
        if (err instanceof NotAuthenticated) showLogin();
        else {
            target.innerHTML = "";
            const p = document.createElement("p");
            p.className = "muted error";
            p.textContent = "Couldn't load: " + err.message;
            target.appendChild(p);
        }
    }
}

function renderClassesList(target, classes) {
    target.innerHTML = "";
    const actions = document.createElement("div");
    actions.className = "classes-actions";
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.textContent = "+ Add class";
    addBtn.addEventListener("click", showAddClass);
    const uploadBtn = document.createElement("button");
    uploadBtn.type = "button";
    uploadBtn.textContent = "+ Upload syllabus";
    if (state.me && !state.me.xai_api_key_set) {
        uploadBtn.classList.add("is-disabled");
        uploadBtn.title = "Set your xAI API key in Settings first";
    }
    uploadBtn.addEventListener("click", () => {
        if (state.me && !state.me.xai_api_key_set) {
            showSettings();
            setXaiStatus("Set your xAI API key first to parse syllabi.", "error");
            return;
        }
        showSyllabusUpload();
    });
    actions.appendChild(addBtn);
    actions.appendChild(uploadBtn);
    target.appendChild(actions);

    if (!classes || classes.length === 0) {
        const p = document.createElement("p");
        p.className = "muted empty";
        p.textContent = "No classes yet. Tap + Add class above to start.";
        target.appendChild(p);
        return;
    }
    const ul = document.createElement("ul");
    ul.className = "classes-list";
    classes.forEach((c) => {
        const li = document.createElement("li");
        li.className = "class-card";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.setAttribute("aria-label", `Open ${c.code}`);
        const code = document.createElement("span");
        code.className = "class-card-code";
        code.textContent = c.code;
        btn.appendChild(code);
        if (c.name) {
            const name = document.createElement("span");
            name.className = "class-card-name";
            name.textContent = c.name;
            btn.appendChild(name);
        }
        btn.addEventListener("click", () => showClassDetail(c.id));
        li.appendChild(btn);
        ul.appendChild(li);
    });
    target.appendChild(ul);
}

// ---- Class detail ----

export async function showClassDetail(classId) {
    showSecondary("#class-detail");
    state.currentClassId = classId;

    const tasksUl = $("#class-detail-tasks");
    const eventsUl = $("#class-detail-events");
    const docsUl = $("#class-detail-docs");
    tasksUl.innerHTML = "";
    eventsUl.innerHTML = "";
    docsUl.innerHTML = "";
    $("#class-detail-tasks-empty").hidden = true;
    $("#class-detail-events-empty").hidden = true;
    $("#class-detail-docs-empty").hidden = true;
    $("#class-detail-syllabus-section").hidden = true;
    $("#class-detail-code").textContent = "Loading…";
    $("#class-detail-name").textContent = "";

    try {
        const data = await api.classDetail(classId);
        $("#class-detail-code").textContent = data.class.code;
        $("#class-detail-name").textContent = data.class.name || "";

        if (data.syllabus && data.syllabus.filename) {
            const url = await api.fileUrl(data.syllabus.filename);
            $("#class-detail-pdf").src = url;
            const openTab = $("#class-pdf-open-tab");
            const dl = $("#class-pdf-download");
            openTab.href = url;
            // Anchor click in a chrome-extension:// origin doesn't actually
            // navigate the panel — open in a real tab instead.
            openTab.onclick = (e) => {
                e.preventDefault();
                chrome.tabs.create({ url });
            };
            dl.href = url;
            dl.setAttribute("download", data.syllabus.filename);
            $("#class-detail-syllabus-section").hidden = false;
        }

        if (data.documents && data.documents.length) {
            for (const d of data.documents) {
                docsUl.appendChild(await renderDocRow(d));
            }
        } else {
            $("#class-detail-docs-empty").hidden = false;
        }

        if (data.tasks.length === 0) $("#class-detail-tasks-empty").hidden = false;
        else data.tasks.forEach((t) => tasksUl.appendChild(renderRow(t, false)));

        if (data.events.length === 0) $("#class-detail-events-empty").hidden = false;
        else data.events.forEach((ev) => eventsUl.appendChild(renderRow(ev, false)));
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            return;
        }
        $("#class-detail-code").textContent = "Couldn't load";
        $("#class-detail-name").textContent = err.message;
    }
}

export function hideClassDetail() {
    returnToList();
}

async function renderDocRow(d) {
    const li = document.createElement("li");
    li.dataset.docId = String(d.id);
    const url = await api.fileUrl(d.filename);
    const link = document.createElement("a");
    link.className = "doc-link";
    link.href = url;
    link.textContent = d.title || d.filename;
    link.title = d.filename;
    link.addEventListener("click", (e) => {
        e.preventDefault();
        chrome.tabs.create({ url });
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "doc-del";
    del.setAttribute("aria-label", `Delete ${d.title || d.filename}`);
    del.textContent = "×";
    del.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete "${d.title || d.filename}"?`)) return;
        try {
            await api.deleteDoc(d.id);
            li.remove();
            const remaining = $("#class-detail-docs").querySelectorAll("li").length;
            if (remaining === 0) $("#class-detail-docs-empty").hidden = false;
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't delete: " + err.message);
        }
    });
    li.appendChild(link);
    li.appendChild(del);
    return li;
}

// Wire one-shot at boot — class-detail back button, doc upload form,
// delete-class button.
export function bindClassDetail() {
    $("#class-detail-back").addEventListener("click", hideClassDetail);

    const docForm = $("#class-detail-doc-upload");
    docForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!state.currentClassId) return;
        const fileInput = $("#class-detail-doc-file");
        const file = fileInput.files[0];
        if (!file) return;
        const title = (docForm.title.value || "").trim();
        setDocStatus("Uploading…", "pending");
        try {
            await api.uploadDoc(state.currentClassId, file, title);
            docForm.reset();
            setDocStatus("Uploaded ✓", "success");
            setTimeout(() => setDocStatus("", ""), 800);
            await showClassDetail(state.currentClassId);
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            setDocStatus("Couldn't upload: " + err.message, "error");
        }
    });

    $("#class-detail-delete").addEventListener("click", async () => {
        if (!state.currentClassId) return;
        const code = $("#class-detail-code").textContent || "this class";
        if (!confirm(`Delete ${code} and everything in it?`)) return;
        try {
            await api.deleteClass(state.currentClassId);
            resetCaches();
            hideClassDetail();
            await ensureLookups();
            await load();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't delete class: " + err.message);
        }
    });
}

function setDocStatus(text, kind) {
    const el = $("#class-detail-doc-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}
