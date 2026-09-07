"use strict";
const $ = (id) => document.getElementById(id);
const ACCEPT = [".pdf", ".png", ".jpg", ".jpeg"];
// The server owns the approved provider/model plan. No provider key or selectable
// paid model is exposed to the browser.
const PROVIDER = "gemini", MODEL = "";
const state = {
  marksItems: [], marksMethod: "", marksAttestation: "", marksAttestationExpiresAt: 0,
  choiceDetected: false, choiceGroupCount: 0, choiceVerified: false,
  marksEffectiveTotal: null, marksRequestVersion: 0,
  sheets: [], jobId: null, jobKey: "", poll: null, scrolled: false,
  pollFails: 0, googleToken: "",
};

function toast(msg, ms = 4500) {
  const t = $("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

function waitForGoogle(timeoutMs = 12000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (window.google?.accounts?.id) {
        clearInterval(timer); resolve(window.google);
      } else if (Date.now() - started > timeoutMs) {
        clearInterval(timer); reject(new Error("Google sign-in could not be loaded."));
      }
    }, 100);
  });
}

function tokenEmail(token) {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(decodeURIComponent(escape(atob(part)))).email || "";
  } catch { return ""; }
}

function tokenExpiryMs(token) {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const exp = Number(JSON.parse(decodeURIComponent(escape(atob(part)))).exp || 0);
    return Number.isFinite(exp) ? exp * 1000 : 0;
  } catch { return 0; }
}

function clearStoredSignIn() {
  state.googleToken = "";
  sessionStorage.removeItem("pwGoogleToken");
}

