// Offline-aware hijack of the class-page event-review forms. Without
// this, clicking Add/Remove/Delete on a parsed-syllabus event while
// offline would just fail (native form POST against a dead network),
// the user gets a "no internet" page, and the optimistic UI never
// happens. With this loaded, the forms call fetch() instead, optimistic
// DOM happens up-front, network failures queue into the same offline
// write-queue tasks already use, and a reconnect replay + soft refresh
// reconciles to canonical server state.
//
// Loaded only on class.html (which is where the forms live).
(function () {
    "use strict";
    var Sync = window.CompassSync;
    if (!Sync) return;  // sync.js failed to load — fall back to native POST

    function eventIdFromRow(li) {
        // The row's forms carry the id in their action URL; grab the
        // first one and parse it out.
        var f = li.querySelector('form[action*="/events/"]');
        if (!f) return null;
        var m = f.action.match(/\/events\/(\d+)\//);
        return m ? Number(m[1]) : null;
    }

    function fadeAndRemoveRow(li) {
        // Visual "this row is gone from this section" cue. We don't
        // actually .remove() because a reconnect replay → soft refresh
        // will swap in the canonical server HTML anyway.
        li.style.opacity = "0.35";
        li.style.transition = "opacity 0.15s";
        li.dataset.queuedOffline = "1";
    }

    function markBulkSection(form, msg) {
        // Disable the bulk button and stamp a small status note next to it.
        var btn = form.querySelector("button");
        if (btn) { btn.disabled = true; btn.textContent = msg; }
    }

    function reload() { window.location.reload(); }

    // POST a form via fetch; on success, reload to render the new server
    // state. On 401, navigate to login. On network failure, run the
    // provided `offlineHandler` to queue + optimistically update DOM.
    async function hijack(form, offlineHandler) {
        try {
            var res = await fetch(form.action, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Accept": "text/html" },
            });
            if (res.status === 401) {
                window.location.href = "/login";
                return;
            }
            if (!res.ok && res.status !== 303 && res.status !== 302) {
                throw new Error(res.status + " " + res.statusText);
            }
            reload();
        } catch (err) {
            if (Sync.isOffline && Sync.isOffline(err)) {
                await offlineHandler();
                return;
            }
            alert("Couldn't update: " + (err.message || err));
        }
    }

    function bindRowToggle(form, addToCal) {
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            var li = form.closest("li");
            var id = eventIdFromRow(li);
            if (!id) return;
            hijack(form, async function () {
                await Sync.queueUpsert("events",
                    { id: id, added_to_calendar: addToCal });
                fadeAndRemoveRow(li);
            });
        });
    }

    function bindBulk(form, kind) {
        form.addEventListener("submit", function (e) {
            // Bulk Remove / Delete carry an onsubmit confirm; let it run
            // first (if it returned false, the submit never reaches here).
            e.preventDefault();
            var section = form.closest(".events-subsection") || form.closest(".class-section-body");
            var rows = section ? section.querySelectorAll(".event-list li") : [];
            hijack(form, async function () {
                if (kind === "add-all") {
                    for (var i = 0; i < rows.length; i++) {
                        var id = eventIdFromRow(rows[i]);
                        if (id) await Sync.queueUpsert("events",
                            { id: id, added_to_calendar: true });
                        fadeAndRemoveRow(rows[i]);
                    }
                } else if (kind === "remove-all") {
                    for (var j = 0; j < rows.length; j++) {
                        var id2 = eventIdFromRow(rows[j]);
                        if (id2) await Sync.queueUpsert("events",
                            { id: id2, added_to_calendar: false });
                        fadeAndRemoveRow(rows[j]);
                    }
                } else if (kind === "delete-all") {
                    // delete-all: walk every row in BOTH subsections, not
                    // just one. The section closest() lookup above lands
                    // on class-section-body when the form's in the banner,
                    // so this branch already sees both sets.
                    var allRows = document.querySelectorAll(".class-section-body .event-list li");
                    for (var k = 0; k < allRows.length; k++) {
                        var id3 = eventIdFromRow(allRows[k]);
                        if (id3) await Sync.queueDelete("events", id3);
                        fadeAndRemoveRow(allRows[k]);
                    }
                }
                markBulkSection(form, "Queued — will sync");
            });
        });
    }

    function init() {
        document.querySelectorAll('form[action*="/add-to-calendar"]').forEach(function (f) {
            bindRowToggle(f, true);
        });
        document.querySelectorAll('form[action*="/remove-from-calendar"]').forEach(function (f) {
            bindRowToggle(f, false);
        });
        document.querySelectorAll('form[action*="/events/add-all"]').forEach(function (f) {
            bindBulk(f, "add-all");
        });
        document.querySelectorAll('form[action*="/events/remove-all"]').forEach(function (f) {
            bindBulk(f, "remove-all");
        });
        document.querySelectorAll('form[action*="/events/delete-all"]').forEach(function (f) {
            bindBulk(f, "delete-all");
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
