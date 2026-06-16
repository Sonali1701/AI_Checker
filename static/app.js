"use strict";
const $ = (id) => document.getElementById(id);
const MODELS = {
  gemini: ["gemini-2.5-flash", "gemini-2.5-pro"],
  claude: ["claude-opus-4-7", "claude-sonnet-4-6"],
};
const ACCEPT = [".pdf", ".png", ".jpg", ".jpeg"];
const state = { marksItems: [], sheets: [], jobId: null, poll: null, serverKeys: {} };

function toast(msg, ms = 3800) {
  const t = $("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

// ---- init ----
async function init() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    state.serverKeys = cfg;
    $("usdInr").value = cfg.default_usd_to_inr ?? 94;
  } catch (e) { /* server not up yet */ }
  fillModels();
  updateKeyUI();
  $("provider").onchange = () => { fillModels(); updateKeyUI(); };
  $("apiKey").oninput = refreshChecklist;
  $("settingsToggle").onclick = () => $("settings").classList.toggle("hidden");

  document.querySelectorAll("input[name=keySource]").forEach(r =>
    r.onchange = () => { toggleKeySource(); refreshChecklist(); });
  toggleKeySource();

  $("qpFile").onchange = refreshChecklist;
  $("akFile").onchange = refreshChecklist;
  $("rubricText").oninput = refreshChecklist;

  // Multi-sheet: browse + drag&drop, accumulating into state.sheets
  $("pickSheets").onclick = () => $("saFiles").click();
  $("saFiles").onchange = (e) => { addSheets(e.target.files); e.target.value = ""; };
  $("clearSheets").onclick = () => { state.sheets = []; renderSheets(); };
  const dz = $("dropzone");
  ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => {
    e.preventDefault(); dz.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => {
    e.preventDefault(); if (ev === "dragleave" && dz.contains(e.relatedTarget)) return; dz.classList.remove("drag");
  }));
  dz.addEventListener("drop", e => addSheets(e.dataTransfer.files));

  $("detectMarks").onclick = detectMarks;
  $("genRubric").onclick = genRubric;
  $("addMark").onclick = () => { state.marksItems.push({ qid: "", max: 0, part: "" }); renderMarks(); };
  $("evaluate").onclick = evaluate;
  $("newBatch").onclick = resetResults;

  refreshChecklist();
}

function fillModels() {
  const p = $("provider").value;
  $("model").innerHTML = MODELS[p].map(m => `<option value="${m}">${m}</option>`).join("");
}
function serverHasKey() {
  return $("provider").value === "claude" ? state.serverKeys.has_anthropic_key : state.serverKeys.has_google_key;
}
function updateKeyUI() {
  // On a deployed server the key is configured — hide the field so end users never deal with it.
  const has = serverHasKey();
  $("apiKeyWrap").classList.toggle("hidden", !!has);
  $("keyHint").textContent = has ? "" : "(no server key — enter one)";
  refreshChecklist();
}
function keyReady() { return serverHasKey() || $("apiKey").value.trim().length > 0; }

function toggleKeySource() {
  const v = document.querySelector("input[name=keySource]:checked").value;
  $("keyUpload").classList.toggle("hidden", v !== "upload");
  $("keyRubric").classList.toggle("hidden", v !== "rubric");
}