async function apiFetch(url, options = {}) {
  if (!state.googleToken) throw new Error("Sign in with your @pw.live account first.");
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${state.googleToken}`);
  return fetch(url, { ...options, headers });
}

async function validateSignIn(token) {
  if (!token) throw new Error("Google did not return a sign-in credential. Please try again.");
  const email = tokenEmail(token);
  if (email && !email.toLowerCase().endsWith("@pw.live")) {
    throw new Error("Only @pw.live Google accounts can use this app.");
  }
  const expiresAt = tokenExpiryMs(token);
  if (expiresAt && expiresAt <= Date.now() + 5000) {
    clearStoredSignIn();
    throw new Error("Your Google session expired. Please sign in again.");
  }
  const res = await fetch("/api/auth/status", {
    headers: { Authorization: `Bearer ${token}` },
  });
  let data; try { data = await res.json(); } catch { data = {}; }
  if (!res.ok || !data.allowed) throw new Error(data.detail || "This account is not authorized.");
  state.googleToken = token;
  sessionStorage.setItem("pwGoogleToken", token);
  document.body.classList.remove("auth-locked");
  // Collapse the card to a signed-in state — no dangling "Sign in with Google" button.
  $("googleSignIn").classList.add("hidden");
  $("authSub").classList.add("hidden");
  $("authTitle").textContent = "Account";
  $("authStatus").textContent = `✅ Signed in${email ? " as " + email : ""}.`;
  $("signOut").classList.remove("hidden");
  refreshChecklist();
}

async function handleGoogleCredential(response) {
  try {
    await validateSignIn(response.credential || "");
  } catch (e) {
    clearStoredSignIn();
    window.google?.accounts?.id?.disableAutoSelect();
    document.body.classList.add("auth-locked");
    $("authStatus").textContent = e.message;
  }
}

async function initAuth() {
  try {
    const configRes = await fetch("/api/config");
    const config = await configRes.json();
    if ($("buildId")) $("buildId").textContent = `build ${config.build_id || "unknown"}`;
    if (!config.google_client_id) {
      $("authStatus").innerHTML =
        "⚠️ <b>Sign-in is not configured.</b> The app needs the PW Google OAuth client ID. " +
        "Either place a <code>pw_oauth_config.json</code> file next to the app " +
        "(contents: <code>{\"google_client_id\": \"….apps.googleusercontent.com\"}</code>) " +
        "or set the <code>PW_GOOGLE_CLIENT_ID</code> environment variable, then restart. " +
        "The ID is in Render → ai-checker → Environment, or Google Cloud Console → Credentials.";
      return;
    }
    await waitForGoogle();
    google.accounts.id.initialize({
      client_id: config.google_client_id,
      callback: handleGoogleCredential,
      auto_select: false,
      hd: "pw.live",
    });
    google.accounts.id.renderButton($("googleSignIn"), {
      theme: "outline", size: "large", text: "signin_with", shape: "pill",
    });
    const saved = sessionStorage.getItem("pwGoogleToken") || "";
    const savedExpiry = tokenExpiryMs(saved);
    if (saved && (!savedExpiry || savedExpiry > Date.now() + 5000)) {
      await validateSignIn(saved);
    } else {
      clearStoredSignIn();
      $("authStatus").textContent = saved
        ? "Your previous session expired. Sign in again to continue."
        : "Sign in to continue.";
    }
  } catch (e) {
    clearStoredSignIn();
    $("authStatus").textContent = e.message;
  }
}

function invalidateMarksAttestation(reason = "ai_stale") {
  state.marksAttestation = "";
  state.marksAttestationExpiresAt = 0;
  state.choiceGroupCount = 0;
  state.choiceVerified = false;
  state.marksEffectiveTotal = null;
  state.marksRequestVersion += 1;
  if (state.marksMethod === "ai_verified") state.marksMethod = reason;
}

function adoptMarksResponse(data) {
  state.marksItems = Array.isArray(data.items) ? data.items : [];
  state.marksMethod = data.method || "";
  state.marksAttestation = data.marks_attestation || "";
  state.marksAttestationExpiresAt = Number(data.marks_attestation_expires_at || 0);
  state.choiceDetected = Boolean(data.choice_detected);
  state.choiceGroupCount = Array.isArray(data.choice_groups) ? data.choice_groups.length : 0;
  state.choiceVerified = Boolean(
    data.choice_verified && state.marksAttestation && state.choiceGroupCount
  );
  const effective = Number(data.effective_total);
  state.marksEffectiveTotal = Number.isFinite(effective) ? effective : null;
}

function marksContextSnapshot() {
  const qp = $("qpFile").files[0];
  return JSON.stringify({
    file: qp ? [qp.name, qp.size, qp.lastModified] : null,
    studentClass: $("studentClass").value,
    subject: $("subject").value,
    examType: $("examType").value,
    totalMarks: $("totalMarks").value,
  });
}

// ---- init ----
function init() {
  document.querySelectorAll("input[name=keySource]").forEach(r =>
    r.onchange = () => { toggleKeySource(); refreshChecklist(); });
  toggleKeySource();

  $("qpFile").onchange = onQpChange;           // auto-extract questions on upload
  $("akFile").onchange = refreshChecklist;
  ["studentClass", "subject", "examType"].forEach(id =>
    $(id).addEventListener("input", () => {
      invalidateMarksAttestation();
      refreshChecklist();
    }));
  $("totalMarks").oninput = () => {            // re-check printed-sum vs official total
    invalidateMarksAttestation();
    updateLockedTotal();
    if (state.marksItems.length) {
      $("marksMethod").textContent = marksSummary(
        state.marksItems.length, marksItemsTotal(), state.marksMethod
      );
    }
    refreshChecklist();
  };

  $("pickSheets").onclick = () => $("saFiles").click();
  $("saFiles").onchange = (e) => { addSheets(e.target.files); e.target.value = ""; };
  $("clearSheets").onclick = () => { state.sheets = []; renderSheets(); };
  const dz = $("dropzone");
  ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => {
    e.preventDefault(); if (ev === "dragleave" && dz.contains(e.relatedTarget)) return; dz.classList.remove("drag");
  }));
  dz.addEventListener("drop", e => addSheets(e.dataTransfer.files));

  $("detectMarks").onclick = detectMarks;
  $("genRubric").onclick = genRubric;
  $("evaluate").onclick = evaluate;
  $("newBatch").onclick = resetResults;
  $("signOut").onclick = () => {
    clearStoredSignIn();
    invalidateMarksAttestation();
    window.google?.accounts?.id?.disableAutoSelect();
    document.body.classList.add("auth-locked");
    $("googleSignIn").classList.remove("hidden");
    $("authSub").classList.remove("hidden");
    $("authTitle").textContent = "Sign in to AI Copy Checker";
    $("authStatus").textContent = "Signed out.";
    $("signOut").classList.add("hidden");
    refreshChecklist();
  };

  refreshChecklist();
}

function toggleKeySource() {
  const v = document.querySelector("input[name=keySource]:checked").value;
  $("keyUpload").classList.toggle("hidden", v !== "upload");
  $("keyRubric").classList.toggle("hidden", v !== "rubric");
}

function onQpChange() {
  state.marksRequestVersion += 1;
  state.marksItems = [];
  state.marksMethod = "";
  state.marksAttestation = "";
  state.marksAttestationExpiresAt = 0;
  state.choiceDetected = false;
  state.choiceGroupCount = 0;
  state.choiceVerified = false;
  state.marksEffectiveTotal = null;
  renderMarks();
  refreshChecklist();
  autoDetectMarks();   // surface the extracted questions immediately (regex, free)
}

// ---- multi-sheet handling ----
function addSheets(fileList) {
  let skipped = 0, empties = 0;
  for (const f of fileList) {
    if (!ACCEPT.some(ext => f.name.toLowerCase().endsWith(ext))) { skipped++; continue; }
    if (!f.size) { empties++; continue; }                         // 0-byte / failed read
    if (state.sheets.some(s => s.name === f.name && s.size === f.size)) continue;
    state.sheets.push(f);
  }
  renderSheets();
  if (skipped) toast(`${skipped} file(s) skipped — only PDF/PNG/JPG allowed.`);
  if (empties) toast(`${empties} file(s) were empty (0 bytes) — re-export or re-scan and try again.`);
}
function renderSheets() {
  const n = state.sheets.length;
  const kb = state.sheets.reduce((a, f) => a + f.size, 0) / 1024;
  const sizeTxt = kb > 1024 ? (kb / 1024).toFixed(1) + " MB" : Math.round(kb) + " KB";
  $("sheetListWrap").classList.toggle("hidden", n === 0);
  $("sheetCount").textContent = n ? `${n} sheet${n !== 1 ? "s" : ""} · ${sizeTxt}` : "0 sheets";
  const ul = $("sheetList");
  ul.innerHTML = state.sheets.map((f, i) =>
    `<li><span class="nm">${esc(f.name)}</span><span class="sz">${(f.size / 1024).toFixed(0)} KB</span>` +
    `<button class="rm" data-i="${i}" title="Remove">✕</button></li>`).join("");
  ul.querySelectorAll(".rm").forEach(b => b.onclick = () => { state.sheets.splice(+b.dataset.i, 1); renderSheets(); });
  if (kb / 1024 > 40) toast("Large upload (>40 MB). On the free tier, grade in smaller batches if it stalls.");
  refreshChecklist();
}

// ---- shared form bits ----
function commonForm() {
  const fd = new FormData();
  fd.append("provider", PROVIDER);
  fd.append("model", MODEL);
  fd.append("student_class", $("studentClass").value);
  fd.append("subject", $("subject").value);
  fd.append("exam_type", $("examType").value);
  fd.append("total_marks", $("totalMarks").value || "");
  fd.append("exam_level", $("studentClass").value);
  return fd;
}

// ---- marks / question extraction ----
async function autoDetectMarks() {
  const qp = $("qpFile").files[0];
  if (!qp) return;
  const requestVersion = state.marksRequestVersion;
  const requestContext = marksContextSnapshot();
  // Free text-layer reading only. Strict grading still requires a proven graph.
  const fd = commonForm(); fd.append("question_paper", qp); fd.append("mode", "auto");
  $("marksMethod").textContent = "Reading questions & marks from the paper…";
  try {
    const res = await apiFetch("/api/detect-marks", { method: "POST", body: fd });
    const data = await res.json();
    if (requestVersion !== state.marksRequestVersion ||
        requestContext !== marksContextSnapshot()) return;
    state.choiceDetected = Boolean(data.choice_detected);
    if (!res.ok) {
      $("marksMethod").textContent = "Couldn't build a deterministic marks table. Use Detect & verify max marks.";
      return;
    }
    if (data.paper_total && !$("totalMarks").value) $("totalMarks").value = data.paper_total;
    if (data.method === "none" || !data.items.length) {
      const t = data.paper_total || $("totalMarks").value;
      $("marksMethod").textContent =
        `ℹ️ No deterministic printed marks table was found${t ? ` (paper total ${t})` : ""}. ` +
        `Use Detect & verify max marks to build a server-verified table.`;
      refreshChecklist();
      return;
    }
    adoptMarksResponse(data);
    $("marksMethod").textContent = marksSummary(data.items.length, data.total, data.method);
    renderMarks();
  } catch (e) {
    if (requestVersion === state.marksRequestVersion) {
      $("marksMethod").textContent = "Marks reading failed: " + e.message;
    }
  }
}

// Cross-check the printed per-part sum against the user-entered paper total. CA/professional
// papers with internal choice ("answer any N") legitimately print MORE marks than the total.
const METHOD_LABEL = {
  regex: "from the printed paper",
  sections: "from deterministic printed section ranges",
  ai: "read by AI from the paper text + structure, cross-checked against the printed tags",
  ai_verified: "independently matched by Claude and Gemini",
  ai_unverified: "read by one provider; independent verification failed",
  ai_unverified_total: "read by AI, but no official total was available",
  ai_unverified_evidence: "the paper has no complete text layer for safe verification",
  ai_disagreement: "provider disagreement; preview only",
  ai_choice_unverified: "choice alternatives found; selection graph is not yet verified",
  ai_total_unverified: "the question rows do not match the official total",
  ai_stale: "verification expired because paper details changed",
  ai_mismatch: "AI reading does not match the printed marks; automatic evaluation is locked",
  regex_fallback: "AI reading failed; raw printed-tag preview only",
};

function marksSummary(count, printedTotal, method) {
  const how = METHOD_LABEL[method] || method || "from the printed paper";
  const entered = parseFloat($("totalMarks").value);
  const tokenVerified = method === "ai_verified" && Boolean(state.marksAttestation);
  const trusted = tokenVerified ||
    (!state.choiceDetected && ["regex", "sections"].includes(method));
  const head = `✅ Extracted ${count} questions · parts sum to ${printedTotal} marks (${how}).`;
  if (tokenVerified && state.choiceVerified) {
    const effective = state.marksEffectiveTotal ?? entered;
    const n = state.choiceGroupCount;
    return `✅ Extracted ${count} scorable parts · all printed alternatives sum to ${printedTotal}. ` +
      `${n} internal-choice rule${n === 1 ? "" : "s"} independently matched by Claude and Gemini; ` +
      `effective paper total ${effective}.`;
  }
  if (entered && Math.abs(printedTotal - entered) > 0.25) {
    if (!trusted) {
      return `⚠️ Parts sum to ${printedTotal}, but the official total is ${entered}. ` +
        `The internal-choice selection graph is not verified, so automatic evaluation remains locked.`;
    }
    return `✅ The complete question graph is independently verified; effective paper total ${entered}.`;
  }
  return trusted
    ? `${head} These qids/maxima are locked and server-verified.`
    : `${head} This is a preview only and cannot unlock automatic evaluation.`;
}

async function detectMarks() {
  const qp = $("qpFile").files[0];
  if (!qp) return toast("Upload a question paper first.");
  invalidateMarksAttestation();
  refreshChecklist();
  const requestVersion = state.marksRequestVersion;
  const requestContext = marksContextSnapshot();
  const fd = commonForm(); fd.append("question_paper", qp);
  setBusy($("detectMarks"), "Detecting and verifying…");
  try {
    const res = await apiFetch("/api/detect-marks", { method: "POST", body: fd });
    const data = await res.json();
    if (requestVersion !== state.marksRequestVersion ||
        requestContext !== marksContextSnapshot()) {
      toast("Paper details changed while marks were being verified. Run verification again.");
      return;
    }
    if (!res.ok) throw new Error(data.detail || "failed");
    adoptMarksResponse(data);
    if (data.paper_total && !$("totalMarks").value) $("totalMarks").value = data.paper_total;
    $("marksMethod").textContent = data.items.length
      ? marksSummary(data.items.length, data.total, data.method) +
        (data.verification_detail ? " " + data.verification_detail : "")
      : "ℹ️ No question/maxima graph could be verified from this paper.";
    renderMarks();
  } catch (e) {
    if (requestVersion !== state.marksRequestVersion ||
        requestContext !== marksContextSnapshot()) return;
    invalidateMarksAttestation();
    $("marksMethod").textContent = "❌ Marks verification failed: " + e.message +
      " — automatic release remains unavailable for this paper.";
    toast("Could not build the marks table: " + e.message, 9000);
    refreshChecklist();
  }
  finally { unBusy($("detectMarks"), "🔢 Detect & verify max marks"); }
}

function renderMarks() {
  const tb = $("marksTable").querySelector("tbody");
  tb.innerHTML = "";
  state.marksItems.forEach(it => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td><input readonly value="${esc(it.qid ?? "")}"></td>` +
      `<td class="max"><input readonly type="number" step="0.5" value="${it.max ?? 0}"></td>` +
      `<td><input readonly value="${esc(it.part ?? "")}"></td>`;
    tb.appendChild(tr);
  });
  $("marksTable").classList.toggle("hidden", state.marksItems.length === 0);
  updateLockedTotal();
  refreshChecklist();
}
function marksItemsTotal() {
  return Math.round(state.marksItems.reduce(
    (total, row) => total + (String(row.qid).trim() ? (parseFloat(row.max) || 0) : 0), 0
  ) * 100) / 100;
}

