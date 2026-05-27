(function () {
    "use strict";

    function escapeHTML(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    function fmtSize(b) {
        if (b < 1024) return b + " B";
        var k = b / 1024;
        if (k < 1024) return Math.round(k) + " KB";
        return (k / 1024).toFixed(1) + " MB";
    }

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

    function wireZone(zone) {
        var input = zone.querySelector('input[type="file"]');
        if (!input) return;
        var picked = zone.querySelector(".drop-picked");
        var accept = input.accept || "";

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
                // Older Safari fallback: can't programmatically set input.files;
                // user has to use click-to-choose instead of drag.
                return;
            }
            update();
        }

        input.addEventListener("change", update);

        ["dragenter", "dragover"].forEach(function (ev) {
            zone.addEventListener(ev, function (e) {
                e.preventDefault();
                zone.classList.add("drag");
            });
        });
        zone.addEventListener("dragleave", function (e) {
            // dragleave fires when entering child elements too; only clear when truly leaving
            if (!zone.contains(e.relatedTarget)) zone.classList.remove("drag");
        });
        zone.addEventListener("drop", function (e) {
            e.preventDefault();
            zone.classList.remove("drag");
            var files = e.dataTransfer && e.dataTransfer.files;
            if (files && files.length) setFile(files[0]);
        });

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
    }

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
                // localStorage may be unavailable in private mode; toggle still works for the session.
            }
            setLabel();
        });
    }

    function wireSubmitGuard(form) {
        form.addEventListener("submit", function (e) {
            if (form.dataset.submitting === "1") {
                e.preventDefault();
                return;
            }
            var input = form.querySelector('input[type="file"]');
            if (input && input.required && !(input.files && input.files.length)) {
                // Let the browser surface its native "please pick a file" prompt;
                // don't latch the guard.
                return;
            }
            form.dataset.submitting = "1";
            var btn = form.querySelector('button[type="submit"], input[type="submit"]');
            if (btn) {
                btn.disabled = true;
                btn.setAttribute("aria-busy", "true");
                if (!btn.dataset.originalLabel) {
                    btn.dataset.originalLabel = btn.textContent;
                }
                btn.textContent = "Uploading…";
            }
        });
    }

    function init() {
        document.querySelectorAll("[data-upload-zone]").forEach(wireZone);
        document.querySelectorAll("[data-upload-zone]").forEach(function (zone) {
            var form = zone.closest("form");
            if (form && !form.dataset.submitGuard) {
                form.dataset.submitGuard = "1";
                wireSubmitGuard(form);
            }
        });
        wireThemeToggle();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
