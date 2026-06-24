// Login + signup. Both surfaces post to the Compass server using the
// session-cookie auth path; on success they verify via /me.json and
// transition into the app. Server URL hint surfaces the configured base
// so users pointing at the wrong host can fix it before guessing why
// their password "doesn't work".

import { api, NotAuthenticated } from "../api.js";
import { state } from "../state.js";
import { showApp, showLogin, showSignup } from "../nav.js";
import { ensureLookups } from "../lookups.js";
import { autoSaveTimezone } from "../behaviors/timezone.js";
import { load } from "./index.js";

const $ = (sel) => document.querySelector(sel);

export function bindLogin() {
    const loginForm = $("#login-form");
    const signupForm = $("#signup-form");

    // Surface the configured server URL on the login screen.
    api.base().then((url) => {
        const el = $("#login-server-url");
        if (el) el.textContent = url;
    });

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const email = (loginForm.email.value || "").trim();
        const password = loginForm.password.value || "";
        if (!email || !password) return;
        setLoginStatus("Signing in…", "pending");
        try {
            // api.login authenticates and stores the per-user bearer token;
            // every later request rides that token (the session cookie can't
            // travel to a chrome-extension origin).
            await api.login(email, password);
            const me = await api.me().catch(() => null);
            if (!me) {
                setLoginStatus("Couldn't sign in. Double-check the server URL.", "error");
                return;
            }
            state.me = me;
            setLoginStatus("", "");
            loginForm.reset();
            showApp();
            await Promise.all([ensureLookups(), autoSaveTimezone()]);
            await load();
        } catch (err) {
            // A 401 from /login surfaces as NotAuthenticated (wrong creds);
            // anything else is a connectivity / server-URL problem.
            if (err instanceof NotAuthenticated) {
                setLoginStatus("Wrong email or password.", "error");
            } else {
                setLoginStatus("Couldn't sign in: " + err.message, "error");
            }
        }
    });

    signupForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const email = (signupForm.email.value || "").trim();
        const pw = signupForm.password.value || "";
        const confirmPw = signupForm.password_confirm.value || "";
        if (!email || !pw) return;
        if (pw !== confirmPw) {
            setSignupStatus("Passwords don't match.", "error");
            return;
        }
        setSignupStatus("Creating account…", "pending");
        try {
            await api.signup(email, pw);
            const me = await api.me().catch(() => null);
            if (!me) {
                setSignupStatus("Account created but couldn't sign in. Try logging in.", "error");
                return;
            }
            state.me = me;
            setSignupStatus("", "");
            signupForm.reset();
            showApp();
            await Promise.all([ensureLookups(), autoSaveTimezone()]);
            await load();
        } catch (err) {
            setSignupStatus(err.message || "Couldn't sign up.", "error");
        }
    });

    $("#login-open-options").addEventListener("click", (e) => {
        e.preventDefault();
        chrome.runtime.openOptionsPage();
    });
    $("#login-open-signup").addEventListener("click", (e) => {
        e.preventDefault();
        showSignup();
    });
    $("#signup-back-to-login").addEventListener("click", (e) => {
        e.preventDefault();
        showLogin();
    });
}

function setLoginStatus(text, kind) {
    const el = $("#login-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

function setSignupStatus(text, kind) {
    const el = $("#signup-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

export { setLoginStatus };