function updateLockedTotal() {
  const t = marksItemsTotal();
  const entered = parseFloat($("totalMarks").value);
  if (state.marksMethod === "ai_verified" && state.marksAttestation &&
      state.choiceVerified && Number.isFinite(state.marksEffectiveTotal)) {
    $("lockedTotal").textContent =
      `${state.marksEffectiveTotal} (verified; all alternatives sum to ${t})`;
    return;
  }
  $("lockedTotal").textContent = (entered && Math.abs(t - entered) > 0.25)
    ? `${entered} (question rows sum to ${t} — not verified)`
    : t;
}

// ---- rubric ----
async function genRubric() {
  const qp = $("qpFile").files[0];
  if (!qp) return toast("Upload a question paper first.");
  const fd = commonForm(); fd.append("question_paper", qp);
  setBusy($("genRubric"), "Generating…");
  try {
    const res = await apiFetch("/api/generate-rubric", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "failed");
    $("rubricText").value = data.rubric; refreshChecklist();
  } catch (e) { toast("Rubric generation failed: " + e.message); }
  finally { unBusy($("genRubric"), "✨ Generate rubric from question paper"); }
}

// ---- checklist ----
function refreshChecklist() {
  if (state.marksAttestation && state.marksAttestationExpiresAt &&
      Date.now() / 1000 >= state.marksAttestationExpiresAt) {
    invalidateMarksAttestation("ai_stale");
  }
  const ks = document.querySelector("input[name=keySource]:checked").value;
  const qp = $("qpFile").files.length > 0;
  const sa = state.sheets.length;
  const keyOk = ks === "upload" && $("akFile").files.length > 0;
  const qids = state.marksItems.map(r => String(r.qid || "").toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.|\.$/g, ""));
  const marksOk = qids.length > 0 && qids.every(Boolean) &&
    new Set(qids).size === qids.length &&
    state.marksItems.every(r => Number.isFinite(Number(r.max)) && Number(r.max) > 0);
  const detailsOk = $("studentClass").value.trim() && $("subject").value.trim() && Number($("totalMarks").value) > 0;
  const rows = [
    [detailsOk, "Level, subject, and official total entered"],
    [qp, "Official question paper uploaded"],
    [sa > 0, `Answer sheet(s) added${sa ? " — " + sa : ""}`],
    [keyOk, "Official answer key uploaded (generated drafts are not releasable)"],
  ];
  // Independent Claude+Gemini verification no longer gates evaluation. The marks table
  // is listed only while it still blocks, so nothing disables Evaluate invisibly.
  if (!marksOk) rows.push([false, "Question/maxima rows need unique ids and positive maxima"]);
  $("checklist").innerHTML = rows.map(([ok, t]) => `<li>${ok ? "✅" : "⬜"} ${t}</li>`).join("");
  $("evaluate").disabled = !(state.googleToken && detailsOk && qp && sa > 0 && keyOk && marksOk);
  $("evaluate").textContent = sa > 1 ? `🚀 Evaluate ${sa} sheets` : "🚀 Evaluate";
}

