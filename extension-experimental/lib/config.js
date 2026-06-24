// Single source of truth for the Compass server the extension talks to.
//
// A browser extension is static files running in the user's browser — it is
// downloaded from the Chrome Web Store, NOT served by Heroku, so it can't read
// a Heroku environment variable. The production URL is therefore baked in
// here. This is the ONE place to change it.
//
// For local development, temporarily set this to "http://localhost:8000"
// (and add a matching host_permissions entry in manifest.json), then revert
// before packaging for the Web Store.
export const SERVER_URL = "https://dannibar-compass-44cf6055d5e3.herokuapp.com";
