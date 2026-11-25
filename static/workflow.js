"use strict";
const el = id => document.getElementById(id);
let rows = [], runId = "", busy = false, importId = "", loadedTabs = [], sheetGeneration = 0, requiresGoogleToken = false;
let examDocuments = null, examDocumentsFetch = "", examDocumentsVersion = 0;
// Step 3 shows ONE run: the run this tab started or opened with Show here; it is kept
// per tab (sessionStorage), so each workflow tab follows its own test.
const TAB_RUN_KEY = "workflowTabRun";
try { runId = sessionStorage.getItem(TAB_RUN_KEY) || ""; } catch {}
function setTabRun(id) { runId = id || ""; try { if (runId) sessionStorage.setItem(TAB_RUN_KEY, runId); else sessionStorage.removeItem(TAB_RUN_KEY); } catch {} }
let lastRun = null;            // the run last shown in step 3 (its options fix the portal account pair)
function signedInEmail() {
  try { const part = (window.WorkflowAuth.getToken() || "").split(".")[1].replace(/-/g, "+").replace(/_/g, "/"); return JSON.parse(decodeURIComponent(escape(atob(part)))).email || ""; }
  catch { return ""; }
}
const text = (tag, value) => { const node = document.createElement(tag); node.textContent = value ?? ""; return node; };
function message(value, error = false) { el("message").textContent = value; el("message").classList.toggle("error", error); }
const loginEmail = id => el(id).value.trim().toLowerCase();
function updateAccountPair() {
  const skipAssignment = el("alreadyAssigned").checked;
  el("admin").required = !skipAssignment;
  const admin = loginEmail("admin"), evaluator = loginEmail("evaluator");
  el("accountPairGuidance").textContent = `${skipAssignment ? "Admin login: not needed (assignment skipped)." : `Admin login ID: ${admin || "enter the admin email above"} — assigns copies.`} Evaluator login ID: ${evaluator || "enter the evaluator email above"} — fetches exam PDFs, uploads checked copies and posts rejections.`;
}
function savedAccountPair(options = {}) {
  const admin = options.admin_email || "original shared admin login (legacy run; email was not recorded)";
  return `${options.already_assigned ? "Admin login: not needed (assignment skipped)." : `Admin login ID: ${admin}.`} Evaluator login ID: ${options.evaluator_email || "the evaluator saved for this run"}.`;
}
async function selectedSavedRun() {
  if (!runId) throw new Error("Select a saved run.");
  return lastRun?.id === runId ? lastRun : (await api(`/api/workflow/${runId}`)).json();
}
el("admin").oninput = el("evaluator").oninput = el("alreadyAssigned").onchange = updateAccountPair;
updateAccountPair();
function sheetMessage(value, error = false) { el("sheetStatus").textContent = value; el("sheetStatus").classList.toggle("error", error); message(value, error); }
function sheetGuard(fn) { return guard(async () => { const session = window.WorkflowAuth.getSessionId(); try { await fn(); } catch (error) { if (!error.silent && session === window.WorkflowAuth.getSessionId() && window.WorkflowAuth.getToken()) sheetMessage(error.message, true); throw error; } }); }
function sheetTokenHeaders(headers = {}) { const token = window.WorkflowSheets.getToken(); return token ? {...headers, "X-Google-Sheets-Token": token} : headers; }
async function api(path, options = {}) {
  const token = window.WorkflowAuth.getToken();
  if (!token) throw new Error("Sign in here with your @pw.live account first.");
  const session = window.WorkflowAuth.getSessionId();
  function checkSession() { if (session !== window.WorkflowAuth.getSessionId() || token !== window.WorkflowAuth.getToken()) throw new Error("Your sign-in changed. Please try again."); }
  const response = await fetch(path, {...options, headers: {...options.headers, Authorization: `Bearer ${token}`}});
  checkSession();
  if (!response.ok) { let body; try { body = await response.json(); } catch { body = {}; } checkSession(); const reason = typeof body.detail === "string" ? body.detail : typeof body.detail?.message === "string" ? body.detail.message : `Request failed (${response.status})`; if (response.status === 401) window.WorkflowAuth.invalidate(reason); const error = new Error(reason); error.status = response.status; error.detail = body.detail; throw error; }
  async function read(method) { const data = await response[method](); checkSession(); return data; }
  return {json: () => read("json"), blob: () => read("blob")};
}
function guard(fn) { return async () => { const session = window.WorkflowAuth.getSessionId(); try { await fn(); } catch (error) { if (!error.silent && session === window.WorkflowAuth.getSessionId()) message(error.message, true); } }; }
function testKey(r) { return JSON.stringify([r.program_name, r.test_series_name, r.test_id, r.test_name]); }
function displayed() { return rows.filter(r => testKey(r) === el("test").value); }
function completedSheetRow(row) {
  if (typeof row.skip_processing === "boolean") return row.skip_processing;
  return ["uploaded", "rejected", "not downloadable file", "already evaluated", "duplicate", "superseded"].includes(String(row.workflow_status || "").trim().toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " "));
}
function renderCopies() {
  const body = el("copies"); body.replaceChildren();
  const shown = displayed();
  for (const row of shown) {
    const tr = document.createElement("tr"), td = document.createElement("td"), input = document.createElement("input");
    input.type = "checkbox"; input.disabled = completedSheetRow(row); input.checked = !input.disabled; input.dataset.id = row.submission_id; td.append(input); tr.append(td);
    tr.append(text("td", row.student_name || row.student_id), text("td", row.submission_id), text("td", row.portal_status), text("td", row.workflow_status || "pending")); body.append(tr);
  }
  const skipped = shown.filter(completedSheetRow).length;
  el("selectAll").checked = true; el("count").textContent = `${shown.length - skipped} copies ready${skipped ? ` · ${skipped} completed copies skipped` : ""}`;
  el("totalMarks").value = shown[0]?.total_marks || "";
}
function examDocumentsStatus(value, error = false) { el("examDocumentsStatus").textContent = value; el("examDocumentsStatus").classList.toggle("error", error); }
let previewUrl = "";
function closePreview() { el("documentPreview").classList.add("hidden"); el("documentFrame").removeAttribute("src"); if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = ""; } }
async function showDocument(path, title) {
  const blob = await (await api(path)).blob();
  closePreview(); previewUrl = URL.createObjectURL(blob);
  el("documentPreviewTitle").textContent = title; el("documentFrame").src = previewUrl; el("documentPreview").classList.remove("hidden");
  el("documentPreview").scrollIntoView({block: "nearest"});   // instant: a click during a smooth scroll can miss its button
}
el("closeDocumentPreview").onclick = closePreview;
function previewButton(label, path, title) { const button = text("button", label); button.type = "button"; button.onclick = guard(() => showDocument(path, title)); return button; }
function keyRequired() { return !el("useQuestionPaperAsKey").checked && !examDocuments?.documents?.answer_key; }
function clearExamDocuments(note = "") {
  examDocumentsVersion += 1;
  if (examDocumentsFetch) { const fetchId = examDocumentsFetch; examDocumentsFetch = ""; guard(async () => { await api(`/api/workflow/exam-documents/${fetchId}/cancel`, {method: "POST"}); })(); }
  examDocuments = null; el("clearExamDocuments").classList.add("hidden"); el("fetchExamDocuments").disabled = false; el("examDocumentsActions").replaceChildren();
  examDocumentsStatus(note); el("paper").required = true; el("key").required = keyRequired();
}
function applyExamDocuments(entry) {
  examDocuments = entry; const d = entry.documents, parts = [];
  if (d.question_paper) parts.push(`question paper ${d.question_paper.filename} (${d.question_paper.pages} pages)`);
  if (d.answer_key) parts.push(`Model Answer ${d.answer_key.files > 1 ? d.answer_key.files + " files merged, " : ""}${d.answer_key.pages} pages`);
  examDocumentsStatus(`Fetched from the portal: ${parts.join(" · ")}. An uploaded file takes priority.`);
  const actions = el("examDocumentsActions"); actions.replaceChildren();
  if (d.question_paper) actions.append(previewButton("View question paper", `/api/workflow/exam-documents/${entry.fetch_id}/question_paper`, `Question paper fetched from the portal: ${d.question_paper.filename}`));
  if (d.answer_key) actions.append(previewButton("View Model Answer", `/api/workflow/exam-documents/${entry.fetch_id}/answer_key`, `Model Answer fetched from the portal: ${d.answer_key.sources.join(", ")}`));
  el("clearExamDocuments").classList.remove("hidden"); el("paper").required = !d.question_paper; el("key").required = keyRequired();
}
el("fetchExamDocuments").onclick = guard(async () => {
  const selected = displayed()[0];
  if (!selected) throw new Error("Import a sheet and select a test first.");
  const evaluator = loginEmail("evaluator");
  if (!evaluator) throw new Error("Enter the evaluator email in step 2 first: the portal is read through that evaluator's Chrome window.");
  if (!el("evaluator").checkValidity()) throw new Error("Enter a valid evaluator login email in step 2.");
  clearExamDocuments(`Starting… Use EVALUATOR login ID ${evaluator} in its portal Chrome window.`);
  const version = examDocumentsVersion; el("fetchExamDocuments").disabled = true;
  try {
    const body = JSON.stringify({test_id: selected.test_id, test_name: selected.test_name, program_name: selected.program_name, test_series_name: selected.test_series_name, evaluator_email: evaluator, portal_direct: el("portalDirect").checked});
    let entry = await (await api("/api/workflow/exam-documents", {method: "POST", headers: {"Content-Type": "application/json"}, body})).json();
    if (version !== examDocumentsVersion) return;
    examDocumentsFetch = entry.fetch_id;
    while (!["ready", "failed"].includes(entry.status)) {
      examDocumentsStatus(entry.detail || entry.status);
      await new Promise(resolve => setTimeout(resolve, 2000));
      if (version !== examDocumentsVersion) return;
      entry = await (await api(`/api/workflow/exam-documents/${entry.fetch_id}`)).json();
      if (version !== examDocumentsVersion) return;
    }
    examDocumentsFetch = "";
    if (entry.status === "failed") throw new Error(entry.error || "The portal fetch failed.");
    applyExamDocuments(entry);
  } catch (error) { if (version === examDocumentsVersion) { examDocumentsFetch = ""; examDocumentsStatus(error.message, true); } throw error; }
  finally { if (version === examDocumentsVersion) el("fetchExamDocuments").disabled = false; }
});
el("clearExamDocuments").onclick = () => clearExamDocuments("Using uploaded files.");
el("viewRunPaper").onclick = guard(() => showDocument(`/api/workflow/${runId}/document/question_paper`, "Question paper used by the selected run"));
el("viewRunKey").onclick = guard(() => showDocument(`/api/workflow/${runId}/document/answer_key`, "Answer key used by the selected run"));
el("changeInputs").onclick = guard(async () => {
  if (!runId || !lastRun) throw new Error("Open a run in this tab first (Show here on a saved run).");
  const o = lastRun.options || {};
  el("runEvaluator").value = o.evaluator_email || ""; el("runAdmin").value = o.admin_email || ""; el("runTotal").value = o.total_marks || "";
  el("runThreshold").value = o.review_min_confidence ?? 0; el("runAlertEmail").value = o.alert_email || signedInEmail();
  el("runAutoRejections").checked = o.submit_rejections !== false;
  const check = lastRun.marks_check || {};
  if (o.operator_marks) showMarks("marksHomeRun", o.operator_marks.items, marksChoiceLines(o.operator_marks.choices), true);
  else showMarks("marksHomeRun", check.items, marksChoiceLines(check.operator_choices?.length ? check.operator_choices : check.choice_groups), false);
  el("runInputsPanel").classList.remove("hidden");
});
el("cancelRunInputs").onclick = () => { el("runInputsPanel").classList.add("hidden"); hideRunMarks(); };
el("saveRunInputs").onclick = guard(async () => {
  const body = {evaluator_email: el("runEvaluator").value.trim(), admin_email: el("runAdmin").value.trim(), total_marks: el("runTotal").value.trim(),
                review_min_confidence: Number(el("runThreshold").value), alert_email: el("runAlertEmail").value.trim(), submit_rejections: el("runAutoRejections").checked,
                operator_marks: el("useOperatorMarks").checked ? readMarks() : null};
  await api(`/api/workflow/${runId}/inputs`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  el("runInputsPanel").classList.add("hidden"); hideRunMarks();
  message("Run inputs saved. Click Resume / retry: copies assigned to the old evaluator are moved to the new one, skipped copies get another go, and held faculty-review copies are reconsidered.");
  await poll();
});
async function openCheckedFolder(id) {
  const reply = await (await api(`/api/workflow/${id}/open-folder`, {method: "POST"})).json();
  message(`Opened the checked copies folder: ${reply.folder}`);
}
el("openCheckedFolder").onclick = guard(() => openCheckedFolder(runId));
el("downloadReport").onclick = guard(async () => {
  const blob = await (await api(`/api/workflow/${runId}/report.csv`)).blob();
  const url = URL.createObjectURL(blob), a = document.createElement("a"); a.href = url; a.download = `run-${runId.slice(0, 8)}-report.csv`; a.click(); URL.revokeObjectURL(url);
});
el("test").onchange = guard(async () => { renderCopies(); clearExamDocuments(); });
el("selectAll").onchange = () => el("copies").querySelectorAll("input:not(:disabled)").forEach(i => { i.checked = el("selectAll").checked; });
function clearCopies() { rows = []; el("copies").replaceChildren(); el("count").textContent = ""; el("totalMarks").value = ""; el("test").replaceChildren(text("option", "Read a tab first")); clearExamDocuments(); }
function resetSheet() { sheetGeneration += 1; importId = ""; loadedTabs = []; requiresGoogleToken = false; const option = text("option", "Load a sheet to see its tabs"); option.value = ""; el("sheetTab").replaceChildren(option); el("sheetSource").textContent = ""; el("sheetStatus").textContent = ""; clearCopies(); }
el("sheetUrl").oninput = () => { el("sheetFile").value = ""; resetSheet(); };
el("sheetFile").onchange = () => { if (el("sheetFile").files[0]) el("sheetUrl").value = ""; resetSheet(); };
el("sheetTab").onchange = () => { sheetGeneration += 1; clearCopies(); };
async function loadTabs() {
  resetSheet(); const version = sheetGeneration;
  sheetMessage("Loading sheet tabs…");
  const fd = new FormData();
  if (el("sheetFile").files[0]) fd.append("sheet", el("sheetFile").files[0]);
  else { if (!el("sheetUrl").value.trim()) throw new Error("Enter a Google Sheet URL or upload an Excel / CSV file."); fd.append("sheet_url", el("sheetUrl").value.trim()); }
  const googleToken = el("sheetFile").files[0] ? "" : window.WorkflowSheets.getToken();
  el("loadTabs").disabled = true;
  let data;
  try { data = await (await api("/api/workflow/sheet-tabs", {method: "POST", body: fd, headers: googleToken ? {"X-Google-Sheets-Token": googleToken} : {}})).json(); }
  catch (error) { if (version !== sheetGeneration) error.silent = true; throw error; }
  finally { el("loadTabs").disabled = false; }
  if (version !== sheetGeneration) return false;
  importId = data.import_id; loadedTabs = data.tabs; requiresGoogleToken = Boolean(data.requires_google_token); clearCopies();
  const select = el("sheetTab"); select.replaceChildren();
  if (loadedTabs.length > 1) { const placeholder = text("option", "Choose a tab…"); placeholder.value = ""; select.append(placeholder); }
  for (const tab of loadedTabs) { const option = text("option", tab.name); option.value = tab.name; select.append(option); }
  el("sheetSource").textContent = `Loaded ${data.source_name || "sheet"} · ${loadedTabs.length} tab${loadedTabs.length === 1 ? "" : "s"}`;
  sheetMessage("Choose a tab, then click Read selected tab.");
  return true;
}
el("connectSheets").onclick = sheetGuard(async () => { await window.WorkflowSheets.connect(); resetSheet(); if (el("sheetUrl").value.trim() && !el("sheetFile").files[0]) await loadTabs(); });
el("loadTabs").onclick = sheetGuard(loadTabs);
el("preview").onclick = sheetGuard(async () => {
  if (!importId && !await loadTabs()) return;
  const worksheet = el("sheetTab").value;
  if (!worksheet) { sheetMessage("Select the tab you want to import."); return; }
  const version = sheetGeneration; clearCopies(); sheetMessage(`Reading ${worksheet}… Large tabs may take a little longer.`); const fd = new FormData();
  fd.append("import_id", importId); fd.append("worksheet", worksheet);
  const googleToken = requiresGoogleToken ? window.WorkflowSheets.getToken() : "";
  let data;
  el("preview").disabled = true;
  try { data = await (await api("/api/workflow/preview", {method: "POST", body: fd, headers: googleToken ? {"X-Google-Sheets-Token": googleToken} : {}})).json(); }
  catch (error) { if (version !== sheetGeneration) error.silent = true; else if (error.status === 404) resetSheet(); throw error; }
  finally { el("preview").disabled = false; }
  if (version !== sheetGeneration) return;
  rows = data.rows;
  const select = el("test"); select.replaceChildren(); const seen = new Set();
  for (const row of rows) { const key = testKey(row); if (seen.has(key)) continue; seen.add(key); const option = text("option", `${row.test_series_name} — ${row.test_name}`); option.value = key; select.append(option); }
  renderCopies(); sheetMessage(`Imported ${rows.length} copies. Select one test and supply its exam inputs.`);
});
el("start").onclick = guard(async () => {
  if (busy) return;
  const ids = new Set([...el("copies").querySelectorAll("input:checked")].map(i => i.dataset.id));
  const selected = displayed().filter(r => ids.has(r.submission_id) && !completedSheetRow(r));
  if (!selected.length) throw new Error("Select at least one copy.");
  const combinedPaper = el("useQuestionPaperAsKey").checked, fetched = examDocuments?.documents || {};
  if (!(el("paper").files[0] || fetched.question_paper) || !(combinedPaper || el("key").files[0] || fetched.answer_key)) throw new Error("Upload the official question paper and marking scheme, fetch them from the portal, or select the combined PDF option.");
  updateAccountPair();
  if (!el("admin").checkValidity()) throw new Error("Enter a valid admin login email in step 2, or skip assignment if every selected copy is already assigned to this evaluator.");
  if (!el("evaluator").checkValidity()) throw new Error("Enter a valid evaluator login email in step 2.");
  const options = {admin_email: loginEmail("admin"), evaluator_email: loginEmail("evaluator"), deadline: el("deadline").value, category: el("category").value, student_class: el("studentClass").value.trim(), subject: el("subject").value.trim(), copies_in_flight: Number(el("copiesInFlight").value) || 5, total_marks: el("totalMarks").value, already_assigned: el("alreadyAssigned").checked,
                   alert_email: el("alertEmail").value.trim() || signedInEmail(), review_min_confidence: Number(el("reviewThreshold").value), submit_rejections: el("autoRejections").checked, notice_for_unreadable: el("noticeForUnreadable").checked, portal_direct: el("portalDirect").checked,
                   operator_marks: el("useOperatorMarks").checked && el("marksPanel").parentElement === el("marksHomeStart") ? readMarks() : null};
  const fd = new FormData(); fd.append("rows", JSON.stringify(selected)); fd.append("options", JSON.stringify(options));
  if (importId) { fd.append("import_id", importId); fd.append("worksheet", el("sheetTab").value); }
  if (el("paper").files[0]) fd.append("question_paper", el("paper").files[0]);
  if (!combinedPaper && el("key").files[0]) fd.append("answer_key", el("key").files[0]);
  if (examDocuments) fd.append("exam_documents_id", examDocuments.fetch_id);
  fd.append("question_page_range", el("questionPageRange").value.trim()); fd.append("answer_page_range", el("answerPageRange").value.trim()); fd.append("use_question_paper_as_key", String(combinedPaper));
  busy = true; el("start").disabled = true;
  message("Checking the question paper's marks before anything starts (this can take a minute)…");
  try { let run;
    try { run = await (await api("/api/workflow/start", {method: "POST", body: fd, headers: sheetTokenHeaders()})).json(); }
    catch (error) {
      const marks = error.detail?.marks;
      if (marks) {   // the paper's marks need fixing: nothing started; show them to correct
        const lines = marks.operator_choices?.length ? marksChoiceLines(marks.operator_choices) : marksChoiceLines(marks.choice_groups);
        showMarks("marksHomeStart", marks.items, lines, Boolean(marks.items?.length));
        el("marksPanel").scrollIntoView({block: "nearest"});
      }
      throw error;
    }
    setTabRun(run.id); await refreshRuns(); message(`Workflow started. ${savedAccountPair(options)} ${options.already_assigned ? "Assignment is skipped; use the evaluator's Chrome window for upload." : "Use the admin's Chrome window for assignment and the evaluator's Chrome window for upload."} Existing sessions are reused; each login banner names its account.`); }
  finally { busy = false; el("start").disabled = false; }
});
el("useQuestionPaperAsKey").onchange = () => {
  const combined = el("useQuestionPaperAsKey").checked;
  el("separateKeyFile").classList.toggle("hidden", combined);
  el("key").disabled = combined;
  el("key").required = keyRequired();
};
// ----------------------------------------------------------------------------- the paper's marks
// Checked on Start (and before a Resume touches any copy). When they cannot be read or do not
// add up to the official total, nothing starts; this editor shows what was read to correct, or
// takes the marks from scratch. The server checks them again, by the same rules as a reading.
function marksChoiceLines(groups) {
  return (groups || []).map(group => group.select !== undefined
    ? `${group.select} of ${(group.options || []).map(option => option.join("+")).join(", ")}`
    : `${group.select_count} of ${(group.options || []).map(option => (option.question_ids || []).join("+")).join(", ")}`).join("\n");
}
function addMarksRow(qid = "", max = "") {
  const tr = document.createElement("tr"), question = document.createElement("input"), marks = document.createElement("input"), remove = text("button", "Remove");
  question.type = "text"; question.className = "marksQid"; question.value = qid; question.placeholder = "Q1"; question.setAttribute("aria-label", "Question");
  marks.type = "number"; marks.className = "marksMax"; marks.min = "0.5"; marks.step = "0.5"; marks.value = max; marks.setAttribute("aria-label", "Marks");
  remove.type = "button"; remove.onclick = () => { tr.remove(); updateMarksSum(); };
  question.oninput = marks.oninput = updateMarksSum;
  tr.append(...[question, marks, remove].map(node => { const td = document.createElement("td"); td.append(node); return td; }));
  el("marksRows").append(tr);
}
function readMarks() {
  const items = [...el("marksRows").querySelectorAll("tr")].map(tr => ({qid: tr.querySelector(".marksQid").value.trim(), max: Number(tr.querySelector(".marksMax").value)}))
    .filter(row => row.qid || row.max);
  const choices = el("marksChoices").value.split("\n").map(line => line.trim()).filter(Boolean).map(line => {
    const found = line.match(/^(\d+)\s+of\s+(.+)$/i);
    if (!found) throw new Error(`Write each choice rule like "2 of Q21, Q22, Q23": ${line}`);
    return {select: Number(found[1]), options: found[2].split(",").map(option => option.split("+").map(qid => qid.trim()).filter(Boolean)).filter(option => option.length)};
  });
  return {items, choices};
}
function updateMarksSum() {
  let marks;
  try { marks = readMarks(); } catch (error) { el("marksSum").textContent = error.message; return; }
  const under = (row, qid) => row.qid === qid || row.qid.startsWith(qid + ".") || row.qid.startsWith(qid + "(");
  const chosen = new Set();
  let total = 0;
  for (const choice of marks.choices) {
    const values = choice.options.map(option => option.reduce((sum, qid) => sum + marks.items.filter(row => under(row, qid)).reduce((s, row) => s + (row.max || 0), 0), 0)).sort((a, b) => b - a);
    total += values.slice(0, choice.select).reduce((sum, value) => sum + value, 0);
    for (const option of choice.options) for (const qid of option) for (const row of marks.items) if (under(row, qid)) chosen.add(row.qid);
  }
  total += marks.items.filter(row => !chosen.has(row.qid)).reduce((sum, row) => sum + (row.max || 0), 0);
  const official = Number(el({marksHomeRun: "runTotal", marksHomeAutopilot: "apMarksTotal"}[el("marksPanel").parentElement.id] || "totalMarks").value);
  el("marksSum").textContent = `These marks add up to ${+total.toFixed(2)}${marks.choices.length ? " after the choice rules" : ""}; the official total is ${official || "not entered yet"}.`
    + (official && Math.abs(total - official) > 0.01 ? " They must agree before the run can start." : "");
}
function showMarks(home, items, choiceLines, use) {
  el(home).append(el("marksPanel")); el("marksPanel").classList.remove("hidden");
  el("marksRows").replaceChildren();
  for (const item of items || []) addMarksRow(item.qid, item.max);
  if (!(items || []).length) addMarksRow();
  el("marksChoices").value = choiceLines || ""; el("useOperatorMarks").checked = Boolean(use);
  updateMarksSum();
}
function hideRunMarks() { if (el("marksPanel").parentElement === el("marksHomeRun")) { el("marksHomeStart").append(el("marksPanel")); el("marksPanel").classList.add("hidden"); } }
el("openMarks").onclick = () => { if (el("marksPanel").classList.contains("hidden") || el("marksPanel").parentElement !== el("marksHomeStart")) showMarks("marksHomeStart", [], "", true); };
el("addMarksRow").onclick = () => { addMarksRow(); updateMarksSum(); };
el("marksChoices").oninput = updateMarksSum;
el("totalMarks").addEventListener("input", updateMarksSum); el("runTotal").addEventListener("input", updateMarksSum);
function stageCount(run, ...stages) { return run.rows.filter(r => stages.includes(r.stage)).length; }
const ATTENTION_STAGES = ["faculty_review", "rejected", "upload_uncertain", "assignment_uncertain", "rejection_uncertain"];
const STAGE_TEXT = {pending: "waiting", checked: "checked, waiting to upload", faculty_review: "held for faculty review", rejected: "rejected, not posted yet",
                    rejection_submitted: "rejection posted", already_evaluated: "already evaluated on the portal",
                    assignment_uncertain: "assignment not confirmed: check the portal", upload_uncertain: "upload not confirmed: check the portal",
                    rejection_uncertain: "rejection not confirmed: check the portal"};
function flaggedCount(run) { return run.rows.filter(r => r.stage === "uploaded" && r.flagged_for_review).length; }
function stageText(row) { return row.stage === "uploaded" && row.flagged_for_review ? "uploaded, flagged for faculty review" : STAGE_TEXT[row.stage] || row.stage.replaceAll("_", " "); }
function statusText(run) {
  const skipped = stageCount(run, "skipped"), deciding = stageCount(run, ...ATTENTION_STAGES);
  if (run.status === "complete") return skipped ? `finished, ${skipped} skipped` : "complete";
  if (run.status === "needs_attention") return `finished, ${deciding} need${deciding === 1 ? "s" : ""} a decision${skipped ? `, ${skipped} skipped` : ""}`;
  return run.status.replaceAll("_", " ");
}
async function refreshRuns() {
  const data = await (await api("/api/workflow/runs")).json(); el("savedRuns").replaceChildren();
  const every = {rows: data.runs.flatMap(run => run.rows)};
  el("savedTotals").textContent = data.runs.length ? `All saved runs on this computer. ${copyTotals({uploaded: stageCount(every, "uploaded"), already_evaluated: stageCount(every, "already_evaluated"),
    rejected: stageCount(every, "rejection_submitted"), rejected_waiting: stageCount(every, "rejected"), checked_waiting: stageCount(every, "checked"), skipped: stageCount(every, "skipped")})}` : "";
  for (const run of data.runs) {
    const tr = document.createElement("tr"), show = text("button", run.id === runId ? "Shown here" : "Show here"); show.type = "button"; show.disabled = run.id === runId;
    show.onclick = guard(async () => { setTabRun(run.id); el("runInputsPanel").classList.add("hidden"); await refreshRuns(); el("currentRunTitle").scrollIntoView({block: "nearest"}); });
    const action = document.createElement("td"); action.append(show);
    tr.append(text("td", run.created_at.slice(0, 16).replace("T", " ")), text("td", run.rows[0]?.test_name || ""), text("td", statusText(run)),
              text("td", `${stageCount(run, "uploaded")} / ${run.rows.length}`), text("td", stageCount(run, "already_evaluated")), text("td", `${flaggedCount(run)} / ${stageCount(run, "faculty_review")}`),
              text("td", `${stageCount(run, "rejection_submitted")} / ${stageCount(run, "rejected")}`), text("td", stageCount(run, "skipped")),
              text("td", run.options?.evaluator_email || ""), action);
    el("savedRuns").append(tr);
  }
  if (runId && !data.runs.some(run => run.id === runId)) setTabRun("");
  await poll();
}
async function poll() {
  if (!runId) {
    lastRun = null; el("progress").replaceChildren(); el("runStatus").textContent = "";
    el("currentRunTitle").textContent = "No run in this tab yet. Start one above, or choose Show here on a saved run below.";
    el("runAccounts").textContent = "This tab has no run open."; el("submitRejections").disabled = true;
    el("viewRunPaper").disabled = el("viewRunKey").disabled = el("downloadReport").disabled = el("changeInputs").disabled = true;
    el("openCheckedFolder").disabled = true; el("checkedFolder").textContent = "";
    return;
  }
  const selectedRun = runId;
  const run = await (await api(`/api/workflow/${selectedRun}`)).json();
  if (runId !== selectedRun) return;
  lastRun = run;
  el("currentRunTitle").textContent = `${run.rows[0]?.test_name || "Run"} — ${statusText(run)} (started ${run.created_at.slice(0, 16).replace("T", " ")})`;
  el("runAccounts").textContent = `Saved run's portal accounts — ${savedAccountPair(run.options)} Resume / retry uses these saved accounts; editing step 2 applies to new runs.`;
  const rejectedWaiting = run.rows.filter(r => r.stage === "rejected").length, rejectionsPosted = run.rows.filter(r => r.stage === "rejection_submitted").length;
  const skipped = run.rows.filter(r => r.stage === "skipped").length, flagged = flaggedCount(run), held = stageCount(run, "faculty_review");
  const meaning = {interrupted: " (this app was restarted; click Resume / retry to continue)", complete: skipped ? " (Resume / retry tries the skipped copies again)" : "", needs_attention: " (the copies marked below need a decision; the report lists them)", stopped: " (stopped by you)", failed: " (could not run)"}[run.status] || "";
  el("runStatus").textContent = `${statusText(run)}${meaning} · ${run.rows.filter(r => r.stage === "uploaded").length}/${run.rows.length} uploaded${flagged ? ` (${flagged} flagged for faculty review)` : ""}${stageCount(run, "already_evaluated") ? ` · ${stageCount(run, "already_evaluated")} already evaluated` : ""}${held ? ` · ${held} held for faculty review` : ""}${skipped ? ` · ${skipped} skipped` : ""}${run.notice ? ` · ${run.notice}` : ""}${rejectedWaiting || rejectionsPosted ? ` · ${rejectionsPosted} rejection${rejectionsPosted === 1 ? "" : "s"} posted, ${rejectedWaiting} waiting` : ""}${run.error ? " · " + run.error : ""}`;
  el("submitRejections").disabled = run.status === "running" || !rejectedWaiting;
  el("viewRunPaper").disabled = el("viewRunKey").disabled = el("downloadReport").disabled = el("changeInputs").disabled = false;
  const saved = run.rows.filter(r => r.checked_file).length, waiting = stageCount(run, "checked");
  el("openCheckedFolder").disabled = !saved;
  el("checkedFolder").textContent = run.checked_folder ? `Checked copies: ${saved} saved in ${run.checked_folder}${waiting ? ` · ${waiting} checked, waiting to upload` : ""}${run.checking_done && run.status === "running" ? " · every copy is checked; uploads go on" : ""}` : "";
  el("progress").replaceChildren();
  for (const row of run.rows) {
    const rejection = ["rejected", "rejection_uncertain", "rejection_submitted"].includes(row.stage) && row.rejection_reason ? `Rejection comment: ${row.rejection_reason}` : "";
    const sheetError = row.sheet_sync_error ? `Student sheet status pending: ${row.sheet_sync_error}` : "";
    const cleaned = row.files_cleaned ? "Local PDFs removed after confirmed portal submission." : "";
    const tr = document.createElement("tr"), details = text("td", [rejection, row.error && row.error !== row.rejection_reason ? row.error : "", row.note, sheetError, cleaned].filter(Boolean).join(" · "));
    if (!row.files_cleaned && ((row.has_checked_pdf !== false && ["checked", "faculty_review", "uploaded", "upload_uncertain"].includes(row.stage)) ||
        (row.stage === "skipped" && row.has_checked_pdf === true))) {
      const button = text("button", "Download checked PDF"); button.type = "button";
      button.onclick = guard(async () => { const blob = await (await api(`/api/workflow/${selectedRun}/copy/${row.submission_id}`)).blob(); const url = URL.createObjectURL(blob), a = document.createElement("a"); a.href = url; a.download = `checked_${row.submission_id}.pdf`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }); details.append(document.createElement("br"), button);
    }
    if (["assignment_uncertain", "upload_uncertain", "rejection_uncertain"].includes(row.stage)) {
      for (const [label, outcome] of [["Confirmed in portal", "confirmed"], ["Verified not done", "not_done"]]) {
        const button = text("button", label); button.type = "button"; button.disabled = run.status === "running";
        button.onclick = guard(async () => { await api(`/api/workflow/${selectedRun}/reconcile`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({submission_id: row.submission_id, outcome})}); await poll(); }); details.append(document.createElement("br"), button);
      }
    }
    const operation = run.status === "running" ? (row.operation || (run.current?.submission_id === row.submission_id ? run.current.operation : "")) : "";
    tr.append(text("td", row.student_name || row.student_id), text("td", operation ? operation + "…" : stageText(row)), text("td", row.awarded_marks ?? ""), details); el("progress").append(tr);
  }
}
el("refresh").onclick = guard(refreshRuns);
el("resume").onclick = guard(async () => { const run = await selectedSavedRun(); await api(`/api/workflow/${run.id}/resume`, {method: "POST", headers: sheetTokenHeaders()}); message(`Resuming with the saved portal accounts. ${savedAccountPair(run.options)} Completed stages are kept; portal actions with uncertain outcomes need reconciliation.`); await poll(); });
el("stop").onclick = guard(async () => { if (runId) { const data = await (await api(`/api/workflow/${runId}/stop`, {method: "POST"})).json(); message(data.status === "not running in this app" ? "This run is not active in this app (it was interrupted). Use Resume / retry to continue it here." : "Stopping after the current operation finishes."); } });
el("submitRejections").onclick = guard(async () => {
  const run = await selectedSavedRun(), evaluator = run.options?.evaluator_email || "saved for this run";
  if (!window.confirm(`Post every rejected copy of this run to the portal: the original PDF, the rejection reason as the comment and total marks 0, from EVALUATOR login ID ${evaluator}. Continue?`)) return;
  await api(`/api/workflow/${run.id}/submit-rejections`, {method: "POST"}); message(`Submitting rejected copies from EVALUATOR login ID ${evaluator}. Sign in to that account in the Chrome window if it asks.`); await poll();
});
const tomorrow = new Date(Date.now() + 86400000); tomorrow.setMinutes(tomorrow.getMinutes() - tomorrow.getTimezoneOffset()); el("deadline").value = tomorrow.toISOString().slice(0, 16);
setInterval(() => { if (runId && window.WorkflowAuth.getToken()) guard(poll)(); }, 4000);
// ----------------------------------------------------------------------------- alerts
function showAlerts(state) {
  const active = state?.active;
  el("alertBanner").classList.toggle("hidden", !active);
  if (active) el("alertText").textContent = `${active.title}. ${active.body}`;
  const email = state?.email || {};
  el("alertStatus").textContent = `Email ${email.configured ? `set up from ${email.sender}` : "not set up yet: save the sender's app password"}.${email.detail ? ` Last email: ${email.detail}` : ""}`;
  if (email.sender && document.activeElement !== el("alertSender")) el("alertSender").value = email.sender;
}
async function pollAlerts() { showAlerts(await (await api("/api/workflow/alerts")).json()); }
el("dismissAlert").onclick = guard(async () => showAlerts(await (await api("/api/workflow/alerts/ack", {method: "POST"})).json()));
el("saveAlertEmail").onclick = guard(async () => {
  const body = {sender: el("alertSender").value.trim()}; if (el("alertPassword").value) body.app_password = el("alertPassword").value;
  showAlerts(await (await api("/api/workflow/alerts/email", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})).json());
  el("alertPassword").value = ""; message("Email settings saved on this computer.");
});
el("testAlert").onclick = guard(async () => {
  const to = el("alertEmail").value.trim() || signedInEmail();
  showAlerts(await (await api("/api/workflow/alerts/test", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({to})})).json());
  message(`Test alert raised: a Windows notification and this page's banner (no sound), and an email to ${to || "nobody (no alert email)"}.`);
});
setInterval(() => { if (window.WorkflowAuth.getToken()) guard(pollAlerts)(); }, 4000);
// ----------------------------------------------------------------------------- autopilot
// Two sheets in, every test checked: the server reads both tabs once (Check sheets), the
// operator may correct each test's inputs, and Start hands the plan to the pairs.
let autopilotPlan = null, autopilotPairs = [];
const AP_CATEGORIES = [["school", "School board"], ["vidyapeeth", "Vidyapeeth"], ["commerce", "Commerce"], ["ca", "CA / CMA / CS"]];
async function importSheet(url) {
  if (!window.WorkflowSheets.getToken()) await window.WorkflowSheets.connect();
  const fd = new FormData(); fd.append("sheet_url", url);
  return (await api("/api/workflow/sheet-tabs", {method: "POST", body: fd, headers: {"X-Google-Sheets-Token": window.WorkflowSheets.getToken()}})).json();
}
function apCell(value, field, test) {
  const td = document.createElement("td");
  if (!autopilotPlan) { td.textContent = value ?? ""; return td; }
  let input;
  if (field === "category") {
    input = document.createElement("select");
    input.append(text("option", "(unknown)"), ...AP_CATEGORIES.map(([value, label]) => { const option = text("option", label); option.value = value; return option; }));
    input.firstChild.value = ""; input.value = value || "";
  } else { input = document.createElement("input"); input.type = field === "total_marks" ? "number" : "text"; input.value = value ?? ""; }
  input.dataset.key = test.key; input.dataset.field = field; input.setAttribute("aria-label", `${field.replace("_", " ")} of ${test.test_name}`);
  td.append(input); return td;
}
function copyTotals(totals) {
  const extra = [[totals.rejected_waiting, "rejected, waiting to post"], [totals.checked_waiting, "checked, waiting to upload"], [totals.skipped, "skipped"]]
    .filter(([count]) => count).map(([count, label]) => ` · ${count} ${label}`).join("");
  return `Copies: ${totals.uploaded} uploaded · ${totals.already_evaluated} already evaluated · ${totals.rejected} rejected${extra}${totals.copies ? ` (of ${totals.copies})` : ""}`;
}
function apLink(url, label) {
  if (!/^https?:\/\//i.test(url || "")) return null;
  const link = text("a", label); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
}
function showMarksProblems(tests) {
  const problems = tests.filter(test => test.status === "marks_problem");
  el("apMarks").hidden = !problems.length;
  el("apMarksTitle").textContent = `Marks problems so far: ${problems.length} (${problems.reduce((sum, test) => sum + Number(test.copies || 0), 0)} copies)`;
  el("apMarksLinks").hidden = problems.every(test => test.paper_link);
  el("apMarksRows").replaceChildren(...problems.map(test => { const tr = document.createElement("tr"), paper = document.createElement("td"), answers = document.createElement("td");
    paper.append(apLink(test.paper_link, "Open paper") || "Not read yet");
    (test.key_links || []).forEach((url, index, all) => { const link = apLink(url, all.length > 1 ? `Model Answer ${index + 1}` : "Open Model Answer"); if (link) answers.append(link, " "); });
    const action = document.createElement("td"), enter = text("button", "Enter marks"), again = text("button", "Check again");
    enter.type = again.type = "button"; enter.onclick = () => openTestMarks(test);
    again.onclick = guard(async () => { await sendTestMarks({key: test.key}); message(`${test.test_name}: its marks will be read again.`); });
    action.append(enter, " ", again);
    tr.append(text("td", test.tab ? `${test.test_name} (tab: ${test.tab})` : test.test_name), text("td", test.copies), text("td", test.detail), paper, answers, action); return tr; }));
}
// The portal has no Model Answer for some tests. Such a test waits for one instead of being
// skipped: the file is given here and every tab of that test runs with it (19 Nov 2026).
function showKeyProblems(tests) {
  const waiting = tests.filter(test => test.status === "key_missing");
  el("apKeys").hidden = !waiting.length;
  el("apKeysTitle").textContent = `Tests waiting for a Model Answer: ${waiting.length} (${waiting.reduce((sum, test) => sum + Number(test.copies || 0), 0)} copies)`;
  el("apKeysRows").replaceChildren(...waiting.map(test => { const tr = document.createElement("tr"), paper = document.createElement("td"), pick = document.createElement("td");
    paper.append(apLink(test.paper_link, "Open paper") || "Not read yet");
    const file = document.createElement("input"); file.type = "file"; file.accept = ".pdf";
    pick.append(file);
    const action = document.createElement("td"), give = text("button", "Use this Model Answer");
    give.type = "button";
    give.onclick = guard(async () => {
      if (!file.files[0]) throw new Error("Choose the Model Answer PDF for this test first.");
      const body = new FormData(); body.append("key", test.key); body.append("answer_key", file.files[0]);
      showAutopilot(await (await api("/api/workflow/autopilot/answer-key", {method: "POST", body})).json());
      message(`${test.test_name} will run with ${file.files[0].name}. The next free pair takes it.`);
    });
    action.append(give);
    tr.append(text("td", test.tab ? `${test.test_name} (tab: ${test.tab})` : test.test_name), text("td", test.copies), text("td", test.detail), paper, pick, action); return tr; }));
}
let marksTest = null;
function openTestMarks(test) {
  marksTest = test;
  el("apMarksFor").textContent = `Marks for ${test.test_name}. Every tab of this test runs with them.`;
  el("apMarksTotal").value = test.total_marks ?? "";
  el("useOperatorMarksLine").classList.add("hidden"); el("apMarksEntry").classList.remove("hidden");
  showMarks("marksHomeAutopilot", test.marks_items, marksChoiceLines(test.marks_choices), true);
  el("apMarksEntry").scrollIntoView({block: "nearest"});
}
function closeTestMarks() {
  marksTest = null; el("apMarksEntry").classList.add("hidden"); el("useOperatorMarksLine").classList.remove("hidden");
  if (el("marksPanel").parentElement === el("marksHomeAutopilot")) { el("marksHomeStart").append(el("marksPanel")); el("marksPanel").classList.add("hidden"); }
}
async function sendTestMarks(body) {
  showAutopilot(await (await api("/api/workflow/autopilot/marks", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})).json());
}
// Approval Management > Review runs as each test's uploads land: that pair's reviewer approves the
// copies it put on the portal, with grade A, and a sweep at the end takes anything that pass could
// not (25 Nov 2026). Without a reviewer column nothing is reviewed at all (12 Nov 2026).
function reviewLine(plan, pairs, state) {
  if (plan) return pairs.some(pair => pair.reviewer)
    ? "As each test's copies finish uploading, that pair's reviewer approves them with grade A."
    : "No reviewer column in the accounts tab, so copies are left pending review.";
  const review = state?.review;
  if (!review?.status) return "";
  const counts = Object.entries(review.counts || {}).map(([name, count]) => `${count} ${name}`).join(", ");
  const last = (review.lines || []).slice(-1)[0] || "";
  return `Review ${review.status}: ${counts || "nothing yet"}.${last ? " " + last : ""}`;
}
function showAutopilot(state) {
  const plan = autopilotPlan, tests = plan ? plan.tests : state?.tests || [], pairs = plan ? plan.pairs : state?.pairs || [];
  showMarksProblems(plan ? [] : tests);
  showKeyProblems(plan ? [] : tests);
  const running = state?.status === "running";
  el("apStart").disabled = !plan || running; el("apStop").disabled = !running; el("apCheck").disabled = running; el("apSignIn").disabled = !pairs.length;
  autopilotPairs = pairs;
  const counts = Object.entries(state?.counts || {}).map(([name, count]) => `${count} ${name.replaceAll("_", " ")}`).join(", ");
  el("apStatus").textContent = plan ? `Plan: ${plan.tests.length} tests, ${plan.copies} copies, ${plan.pairs.length} pairs (${plan.windows} portal windows). Correct any input below, then Start autopilot.`
    : state && state.status !== "none" ? `Autopilot ${state.status.replaceAll("_", " ")} (started ${String(state.created_at || "").replace("T", " ").slice(0, 16)}): ${counts || "no tests"}.` : "";
  el("apTotals").textContent = !plan && state?.copy_totals ? copyTotals(state.copy_totals) : "";
  el("apReview").textContent = reviewLine(plan, pairs, state);
  el("apPairs").replaceChildren(...pairs.map(pair => { const tr = document.createElement("tr");
    tr.append(text("td", pair.admin), text("td", pair.evaluator), text("td", pair.reviewer || "none"),
              text("td", plan ? (pair.has_password ? "from the sheet" : "the one saved in section 6") : "saved"),
              text("td", plan ? "" : `${pair.test ? pair.test + " · " : ""}${pair.state || ""}`)); return tr; }));
  el("apTests").replaceChildren(...tests.map(test => { const tr = document.createElement("tr");
    tr.append(text("td", test.tab ? `${test.test_name} (tab: ${test.tab})` : test.test_name), apCell(test.category, "category", test), apCell(test.student_class, "student_class", test), apCell(test.subject, "subject", test),
              apCell(test.total_marks, "total_marks", test), text("td", test.copies),
              text("td", plan ? (test.problem ? `Will be skipped: ${test.problem}` : test.status === "skipped" ? test.detail : "ready") : `${test.status.replaceAll("_", " ")}${test.progress ? " · " + test.progress : ""}${test.detail ? " · " + test.detail : ""}`));
    const action = document.createElement("td");
    if (!plan && test.run_id) { const show = text("button", "Show here"); show.type = "button";
      show.onclick = guard(async () => { setTabRun(test.run_id); await refreshRuns(); el("currentRunTitle").scrollIntoView({block: "nearest"}); }); action.append(show);
      const folder = text("button", "Open folder"); folder.type = "button"; folder.onclick = guard(() => openCheckedFolder(test.run_id)); action.append(folder); }
    tr.append(action); return tr; }));
}
async function pollAutopilot() { if (!autopilotPlan) showAutopilot(await (await api("/api/workflow/autopilot")).json()); }
el("apMarksTotal").addEventListener("input", updateMarksSum);
el("apMarksCancel").onclick = closeTestMarks;
el("apMarksRun").onclick = guard(async () => {
  const name = marksTest.test_name;
  await sendTestMarks({key: marksTest.key, total_marks: el("apMarksTotal").value, operator_marks: readMarks()});
  closeTestMarks(); message(`${name} will run with the marks you entered. The next free pair takes it.`);
});
el("apMarksLinks").onclick = guard(async () => {
  message("Reading the paper links from the portal…");
  showAutopilot(await (await api("/api/workflow/autopilot/paper-links", {method: "POST"})).json());
  message("Paper links read.");
});
el("apCheck").onclick = guard(async () => {
  const students = el("apStudentsUrl").value.trim(), accounts = el("apAccountsUrl").value.trim();
  if (!students || !accounts || !el("apStudentsTab").value.trim() || !el("apAccountsTab").value.trim()) throw new Error("Give both sheet links and both tab names.");
  message("Reading the student tabs and the accounts tab (each large tab can take a minute)…");
  const studentImport = await importSheet(students), accountImport = students === accounts ? studentImport : await importSheet(accounts);
  autopilotPlan = await (await api("/api/workflow/autopilot/plan", {method: "POST", headers: {"Content-Type": "application/json", "X-Google-Sheets-Token": window.WorkflowSheets.getToken()},
    body: JSON.stringify({students: {import_id: studentImport.import_id, worksheet: el("apStudentsTab").value.trim()}, accounts: {import_id: accountImport.import_id, worksheet: el("apAccountsTab").value.trim()}})})).json();
  showAutopilot(null);
  const tabCount = new Set(autopilotPlan.tests.map(test => test.tab)).size;
  message(`Sheets read: ${tabCount > 1 ? `${tabCount} tabs, ` : ""}${autopilotPlan.tests.length} tests and ${autopilotPlan.pairs.length} admin + evaluator pairs. Check the inputs, then Start autopilot.`);
});
el("apStart").onclick = guard(async () => {
  const overrides = {};
  for (const input of el("apTests").querySelectorAll("[data-key]")) (overrides[input.dataset.key] ||= {})[input.dataset.field] = input.value;
  const state = await (await api("/api/workflow/autopilot/start", {method: "POST", headers: sheetTokenHeaders({"Content-Type": "application/json"}), body: JSON.stringify({plan_id: autopilotPlan.plan_id, overrides, direct: el("apDirect").checked, copies_at_once: el("apCopiesAtOnce").value})})).json();
  autopilotPlan = null; showAutopilot(state);
  message("Autopilot started. Each pair takes the next test by itself; its portal windows work hidden and sign themselves in, and one comes on screen only when it needs you. Keep this app running.");
});
el("apSignIn").onclick = guard(async () => {
  // The reviewer is signed in here too (25 Nov 2026). Its window used to open only when the first
  // review ran, so an account that needs a person stalled the review instead of being caught now.
  const accounts = autopilotPairs.flatMap(pair => [{role: "admin", account: pair.admin}, {role: "evaluator", account: pair.evaluator}]
    .concat(pair.reviewer ? [{role: "evaluator", account: pair.reviewer}] : []));
  showWindows(await (await api("/api/workflow/windows/sign-in", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({accounts})})).json());
  message(`Opening the portal window of each of the ${accounts.length} IDs. The ones already signed in stay hidden; sign in once in each window that comes on screen, and it hides itself after. You can start the autopilot meanwhile.`);
  el("windowsCard").scrollIntoView({block: "nearest"});
});
el("apStop").onclick = guard(async () => {
  if (!window.confirm("Stop the autopilot? Running tests stop after their current step; no new test starts.")) return;
  showAutopilot(await (await api("/api/workflow/autopilot/stop", {method: "POST"})).json()); message("Autopilot stopped.");
});
setInterval(() => { if (window.WorkflowAuth.getToken()) guard(pollAutopilot)(); }, 5000);
// ----------------------------------------------------------------------------- portal logins
function showPortalLogin(state) {
  const failed = (state?.recent || []).filter(entry => !entry.ok);
  const suspended = new Set(state?.suspended || []);
  el("portalLoginStatus").textContent = (state?.configured ? "Saved on this computer: logged-out windows sign themselves in." : "Not saved: a logged-out window comes on screen for you to sign in.")
    + failed.map(entry => ` Could not sign in ${entry.account} (${entry.at.slice(11, 16)}): ${entry.detail}. `
      + (suspended.has(String(entry.account).toLowerCase()) ? "Not tried again until the password is saved." : "It tries again by itself.")).join("");
}
async function pollPortalLogin() { showPortalLogin(await (await api("/api/workflow/portal-login")).json()); }
async function savePortalPassword(password) {
  showPortalLogin(await (await api("/api/workflow/portal-login", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({password})})).json());
  el("portalPassword").value = "";
}
el("savePortalPassword").onclick = guard(async () => {
  if (!el("portalPassword").value) throw new Error("Type the portal password first.");
  await savePortalPassword(el("portalPassword").value); message("Portal password saved on this computer. Logged-out windows now sign themselves in.");
});
el("removePortalPassword").onclick = guard(async () => { await savePortalPassword(""); message("Portal password removed. Logged-out windows wait for you to sign in."); });
setInterval(() => { if (window.WorkflowAuth.getToken()) guard(pollPortalLogin)(); }, 15000);
// ----------------------------------------------------------------------------- portal windows (build 140)
let windowShotUrl = "";
function closeWindowShot() { el("windowShot").classList.add("hidden"); el("windowShotImage").removeAttribute("src"); if (windowShotUrl) { URL.revokeObjectURL(windowShotUrl); windowShotUrl = ""; } }
el("closeWindowShot").onclick = closeWindowShot;
function windowLabel(w) { return `${w.role === "admin" ? "Admin" : "Evaluator"} ${w.account || "(first window)"}`; }
function windowNow(w) {
  if (!w.open) return "closed";
  if (w.wanted !== w.shown) return w.wanted ? "coming on screen…" : "hiding…";
  return w.why === "sign_in" ? `on screen: ${w.reason}` : w.why === "problem" ? "on screen: a copy failed here on its last attempt (latest problem); Hide when you have looked"
    : w.why === "you" ? "on screen (shown by you)" : w.shown ? "on screen" : w.signed_in ? "hidden, signed in" : "hidden, working";
}
function showWindows(state) {
  const windows = state?.windows || [], open = windows.filter(w => w.open), needs = open.filter(w => w.why === "sign_in" || w.why === "problem");
  el("showAllWindows").checked = !!state?.show_all;
  el("windowsStatus").textContent = !open.length ? "No portal window is open." : `${open.length} open, ${open.filter(w => w.signed_in).length} signed in, ${open.filter(w => w.shown).length} on screen`
    + (needs.length ? `; ${needs.length} need${needs.length === 1 ? "s" : ""} you: ${needs.map(windowLabel).join(", ")}.` : ".");
  el("windowsStatus").classList.toggle("error", needs.length > 0);
  el("portalWindows").replaceChildren(...windows.map(w => { const tr = document.createElement("tr");
    const problem = text("td", w.problem ? `${w.problem}${w.at ? " (" + w.at.replace("T", " ").slice(0, 16) + ")" : ""}` : "");
    if (w.screenshot) { const picture = text("button", "Picture"); picture.type = "button";
      picture.onclick = guard(async () => { const blob = await (await api(`/api/workflow/windows/screenshot/${encodeURIComponent(w.screenshot)}`)).blob();
        closeWindowShot(); windowShotUrl = URL.createObjectURL(blob); el("windowShotImage").src = windowShotUrl;
        el("windowShotTitle").textContent = `${windowLabel(w)}: the portal when the step failed`; el("windowShot").classList.remove("hidden"); el("windowShot").scrollIntoView({block: "nearest"}); });
      problem.append(document.createElement("br"), picture); }
    const action = document.createElement("td");
    if (w.open) { const show = !w.wanted, button = text("button", show ? "Show" : "Hide"); button.type = "button"; button.disabled = !!state.show_all;
      button.onclick = guard(async () => { showWindows(await (await api("/api/workflow/windows/show", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({role: w.role, account: w.account, show})})).json());
        message(show ? `The window of ${windowLabel(w)} comes on screen after its current step.` : `The window of ${windowLabel(w)} is hidden after its current step; it keeps working.`); });
      action.append(button); }
    tr.append(text("td", windowLabel(w)), text("td", windowNow(w)), problem, action); return tr; }));
}
async function pollWindows() { showWindows(await (await api("/api/workflow/windows")).json()); }
el("showAllWindows").onchange = guard(async () => {
  const showAll = el("showAllWindows").checked;
  showWindows(await (await api("/api/workflow/windows", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({show_all: showAll})})).json());
  message(showAll ? "Every portal window comes on screen after its current step." : "Portal windows are hidden after their current step, unless one needs you.");
});
setInterval(() => { if (window.WorkflowAuth.getToken()) guard(pollWindows)(); }, 5000);
window.WorkflowAuth.init({onSignedIn: guard(async () => { if (!el("alertEmail").value) el("alertEmail").value = signedInEmail(); await refreshRuns(); await pollAlerts(); await pollPortalLogin(); await pollAutopilot(); await pollWindows(); }), onSignedOut: () => {
  lastRun = null; el("savedRuns").replaceChildren(); el("progress").replaceChildren(); el("runStatus").textContent = ""; el("runAccounts").textContent = "Sign in to see saved runs and their portal login IDs."; el("submitRejections").disabled = true; el("viewRunPaper").disabled = el("viewRunKey").disabled = true; closePreview(); closeWindowShot(); el("portalWindows").replaceChildren(); el("windowsStatus").textContent = ""; window.WorkflowSheets.reset(); resetSheet();
}});