// ---- evaluate + poll ----
async function evaluate() {
  refreshChecklist();
  if ($("evaluate").disabled) {
    return toast("Complete the unchecked requirements before evaluation.");
  }
  const ks = document.querySelector("input[name=keySource]:checked").value;
  const fd = commonForm();
  if ($("qpFile").files[0]) fd.append("question_paper", $("qpFile").files[0]);
  for (const f of state.sheets) fd.append("answer_sheets", f);
  fd.append("key_source", ks);
  if (ks === "upload") fd.append("answer_key", $("akFile").files[0]);
  else fd.append("rubric_text", $("rubricText").value);
  fd.append("marks_items", JSON.stringify(state.marksItems));
  fd.append("marks_source", state.marksMethod || "unknown");
  fd.append("marks_attestation", state.marksAttestation || "");

  state.scrolled = false; state.pollFails = 0;
  resetResults();
  $("evaluate").disabled = true;
  $("progress").classList.remove("hidden");
  setBar(0, "Uploading…");
  try {
    const res = await apiFetch("/api/evaluate", { method: "POST", body: fd });
    let data; try { data = await res.json(); } catch { data = {}; }
    if (!res.ok) throw new Error(data.detail || `server returned ${res.status}`);
    state.jobId = data.job_id;
    // Per-job download key — the PDF/ZIP links are plain hrefs and cannot send the
    // Authorization header, so results are gated on this instead of being public.
    state.jobKey = data.download_key || "";
    setBar(0, "Queued…");
    state.poll = setInterval(pollJob, 1500);
    pollJob();
  } catch (e) {
    if (/verified marks proof|detect marks again|question graph changed/i.test(e.message)) {
      invalidateMarksAttestation();
      $("marksMethod").textContent =
        "Verification expired after a restart or time limit. Click Detect & verify max marks again.";
    }
    toast("Couldn't start evaluation: " + e.message);
    $("progress").classList.add("hidden");
    refreshChecklist();
  }
}

