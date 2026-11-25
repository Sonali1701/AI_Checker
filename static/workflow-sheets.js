"use strict";
window.WorkflowSheets = (() => {
  const scope = "https://www.googleapis.com/auth/spreadsheets";
  let clientId = "", accessToken = "", expiresAt = 0, timer = null, cancel = null, generation = 0;
  const status = (value, error = false) => {
    const node = document.getElementById("sheetAccessStatus");
    node.textContent = value; node.classList.toggle("error", error);
  };
  function reset() {
    generation += 1; clearTimeout(timer); accessToken = ""; expiresAt = 0;
    cancel?.(new Error("Your sign-in changed. Connect Google Sheets again.")); cancel = null;
    status("Connect the Google account that can edit the student sheet to save copy statuses.");
  }
  function getToken() {
    if (accessToken && Date.now() >= expiresAt) { reset(); status("Google Sheets connection expired. Connect again to save pending copy statuses.", true); }
    return accessToken;
  }
  function connect() {
    if (!window.WorkflowAuth.getToken()) return Promise.reject(new Error("Sign in to the workflow first."));
    if (cancel) return Promise.reject(new Error("Complete or close the Google connection window first."));
    if (!clientId || !window.google?.accounts?.oauth2) return Promise.reject(new Error("Google Sheets connection is unavailable. Refresh this page or upload an Excel file."));
    const version = ++generation, session = window.WorkflowAuth.getSessionId();
    status("Choose the Google account that can edit the student sheet…");
    return new Promise((resolve, reject) => {
      let settled = false;
      async function finish(error, response) {
        if (settled) return; settled = true;
        if (version !== generation || session !== window.WorkflowAuth.getSessionId()) { reject(new Error("Your sign-in changed. Connect Google Sheets again.")); return; }
        if (error) { cancel = null; status(error.message, true); reject(error); return; }
        const seconds = Number(response.expires_in);
        if (!response.access_token || !Number.isFinite(seconds) || seconds <= 30 || !google.accounts.oauth2.hasGrantedAllScopes(response, scope)) {
          cancel = null; const denied = new Error("Google Sheets edit permission was not granted. Connect again to save statuses, or upload an Excel file."); status(denied.message, true); reject(denied); return;
        }
        try {
          await api("/api/workflow/sheets/connect", {method: "POST",
            headers: {"Content-Type": "application/json", "X-Google-Sheets-Token": response.access_token},
            body: JSON.stringify({expires_in: seconds - 30})});
          if (version !== generation || session !== window.WorkflowAuth.getSessionId()) throw new Error("Your sign-in changed. Connect Google Sheets again.");
          accessToken = response.access_token; expiresAt = Date.now() + (seconds - 30) * 1000;
          clearTimeout(timer); timer = setTimeout(() => { reset(); status("Google Sheets connection expired. Connect again to save pending copy statuses.", true); }, Math.min(2147483647, expiresAt - Date.now()));
          cancel = null; status("Google Sheets connected. Copy statuses will be saved to the student tab; reconnect here if access expires."); resolve();
        } catch (error) { if (version === generation) { cancel = null; status(error.message, true); } reject(error); }
      }
      cancel = error => { if (!settled) { settled = true; reject(error); } };
      try {
        const client = google.accounts.oauth2.initTokenClient({
          client_id: clientId, scope, include_granted_scopes: false,
          callback: response => finish(response.error ? new Error("Google Sheets access was declined. Connect again or upload an Excel file.") : null, response),
          error_callback: error => finish(new Error(error.type === "popup_failed_to_open" ? "Allow the Google sign-in popup, then click Connect Google Sheets again." : "Google connection window closed. Click Connect Google Sheets to try again."))
        });
        client.requestAccessToken({prompt: "select_account"});
      } catch { finish(new Error("Unable to open Google Sheets connection. Refresh the page and try again.")); }
    });
  }
  return {configure: id => { clientId = id || ""; }, connect, getToken, reset};
})();
