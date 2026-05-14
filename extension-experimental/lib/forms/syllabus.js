// Syllabus upload + parse polling. Drag-drop or click-to-pick a PDF;
// POST to /syllabus → poll /syllabus/{id}/status.json every 2s until done
// or error. On success, drill into the new class.

import { api, NotAuthenticated } from "../api.js";
import { resetCaches } from "../state.js";
import { showLogin, showSecondary, returnToList } from "../nav.js";
import { ensureLookups } from "../lookups.js";
import { showSettings, setXaiStatus } from "./settings.js";
import { showClassDetail } from "../views/classes.js";

const $ = (sel) => document.querySelector(sel);

let pickedFile = null;
let pollTimer = null;

export function showSyllabusUpload() {
    showSecondary("#syllabus-upload-view");
    pickedFile = null;
    $("#syllabus-file-input").value = "";
    const picked = $("#syllabus-picked");
    picked.hidden = true;
    picked.textContent = "";
    $("#syllabus-upload-btn").disabled = true;
    setStatus("", "");
    $("#syllabus-upload-stage").hidden = false;
    $("#syllabus-parse-stage").hidden = true;
    $("#syllabus-parse-actions").hidden = true;
}

function hideSyllabusUpload() {
    if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
    }
    returnToList();
}

function setStatus(text, kind) {
    const el = $("#syllabus-upload-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

function pickFile(file) {
    if (!file) return;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
        setStatus("Only PDF files are accepted.", "error");
        return;
    }
    if (file.size > 25 * 1024 * 1024) {
        setStatus("File is too big (25 MB max).", "error");
        return;
    }
    pickedFile = file;
    const picked = $("#syllabus-picked");
    picked.textContent = file.name + " · " + Math.round(file.size / 1024) + " KB";
    picked.hidden = false;
    $("#syllabus-upload-btn").disabled = false;
    setStatus("", "");
}

export function bindSyllabus() {
    const drop = $("#syllabus-drop");
    const fileInput = $("#syllabus-file-input");
    const uploadBtn = $("#syllabus-upload-btn");

    ["dragenter", "dragover"].forEach((evName) =>
        drop.addEventListener(evName, (e) => {
            e.preventDefault();
            drop.classList.add("drop-active");
        })
    );
    ["dragleave", "drop"].forEach((evName) =>
        drop.addEventListener(evName, (e) => {
            e.preventDefault();
            drop.classList.remove("drop-active");
        })
    );
    drop.addEventListener("drop", (e) => {
        const f = e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) pickFile(f);
    });
    drop.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", (e) => pickFile(e.target.files[0]));

    uploadBtn.addEventListener("click", async () => {
        if (!pickedFile) return;
        setStatus("Uploading…", "pending");
        try {
            const r = await api.uploadSyllabus(pickedFile);
            $("#syllabus-upload-stage").hidden = true;
            $("#syllabus-parse-stage").hidden = false;
            $("#syllabus-parse-actions").hidden = true;
            $("#syllabus-parse-status").textContent = "Parsing…";
            poll(r.syllabus_id, r.class_id);
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); hideSyllabusUpload(); return; }
            if ((err.message || "").includes("need_key")) {
                hideSyllabusUpload();
                showSettings();
                setXaiStatus("Set your xAI API key first to parse syllabi.", "error");
                return;
            }
            setStatus(err.message || "Couldn't upload.", "error");
        }
    });

    $("#syllabus-back").addEventListener("click", hideSyllabusUpload);
    $("#syllabus-retry").addEventListener("click", () => showSyllabusUpload());
}

function poll(syllabusId, classId) {
    api.syllabusStatus(syllabusId).then((s) => {
        const status = s && s.status;
        const statusEl = $("#syllabus-parse-status");
        if (status === "done") {
            statusEl.textContent = "Done ✓";
            resetCaches();
            ensureLookups().then(() => {
                hideSyllabusUpload();
                showClassDetail(classId);
            });
            return;
        }
        if (status && status.startsWith("error")) {
            statusEl.textContent = status;
            $("#syllabus-parse-actions").hidden = false;
            return;
        }
        if (status === "missing") {
            statusEl.textContent = "Syllabus disappeared. Try again.";
            $("#syllabus-parse-actions").hidden = false;
            return;
        }
        statusEl.textContent = "Parsing… (" + (status || "pending") + ")";
        pollTimer = setTimeout(() => poll(syllabusId, classId), 2000);
    }).catch((err) => {
        if (err instanceof NotAuthenticated) { showLogin(); hideSyllabusUpload(); return; }
        $("#syllabus-parse-status").textContent = "Status fetch failed: " + err.message;
        $("#syllabus-parse-actions").hidden = false;
    });
}