function setBar(frac, text) {
  $("barFill").style.width = Math.round(frac * 100) + "%";
  $("progressText").textContent = text;
}

async function pollJob() {
  if (!state.jobId) return;
  let job;
  try {
    const r = await apiFetch(`/api/jobs/${state.jobId}?k=${encodeURIComponent(state.jobKey)}`);
    if (!r.ok) throw new Error("status " + r.status);
    job = await r.json();
    state.pollFails = 0;
  } catch (e) {
    // Job lost (instance restart) or transient network error — tolerate a few, then stop.
    if (++state.pollFails >= 4) {
      clearInterval(state.poll); state.poll = null;
      $("progress").classList.add("hidden"); $("evaluate").disabled = false;
      toast("Lost the grading job (the server may have restarted). Any completed sheets are shown above; re-run the rest.");
    }
    return;
  }
  const frac = job.total ? job.done / job.total : 0;
  setBar(frac, `Graded ${job.done}/${job.total}${job.current ? " · " + job.current : ""}`);

  // Render results LIVE as each sheet finishes — don't make the user wait for the whole batch.
  const finished = job.status === "done" || job.status === "error";
  if ((job.results && job.results.length) || finished) renderResults(job, !finished);

  if (finished) {
    clearInterval(state.poll); state.poll = null;
    $("progress").classList.add("hidden"); $("evaluate").disabled = false;
    if (job.status === "error" && !(job.results && job.results.length)) {
      toast("Evaluation failed: " + (job.error || "unknown error"));
    }
  }
}

