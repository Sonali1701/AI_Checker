"use strict";
const $ = (id) => document.getElementById(id);
const ACCEPT = [".pdf", ".png", ".jpg", ".jpeg"];
// A category that reads linked work (School of Innovation) also takes notebooks,
// workbooks, Power BI, Tableau, Word and code files, and a text file of links.
const LINKED_WORK_ACCEPT = [".ipynb", ".xlsx", ".xlsm", ".csv", ".pbix", ".pbit", ".twb", ".twbx",
  ".docx", ".txt", ".url", ".py", ".sql", ".js", ".html", ".css", ".r", ".java"];
const readsLinkedWork = () => Boolean((state.categories || []).find(
  c => c.id === ($("paperType") ? $("paperType").value : "") && c.linked_work));
const sheetAccept = () => readsLinkedWork() ? ACCEPT.concat(LINKED_WORK_ACCEPT) : ACCEPT;
// A category that writes its own model answer key when none is uploaded (teaching exams,
// PW Skills assignments without a solutions file) offers the "No answer key" choice.
const generatesKey = () => Boolean((state.categories || []).find(
  c => c.id === ($("paperType") ? $("paperType").value : "") && c.generates_key));
// The server owns the approved provider/model plan. No provider key or selectable
// paid model is exposed to the browser.
const PROVIDER = "claude", MODEL = "";
const state = {
  marksItems: [], marksMethod: "", marksAttestation: "", marksAttestationExpiresAt: 0,
  choiceDetected: false, choiceGroupCount: 0, choiceVerified: false,
  marksEffectiveTotal: null, marksRequestVersion: 0,
  marksBusy: false, evaluationBusy: false, marksVerificationDetail: "",
  marksFailureCode: "", marksRetryAt: 0, marksRetryTimer: null,
  sheets: [], jobId: null, jobKey: "", poll: null, scrolled: false,
  pollFails: 0, googleToken: "",
  // Design and drawing papers: the questions read from the paper, with the marks
  // the operator types for each (design papers rarely print them).
  design: { questions: [], busy: false, notes: [], requestVersion: 0 },
  // Folder mode: what "Check folder" found (null until checked).
  bulkPreview: null, bulkBusy: false,
};
const isDesign = () => Boolean($("paperType")) && $("paperType").value === "design";
// Teaching exams come with no key: the server writes a model answer key from the paper.
const isTeaching = () => Boolean($("paperType")) && $("paperType").value === "teaching";
// A category whose papers have no class (School of Innovation assignments belong to
// a course): the class field is hidden and the server uses the category's own label.
const noLevel = () => Boolean((state.categories || []).find(
  c => c.id === ($("paperType") ? $("paperType").value : "") && c.level_label));
const isFolder = () => !isDesign() &&
  document.querySelector("input[name=sheetSource]:checked")?.value === "folder";

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
  // The server answers a Google sign-in with its own session token, valid as long as
  // the PW proxy's 7-day pass; it is used from here on instead of the hourly Google token.
  state.googleToken = data.session_token || token;
  sessionStorage.setItem("pwGoogleToken", state.googleToken);
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
    state.categories = Array.isArray(config.categories) ? config.categories : [];
    showCategoryHint();
    state.vpFields = config.vidyapeeth_fields || {};
    fillVidyapeethSelects();
    if ($("buildId")) $("buildId").textContent = `build ${config.build_id || "unknown"}` +
      (config.budget_cap_enabled === false ? " · budget cap off" : "");
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

function showCategoryHint() {
  const hint = $("categoryHint");
  if (!hint) return;
  const id = $("paperType").value;
  const found = (state.categories || []).find(c => c.id === id);
  hint.textContent = found ? found.hint
    : "CA papers are recognised from the class (CA Inter, CA Foundation), commerce from Class 11 or 12 commerce subjects, everything else is marked as a school board paper. Choose PW Vidyapeeth or Design yourself.";
}

function invalidateMarksAttestation(reason = "ai_stale") {
  state.marksAttestation = "";
  state.marksAttestationExpiresAt = 0;
  state.choiceGroupCount = 0;
  state.choiceVerified = false;
  state.marksEffectiveTotal = null;
  state.marksVerificationDetail = "";
  state.marksFailureCode = "";
  state.marksRetryAt = 0;
  clearTimeout(state.marksRetryTimer);
  state.marksRequestVersion += 1;
  if (state.marksMethod === "ai_verified") state.marksMethod = reason;
}

function adoptMarksResponse(data) {
  state.marksItems = Array.isArray(data.items) ? data.items : [];
  state.marksMethod = data.method || "";
  state.marksAttestation = data.marks_attestation || "";
  state.marksAttestationExpiresAt = Number(data.marks_attestation_expires_at || 0);
  state.marksVerificationDetail = data.verification_detail || "";
  state.marksFailureCode = data.failure_code || "";
  state.marksRetryAt = Date.now() + Math.max(0, Number(data.retry_after_seconds) || 0) * 1000;
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
    category: $("paperType").value,
    totalMarks: $("totalMarks").value,
    questionPages: $("questionPageRange").value.trim(),
    answerPages: $("answerPageRange").value.trim(),
    combinedPaper: useCombinedPaper(),
  });
}

