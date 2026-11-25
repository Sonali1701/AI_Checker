"use strict";
(() => {
  const byId = id => document.getElementById(id);
  const STAGES = {queued: "Waiting", checking: "Checking", ready: "Checked, waiting for you", posting: "Posting",
                  posted: "Posted", held: "Needs a person", already_evaluated: "Evaluated by someone else"};
  const RUNS = {created: "Starting", running: "Running", posting: "Posting", done: "Finished", stopped: "Stopped",
                failed: "Stopped by a problem", needs_sign_in: "Admin sign-in needed", interrupted: "Interrupted"};
  const TILES = [["all", "All copies", () => true], ["ready", "Waiting for you", c => c.stage === "ready"],
                 ["held", "Needs a person", c => c.stage === "held"],
                 ["moving", "In progress", c => ["queued", "checking", "posting"].includes(c.stage)],
                 ["posted", "Posted", c => ["posted", "already_evaluated"].includes(c.stage)]];
  const SLOW_MINUTES = 15;     // a copy usually takes 2 to 5 minutes to check
  const LEARNER = /^(\d{10}|[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+)$/;
  let found = [], picked = null, titles = [], currentRun = null, ticked = new Set(), editing = "", tile = "all";
  let searchTimer = null, searchSeq = 0, lastQuery = null, signedIn = false;

  async function api(path, options = {}) {
    const token = window.WorkflowAuth.getToken();
    if (!token) throw new Error("Sign in first.");
    const headers = {Authorization: `Bearer ${token}`};
    if (options.body) headers["Content-Type"] = "application/json";
    const response = await fetch(`/api/skills${path}`, {...options, headers});
    if (!response.ok) {
      let detail = ""; try { detail = (await response.json()).detail; } catch {}
      if (response.status === 401) window.WorkflowAuth.invalidate(detail || "Sign in again.");
      throw new Error(typeof detail === "string" && detail ? detail : `Request failed (${response.status})`);
    }
    return response.json();
  }
  // As skills_workflow.comment_html sends it: each line a paragraph, the heading in bold.
  function postedLength(text) {
    return text.split("\n").map(line => line.trim()).filter(Boolean).reduce((total, line) => {
      const escaped = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").length;
      return total + escaped + (line === "Where marks were lost:" ? 24 : 7);
    }, 0);
  }
  function el(tag, props = {}, ...children) {
    const node = Object.assign(document.createElement(tag), props);
    children.filter(child => child !== null && child !== undefined && child !== "").forEach(child => node.append(child));
    return node;
  }
  let toastTimer = null;
  function toast(message, kind = "") {
    const box = byId("toast");
    box.textContent = message; box.className = `toast ${kind}`;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => box.classList.add("hidden"), kind === "bad" ? 9000 : 5000);
  }
  const guarded = fn => async (...args) => { try { await fn(...args); } catch (error) { toast(error.message, "bad"); } };
  const when = seconds => new Date(seconds * 1000).toLocaleString([], {day: "numeric", month: "short", hour: "numeric", minute: "2-digit"});
  const plain = text => String(text || "").toLowerCase().replace(/\s+/g, " ").trim();
  function ago(seconds) {
    const minutes = Math.max(0, Math.floor((Date.now() / 1000 - seconds) / 60));
    return minutes < 1 ? "under a minute" : minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  }
  // The typed words marked in the text, built from text nodes only.
  function highlight(text, query) {
    const words = plain(query).split(/[\s|]+/).filter(word => word.length > 1);
    const fragment = document.createDocumentFragment();
    const source = String(text || ""), lower = source.toLowerCase();
    let at = 0;
    while (at < source.length) {
      let best = -1, size = 0;
      words.forEach(word => { const i = lower.indexOf(word, at); if (i >= 0 && (best < 0 || i < best)) { best = i; size = word.length; } });
      if (best < 0) { fragment.append(source.slice(at)); break; }
      fragment.append(source.slice(at, best), el("mark", {textContent: source.slice(best, best + size)}));
      at = best + size;
    }
    return fragment;
  }

  // 1. Admin portal.
  async function pollSignIn() {
    const state = await api("/sign-in");
    const labels = {signed_out: "Not signed in to the admin portal.", opening: "Opening the sign-in window…",
                    waiting: "Sign in in the window that opened.", signed_in: "Signed in to the admin portal.",
                    failed: "The sign-in window could not be used."};
    byId("adminState").textContent = [labels[state.state] || state.state, state.detail].filter(Boolean).join(" ");
    byId("adminDot").className = `dot ${state.state === "signed_in" ? "ok" : ["opening", "waiting"].includes(state.state) ? "wait" : "bad"}`;
    byId("adminOpen").disabled = ["opening", "waiting"].includes(state.state);
    const was = signedIn;
    signedIn = state.state === "signed_in";
    byId("adminOpen").classList.toggle("hidden", signedIn);
    byId("adminHelp").classList.toggle("hidden", signedIn);
    if (signedIn && !was && lastQuery === null) await search();
    drawStart();
  }

  // 2. Batch.
  async function search(immediate = true) {
    if (!signedIn) { byId("searchInfo").textContent = "Sign in to the admin portal first."; return; }
    const query = byId("courseSearch").value.trim();
    if (!immediate && query.length === 1) return;
    const seq = ++searchSeq;
    lastQuery = query;
    byId("searchInfo").className = "info";
    byId("searchInfo").textContent = query ? `Searching for "${query}"…` : "Loading batches…";
    const result = await api(`/courses?q=${encodeURIComponent(query)}`);
    if (seq !== searchSeq) return;           // a newer search has started
    found = result.courses;
    try { localStorage.setItem("skillsBatchSearch", query); } catch {}
    const info = byId("searchInfo"), exact = found.filter(course => course.match === "exact");
    if (!query) { info.textContent = `${found.length} batches shown. Type a batch code or name to find yours.`; }
    else if (!found.length) { info.className = "info bad"; info.textContent = `No batch found for "${query}". Check the spelling, or search by the batch code.`; }
    else if (exact.length === 1) {
      const others = found.length - 1;
      info.className = "info good";
      info.textContent = `Exact match found and chosen.${others ? ` ${others} other batch${others > 1 ? "es" : ""} also matched: press Change batch to see them.` : ""}`;
    }
    else if (exact.length > 1) { info.className = "info good"; info.textContent = `${exact.length} exact matches, shown first. Pick the right one.`; }
    else { info.textContent = `${found.length} batches found${result.more ? " (the first ones only, type more to narrow it)" : ""}. No exact match: pick from the list or type the full batch code.`; }
    drawBatches(query);
    if (exact.length === 1 && query) await pick(exact[0]);
  }
  function drawBatches(query) {
    byId("batchList").replaceChildren(...found.map(course => {
      const item = el("button", {type: "button", className: `batch-item${course.match === "exact" ? " exact" : ""}`, role: "option",
                                 onclick: guarded(() => pick(course))},
        el("span", {className: "name"}, el("span", {className: "title"}, highlight(course.title, query)),
           course.batch ? el("span", {className: "code"}, highlight(course.batch, query)) : null),
        course.match === "exact" ? el("span", {className: "tag exact", textContent: "Exact match"}) : null,
        el("span", {className: "tag pick", textContent: "Choose"}));
      return item;
    }));
  }
  async function pick(course) {
    picked = course;
    byId("pickedTitle").textContent = course.title;
    byId("pickedCode").textContent = course.batch || "";
    byId("pickedCode").classList.toggle("hidden", !course.batch);
    byId("pickedBatch").classList.remove("hidden");
    byId("batchFinder").classList.add("hidden");
    drawStart();
    await loadTitles();
  }
  function changeBatch() {
    picked = null; titles = [];
    byId("pickedBatch").classList.add("hidden");
    byId("batchFinder").classList.remove("hidden");
    byId("titles").replaceChildren(el("span", {className: "empty", textContent: "Choose a batch first."}));
    drawTitleCount(); drawStart();
    byId("courseSearch").focus();
  }

  // Assignments.
  async function loadTitles() {
    const box = byId("titles");
    box.replaceChildren(el("span", {className: "empty", textContent: "Loading the batch's assignments…"}));
    const id = picked.id;
    const result = await api(`/courses/${encodeURIComponent(id)}/assignments`);
    if (!picked || picked.id !== id) return;
    titles = result.titles;
    box.replaceChildren(...(titles.length ? titles.map(title => el("label", {},
      el("input", {type: "checkbox", value: title, className: "title", onchange: () => { drawTitleCount(); drawStart(); }}), title))
      : [el("span", {className: "empty", textContent: "The portal lists no assignments for this batch."})]));
    filterTitles(); drawTitleCount(); drawStart();
  }
  const tickedTitles = () => [...document.querySelectorAll(".title:checked")].map(box => box.value);
  function filterTitles() {
    const words = plain(byId("titleFilter").value).split(" ").filter(Boolean);
    document.querySelectorAll("#titles label").forEach(label => {
      label.classList.toggle("hidden", !words.every(word => plain(label.textContent).includes(word)));
    });
  }
  function drawTitleCount() {
    const chosen = tickedTitles().length;
    byId("titleCount").textContent = !titles.length ? "" : chosen ? `${chosen} of ${titles.length} ticked` : `all ${titles.length} (none ticked)`;
  }

  // Learners.
  function learnerList() {
    return byId("learners").value.split(/[,;\n]+/).map(part => part.trim()).filter(Boolean);
  }
  function drawLearners() {
    byId("learnerChips").replaceChildren(...learnerList().map(item => el("span", {
      className: `chip${LEARNER.test(item) ? "" : " bad"}`, textContent: item,
      title: LEARNER.test(item) ? "" : "Not a 10-digit phone number or an email"})));
    drawStart();
  }

  // Start.
  function drawStart() {
    const learners = learnerList(), bad = learners.filter(item => !LEARNER.test(item));
    const chosen = tickedTitles().length, most = Number(byId("mostCopies").value) || 0, auto = byId("autoPost").checked;
    byId("autoWarn").classList.toggle("hidden", !auto);
    const summary = byId("startSummary");
    let ready = signedIn && picked && !bad.length;
    if (!signedIn) summary.textContent = "Sign in to the admin portal to start.";
    else if (!picked) summary.textContent = "Choose a batch to start.";
    else if (bad.length) summary.textContent = `Fix the learner entry: ${bad.join(", ")} is not a 10-digit phone number or an email.`;
    else summary.replaceChildren("Checks the not-evaluated copies of ", el("b", {textContent: picked.batch || picked.title}),
      ` · ${chosen ? `${chosen} assignment${chosen > 1 ? "s" : ""}` : "every assignment"}`,
      ` · ${learners.length ? `${learners.length} learner${learners.length > 1 ? "s" : ""}` : "every learner"}`,
      ` · ${most ? `up to ${most} copies` : "all copies"}`,
      ` · marks ${auto ? "post by themselves" : "wait for you to approve"}.`);
    byId("start").disabled = !ready;
  }
  async function start() {
    if (!picked) { toast("Choose a batch.", "bad"); return; }
    const auto = byId("autoPost").checked;
    if (auto && !confirm("Marks will go to learners with no one reading them first, and Evaluate cannot be undone by the learner. Go ahead?")) return;
    byId("start").disabled = true;
    try {
      const run = await api("/runs", {method: "POST", body: JSON.stringify({
        course: {id: picked.id, title: picked.batch ? `${picked.title} | ${picked.batch}` : picked.title},
        titles: tickedTitles(), learners: byId("learners").value,
        most_copies: Number(byId("mostCopies").value) || 0, copies_at_once: Number(byId("copiesAtOnce").value) || 3, auto_post: auto})});
      toast("Started. Copies are being fetched from the portal.", "ok");
      await openRun(run.id);
      await loadRuns();
    } finally { drawStart(); }
  }

  // 3. Runs.
  function progress(run) {
    const counts = run.counts || {}, total = run.copies || 0;
    const done = total - ["queued", "checking", "posting"].reduce((n, stage) => n + (counts[stage] || 0), 0);
    return {total, done};
  }
  async function loadRuns() {
    const result = await api("/runs");
    const rows = result.runs.map(run => {
      const {total, done} = progress(run);
      const counts = Object.entries(run.counts || {}).map(([stage, n]) => `${STAGES[stage] || stage}: ${n}`).join(" · ");
      return el("tr", {className: currentRun && currentRun.id === run.id ? "current" : ""},
        el("td", {textContent: when(run.created_at)}), el("td", {textContent: run.course.title}),
        el("td", {}, el("span", {className: `pill ${run.status}`, textContent: RUNS[run.status] || run.status})),
        el("td", {}, el("div", {textContent: total ? `${done} of ${total} copies done` : "No copies yet"}),
           total ? el("div", {className: "mini-bar"}, el("span", {style: `width:${Math.round(100 * done / total)}%`})) : null,
           counts ? el("div", {className: "note", textContent: counts}) : null),
        el("td", {}, el("button", {type: "button", className: "ghost-btn small", textContent: "Open", onclick: guarded(() => openRun(run.id))})));
    });
    byId("runs").replaceChildren(...(rows.length ? rows : [el("tr", {className: "empty-row"}, el("td", {colSpan: 5, textContent: "No runs yet. Choose a batch above and press Fetch and check."}))]));
  }

  // 4. Copies of one run.
  async function openRun(id) {
    currentRun = await api(`/runs/${id}`);
    ticked = new Set(); editing = ""; tile = "all";
    drawRun();
    byId("runCard").scrollIntoView({behavior: "smooth", block: "start"});
  }
  async function refreshRun() {
    if (!currentRun) return;
    const fresh = await api(`/runs/${currentRun.id}`);
    const changed = fresh.updated_at !== currentRun.updated_at || fresh.status !== currentRun.status;
    const statusChanged = fresh.status !== currentRun.status;
    currentRun = fresh;
    if (editing) { if (changed) drawStatus(); }
    else if (changed || busy()) drawRun();           // a busy run also needs its timers redrawn
    if (statusChanged) await loadRuns();
  }
  const busy = () => ["running", "posting", "created"].includes(currentRun.status);
  function drawStatus() {
    const run = currentRun;
    byId("runTitle").textContent = run.course.title;
    byId("runPill").className = `pill ${run.status}`;
    byId("runPill").textContent = RUNS[run.status] || run.status;
    byId("runStatus").textContent = [run.error, run.skipped_elsewhere ? `${run.skipped_elsewhere} copies left out because another run has them.` : ""].filter(Boolean).join(" ");
    const options = run.options || {};
    byId("runMeta").textContent = [`Started ${when(run.created_at)}`,
      (options.learners || []).length ? `learners ${options.learners.join(", ")}` : "",
      options.auto_post ? "posts by itself" : "waits for approval"].filter(Boolean).join(" · ");
    byId("tiles").replaceChildren(...TILES.map(([key, label, test]) => {
      const count = run.copies.filter(test).length;
      return el("button", {type: "button", className: `tile${tile === key ? " on" : ""}${key === "held" && count ? " attn" : ""}`,
                           onclick: () => { tile = key; drawRun(); }},
        el("div", {className: "v", textContent: String(count)}), el("div", {className: "k", textContent: label}));
    }));
    byId("stop").disabled = !busy();
    byId("resume").disabled = busy();
    byId("selectReady").disabled = busy() || !run.copies.some(copy => copy.stage === "ready");
    byId("post").disabled = busy() || !ticked.size;
    byId("post").textContent = ticked.size ? `Post ${ticked.size} ticked mark${ticked.size > 1 ? "s" : ""}` : "Post ticked marks";
  }
  async function openPdf(copy) {
    const response = await fetch(`/api/skills/runs/${currentRun.id}/copies/${copy.id}/checked.pdf`, {headers: {Authorization: `Bearer ${window.WorkflowAuth.getToken()}`}});
    if (!response.ok) throw new Error("No checked copy to show.");
    window.open(URL.createObjectURL(await response.blob()), "_blank");
  }
  function editor(copy) {
    const marks = el("input", {type: "number", min: "0.01", step: "0.01", max: String(copy.out_of), value: copy.marks ?? "", className: "marks"});
    const text = el("textarea", {value: copy.feedback || ""});
    const limit = currentRun.comment_limit || 500, count = el("div", {className: "counter"});
    const button = el("button", {type: "button", className: "primary-btn small", textContent: "Save"});
    const recount = () => {
      const used = postedLength(text.value);
      count.textContent = `${used} of ${limit} characters as the portal counts them`;
      count.classList.toggle("over", used > limit);
      button.disabled = used > limit;
    };
    text.oninput = recount;
    recount();
    button.onclick = guarded(async () => {
      currentRun = await api(`/runs/${currentRun.id}/copies/${copy.id}`, {method: "PATCH", body: JSON.stringify({marks: Number(marks.value), feedback: text.value})});
      editing = ""; drawRun(); toast("Saved.", "ok");
    });
    return el("tr", {className: "editor"}, el("td", {colSpan: 6},
      el("label", {}, `Marks out of ${copy.out_of}`, marks),
      el("label", {style: "margin-top:10px"}, "Feedback the learner reads", text), count,
      el("div", {className: "actions", style: "margin-top:10px;display:flex;gap:8px"}, button,
         el("button", {type: "button", className: "ghost-btn small", textContent: "Cancel", onclick: () => { editing = ""; drawRun(); }}))));
  }
  function row(copy) {
    const canTick = copy.stage === "ready";
    if (!canTick) ticked.delete(copy.id);
    const box = el("input", {type: "checkbox", disabled: !canTick || busy(), checked: ticked.has(copy.id),
                             title: canTick ? "Tick to post" : "Only checked copies can be posted", onchange: () => {
      if (box.checked) ticked.add(copy.id); else ticked.delete(copy.id);
      drawStatus();
    }});
    const status = el("td", {}, el("span", {className: `pill ${copy.stage}`, textContent: STAGES[copy.stage] || copy.stage}));
    const moving = ["queued", "checking", "posting"].includes(copy.stage) && busy();
    if (moving && copy.stage_at) {
      const minutes = (Date.now() / 1000 - copy.stage_at) / 60;
      const doing = String(copy.operation || STAGES[copy.stage]);
      const slow = copy.stage === "checking" && minutes > SLOW_MINUTES;
      status.append(el("div", {className: `note${slow ? " slow" : ""}`,
        textContent: `${doing.charAt(0).toUpperCase()}${doing.slice(1)} (${ago(copy.stage_at)} so far)` +
                     (slow ? ". This is longer than usual: a copy normally takes 2 to 5 minutes." : "")}));
    } else if (copy.operation) status.append(el("div", {className: "note", textContent: copy.operation}));
    if (copy.note) status.append(el("div", {className: "note", textContent: copy.note}));
    if ((copy.reasons || []).length && copy.stage === "ready") status.append(el("div", {className: "note", textContent: `To look at: ${copy.reasons.join(" ")}`}));
    const actions = el("td", {className: "row-actions"});
    if (copy.has_checked_pdf) actions.append(el("button", {type: "button", className: "ghost-btn small", textContent: "Checked PDF", onclick: guarded(() => openPdf(copy))}));
    if (!busy() && ["ready", "held"].includes(copy.stage) && copy.marks != null) actions.append(el("button", {type: "button", className: "ghost-btn small", textContent: "Read / change", onclick: () => { editing = copy.id; drawRun(); }}));
    if (!busy() && ["ready", "held"].includes(copy.stage)) actions.append(el("button", {type: "button", className: "ghost-btn small", textContent: "Check again", onclick: guarded(async () => {
      if (!confirm("Check this copy again? The AI check is paid for again.")) return;
      currentRun = await api(`/runs/${currentRun.id}/copies/${copy.id}/check-again`, {method: "POST"}); drawRun(); })}));
    const shown = copy.stage === "posted" ? copy.posted_marks ?? copy.marks : copy.marks;
    return el("tr", {}, el("td", {}, box),
      el("td", {className: "learner"}, el("div", {className: "nm", textContent: copy.student || "(no name)"}),
         el("div", {className: "note", textContent: [copy.email, copy.phone].filter(Boolean).join(" · ")})),
      el("td", {textContent: copy.assignment}),
      el("td", {className: "marks-big", textContent: shown == null ? "–" : `${shown} / ${copy.out_of}`}), status, actions);
  }
  function drawRun() {
    byId("runCard").classList.remove("hidden");
    const test = (TILES.find(([key]) => key === tile) || TILES[0])[2];
    const rows = [];
    currentRun.copies.filter(test).forEach(copy => { rows.push(row(copy)); if (editing === copy.id) rows.push(editor(copy)); });
    currentRun.copies.filter(copy => !test(copy)).forEach(copy => { if (copy.stage !== "ready") ticked.delete(copy.id); });
    byId("copies").replaceChildren(...(rows.length ? rows : [el("tr", {className: "empty-row"}, el("td", {colSpan: 6,
      textContent: currentRun.copies.length ? "No copies in this group." : busy() ? "Fetching copies from the portal…" : "This run found no copies to check."}))]));
    drawStatus();
    guarded(loadReport)();
  }
  async function act(path, body) { currentRun = await api(`/runs/${currentRun.id}/${path}`, {method: "POST", body: JSON.stringify(body || {})}); drawRun(); await loadRuns(); }

  // The run report (skills_report.py): shown once a run is not busy, refreshed when it changes.
  let reportFor = "";
  async function loadReport() {
    if (!currentRun || busy()) { byId("reportBox").classList.add("hidden"); reportFor = ""; return; }
    const key = `${currentRun.id}:${currentRun.updated_at}:${currentRun.status}`;
    if (key === reportFor) return;
    reportFor = key;
    const report = await api(`/runs/${currentRun.id}/report`);
    if (!currentRun || `${currentRun.id}:${currentRun.updated_at}:${currentRun.status}` !== key) return;
    const c = report.counts, parts = [];
    const add = (n, words) => { if (n) parts.push(`${n} ${words}`); };
    add(c.posted, "posted"); add(c.waiting_for_you, "waiting for you"); add(c.needs_a_person, c.needs_a_person > 1 ? "need a person" : "needs a person");
    add(c.evaluated_elsewhere, "evaluated by someone else"); add(c.in_progress, "still in progress");
    const body = [el("p", {}, el("b", {textContent: `${c.copies} ${c.copies === 1 ? "copy" : "copies"}`}), parts.length ? `: ${parts.join(", ")}.` : ".")];
    if (c.left_out_other_runs) body.push(el("p", {textContent: `${c.left_out_other_runs} more were left out because another run has them.`}));
    if (report.average_percent !== null && report.average_percent !== undefined) body.push(el("p", {textContent: `Average marks of the posted copies: ${report.average_percent}%.`}));
    if (report.reasons.length) {
      body.push(el("p", {}, el("b", {textContent: "Not posted, by reason"})));
      body.push(el("ul", {}, ...report.reasons.map(item => el("li", {}, el("b", {textContent: String(item.copies)}), item.reason))));
    }
    const links = report.links;
    if (links.total) body.push(el("p", {textContent: `Links: ${links.total} in ${links.copies_with_links} ${links.copies_with_links === 1 ? "copy" : "copies"}. ` +
      `${links["Opened and read"]} opened and read, ${links["Not opened"]} not opened` + (links["Not reached yet"] ? `, ${links["Not reached yet"]} not reached yet.` : ".")}));
    byId("reportBody").replaceChildren(...body);
    byId("reportBox").classList.remove("hidden");
  }
  async function downloadReport() {
    const response = await fetch(`/api/skills/runs/${currentRun.id}/report.xlsx`, {headers: {Authorization: `Bearer ${window.WorkflowAuth.getToken()}`}});
    if (!response.ok) throw new Error("The report could not be made.");
    const name = (/filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "") || [])[1] || "PW Skills report.xlsx";
    const link = el("a", {href: URL.createObjectURL(await response.blob()), download: name});
    document.body.append(link); link.click(); link.remove();
  }

  byId("adminOpen").onclick = guarded(async () => { await api("/sign-in", {method: "POST"}); await pollSignIn(); });
  byId("searchForm").onsubmit = event => { event.preventDefault(); clearTimeout(searchTimer); guarded(search)(true); };
  byId("courseSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => guarded(search)(false), 600); };
  byId("changeBatch").onclick = changeBatch;
  byId("titleFilter").oninput = filterTitles;
  byId("titlesAll").onclick = () => { document.querySelectorAll("#titles label:not(.hidden) .title").forEach(box => { box.checked = true; }); drawTitleCount(); drawStart(); };
  byId("titlesNone").onclick = () => { document.querySelectorAll(".title").forEach(box => { box.checked = false; }); drawTitleCount(); drawStart(); };
  byId("learners").oninput = drawLearners;
  ["mostCopies", "copiesAtOnce", "autoPost"].forEach(id => { byId(id).oninput = drawStart; byId(id).onchange = drawStart; });
  byId("start").onclick = guarded(start);
  byId("selectReady").onclick = () => { tile = "ready"; currentRun.copies.forEach(copy => { if (copy.stage === "ready") ticked.add(copy.id); }); drawRun(); };
  byId("post").onclick = guarded(async () => {
    if (!confirm(`Post ${ticked.size} mark(s) to the portal? Learners see them at once and cannot submit again.`)) return;
    await act("post", {ids: [...ticked]}); ticked = new Set(); toast("Posting started.", "ok"); });
  byId("stop").onclick = guarded(async () => { await act("stop"); toast("Stopping after the copies in hand."); });
  byId("downloadReport").onclick = guarded(downloadReport);
  byId("resume").onclick = guarded(async () => { await act("resume"); toast("Resumed: new and unfinished copies are being fetched.", "ok"); });
  try { byId("courseSearch").value = localStorage.getItem("skillsBatchSearch") || ""; } catch {}
  // The PW Skills tool (PWSkillsChecker.exe) has no copy workflow to link to.
  fetch("/api/config").then(response => response.json()).then(config => {
    if (config.app_mode === "skills") byId("workflowLink").classList.add("hidden");
  }).catch(() => {});
  setInterval(() => { if (window.WorkflowAuth.getToken()) guarded(pollSignIn)(); }, 4000);
  setInterval(() => { if (window.WorkflowAuth.getToken()) guarded(refreshRun)(); }, 3000);
  setInterval(() => { if (window.WorkflowAuth.getToken()) guarded(loadRuns)(); }, 8000);
  window.WorkflowAuth.init({onSignedIn: guarded(async () => { await pollSignIn(); await loadRuns(); }),
                            onSignedOut: () => { currentRun = null; signedIn = false; byId("runCard").classList.add("hidden"); }});
})();