// ---- results (live; no cost shown) ----
function renderResults(job, inProgress = false) {
  const results = job.results || [];
  const ok = results.filter(r => r.ok);
  const released = results.filter(r => r.release === true);
  const failed = results.filter(r => !r.ok);
  const total = job.total || results.length;

  $("results").classList.remove("hidden");
  $("resultsTitle").textContent = inProgress
    ? `📋 Grading ${results.length}/${total}…`
    : `📋 Results — ${total} sheet${total !== 1 ? "s" : ""}`;
  if (!state.scrolled) { $("results").scrollIntoView({ behavior: "smooth" }); state.scrolled = true; }

  let html = "";
  if (total > 1) {
    const avg = ok.length ? ok.reduce((a, r) => a + r.percent, 0) / ok.length : 0;
    html = metric("Graded", `${results.length}/${total}`) +
      metric("Average", ok.length ? `${avg.toFixed(0)}%` : "—") +
      metric("Released", released.length) +
      metric("Ungradable / failed", results.length - released.length);
  }
  $("summary").innerHTML = html;

  const zip = $("zipBtn");
  if (!inProgress && released.length > 1) { zip.href = `/api/jobs/${state.jobId}/zip?k=${encodeURIComponent(state.jobKey)}`; zip.classList.remove("hidden"); }
  else zip.classList.add("hidden");

  const box = $("resultRows");
  if (total === 1) {
    box.innerHTML = results.length ? (ok.length ? detailHtml(ok[0]) : failHtml(failed[0]))
      : `<p class="muted">⏳ Grading…</p>`;
    return;
  }

  // Batch: a row per sheet (completed → score; not-yet → queued), plus details for completed.
  let t = `<table class="rtable"><thead><tr><th>Student</th><th>Score</th><th>%</th><th>Status</th></tr></thead><tbody>`;
  for (const r of results) {
    t += r.ok
      ? `<tr><td>${esc(r.student)}</td><td class="s">${fmt(r.score)} / ${fmt(r.max)}</td><td>${r.percent}%</td><td class="${r.release === true ? "ok" : "bad"}">${r.release === true ? "✅ released" : "⛔ ungradable"}</td></tr>`
      : `<tr><td>${esc(r.student)}</td><td>—</td><td>—</td><td class="bad">❌ ${esc(r.error || "")}</td></tr>`;
  }
  for (let i = results.length; i < total; i++) {
    const nm = state.sheets[i] ? esc(state.sheets[i].name) : `sheet ${i + 1}`;
    t += `<tr><td class="muted">${nm}</td><td>—</td><td>—</td><td class="muted">${i === results.length ? "⏳ grading…" : "queued"}</td></tr>`;
  }
  t += "</tbody></table>";
  t += results.map(r => r.ok
    ? `<details class="rdetail"><summary>✅ ${esc(r.student)} — ${fmt(r.score)}/${fmt(r.max)}</summary>${detailHtml(r)}</details>`
    : `<details class="rdetail"><summary>❌ ${esc(r.student)}</summary>${failHtml(r)}</details>`).join("");
  box.innerHTML = t;
}

