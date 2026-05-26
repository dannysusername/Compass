// Compass service worker — Step 1: offline VIEWING.
//
// Strategy:
//   - Navigations (HTML pages): network-first. Online → always fresh, and we
//     stash a copy. Offline → serve the last saved copy of that page, or the
//     offline fallback if it was never visited online.
//   - Static assets (/static/*): stale-while-revalidate — instant from cache,
//     refreshed in the background.
//   - Other same-origin GETs (e.g. *.json): network-first with cache fallback.
//   - Only GET is touched; POST/PUT/etc. always go straight to the network
//     (no offline writes yet — that's Step 2).
//
// Bump CACHE when the precache list or strategy changes to retire old caches.

const CACHE = "compass-v5";
const OFFLINE_URL = "/static/offline.html";
const PRECACHE = [
  OFFLINE_URL,
  "/static/styles.css",
  "/static/sync.js",
  "/static/todo.js",
  "/static/modal.js",
  "/static/upload.js",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const fresh = await fetch(request);
    if (fresh && fresh.ok) cache.put(request, fresh.clone());
    return fresh;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    // For a page navigation with nothing cached, show the offline shell.
    if (request.mode === "navigate") return cache.match(OFFLINE_URL);
    throw err;
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((resp) => { if (resp && resp.ok) cache.put(request, resp.clone()); return resp; })
    .catch(() => null);
  return cached || network || fetch(request);
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;                 // writes pass through
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;      // don't touch cross-origin

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
  } else if (url.pathname.startsWith("/static/")) {
    event.respondWith(staleWhileRevalidate(request));
  } else {
    event.respondWith(networkFirst(request));
  }
});
