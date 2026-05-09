// Service worker. The popup uses chrome.runtime.sendMessage to ask us
// to open the side panel for the current window — chrome.sidePanel.open()
// requires a user gesture AND a tab context, which the popup doesn't
// always have direct access to. Routing through the worker is the
// reliable pattern.

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "open_side_panel") {
        chrome.windows.getCurrent().then((win) => {
            chrome.sidePanel.open({ windowId: win.id })
                .then(() => sendResponse({ ok: true }))
                .catch((err) => sendResponse({ ok: false, error: String(err) }));
        });
        return true; // keep channel open for async sendResponse
    }
});
