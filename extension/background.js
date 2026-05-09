// Service worker. Three jobs:
//   1. Make the toolbar icon open the side panel directly (no popup).
//   2. Register the right-click "Add to Compass" context menu items and
//      handle their clicks — POSTs the captured page/selection to
//      /tasks via the user's existing Compass session cookie.
//   3. A legacy message relay for opening the side panel from anywhere
//      (kept for older Chrome builds where setPanelBehavior isn't honored).

const DEFAULT_COMPASS_URL = "http://localhost:8000";

// ---- Side-panel-on-action --------------------------------------------------
// Chrome 116+ honors this; clicking the toolbar icon opens the side panel
// directly. The side panel itself handles login + every other surface, so
// there's no popup to bounce through. Wrapped in try/catch so older builds
// fall through to the chrome.action.onClicked path below.
chrome.runtime.onInstalled.addListener(() => {
    try {
        chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
    } catch (_) { /* older Chrome: handled by onClicked fallback */ }
});

chrome.action.onClicked.addListener(async (tab) => {
    // Fallback for builds where setPanelBehavior didn't take effect.
    try {
        const win = tab && tab.windowId
            ? { windowId: tab.windowId }
            : { windowId: (await chrome.windows.getCurrent()).id };
        await chrome.sidePanel.open(win);
    } catch (err) {
        console.error("sidePanel.open failed:", err);
    }
});

// Legacy relay — anything inside the extension can request a side-panel
// open via chrome.runtime.sendMessage({type: 'open_side_panel'}). Not used
// by the main flow anymore but kept so options.html or future surfaces
// don't have to know about chrome.sidePanel directly.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "open_side_panel") {
        chrome.windows.getCurrent().then((win) => {
            chrome.sidePanel.open({ windowId: win.id })
                .then(() => sendResponse({ ok: true }))
                .catch((err) => sendResponse({ ok: false, error: String(err) }));
        });
        return true; // keep the channel open for async sendResponse
    }
});

// ---- Context menus -------------------------------------------------------
// Register on install AND on every browser startup. Chrome persists menu
// items across sessions, so this is mostly belt-and-suspenders, but it
// makes a fresh install work without the user having to navigate first.
function registerMenus() {
    chrome.contextMenus.removeAll(() => {
        chrome.contextMenus.create({
            id: "compass-add-selection",
            title: 'Add "%s" to Compass',
            contexts: ["selection"],
        });
        chrome.contextMenus.create({
            id: "compass-add-page",
            title: "Add this page to Compass",
            contexts: ["page", "link"],
        });
    });
}
chrome.runtime.onInstalled.addListener(registerMenus);
chrome.runtime.onStartup.addListener(registerMenus);

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
    let title;
    if (info.menuItemId === "compass-add-selection") {
        // Selected text becomes the title — capped to keep DB rows small.
        title = (info.selectionText || "").trim().slice(0, 200);
    } else if (info.menuItemId === "compass-add-page") {
        // Page menu: prefer the link's text/URL when invoked on a link,
        // else fall back to the tab's title.
        title = info.linkText || info.linkUrl || (tab && tab.title) || "";
        title = title.trim().slice(0, 200);
    } else {
        return;
    }
    if (!title) {
        flashBadge("!", "#B23A3A");
        return;
    }
    const sourceUrl = info.linkUrl || info.pageUrl || (tab && tab.url) || "";
    const notes = sourceUrl ? `From: ${sourceUrl}` : "";

    try {
        await postQuickTask(title, notes);
        flashBadge("✓", "#5C8A3A");
    } catch (err) {
        // Most likely cause: user not logged into Compass in any tab. The
        // red badge is the signal — they can click the icon, log in, retry.
        flashBadge("!", "#B23A3A");
        console.error("Compass quick-add failed:", err);
    }
});

async function postQuickTask(title, notes) {
    const base = await compassUrl();
    const fd = new FormData();
    fd.append("title", title);
    if (notes) fd.append("notes", notes);
    const r = await fetch(base + "/tasks", {
        method: "POST",
        body: fd,
        credentials: "include",
        headers: { Accept: "application/json" },
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json().catch(() => null);
}

async function compassUrl() {
    const { compass_url } = await chrome.storage.local.get("compass_url");
    return (compass_url || DEFAULT_COMPASS_URL).replace(/\/+$/, "");
}

// Toolbar badge flash — green check for success, red bang for failure.
// 2.5s and gone. Cheaper than chrome.notifications (no icons / perms).
function flashBadge(text, color) {
    chrome.action.setBadgeBackgroundColor({ color });
    chrome.action.setBadgeText({ text });
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2500);
}