function detailHtml(r) {
  const pct = r.max ? Math.round(r.score / r.max * 100) : 0;
  const dl = `/api/jobs/${state.jobId}/pdf/${r.index}?k=${encodeURIComponent(state.jobKey)}`;
  const q = (r.questions || []).map(x =>
    `<div class="qrow"><span class="q">${esc(x.q)}</span><span class="m">${fmt(x.score)}/${fmt(x.max)}</span><span class="r">${esc(x.remark)}</span></div>`).join("");
  return `
    <div class="scoreband"><div class="big">${fmt(r.score)} / ${fmt(r.max)}</div><div class="pct">${pct}%</div></div>
    ${r.warning ? `<p class="bad">⚠️ ${esc(r.warning)}</p>` : ""}
    ${r.release === true
      ? `<a class="primary-btn small" href="${dl}">⬇️ Download evaluated PDF</a>`
      : `<p class="bad">⛔ This copy was not released. The system will not substitute a guessed mark.</p>`}
    ${r.budget?.cap_inr ? `<p class="muted"><b>Provider cost:</b> ₹${Number(r.budget.actual_inr || 0).toFixed(2)} / ₹${Number(r.budget.cap_inr).toFixed(0)} cap</p>` : ""}
    ${r.remarks ? `<p class="muted"><b>Remarks:</b> ${esc(r.remarks)}</p>` : ""}
    <div style="margin-top:8px">${q}</div>`;
}
function failHtml(r) {
  const icon = r.held ? "⛔" : "❌";
  const reason = r.error || "Automatic verification could not release this copy.";
  const label = r.held
    ? `Ungradable — no score was released. Reason: ${reason}`
    : reason;
  return `<p class="bad">${icon} ${esc(r.student)}: ${esc(label)}</p>`;
}

function resetResults() {
  $("results").classList.add("hidden"); $("resultRows").innerHTML = ""; $("summary").innerHTML = "";
}

// ---- utils ----
function setBusy(btn, t) { btn.disabled = true; btn.textContent = t; }
function unBusy(btn, t) { btn.disabled = false; btn.textContent = t; }
function metric(k, v) {
  return `<div class="metric"><div class="v">${v}</div><div class="k">${k}</div></div>`;
}
function fmt(n) { const x = Number(n); return Number.isInteger(x) ? x : x.toFixed(2).replace(/\.?0+$/, ""); }
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

init();
initAuth();
