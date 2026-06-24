// Options page: store/edit the Compass server URL. The popup reads this
// via chrome.storage.local; defaults to the production server if unset.
//
// One subtle bit: when the URL points outside the static `host_permissions`
// list in manifest.json, fetch() with credentials will fail silently
// (Chrome blocks the request). We request `optional_host_permissions` at
// save time so the user can extend access to Heroku / other hosts via a
// permission prompt without editing the manifest.

const $ = (sel) => document.querySelector(sel);

const DEFAULT_URL = "https://dannibar-compass.herokuapp.com";

function setStatus(text, kind) {
    const el = $("#status");
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 2000);
}

async function load() {
    const { compass_url } = await chrome.storage.local.get("compass_url");
    $("#compass-url").value = compass_url || DEFAULT_URL;
}

async function save(e) {
    e.preventDefault();
    let url = $("#compass-url").value.trim().replace(/\/+$/, "");
    if (!url) url = DEFAULT_URL;
    try {
        // Validate it's parseable as a URL.
        new URL(url);
    } catch (_) {
        setStatus("Not a valid URL", "error");
        return;
    }
    // Request host permission for this origin so credentialed fetches go
    // through. Required when the user moves off the bundled localhost
    // entries in manifest.json (e.g., to a Heroku domain).
    const origin = new URL(url).origin + "/*";
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) {
        setStatus("Permission denied — Compass at this URL won't load", "error");
        return;
    }
    await chrome.storage.local.set({ compass_url: url });
    setStatus("Saved ✓", "success");
}

document.getElementById("options-form").addEventListener("submit", save);
load();