// ---- multi-sheet handling ----
function addSheets(fileList) {
  let added = 0, skipped = 0;
  for (const f of fileList) {
    const ok = ACCEPT.some(ext => f.name.toLowerCase().endsWith(ext));
    if (!ok) { skipped++; continue; }
    if (state.sheets.some(s => s.name === f.name && s.size === f.size)) continue; // dedupe
    state.sheets.push(f); added++;
  }
  renderSheets();
  if (skipped) toast(`${skipped} file(s) skipped — only PDF/PNG/JPG allowed.`);
}
function renderSheets() {
  const wrap = $("sheetListWrap"), ul = $("sheetList");
  wrap.classList.toggle("hidden", state.sheets.length === 0);
  $("sheetCount").textContent = `${state.sheets.length} sheet${state.sheets.length !== 1 ? "s" : ""}`;
  ul.innerHTML = state.sheets.map((f, i) =>
    `<li><span class="nm">${esc(f.name)}</span><span class="sz">${(f.size / 1024).toFixed(0)} KB</span>` +
    `<button class="rm" data-i="${i}" title="Remove">✕</button></li>`).join("");
  ul.querySelectorAll(".rm").forEach(b => b.onclick = () => { state.sheets.splice(+b.dataset.i, 1); renderSheets(); });
  refreshChecklist();
}

// ---- shared form bits ----
function commonForm() {
  const fd = new FormData();
  fd.append("provider", $("provider").value);
  fd.append("model", $("model").value);
  fd.append("api_key", $("apiKey").value);
  fd.append("student_class", $("studentClass").value);
  fd.append("subject", $("subject").value);
  return fd;
}

// ---- detect marks ----
async function detectMarks() {
  const qp = $("qpFile").files[0];
  if (!qp) return toast("Upload a question paper first.");
  const fd = commonForm(); fd.append("question_paper", qp);
  setBusy($("detectMarks"), "Detecting…");
  try {
    const res = await fetch("/api/detect-marks", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "failed");
    state.marksItems = data.items;
    $("marksMethod").textContent = `✅ Read ${data.total} marks from ${data.method === "regex" ? "the printed tags (no AI)" : "AI"}. Edit below if needed.`;
    renderMarks();
  } catch (e) { toast("Could not detect marks: " + e.message); }
  finally { unBusy($("detectMarks"), "🔢 Detect max marks from question paper"); }
}