// ---- init ----
function init() {
  document.querySelectorAll("input[name=keySource]").forEach(r =>
    r.onchange = () => { toggleKeySource(); refreshChecklist(); });
  toggleKeySource();

  $("qpFile").onchange = onQpChange;           // auto-extract questions on upload
  $("akFile").onchange = () => { if (isDesign()) resetDesignPaper(); refreshChecklist(); };
  ["questionPageRange", "answerPageRange"].forEach(id =>
    $(id).addEventListener("input", onPageSelectionChange));
  $("useQuestionPaperAsKey").onchange = () => {
    toggleCombinedPaper();
    onPageSelectionChange();
  };
  toggleCombinedPaper();
  $("paperType").onchange = onPaperTypeChange;
  if ($("answerLanguage")) $("answerLanguage").onchange = showLanguageHint;
  $("readDesignPaper").onclick = readDesignPaper;
  ["studentClass", "subject"].forEach(id =>
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

  document.querySelectorAll("input[name=sheetSource]").forEach(r =>
    r.onchange = () => { toggleSheetSource(); refreshChecklist(); });
  ["bulkInput", "bulkOutput", "bulkMachine", "bulkMachines", "bulkWorkers"].forEach(id =>
    $(id).addEventListener("input", () => {
      state.bulkPreview = null; $("bulkPreview").textContent = ""; refreshChecklist();
    }));
  $("bulkCheck").onclick = checkFolder;
  $("bulkStop").onclick = stopFolderRun;
  toggleSheetSource();

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
  if (v !== "upload" && useCombinedPaper()) {
    $("useQuestionPaperAsKey").checked = false;
    toggleCombinedPaper();
    onPageSelectionChange();
  }
}

function onQpChange() {
  invalidateMarksAttestation();
  resetDesignPaper();
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
  if (!isDesign()) autoDetectMarks();   // surface extracted questions immediately (regex, free)
}

function useCombinedPaper() {
  return $("useQuestionPaperAsKey").checked;
}

function toggleCombinedPaper() {
  $("separateKeyFile").classList.toggle("hidden", useCombinedPaper());
  $("akFile").disabled = useCombinedPaper();
}

function onPageSelectionChange() {
  invalidateMarksAttestation();
  state.marksItems = [];
  state.marksMethod = "";
  state.choiceDetected = false;
  resetDesignPaper();
  renderMarks();
  $("marksMethod").textContent = $("qpFile").files[0]
    ? "Page selection changed. Detect & verify max marks for these pages." : "";
  refreshChecklist();
}

function appendPageSelections(fd) {
  fd.append("question_page_range", $("questionPageRange").value.trim());
  fd.append("answer_page_range", $("answerPageRange").value.trim());
  fd.append("use_question_paper_as_key", String(useCombinedPaper()));
}

function appendOfficialAnswerKey(fd) {
  if (!useCombinedPaper() && $("akFile").files[0]) {
    fd.append("answer_key", $("akFile").files[0]);
  }
}

// ---- multi-sheet handling ----
function addSheets(fileList) {
  let skipped = 0, empties = 0;
  for (const f of fileList) {
    if (!sheetAccept().some(ext => f.name.toLowerCase().endsWith(ext))) { skipped++; continue; }
    if (!f.size) { empties++; continue; }                         // 0-byte / failed read
    if (state.sheets.some(s => s.name === f.name && s.size === f.size)) continue;
    state.sheets.push(f);
  }
  renderSheets();
  if (skipped) toast(readsLinkedWork()
    ? `${skipped} file(s) skipped: only PDF, photos, notebooks, Excel, CSV, Power BI, Tableau, Word, code or link files.`
    : `${skipped} file(s) skipped — only PDF/PNG/JPG allowed.`);
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
  appendPageSelections(fd);
  fd.append("provider", PROVIDER);
  fd.append("model", MODEL);
  fd.append("student_class", noLevel() ? "" : $("studentClass").value);
  fd.append("subject", $("subject").value);
  // The category decides the marking rules; exam_type is kept for older servers.
  const category = isDesign() ? "" : $("paperType").value;
  fd.append("category", category);
  fd.append("exam_type", category === "ca" ? "professional" : (category ? "school" : ""));
  fd.append("total_marks", $("totalMarks").value || "");
  fd.append("exam_level", noLevel() ? "" : $("studentClass").value);
  fd.append("answer_language", $("answerLanguage")?.value || "");
  fd.append("leniency", $("leniency")?.value || "standard");
  // Vidyapeeth results sheet: the batch header, typed once for the whole upload.
  if (category === "vidyapeeth") {
    fd.append("centre_name", vpValue("vpCentre"));
    fd.append("course_name", vpValue("vpCourse"));
    fd.append("board", vpValue("vpBoard"));
    fd.append("vertical", vpValue("vpVertical"));
    fd.append("test_name", vpValue("vpTestName"));
    fd.append("test_date", $("testDate")?.value || "");
    fd.append("paper_mode", vpValue("vpMode"));
  }
  return fd;
}

// Languages not yet checked on sample copies send every copy to faculty review.
function showLanguageHint() {
  const hint = $("languageHint");
  if (!hint) return;
  const value = $("answerLanguage").value;
  const unchecked = !["", "english", "hindi"].includes(value);
  hint.textContent = unchecked
    ? "Copies in this language go to faculty review until its handwriting reading is checked on sample copies."
    : "";
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
  ai_verified: "read by Claude and matched to the paper total",
  ai_unverified: "read by one provider; independent verification failed",
  ai_extraction_failed: "marks reading did not finish; no partial table accepted",
  ai_verifier_busy: "verification service is temporarily busy; retry available",
  ai_unverified_total: "read by AI, but no official total was available",
  ai_unverified_evidence: "the paper has no complete text layer for safe verification",
  ai_disagreement: "provider disagreement; preview only",
  ai_choice_unverified: "choice alternatives found; selection graph is not yet verified",
  ai_total_unverified: "the question rows do not match the official total",
  ai_stale: "verification expired because paper details changed",
  ai_mismatch: "AI reading does not match the printed marks; automatic evaluation is locked",
  ai_tag_conflict: "extracted question marks contradict the printed allocations",
  regex_fallback: "AI reading failed; raw printed-tag preview only",
};

function marksSummary(count, printedTotal, method) {
  const how = METHOD_LABEL[method] || method || "from the printed paper";
  const entered = parseFloat($("totalMarks").value);
  const tokenVerified = method === "ai_verified" && Boolean(state.marksAttestation);
  const trusted = tokenVerified ||
    (!state.choiceDetected && ["regex", "sections"].includes(method));
  const head = `${trusted ? "✅" : "⚠️"} Extracted ${count} questions · parts sum to ${printedTotal} marks (${how}).`;
  if (tokenVerified && state.choiceVerified) {
    const effective = state.marksEffectiveTotal ?? entered;
    const n = state.choiceGroupCount;
    return `✅ Extracted ${count} scorable parts · all printed alternatives sum to ${printedTotal}. ` +
      `${n} internal-choice rule${n === 1 ? "" : "s"} read by Claude; ` +
      `effective paper total ${effective}.`;
  }
  if (entered && Math.abs(printedTotal - entered) > 0.25) {
    if (!trusted) {
      if (state.choiceDetected) {
        return `Read ${count} scorable parts with ${printedTotal} marks across the listed alternatives. ` +
          `Official total: ${entered}. Choice verification is pending.`;
      }
      return `⚠️ Parts sum to ${printedTotal}, but the official total is ${entered}. ` +
        `The allocation needs verification before grading.`;
    }
    return `✅ The complete question graph is independently verified; effective paper total ${entered}.`;
  }
  return trusted
    ? `${head} These qids/maxima are locked and server-verified.`
    : `${head} This is a preview only and cannot unlock automatic evaluation.`;
}

async function detectMarks() {
  if (state.marksBusy || state.evaluationBusy) return;
  if (state.marksRetryAt > Date.now()) {
    return toast(state.marksVerificationDetail || "Wait for the verification retry countdown.", 9000);
  }
  const qp = $("qpFile").files[0];
  if (!qp) return toast("Upload a question paper first.");
  invalidateMarksAttestation();
  state.marksBusy = true;
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
      : (data.verification_detail || "ℹ️ No question/maxima graph could be verified from this paper.");
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
  finally {
    state.marksBusy = false;
    refreshChecklist();
  }
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
  if (!state.marksItems.length && entered) {
    $("lockedTotal").textContent = `${entered} (awaiting complete marks verification)`;
    return;
  }
  if (state.marksMethod === "ai_verified" && state.marksAttestation &&
      state.choiceVerified && Number.isFinite(state.marksEffectiveTotal)) {
    $("lockedTotal").textContent =
      `${state.marksEffectiveTotal} (verified; all alternatives sum to ${t})`;
    return;
  }
  if (entered && state.choiceDetected && t > entered) {
    $("lockedTotal").textContent = `${entered} (choice verification pending; listed alternatives total ${t})`;
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
  const requestContext = marksContextSnapshot();
  setBusy($("genRubric"), "Generating…");
  try {
    const res = await apiFetch("/api/generate-rubric", { method: "POST", body: fd });
    const data = await res.json();
    if (requestContext !== marksContextSnapshot()) return;
    if (!res.ok) throw new Error(data.detail || "failed");
    $("rubricText").value = data.rubric; refreshChecklist();
  } catch (e) { toast("Rubric generation failed: " + e.message); }
  finally { unBusy($("genRubric"), "✨ Generate rubric from question paper"); }
}

// ---- checklist ----
function marksAreVerified() {
  const qids = state.marksItems.map(r => String(r.qid || "").toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.|\.$/g, ""));
  if (!qids.length || !qids.every(Boolean) || new Set(qids).size !== qids.length ||
      !state.marksItems.every(r => Number.isFinite(Number(r.max)) && Number(r.max) > 0)) return false;
  const total = Number($("totalMarks").value);
  if (!Number.isFinite(total) || total <= 0) return false;
  if (state.marksMethod === "ai_verified" && Boolean(state.marksAttestation) &&
      (state.marksAttestationExpiresAt === 0 || state.marksAttestationExpiresAt * 1000 > Date.now())) {
    if (state.choiceDetected && !state.choiceVerified) return false;
    const effective = state.choiceVerified ? state.marksEffectiveTotal : marksItemsTotal();
    return Number.isFinite(effective) && Math.abs(effective - total) <= .01;
  }
  return !state.choiceDetected && ["regex", "sections"].includes(state.marksMethod) &&
    Math.abs(marksItemsTotal() - total) <= .01;
}

function refreshChecklist() {
  if (isDesign()) return refreshDesignChecklist();
  if (state.marksAttestation && state.marksAttestationExpiresAt &&
      Date.now() / 1000 >= state.marksAttestationExpiresAt) {
    invalidateMarksAttestation("ai_stale");
  }
  const ks = document.querySelector("input[name=keySource]:checked").value;
  const qp = $("qpFile").files.length > 0;
  const folder = isFolder();
  const sa = folder ? (state.bulkPreview?.to_grade || 0) : state.sheets.length;
  const keyOk = (ks === "generate" && generatesKey()) ||
    (ks === "upload" && (useCombinedPaper() ? qp : $("akFile").files.length > 0));
  const marksOk = marksAreVerified();
  const vidyapeeth = $("paperType").value === "vidyapeeth";
  const headerOk = !vidyapeeth || vidyapeethGaps().length === 0;
  const detailsOk = headerOk && (noLevel() || $("studentClass").value.trim()) && $("subject").value.trim() && Number($("totalMarks").value) > 0;
  const rows = [
    [detailsOk, vidyapeeth ? "Batch header complete (board, vertical, centre, course, class, subject, test, date, mode) and official total entered"
      : noLevel() ? "Subject and official total entered" : "Level, subject, and official total entered"],
    [qp, "Official question paper uploaded"],
    folder
      ? [sa > 0, state.bulkPreview
          ? (sa ? `Folder checked: ${sa} cop${sa === 1 ? "y" : "ies"} to grade` : "Folder checked: nothing left to grade")
          : "Answer-sheet folder checked (click Check folder)"]
      : [sa > 0, `Answer sheet(s) added${sa ? " — " + sa : ""}`],
    [keyOk, ks === "generate" ? "No answer key: the tool writes a model answer key from the question paper"
      : useCombinedPaper() ? "Official answer key uses the selected pages of the question paper upload"
      : "Official answer key uploaded (generated drafts are not releasable)"],
    [marksOk, marksOk ? "Question-wise maximum marks verified" :
      (state.marksBusy ? "Verifying question-wise maximum marks…" :
        state.marksVerificationDetail || "Question-wise marks need verification — click Detect & verify max marks")],
  ];
  $("checklist").innerHTML = rows.map(([ok, t]) => `<li>${ok ? "✅" : "⬜"} ${esc(t)}</li>`).join("");
  const retrySeconds = Math.max(0, Math.ceil((state.marksRetryAt - Date.now()) / 1000));
  $("evaluate").disabled = state.marksBusy || state.evaluationBusy || retrySeconds > 0 ||
    !(state.googleToken && detailsOk && qp && sa > 0 && keyOk);
  $("evaluate").textContent = state.marksBusy ? "Verifying marks before evaluation…" :
    !marksOk ? "Verify marks & evaluate" :
    folder ? `🚀 Start folder run (${sa} cop${sa === 1 ? "y" : "ies"})` :
    sa > 1 ? `🚀 Evaluate ${sa} sheets` : "🚀 Evaluate";
  $("detectMarks").disabled = !state.googleToken || !qp || state.marksBusy || state.evaluationBusy || retrySeconds > 0;
  $("detectMarks").textContent = state.marksBusy ? "Detecting and verifying…" :
    retrySeconds ? `Retry marks verification in ${retrySeconds}s` :
    state.marksFailureCode ? "Retry marks verification" : "🔢 Detect & verify max marks";
  clearTimeout(state.marksRetryTimer);
  if (retrySeconds) state.marksRetryTimer = setTimeout(refreshChecklist, 1000);
}

// ---- evaluate + poll ----
async function evaluate() {
  if (isDesign()) return evaluateDesign();
  refreshChecklist();
  if ($("evaluate").disabled) {
    return toast(state.marksVerificationDetail || "Complete the unchecked requirements before evaluation.", 9000);
  }
  if (!marksAreVerified()) {
    const context = marksContextSnapshot();
    const key = $("akFile").files[0];
    const sheets = [...state.sheets];
    await detectMarks();
    if (!marksAreVerified()) {
      return toast(state.marksVerificationDetail || "Marks could not be verified. See the reading details above.", 9000);
    }
    if (context !== marksContextSnapshot() || key !== $("akFile").files[0] ||
        sheets.length !== state.sheets.length || sheets.some((file, i) => file !== state.sheets[i])) {
      return toast("The uploaded files or paper details changed. Click Evaluate for the current files.", 9000);
    }
    refreshChecklist();
    if ($("evaluate").disabled) return;
  }
  state.evaluationBusy = true;
  refreshChecklist();
  const ks = document.querySelector("input[name=keySource]:checked").value;
  const fd = commonForm();
  const folder = isFolder();
  if ($("qpFile").files[0]) fd.append("question_paper", $("qpFile").files[0]);
  if (folder) appendFolderFields(fd);
  else for (const f of state.sheets) fd.append("answer_sheets", f);
  fd.append("key_source", ks);
  if (ks === "upload") appendOfficialAnswerKey(fd);
  else if (ks === "rubric") fd.append("rubric_text", $("rubricText").value);
  const stepRubricFile = $("stepRubricFile")?.files[0];
  fd.append("marks_items", JSON.stringify(state.marksItems));
  fd.append("marks_source", state.marksMethod || "unknown");
  fd.append("marks_attestation", state.marksAttestation || "");

  state.scrolled = false; state.pollFails = 0;
  resetResults();
  $("evaluate").disabled = true;
  $("progress").classList.remove("hidden");
  setBar(0, "Uploading…");
  try {
    if (stepRubricFile) {
      if (stepRubricFile.size > 1000000) throw new Error("Step marking scheme must be smaller than 1 MB.");
      const rubric = await stepRubricFile.text();
      try { JSON.parse(rubric); } catch { throw new Error("Step marking scheme must be valid JSON."); }
      fd.append("step_rubric", rubric);
    }
    const res = await apiFetch(folder ? "/api/bulk/start" : "/api/evaluate", { method: "POST", body: fd });
    let data; try { data = await res.json(); } catch { data = {}; }
    if (!res.ok) {
      if (res.headers.get("X-Marks-Verification-Required") ||
          /verified marks proof|detect.*marks|question.graph.*verif/i.test(data.detail || "")) {
        invalidateMarksAttestation();
        state.marksVerificationDetail = data.detail || "Verify question-wise marks again.";
        state.marksFailureCode = "verification_required";
        state.marksRetryAt = Date.now() + Math.max(0, Number(res.headers.get("Retry-After")) || 0) * 1000;
        $("marksMethod").textContent = state.marksVerificationDetail;
      }
      throw new Error(data.detail || `server returned ${res.status}`);
    }
    state.jobId = data.job_id;
    // Per-job download key — the PDF/ZIP links are plain hrefs and cannot send the
    // Authorization header, so results are gated on this instead of being public.
    state.jobKey = data.download_key || "";
    if (data.attached) toast("A run for this folder is already going. Showing its progress.", 9000);
    $("bulkStop").classList.toggle("hidden", !folder);
    $("bulkStop").disabled = false;
    setBar(0, folder ? `Starting: ${data.to_grade} copies to grade, ${data.already_done} already finished…` : "Queued…");
    state.poll = setInterval(pollJob, 1500);
    pollJob();
  } catch (e) {
    state.evaluationBusy = false;
    toast("Couldn't start evaluation: " + e.message);
    $("progress").classList.add("hidden");
    refreshChecklist();
  }
}

// ---- design and drawing papers ----
function onPaperTypeChange() {
  document.body.classList.toggle("design-mode", isDesign());
  document.body.classList.toggle("teaching-mode", isTeaching());
  document.body.classList.toggle("key-optional-mode", generatesKey());
  document.body.classList.toggle("no-level-mode", noLevel());
  if ($("saFiles")) $("saFiles").accept = sheetAccept().join(",");
  const vidyapeeth = $("paperType").value === "vidyapeeth";
  document.body.classList.toggle("vidyapeeth-mode", vidyapeeth);
  // The batch header comes first; the rest of the form opens after Next.
  document.body.classList.toggle("vp-step1", vidyapeeth && !state.vpHeaderDone);
  if (vidyapeeth) syncVidyapeethDetails();
  showCategoryHint();
  invalidateMarksAttestation();
  toggleSheetSource();
  // Teaching papers have no key unless one is chosen; a category that cannot write
  // its own key never takes the "no answer key" choice.
  const generate = document.querySelector("input[name=keySource][value=generate]");
  const upload = document.querySelector("input[name=keySource][value=upload]");
  if (isTeaching() && generate && !$("akFile").files[0] && !useCombinedPaper()) {
    generate.checked = true; toggleKeySource();
  } else if (!generatesKey() && generate && generate.checked && upload) {
    upload.checked = true; toggleKeySource();
  }
  if (isDesign()) {
    // The key is optional for a design paper and is always an upload.
    const upload = document.querySelector("input[name=keySource][value=upload]");
    if (upload) { upload.checked = true; toggleKeySource(); }
  } else if ($("qpFile").files[0] && !state.marksItems.length) {
    autoDetectMarks();
  }
  refreshChecklist();
}

function resetDesignPaper() {
  state.design.requestVersion += 1;
  state.design.questions = [];
  state.design.notes = [];
  $("designMethod").textContent = "";
  renderDesignTable();
}

function designTotal() {
  return Math.round(state.design.questions.reduce((t, q) => t + (parseFloat(q.marks) || 0), 0) * 100) / 100;
}

function designMarksOk() {
  const qs = state.design.questions;
  return qs.length > 0 && qs.every(q => Number.isFinite(parseFloat(q.marks)) && parseFloat(q.marks) > 0);
}

function renderDesignTable() {
  const tb = $("designTable").querySelector("tbody");
  tb.innerHTML = "";
  state.design.questions.forEach((q, i) => {
    const tr = document.createElement("tr");
    const split = (q.criteria || []).map(c => `${esc(c.name)} ${Math.round(c.weight)}%`).join(" · ");
    tr.innerHTML =
      `<td><input readonly value="${esc(q.qid)}"></td>` +
      `<td class="max"><input type="number" min="0.5" step="0.5" data-i="${i}" value="${q.marks ?? ""}" placeholder="marks"></td>` +
      `<td class="brief">${esc(q.brief || "")}<span class="crit">${split}</span></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("input[data-i]").forEach(input => input.oninput = () => {
    state.design.questions[+input.dataset.i].marks = input.value;
    $("designTotal").textContent = designTotal();
    refreshChecklist();
  });
  $("designTable").classList.toggle("hidden", state.design.questions.length === 0);
  $("designTotal").textContent = designTotal();
}

async function readDesignPaper() {
  const qp = $("qpFile").files[0];
  if (!qp) return toast("Upload the question paper first.");
  if (state.design.busy) return;
  state.design.busy = true;
  const requestVersion = state.design.requestVersion;
  const requestContext = marksContextSnapshot();
  const answerKey = $("akFile").files[0];
  refreshChecklist();
  setBusy($("readDesignPaper"), "Reading the paper…");
  $("designMethod").textContent = "Reading the questions and building the marking rubric. This takes about half a minute.";
  try {
    const fd = new FormData();
    appendPageSelections(fd);
    fd.append("question_paper", qp);
    appendOfficialAnswerKey(fd);
    const res = await apiFetch("/api/design/read-paper", { method: "POST", body: fd });
    let data; try { data = await res.json(); } catch { data = {}; }
    if (requestVersion !== state.design.requestVersion || requestContext !== marksContextSnapshot() ||
        answerKey !== $("akFile").files[0]) return;
    if (!res.ok) throw new Error(data.detail || `server returned ${res.status}`);
    state.design.questions = (data.questions || []).map(q => ({ ...q, marks: q.printed_marks ?? "" }));
    state.design.notes = data.notes || [];
    const printed = state.design.questions.filter(q => q.printed_marks).length;
    $("designMethod").textContent =
      `✅ ${state.design.questions.length} question(s) read${data.title ? " from " + data.title : ""}. ` +
      (printed === state.design.questions.length
        ? "The marks are printed on the paper. Check them below."
        : "Type the marks for each question below.") +
      (state.design.notes.length ? " " + state.design.notes.join(" ") : "");
    renderDesignTable();
  } catch (e) {
    if (requestVersion !== state.design.requestVersion || requestContext !== marksContextSnapshot()) return;
    resetDesignPaper();
    $("designMethod").textContent = "❌ The paper could not be read: " + e.message;
    toast("The paper could not be read: " + e.message, 9000);
  } finally {
    state.design.busy = false;
    unBusy($("readDesignPaper"), "📐 Read questions from the paper");
    refreshChecklist();
  }
}

function refreshDesignChecklist() {
  const qp = $("qpFile").files.length > 0;
  const sa = state.sheets.length;
  const read = state.design.questions.length > 0;
  const marksOk = designMarksOk();
  const rows = [
    [qp, "Question paper uploaded"],
    [sa > 0, `Answer sheet(s) added${sa ? " — " + sa : ""}`],
    [read, state.design.busy ? "Reading the questions from the paper…" : "Questions read from the paper"],
    [marksOk, read ? `Marks entered for every question — total ${designTotal()}` : "Marks entered for every question"],
  ];
  $("checklist").innerHTML = rows.map(([ok, t]) => `<li>${ok ? "✅" : "⬜"} ${esc(t)}</li>`).join("");
  $("evaluate").disabled = state.design.busy || state.evaluationBusy ||
    !(state.googleToken && qp && sa > 0 && read && marksOk);
  $("evaluate").textContent = sa > 1 ? `🚀 Evaluate ${sa} design sheets` : "🚀 Evaluate design sheet";
  $("readDesignPaper").disabled = !state.googleToken || !qp || state.design.busy || state.evaluationBusy;
}

async function evaluateDesign() {
  refreshChecklist();
  if ($("evaluate").disabled) {
    return toast("Read the questions from the paper and enter the marks for each one first.", 9000);
  }
  state.evaluationBusy = true;
  refreshChecklist();
  const fd = new FormData();
  appendPageSelections(fd);
  fd.append("question_paper", $("qpFile").files[0]);
  appendOfficialAnswerKey(fd);
  for (const f of state.sheets) fd.append("answer_sheets", f);
  fd.append("question_marks", state.design.questions.map(q => `${q.qid}=${parseFloat(q.marks)}`).join(", "));
  fd.append("total_marks", String(designTotal()));
  state.scrolled = false; state.pollFails = 0;
  resetResults();
  $("evaluate").disabled = true;
  $("progress").classList.remove("hidden");
  setBar(0, "Uploading…");
  try {
    const res = await apiFetch("/api/evaluate-design", { method: "POST", body: fd });
    let data; try { data = await res.json(); } catch { data = {}; }
    if (!res.ok) throw new Error(data.detail || `server returned ${res.status}`);
    state.jobId = data.job_id;
    state.jobKey = data.download_key || "";
    setBar(0, "Queued…");
    state.poll = setInterval(pollJob, 1500);
    pollJob();
  } catch (e) {
    state.evaluationBusy = false;
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
      $("progress").classList.add("hidden");
      state.evaluationBusy = false;
      refreshChecklist();
      toast("Lost the grading job (the server may have restarted). Any completed sheets are shown above; re-run the rest.");
    }
    return;
  }
  const frac = job.total ? job.done / job.total : 0;
  const b = job.bulk;
  setBar(frac, b
    ? `Graded ${job.done}/${job.total}` + (b.eta_hours != null ? ` · about ${b.eta_hours} h left` : "") +
      (b.stopping ? " · stopping after the copies in progress…" : "")
    : `Graded ${job.done}/${job.total}${job.current ? " · " + job.current : ""}`);

  // Render results LIVE as each sheet finishes — don't make the user wait for the whole batch.
  const finished = job.status === "done" || job.status === "error";
  if ((job.results && job.results.length) || finished) renderResults(job, !finished);

  if (finished) {
    clearInterval(state.poll); state.poll = null;
    $("bulkStop").classList.add("hidden");
    $("progress").classList.add("hidden");
    state.evaluationBusy = false;
    refreshChecklist();
    if (job.status === "error" && !(job.results && job.results.length)) {
      toast("Evaluation failed: " + (job.error || "unknown error"));
    }
  }
}

// ---- results (live; no cost shown) ----
function renderResults(job, inProgress = false) {
  if (job.bulk) return renderFolderRun(job, inProgress);
  // Every copy ends in one of two places: with the STUDENT (release === true) or
  // with a FACULTY REVIEWER (state === "faculty_review", marked, with reasons).
  // Only a copy that could not be marked at all shows as failed.
  const results = job.results || [];
  const ok = results.filter(r => r.ok);
  const released = results.filter(r => r.release === true);
  const review = results.filter(r => r.release !== true && r.state === "faculty_review");
  // Not a genuine answer sheet for this test (typed pages, notes, another paper,
  // a blank or unreadable scan): refused before grading, sent to nobody.
  const rejected = results.filter(r => r.state === "rejected");
  const failed = results.filter(r => !r.ok && r.state !== "faculty_review" && r.state !== "rejected");
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
      metric("To students", released.length) +
      metric("Faculty review", review.length) +
      metric("Rejected (not genuine)", rejected.length) +
      metric("Failed", failed.length);
  }
  // The key contradicted its own worked solution and was corrected (server-side).
  for (const w of job.key_warnings || []) html += `<p class="warn">⚠️ Answer key corrected: ${esc(w)}</p>`;
  $("summary").innerHTML = html;

  const zip = $("zipBtn");
  if (!inProgress && released.length + review.length > 1) { zip.href = `/api/jobs/${state.jobId}/zip?k=${encodeURIComponent(state.jobKey)}`; zip.classList.remove("hidden"); }
  else zip.classList.add("hidden");

  const box = $("resultRows");
  if (total === 1) {
    const r = results[0];
    box.innerHTML = r ? (r.ok || r.state === "faculty_review" ? detailHtml(r) : failHtml(r))
      : `<p class="muted">⏳ Grading…</p>`;
    return;
  }

  // Batch: a row per sheet (completed → score; not-yet → queued), plus details for completed.
  let t = `<table class="rtable"><thead><tr><th>Student</th><th>Score</th><th>%</th><th>Goes to</th></tr></thead><tbody>`;
  for (const r of results) {
    if (r.release === true) {
      t += `<tr><td>${esc(r.student)}</td><td class="s">${fmt(r.score)} / ${fmt(r.max)}</td><td>${r.percent}%</td><td class="ok">✅ student</td></tr>`;
    } else if (r.state === "faculty_review") {
      const score = r.ok ? `${fmt(r.score)} / ${fmt(r.max)}` : "—";
      t += `<tr><td>${esc(r.student)}</td><td class="s">${score}</td><td>${r.ok ? r.percent + "%" : "—"}</td><td class="warn">📝 faculty review</td></tr>`;
    } else if (r.state === "rejected") {
      t += `<tr><td>${esc(r.student)}</td><td>—</td><td>—</td><td class="bad">🚫 rejected, not a genuine copy</td></tr>`;
    } else {
      t += `<tr><td>${esc(r.student)}</td><td>—</td><td>—</td><td class="bad">❌ ${esc(r.error || "failed")}</td></tr>`;
    }
  }
  for (let i = results.length; i < total; i++) {
    const nm = state.sheets[i] ? esc(state.sheets[i].name) : `sheet ${i + 1}`;
    t += `<tr><td class="muted">${nm}</td><td>—</td><td>—</td><td class="muted">${i === results.length ? "⏳ grading…" : "queued"}</td></tr>`;
  }
  t += "</tbody></table>";
  t += results.map(r => {
    if (r.release === true) return `<details class="rdetail"><summary>✅ ${esc(r.student)} — ${fmt(r.score)}/${fmt(r.max)}</summary>${detailHtml(r)}</details>`;
    if (r.state === "faculty_review") return `<details class="rdetail"><summary>📝 ${esc(r.student)} — ${r.ok ? `${fmt(r.score)}/${fmt(r.max)} · ` : ""}faculty review</summary>${detailHtml(r)}</details>`;
    if (r.state === "rejected") return `<details class="rdetail"><summary>🚫 ${esc(r.student)} — rejected</summary>${failHtml(r)}</details>`;
    return `<details class="rdetail"><summary>❌ ${esc(r.student)}</summary>${failHtml(r)}</details>`;
  }).join("");
  box.innerHTML = t;
}

function detailHtml(r) {
  const dl = `/api/jobs/${state.jobId}/pdf/${r.index}?k=${encodeURIComponent(state.jobKey)}`;
  const review = r.release !== true;
  const reasons = (r.review_reasons || r.held_reasons || []);
  if (review && !r.ok) {
    // Nothing could be marked (wrong paper, not an answer sheet): a review
    // request without a PDF.
    return `
      <p class="warn">📝 Needs a faculty reviewer. This copy could not be marked automatically.</p>
      <ul class="reasons">${reasons.map(x => `<li>${esc(x)}</li>`).join("") || `<li>${esc(r.error || "")}</li>`}</ul>`;
  }
  const pct = r.max ? Math.round(r.score / r.max * 100) : 0;
  const q = (r.questions || []).map(x =>
    `<div class="qrow"><span class="q">${esc(x.q)}</span><span class="m">${fmt(x.score)}/${fmt(x.max)}</span><span class="r">${esc(x.remark)}</span></div>`).join("");
  return `
    <div class="scoreband"><div class="big">${fmt(r.score)} / ${fmt(r.max)}</div><div class="pct">${pct}%</div></div>
    ${review
      ? `<p class="warn">📝 <b>Faculty review.</b> The marked copy is ready, but a reviewer should check it before it goes to the student:</p>
         <ul class="reasons">${reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
         <a class="primary-btn small" href="${dl}">⬇️ Download copy for faculty review</a>`
      : `<a class="primary-btn small" href="${dl}">⬇️ Download evaluated PDF</a>`}
    ${r.warning && !review ? `<p class="bad">⚠️ ${esc(r.warning)}</p>` : ""}
    ${r.budget ? `<p class="muted"><b>Provider cost:</b> ${r.budget.rates_found === false || r.budget.usage_attested === false ? "Unavailable (incomplete billing data)" : `₹${Number(r.budget.actual_inr || 0).toFixed(2)}`}${r.budget.enforced === false ? " · No budget cap" : r.budget.cap_inr ? ` / ₹${Number(r.budget.cap_inr).toFixed(0)} cap` : ""}</p>` : ""}
    ${r.remarks ? `<p class="muted"><b>Remarks:</b> ${esc(r.remarks)}</p>` : ""}
    <div style="margin-top:8px">${q}</div>`;
}
function failHtml(r) {
  if (r.state === "rejected") {
    const why = r.error || "This upload is not a genuine answer sheet for this test.";
    return `<p class="bad">🚫 <b>Rejected before checking.</b> ${esc(r.student)}: ${esc(why)}</p>
      <p class="muted">Nothing was graded and nothing goes to the student or to faculty.</p>`;
  }
  const reason = r.error || "Automatic grading failed for this copy.";
  return `<p class="bad">❌ ${esc(r.student)}: ${esc(reason)} Re-run this sheet.</p>`;
}

// ---- folder mode (bulk.py): thousands of copies read from and written to disk ----
function toggleSheetSource() {
  const folder = isFolder();
  $("folderPanel").classList.toggle("hidden", !folder);
  $("uploadSheets").classList.toggle("hidden", folder);
  $("sheetListWrap").classList.toggle("hidden", folder || !state.sheets.length);
}

function appendFolderFields(fd) {
  fd.append("input_folder", $("bulkInput").value.trim());
  fd.append("output_folder", $("bulkOutput").value.trim());
  fd.append("machine", $("bulkMachine").value || "1");
  fd.append("machines", $("bulkMachines").value || "1");
  fd.append("workers", $("bulkWorkers").value || "20");
}

async function checkFolder() {
  if (!$("bulkInput").value.trim()) return toast("Paste the path of the folder that holds the answer sheets.");
  const btn = $("bulkCheck");
  setBusy(btn, "Checking folder…");
  const fd = commonForm();
  appendFolderFields(fd);
  try {
    const res = await apiFetch("/api/bulk/preview", { method: "POST", body: fd });
    let data; try { data = await res.json(); } catch { data = {}; }
    if (!res.ok) throw new Error(data.detail || `server returned ${res.status}`);
    state.bulkPreview = data;
    const e = data.estimate || {};
    const split = data.parts > 1 ? ` This computer takes ${data.this_part} of them (part ${data.part} of ${data.parts}).` : "";
    $("bulkPreview").innerHTML =
      `Found <b>${data.found}</b> answer sheets.${esc(split)} ` +
      `<b>${data.already_done}</b> already finished, <b>${data.to_grade}</b> to grade.` +
      (data.booklets_split ? `<br>${data.booklets_split} student booklets were found inside multi-student PDFs, split at each cover page.` : "") +
      (data.to_grade ? `<br>Rough estimate at ${data.workers} at a time: about <b>${e.hours} hours</b> and <b>₹${Number(e.cost_inr).toLocaleString("en-IN")}</b>.` : "") +
      `<br>Results will be saved in <span class="bulk-path">${esc(data.target)}</span>`;
  } catch (err) {
    state.bulkPreview = null;
    $("bulkPreview").textContent = "";
    toast("Couldn't check the folder: " + err.message, 9000);
  } finally {
    unBusy(btn, "🔎 Check folder");
    refreshChecklist();
  }
}

async function stopFolderRun() {
  if (!state.jobId) return;
  $("bulkStop").disabled = true;
  try {
    const res = await apiFetch(`/api/jobs/${state.jobId}/stop?k=${encodeURIComponent(state.jobKey)}`, { method: "POST" });
    if (!res.ok) throw new Error(`server returned ${res.status}`);
    toast("Stopping after the copies being marked now. Press Start again later to continue.", 9000);
  } catch (err) {
    $("bulkStop").disabled = false;
    toast("Couldn't stop the run: " + err.message);
  }
}

function renderFolderRun(job, inProgress) {
  const b = job.bulk, c = b.counts || {};
  $("results").classList.remove("hidden");
  $("zipBtn").classList.add("hidden");
  $("resultsTitle").textContent = inProgress
    ? `📁 Folder run: ${job.done}/${job.total} graded…`
    : `📁 Folder run ${job.state === "stopped" ? "stopped" : "finished"}: ${job.done}/${job.total} graded`;
  if (!state.scrolled) { $("results").scrollIntoView({ behavior: "smooth" }); state.scrolled = true; }
  $("summary").innerHTML =
    metric("to students", c.released || 0) + metric("faculty review", c.faculty_review || 0) +
    metric("rejected", c.rejected || 0) + metric("failed", c.failed || 0) +
    metric("cost so far", `₹${Number(b.cost_inr || 0).toLocaleString("en-IN")}`);
  let html = `<p class="muted">Saved in <span class="bulk-path">${esc(b.target)}</span>: ` +
    `<b>to_students</b>, <b>faculty_review</b>, <b>rejected</b> and <b>failed</b> folders, ` +
    `and a results CSV. ${b.already_done ? `${b.already_done} copies were already finished before this run. ` : ""}</p>`;
  if (b.stopped_reason) html += `<p class="${/in a row|could not be saved/.test(b.stopped_reason) ? "bad" : "warn"}">${esc(b.stopped_reason)}</p>`;
  for (const w of job.key_warnings || []) html += `<p class="warn">⚠️ Answer key corrected: ${esc(w)}</p>`;
  if (job.error) html += `<p class="bad">❌ ${esc(job.error)}</p>`;
  const rows = [...(job.results || [])].reverse().slice(0, 25);
  if (rows.length) {
    html += `<p class="muted">Latest copies:</p><table class="rtable"><thead><tr><th>Copy</th><th>Score</th><th>Goes to</th></tr></thead><tbody>`;
    for (const r of rows) {
      const score = r.score != null && r.max ? `${fmt(r.score)} / ${fmt(r.max)}` : "—";
      const where = r.state === "released" ? `<td class="ok">✅ student</td>` :
        r.state === "faculty_review" ? `<td class="warn">📝 faculty review</td>` :
        r.state === "rejected" ? `<td class="bad">🚫 rejected</td>` :
        `<td class="bad">❌ ${esc(r.error || "failed")}</td>`;
      html += `<tr><td>${esc(r.student)}</td><td class="s">${score}</td>${where}</tr>`;
    }
    html += "</tbody></table>";
  }
  $("resultRows").innerHTML = html;
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

// ---- Vidyapeeth batch header (15 Oct 2026) ----
// Board, vertical, centre, course, class, subject, test name, test date and mode of
// paper, typed once for the whole upload; the results sheet uses them for every
// student instead of what each cover says. The generic class, subject and answer
// language inputs are filled from these, so the rest of the page works unchanged.
const VP_MODE_LANGUAGE = { english: "english", hindi: "hindi", hinglish: "", urdu: "urdu",
  gujrati: "gujarati", gujarati: "gujarati", marathi: "marathi", bengali: "bengali" };
const VP_OTHER = "__other__";

function fillVidyapeethSelects() {
  const lists = state.vpFields || {};
  document.querySelectorAll("select.vp-field[data-list]").forEach(select => {
    const chosen = select.value;
    const items = Array.isArray(lists[select.dataset.list]) ? lists[select.dataset.list] : [];
    const prefix = select.dataset.prefix || "";
    select.innerHTML = `<option value="">Choose…</option>` +
      items.map(v => `<option value="${esc(v)}">${esc(prefix + v)}</option>`).join("") +
      (select.dataset.other ? `<option value="${VP_OTHER}">Other (type it)</option>` : "");
    if (chosen && [...select.options].some(o => o.value === chosen)) select.value = chosen;
  });
}

function vpValue(id) {
  const el = $(id);
  if (!el) return "";
  if (el.tagName === "SELECT" && el.value === VP_OTHER) return ($(id + "Other")?.value || "").trim();
  return (el.value || "").trim();
}

function vidyapeethGaps() {
  const need = [["vpBoard", "Board"], ["vpVertical", "Vertical"], ["vpCentre", "Centre name"],
    ["vpCourse", "Course"], ["vpClass", "Class"], ["vpSubject", "Subject"], ["vpTestName", "Test name"],
    ["testDate", "Test date"], ["vpMode", "Mode of paper"]];
  const gaps = need.filter(([id]) => !vpValue(id)).map(([, label]) => label);
  if (!(Number($("totalMarks").value) > 0)) gaps.push("Paper total marks");
  return gaps;
}

function syncVidyapeethDetails() {
  if ($("paperType").value !== "vidyapeeth") return;
  document.querySelectorAll("select.vp-field[data-other]").forEach(select => {
    $(select.id + "Other")?.classList.toggle("hidden", select.value !== VP_OTHER);
  });
  const klass = vpValue("vpClass");
  $("studentClass").value = klass ? "Class " + klass : "";
  $("subject").value = vpValue("vpSubject");
  const language = VP_MODE_LANGUAGE[vpValue("vpMode").toLowerCase()];
  if ($("answerLanguage") && language !== undefined) $("answerLanguage").value = language;
  const gaps = vidyapeethGaps();
  if ($("vpNextHint")) $("vpNextHint").textContent = gaps.length ? "Still needed: " + gaps.join(", ") : "";
  refreshChecklist();
}

function vidyapeethNext() {
  const gaps = vidyapeethGaps();
  if (gaps.length) { toast("Fill in: " + gaps.join(", ")); return; }
  state.vpHeaderDone = true;
  document.body.classList.remove("vp-step1");
  document.querySelector("section.card.later-step")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.querySelectorAll(".vp-field, .vp-other, #totalMarks").forEach(el => {
  el.addEventListener("change", syncVidyapeethDetails);
  el.addEventListener("input", syncVidyapeethDetails);
});
if ($("vpNext")) $("vpNext").onclick = vidyapeethNext;
