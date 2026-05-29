// Offline-aware hijack of the class create + delete forms (web). Without
// this, adding or deleting a class while offline hits a dead network and
// strands the user. With it, those writes queue into the same CompassSync
// offline queue tasks/events already use, and replay via POST /sync on
// reconnect. Online behaviour is unchanged (create reloads, delete goes home).
//
// Loaded on home.html (add-class form) and class.html (delete-class form).
(function () {
    "use strict";
    var Sync = window.CompassSync;
    if (!Sync) return;  // sync.js missing — fall back to native form POST

    // POST the form via fetch. onOk runs on success; offlineHandler runs on a
    // network failure (queue the write). 401 → login; other HTTP error → alert.
    function hijack(form, onOk, offlineHandler) {
        return fetch(form.action, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Accept": "application/json" },
            body: new FormData(form),
        }).then(function (res) {
            if (res.status === 401) { window.location.href = "/login"; return; }
            if (!res.ok && res.status !== 303 && res.status !== 302) {
                throw new Error(res.status + " " + res.statusText);
            }
            return onOk(res);
        }).catch(function (err) {
            if (Sync.isOffline && Sync.isOffline(err)) return offlineHandler();
            alert("Couldn't save: " + (err.message || err));
        });
    }

    // Make an offline-created class selectable right away, so the user can add
    // a task to it offline too. The option's value is the temp client_id; the
    // server resolves it (cross-entity temp-id resolution) on the next push.
    function injectClassOption(localId, code, name) {
        var label = code + (name ? " — " + name : "");
        document.querySelectorAll('select[name="class_id"]').forEach(function (sel) {
            if (sel.querySelector('option[value="' + localId + '"]')) return;
            var opt = document.createElement("option");
            opt.value = localId;
            opt.textContent = label;
            sel.appendChild(opt);
        });
    }

    function bindAddClass(form) {
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            var codeEl = form.querySelector('[name="code"]');
            var nameEl = form.querySelector('[name="name"]');
            var code = ((codeEl && codeEl.value) || "").trim().toUpperCase();
            var name = ((nameEl && nameEl.value) || "").trim();
            if (!code || !name) return;  // native `required` normally guards this
            hijack(form,
                function () { window.location.reload(); },
                function () {
                    return Sync.queueUpsert("classes", { code: code, name: name })
                        .then(function (localId) {
                            injectClassOption(localId, code, name);
                            var btn = form.querySelector('button[type="submit"]');
                            if (btn) btn.textContent = "Saved offline — will sync";
                            form.reset();
                        });
                });
        });
    }

    function bindDeleteClass(form) {
        form.addEventListener("submit", function (e) {
            // The form carries onsubmit="return confirm(...)". A cancelled
            // confirm sets defaultPrevented — bail so we don't delete anyway
            // (same bug class fixed in event-review.js).
            if (e.defaultPrevented) return;
            e.preventDefault();
            var m = (form.getAttribute("action") || "").match(/\/classes\/(\d+)\/delete/);
            if (!m) return;
            var id = Number(m[1]);
            hijack(form,
                function () { window.location.href = "/"; },
                function () {
                    return Sync.queueDelete("classes", id).then(function () {
                        // Don't navigate: the cached home would still list this
                        // class until reconnect, which reads as "it came back."
                        // Mark it deleted in place instead.
                        var note = document.createElement("div");
                        note.className = "offline-banner";
                        note.setAttribute("data-class-deleted", "1");
                        note.textContent = "Class deleted — will sync when you're back online.";
                        (document.querySelector(".class-page") || document.body).prepend(note);
                        var btn = form.querySelector('button[type="submit"]');
                        if (btn) btn.disabled = true;
                    });
                });
        });
    }

    function init() {
        document.querySelectorAll("form.add-class").forEach(bindAddClass);
        document.querySelectorAll('form[action*="/classes/"]').forEach(function (f) {
            if (/\/classes\/\d+\/delete$/.test(f.getAttribute("action") || "")) bindDeleteClass(f);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