function renderMarks() {
  const tb = $("marksTable").querySelector("tbody");
  tb.innerHTML = "";
  state.marksItems.forEach((it, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td><input data-i="${i}" data-k="qid" value="${esc(it.qid ?? "")}"></td>` +
      `<td class="max"><input data-i="${i}" data-k="max" type="number" step="0.5" value="${it.max ?? 0}"></td>` +
      `<td><input data-i="${i}" data-k="part" value="${esc(it.part ?? "")}"></td>` +
      `<td><button class="ghost-btn small" data-del="${i}">✕</button></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("input").forEach(inp => inp.oninput = (e) => {
    const { i, k } = e.target.dataset;
    state.marksItems[i][k] = k === "max" ? parseFloat(e.target.value || 0) : e.target.value;
    updateLockedTotal();
  });
  tb.querySelectorAll("button[data-del]").forEach(b => b.onclick = (e) => {
    state.marksItems.splice(+e.target.dataset.del, 1); renderMarks();
  });
  $("marksTable").classList.toggle("hidden", state.marksItems.length === 0);
  $("marksFooter").classList.toggle("hidden", state.marksItems.length === 0);
  updateLockedTotal();
  refreshChecklist();
}
function updateLockedTotal() {
  const t = state.marksItems.reduce((a, r) => a + (String(r.qid).trim() ? (parseFloat(r.max) || 0) : 0), 0);
  $("lockedTotal").textContent = (Math.round(t * 100) / 100);
}

// ---- rubric ----
async function genRubric() {
  const qp = $("qpFile").files[0];
  if (!qp) return toast("Upload a question paper first.");
  const fd = commonForm(); fd.append("question_paper", qp);
  setBusy($("genRubric"), "Generating…");
  try {
    const res = await fetch("/api/generate-rubric", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "failed");
    $("rubricText").value = data.rubric; refreshChecklist();
  } catch (e) { toast("Rubric generation failed: " + e.message); }
  finally { unBusy($("genRubric"), "✨ Generate rubric from question paper"); }
}

// ---- checklist ----
function refreshChecklist() {
  const ks = document.querySelector("input[name=keySource]:checked").value;
  const qp = $("qpFile").files.length > 0;
  const sa = state.sheets.length;
  const keyOk = ks === "upload" ? $("akFile").files.length > 0 : $("rubricText").value.trim().length > 0;
  const kr = keyReady();
  const marksOk = state.marksItems.some(r => String(r.qid).trim());
  const rows = [
    [kr, "API key ready" + (serverHasKey() ? " (server)" : "")],
    [qp, "Question paper uploaded"],
    [sa > 0, `Answer sheet(s) added${sa ? " — " + sa : ""}`],
    [keyOk, "Answer key / rubric ready"],
    [marksOk, "Max marks locked (recommended)"],
  ];
  $("checklist").innerHTML = rows.map(([ok, t]) => `<li>${ok ? "✅" : "⬜"} ${t}</li>`).join("");
  $("evaluate").disabled = !(qp && sa > 0 && keyOk && kr);
  $("evaluate").textContent = sa > 1 ? `🚀 Evaluate ${sa} sheets` : "🚀 Evaluate";
}

// ---- evaluate + poll ----
async function evaluate() {
  const ks = document.querySelector("input[name=keySource]:checked").value;
  const fd = commonForm();
  fd.append("question_paper", $("qpFile").files[0]);
  for (const f of state.sheets) fd.append("answer_sheets", f);
  fd.append("key_source", ks);
  if (ks === "upload") fd.append("answer_key", $("akFile").files[0]);
  else fd.append("rubric_text", $("rubricText").value);
  fd.append("marks_items", JSON.stringify(state.marksItems));
  fd.append("use_mathpix", $("useMathpix").checked);
  fd.append("mathpix_key", $("mathpixKey").value);
  fd.append("usd_to_inr", $("usdInr").value);
  fd.append("log_to_sheet", $("logSheet").checked);
  fd.append("sheet_tab", $("sheetTab").value);

  $("evaluate").disabled = true;
  $("progress").classList.remove("hidden");
  setBar(0, "Submitting…");
  try {
    const res = await fetch("/api/evaluate", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "failed");
    state.jobId = data.job_id;
    state.poll = setInterval(pollJob, 1500);
    pollJob();
  } catch (e) {
    toast("Evaluate failed: " + e.message);
    $("evaluate").disabled = false; $("progress").classList.add("hidden");
  }
}

function setBar(frac, text) {
  $("barFill").style.width = Math.round(frac * 100) + "%";
  $("progressText").textContent = text;
}

async function pollJob() {
  if (!state.jobId) return;
  let job;
  try { job = await (await fetch("/api/jobs/" + state.jobId)).json(); }
  catch (e) { return; }
  const frac = job.total ? job.done / job.total : 0;
  setBar(frac, `Graded ${job.done}/${job.total}${job.current ? " · " + job.current : ""}`);
  if (job.status === "done" || job.status === "error") {
    clearInterval(state.poll); state.poll = null;
    $("progress").classList.add("hidden");
    $("evaluate").disabled = false;
    if (job.status === "error") return toast("Job failed: " + (job.error || "unknown"));
    renderResults(job);
  }
}

// ---- results ----
function renderResults(job) {
  const ok = job.results.filter(r => r.ok);
  const failed = job.results.filter(r => !r.ok);
  $("results").classList.remove("hidden");
  $("resultsTitle").textContent = `📋 Results — ${job.results.length} sheet${job.results.length !== 1 ? "s" : ""}`;
  $("results").scrollIntoView({ behavior: "smooth" });

  let html = "";
  if (job.results.length > 1 && ok.length) {
    const avg = ok.reduce((a, r) => a + r.percent, 0) / ok.length;
    const inr = ok.reduce((a, r) => a + (r.cost?.total_inr || 0), 0);
    const usd = ok.reduce((a, r) => a + (r.cost?.total_usd || 0), 0);
    html = metric("Graded", `${ok.length}/${job.results.length}`) +
      metric("Average", `${avg.toFixed(0)}%`) +
      metric("Total cost", `$${usd.toFixed(3)}`, `≈ ₹${inr.toFixed(2)}`) +
      metric("Failed", failed.length);
  }
  $("summary").innerHTML = html;

  const zip = $("zipBtn");
  if (ok.length > 1) { zip.href = `/api/jobs/${state.jobId}/zip`; zip.classList.remove("hidden"); }
  else zip.classList.add("hidden");

  const box = $("resultRows");
  if (job.results.length === 1) {
    box.innerHTML = ok.length ? detailHtml(ok[0]) : failHtml(failed[0]);
    return;
  }
  let t = `<table class="rtable"><thead><tr><th>Student</th><th>Score</th><th>%</th><th>Cost (₹)</th><th>Status</th></tr></thead><tbody>`;
  for (const r of job.results) {
    t += r.ok
      ? `<tr><td>${esc(r.student)}</td><td class="s">${fmt(r.score)} / ${fmt(r.max)}</td><td>${r.percent}%</td><td>${(r.cost?.total_inr || 0).toFixed(2)}</td><td class="ok">✅</td></tr>`
      : `<tr><td>${esc(r.student)}</td><td>—</td><td>—</td><td>—</td><td class="bad">❌ ${esc(r.error || "")}</td></tr>`;
  }
  t += "</tbody></table>";
  t += job.results.map(r => r.ok
    ? `<details class="rdetail"><summary>✅ ${esc(r.student)} — ${fmt(r.score)}/${fmt(r.max)}</summary>${detailHtml(r)}</details>`
    : `<details class="rdetail"><summary>❌ ${esc(r.student)}</summary>${failHtml(r)}</details>`).join("");
  box.innerHTML = t;
}

function detailHtml(r) {
  const pct = r.max ? Math.round(r.score / r.max * 100) : 0;
  const dl = `/api/jobs/${state.jobId}/pdf/${r.index}`;
  const q = r.questions.map(x =>
    `<div class="qrow"><span class="q">${esc(x.q)}</span><span class="m">${fmt(x.score)}/${fmt(x.max)}</span><span class="r">${esc(x.remark)}</span></div>`).join("");
  const log = r.log ? `<div class="muted">${r.log.ok ? "📊 " : "⚠️ "}${esc(r.log.msg)}</div>` : "";
  const c = r.cost || {};
  return `
    <div class="scoreband"><div class="big">${fmt(r.score)} / ${fmt(r.max)}</div><div class="pct">${pct}%</div></div>
    <a class="primary-btn small" href="${dl}">⬇️ Download evaluated PDF</a>
    ${r.remarks ? `<p class="muted"><b>Remarks:</b> ${esc(r.remarks)}</p>` : ""}
    <p class="muted">Cost: $${(c.total_usd || 0).toFixed(4)} ≈ ₹${(c.total_inr || 0).toFixed(2)} · ${(c.billed_input_tokens || 0).toLocaleString()} in / ${(c.output_tokens || 0).toLocaleString()} out · ${esc(r.model || "")}</p>
    ${log}
    <div style="margin-top:8px">${q}</div>`;
}
function failHtml(r) { return `<p class="bad">❌ ${esc(r.student)}: ${esc(r.error || "failed")}</p>`; }

function resetResults() {
  $("results").classList.add("hidden"); $("resultRows").innerHTML = ""; state.jobId = null;
}

// ---- utils ----
function setBusy(btn, t) { btn._t = btn.textContent; btn.disabled = true; btn.textContent = t; }
function unBusy(btn, t) { btn.disabled = false; btn.textContent = t; }
function metric(k, v, sub) {
  return `<div class="metric"><div class="v">${v}</div><div class="k">${k}${sub ? " · " + sub : ""}</div></div>`;
}
function fmt(n) { const x = Number(n); return Number.isInteger(x) ? x : x.toFixed(2).replace(/\.?0+$/, ""); }
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

init();
