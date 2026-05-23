"""PWA wiring: manifest, service worker, icons, and the <head> link.

Step 1 (installable app + offline viewing). These are API-level smoke
checks — the real offline behavior is exercised in a browser manually /
via the SW; here we just guard that the files are served correctly and
linked, so a refactor can't silently un-PWA the app.
"""


def test_manifest_served_with_correct_type(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert "manifest" in r.headers["content-type"]
    data = r.json()
    assert data["name"] == "Compass"
    assert data["start_url"] == "/"
    assert data["display"] == "standalone"
    # installability needs a 512 icon + a maskable one
    assert any(i["sizes"] == "512x512" and "any" in i["purpose"] for i in data["icons"])
    assert any("maskable" in i["purpose"] for i in data["icons"])


def test_service_worker_served_at_root_scope(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    # served from / so its scope covers the whole app
    assert r.headers.get("service-worker-allowed") == "/"
    # sanity: it's the real SW, not a stray file
    assert "addEventListener" in r.text and "caches" in r.text


def test_pwa_icons_served(client):
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        r = client.get(f"/static/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"] == "image/png"


def test_pages_link_manifest_and_apple_icon(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert 'rel="manifest"' in r.text
    assert "/manifest.webmanifest" in r.text
    assert 'rel="apple-touch-icon"' in r.text
