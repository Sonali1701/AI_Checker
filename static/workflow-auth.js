"use strict";
window.WorkflowAuth = (() => {
  const byId = id => document.getElementById(id);
  let token = "", callbacks = {}, expiryTimer = null, generation = 0;
  function claims(value) {
    try {
      const part = value.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(decodeURIComponent(escape(atob(part))));
    } catch { return {}; }
  }
  function lock(locked) {
    document.body.classList.toggle("auth-locked", locked);
    document.querySelectorAll(".card:not(#authCard)").forEach(card => { card.inert = locked; });
  }
  // The app answers a Google sign-in with its own session token, valid as long as the
  // PW proxy's 7-day pass, so the page is not signed out every hour when Google's
  // token expires. It is kept in localStorage so every tab of this app shares it.
  function remember(value) {
    try { if (value) localStorage.setItem("pwAppSession", value); else localStorage.removeItem("pwAppSession"); } catch {}
    try { sessionStorage.removeItem("pwGoogleToken"); } catch {}
  }
  function saved() {
    try { return localStorage.getItem("pwAppSession") || sessionStorage.getItem("pwGoogleToken") || ""; } catch { return ""; }
  }
  function signOut(reason = "Sign in here with your authorized @pw.live account to continue.") {
    generation += 1;
    clearTimeout(expiryTimer);
    token = "";
    remember("");
    window.google?.accounts?.id?.disableAutoSelect();
    lock(true);
    byId("authTitle").textContent = "Sign in to copy workflow";
    byId("authSub").classList.remove("hidden");
    byId("googleSignIn").classList.remove("hidden");
    byId("signOut").classList.add("hidden");
    byId("authStatus").textContent = reason;
    callbacks.onSignedOut?.();
  }
  async function accept(credential) {
    const version = ++generation;
    try {
      if (!credential) throw new Error("Google did not return a credential. Please sign in again.");
      const info = claims(credential), expires = Number(info.exp || 0) * 1000;
      if (info.email && !info.email.toLowerCase().endsWith("@pw.live")) throw new Error("Use your authorized @pw.live Google account.");
      if (expires && expires <= Date.now() + 5000) throw new Error("Your session expired. Sign in here again.");
      const response = await fetch("/api/auth/status", {headers: {Authorization: `Bearer ${credential}`}});
      let data; try { data = await response.json(); } catch { data = {}; }
      if (version !== generation) return;
      if (!response.ok || !data.allowed) throw new Error(data.detail || "This account is not authorized.");
      token = data.session_token || credential;
      const until = data.session_token && data.expires_at ? Number(data.expires_at) * 1000 : expires;
      if (data.session_token) remember(token); else { try { sessionStorage.setItem("pwGoogleToken", token); } catch {} }
      clearTimeout(expiryTimer);
      if (until) expiryTimer = setTimeout(() => signOut("Your sign-in expired. Sign in here again, then resume your saved run."), Math.min(2147483647, until - Date.now() - 5000));
      lock(false);
      byId("authTitle").textContent = "Account";
      byId("authSub").classList.add("hidden");
      byId("googleSignIn").classList.add("hidden");
      byId("authRetry").classList.add("hidden");
      byId("signOut").classList.remove("hidden");
      byId("authStatus").textContent = `Signed in${info.email ? " as " + info.email : ""}.`;
      await callbacks.onSignedIn?.();
    } catch (error) {
      if (version !== generation) return;
      throw error;
    }
  }
  function waitForGoogle() {
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const timer = setInterval(() => {
        if (window.google?.accounts?.id) { clearInterval(timer); resolve(); }
        else if (Date.now() - started > 12000) { clearInterval(timer); reject(new Error("Google sign-in could not load. Check your connection and click Retry sign-in.")); }
      }, 100);
    });
  }
  async function init(options = callbacks) {
    callbacks = options;
    lock(true);
    byId("authStatus").textContent = "Preparing sign-in…";
    byId("signOut").onclick = () => {
      const current = token;
      if (current) fetch("/api/auth/signout", {method: "POST", headers: {Authorization: `Bearer ${current}`}}).catch(() => {});
      signOut();
    };
    byId("authRetry").onclick = () => init();
    try {
      const response = await fetch("/api/config");
      if (!response.ok) throw new Error("Unable to load sign-in settings. Click Retry sign-in.");
      const config = await response.json();
      if (!config.google_client_id) throw new Error("Sign-in is not configured. Add the PW Google client ID to this app's configuration, then retry.");
      await waitForGoogle();
      window.WorkflowSheets?.configure(config.google_client_id);
      google.accounts.id.initialize({client_id: config.google_client_id, auto_select: false, hd: "pw.live", callback: async result => {
        try { await accept(result.credential || ""); }
        catch (error) { signOut(error.message); }
      }});
      byId("googleSignIn").replaceChildren();
      google.accounts.id.renderButton(byId("googleSignIn"), {theme: "outline", size: "large", text: "signin_with", shape: "pill"});
      const stored = saved();
      if (stored) await accept(stored);
      else byId("authStatus").textContent = "Sign in here to continue.";
    } catch (error) {
      signOut(error.message);
      byId("authRetry").classList.remove("hidden");
    }
  }
  return {init, signOut, invalidate: signOut, getToken: () => token, getSessionId: () => generation};
})();
