const $ = (id) => document.getElementById(id);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const pretty = (value) => String(value ?? "").replaceAll("_", " ");
const modelName = () => config?.model_names?.[$("model").value] || "The model";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const titles = { request: "Request type", queue: "Team", priority: "Priority", needs_human: "Staff review", topics: "Topics" };
const decisionEvents = ["business_decision", "field_classification", "browser_action"];
let config, client, workflow = "clinic", running = false, job, generation = 0;
let records = [], ledger = [], predictions = {}, currentTicket, route, modelMs = null;
let runFailed = false, finished = false, currentStage = 0;

function isSimulation() { return workflow === "clinic" || workflow === "helpdesk"; }
function isStamp() { return workflow === "stamp"; }
function isExtract() { return workflow === "extract"; }
function isLab() { return workflow === "lab"; }

function status(text, state = "") {
  $("status").textContent = text;
  $("status").parentElement.className = `run-status ${state}`;
}

function fail(error) {
  runFailed = true;
  $("error").textContent = error?.message || String(error);
  $("error").hidden = false;
  status("Run stopped. Check the error and try again.", "failed");
}

function setBusy(value) {
  running = value;
  document.querySelectorAll(".nav-button, #model, #sample, #send, #inbox, #run-browser, #stamp-sample, #stamp-batch, #stamp-upload, #x-document, #x-threshold, #x-upload, #x-run, #x-run-all, #x-add, #x-reset, #x-segment, #lab-document, #lab-run, #lab-upload").forEach((el) => { el.disabled = value; });
  if (typeof xPagerLabel === "function" && config) xPagerLabel();
  $("stamp-check").disabled = value || (!uploadData && !$("stamp-sample").value);
  $("stamp-batch").disabled = value && !stampBatch;
  $("stamp-batch").textContent = value && stampBatch ? "Stop playing ■" : "Play all samples";
  $("stamp-batch").classList.toggle("stop", value && stampBatch);
  $("stamp-download").disabled = value || !stampLedger.length;
  $("subject").readOnly = value;
  $("message").readOnly = value;
  $("stop").disabled = !value;
  $("download").disabled = value || !ledger.length;
  document.querySelectorAll(".ledger-request").forEach((el) => { el.disabled = value; });
}

function stage(number) {
  currentStage = number;
  document.querySelectorAll("[data-stage]").forEach((el) => {
    const n = Number(el.dataset.stage);
    el.classList.toggle("active", n === number);
    el.classList.toggle("done", n < number);
    if (n === number) el.setAttribute("aria-current", "step");
    else el.removeAttribute("aria-current");
  });
  $("input-card").classList.toggle("active", number === 1);
  $("model-card").classList.toggle("active", number === 2 || number === 3);
  $("output-card").classList.toggle("active", number === 4);
}

function renderPredictions() {
  if (!isSimulation()) return;
  $("predictions").innerHTML = Object.entries(config.schemas[workflow]).map(([head, spec]) => {
    const result = predictions[head];
    const count = Object.keys(spec.labels).length;
    let content = '<div class="pending-label"></div>';
    if (result) {
      content = result.predictions.length ? result.predictions.map((item) => {
        const score = item.confidence;
        const percent = typeof score === "number" ? `${(score * 100).toFixed(1)}%` : "Not reported";
        const bar = typeof score === "number" ? `<div class="confidence-bar" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, score * 100))}%"></span></div>` : "";
        return `<div class="prediction-label"><span>${escape(pretty(item.label))}</span><span class="confidence">${escape(percent)}</span></div>${bar}`;
      }).join("") : '<div class="prediction-label">No topic above threshold</div>';
    }
    return `<div class="prediction ${result ? "" : "pending"}"><div class="prediction-head"><span>${escape(titles[head])}</span><span>${count} labels${spec.multi_label ? " · multi" : ""}</span></div>${content}</div>`;
  }).join("");
  $("model-time").textContent = modelMs === null ? "Model time · waiting" : `Model call · ${(modelMs / 1000).toFixed(2)}s`;
}

function renderRules() {
  $("rule-placeholder").hidden = Boolean(route);
  $("rules").innerHTML = (route?.rules || []).map((rule) => `<li>${escape(rule)}</li>`).join("");
}

function priorityBadge(value) {
  const level = ["urgent", "high", "soon"].includes(value) ? value : "";
  return `<span class="priority ${level}">${escape(value)}</span>`;
}

function renderOutput(output) {
  $("output-empty").hidden = Boolean(output);
  $("ticket-output").hidden = !output;
  $("ticket-output").innerHTML = "";
  if (!output) return;
  const review = output.review === "review_required" ? "Staff review required" : "Standard staff queue";
  const queue = pretty(output.queue);
  const acknowledgment = `Your request ${output.ticket_id} has been sent to ${queue}.${output.review === "review_required" ? " A staff member needs to review it." : " It is waiting in the staff queue."}`;
  $("ticket-output").innerHTML = `
    <div class="ticket-header"><span>${escape(output.ticket_id)}</span><span class="ticket-state">Ticket produced</span></div>
    <div class="destination"><span class="eyebrow">Assigned queue</span><strong>${escape(queue)}</strong></div>
    <dl class="ticket-fields">
      <div><dt>Category</dt><dd>${escape(pretty(output.category))}</dd></div>
      <div><dt>Priority</dt><dd>${priorityBadge(output.priority)}</dd></div>
      <div><dt>Review</dt><dd>${escape(review)}</dd></div>
      ${output.tags.length ? `<div><dt>Topics</dt><dd>${escape(output.tags.map(pretty).join(", "))}</dd></div>` : ""}
    </dl>
    <div class="acknowledgment"><div class="eyebrow">Template acknowledgment / simulated</div><p>${escape(acknowledgment)}</p></div>`;
}

function renderLedger(selectedID) {
  $("ledger-count").textContent = ledger.length;
  $("run-count").textContent = isSimulation() ? `${ledger.length} ticket${ledger.length === 1 ? "" : "s"} produced` : "0 model decisions";
  $("ledger-empty").hidden = ledger.length > 0;
  $("ledger-table").hidden = ledger.length === 0;
  $("ledger-rows").innerHTML = ledger.map((entry, index) => {
    const record = entry.output;
    return `<tr class="${record.ticket_id === selectedID ? "selected" : ""}"><td><button class="ledger-request" data-index="${index}" ${running ? "disabled" : ""}><span>${escape(record.ticket_id)}</span>${escape(record.subject)}</button></td><td>${escape(pretty(record.queue))}</td><td>${priorityBadge(record.priority)}</td><td>${record.review === "review_required" ? "Required" : "Standard queue"}</td><td>${(entry.modelMs / 1000).toFixed(2)}s</td></tr>`;
  }).join("");
}

function updateMessageCount() { $("character-count").textContent = `${$("message").value.length} / 4,000`; }

function clearFlow() {
  predictions = {};
  currentTicket = null;
  route = null;
  modelMs = null;
  $("request-id").textContent = "Draft";
  stage(0);
  renderPredictions();
  renderRules();
  renderOutput(null);
}

function resetRun() {
  records = [];
  ledger = [];
  runFailed = false;
  finished = false;
  $("error").hidden = true;
  $("event-log").textContent = "No events yet.";
  $("screenshot").hidden = true;
  $("screenshot").removeAttribute("src");
  $("browser-empty").hidden = false;
  $("browser-latest").textContent = "Field and action classifications will appear here.";
  $("browser-confidence").textContent = "Waiting";
  $("model-time").textContent = isSimulation() ? "Model time · waiting" : "Run time · waiting";
  $("download").disabled = true;
  clearFlow();
  renderLedger();
}

function selectSample() {
  const ticket = config.samples[workflow][Number($("sample").value)];
  $("subject").value = ticket?.subject || "";
  $("message").value = ticket?.message || "";
  resetRun();
  updateMessageCount();
  status("Ready. Send a request to follow its flow.");
}

function selectWorkflow(id) {
  if (running || !config.workflows.some((item) => item.id === id)) return;
  workflow = id;
  history.replaceState(null, "", `#${id}`);
  const item = config.workflows.find((item) => item.id === id);
  document.querySelectorAll(".nav-button").forEach((button) => {
    if (button.dataset.workflow === id) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("workflow-title").textContent = item.name;
  fillModels();
  $("stamp-demo").hidden = !isStamp();
  $("extract-demo").hidden = !isExtract();
  $("lab-demo").hidden = !isLab();
  if (isLab()) {
    $("workflow-kind").textContent = "Document vision / 09";
    $("workflow-description").textContent = "Read a photographed lab report into checked results: tests, values, units, ranges and flags.";
    $("mode-badge").textContent = "OCR · d1 decisions · d1 vision";
    $("simulation").hidden = true;
    $("browser-demo").hidden = true;
    $("footer-note").textContent = "One real report with patient details redacted, and synthetic reports with answer keys.";
    resetRun();
    $("run-count").textContent = "0 tests read";
    $("model-time").textContent = "Model time · waiting";
    if (!labState.docId) labSelect();
    status("Ready. Read a sample report, or upload a photo of one.");
    return;
  }
  if (isExtract()) {
    $("workflow-kind").textContent = "Document vision / 08";
    $("workflow-description").textContent = "Extract spans of any length from a real PDF and see exactly which lines each one covers.";
    $("mode-badge").textContent = "Live extraction · GPU";
    $("simulation").hidden = true;
    $("browser-demo").hidden = true;
    $("footer-note").textContent = "Public-domain U.S. government documents. Real model predictions.";
    resetRun();
    $("run-count").textContent = "0 spans";
    $("model-time").textContent = "Model time · waiting";
    if (!xState.docId) xSelectDocument(); else xRenderPage(false);
    status("Ready. Extract this page, or every page of the document.");
    return;
  }
  if (isStamp()) {
    $("workflow-kind").textContent = "Document vision / 07";
    $("workflow-description").textContent = "Check that a certificate carries the right official stamp, from a scan or a phone photo.";
    $("mode-badge").textContent = "Live vision inference";
    $("simulation").hidden = true;
    $("browser-demo").hidden = true;
    $("footer-note").textContent = "Synthetic certificates. Real model predictions on images.";
    resetRun();
    resetStamp();
    updateModel();
    status("Ready. Pick a certificate or upload a photo, then check it.");
    return;
  }
  $("workflow-kind").textContent = isSimulation() ? `Request routing / ${id === "clinic" ? "05" : "06"}` : "Browser automation";
  $("workflow-description").textContent = id === "clinic" ? "Follow a patient message from the inbox to the right clinic team." : id === "helpdesk" ? "Follow an employee request from a shared inbox to the team that can help." : "Watch the model choose each action and Playwright carry it out.";
  $("mode-badge").textContent = isSimulation() ? "Simulation · live inference" : "Live browser · live inference";
  $("simulation").hidden = !isSimulation();
  $("browser-demo").hidden = isSimulation();
  $("footer-note").textContent = isSimulation() ? "Real model predictions. Simulated request delivery." : "Synthetic data. Live browser execution.";
  if (isSimulation()) {
    $("sender-title").textContent = id === "clinic" ? "The patient writes" : "The employee writes";
    $("channel-label").textContent = id === "clinic" ? "Patient message" : "Employee message";
    $("sample").innerHTML = config.samples[id].map((ticket, index) => `<option value="${index}">${escape(ticket.subject)}</option>`).join("") + '<option value="custom">Write your own request</option>';
    $("inbox").textContent = `Play sample inbox · ${config.samples[id].length} requests`;
    $("taxonomy").innerHTML = Object.entries(config.schemas[id]).map(([head, spec]) => {
      const labels = Array.isArray(spec.labels) ? spec.labels : Object.keys(spec.labels);
      return `<h3>${escape(titles[head])}</h3><p>${escape(labels.join(" · "))}</p>`;
    }).join("");
    selectSample();
  } else {
    resetRun();
    status("Ready to run the browser workflow.");
  }
  updateModel();
}

function fillModels() {
  const options = isLab()
    ? config.lab.models.map((m) => [m.id, m.name])
    : isExtract()
    ? config.extract.models.map((m) => [m.id, m.name])
    : isStamp()
    ? config.stamp.models.map((m) => [m.id, m.name])
    : config.models.map((m) => [m, config.model_names[m]]);
  if ([...$("model").options].map((o) => o.value).join() === options.map(([id]) => id).join()) return;
  $("model").innerHTML = options.map(([id, name]) => `<option value="${escape(id)}">${escape(name)}</option>`).join("");
}

const modelNotes = {
  omni: "Default. Passed 12/12 live browser runs; every field and claim decision correct.",
  "3b": "Same decisions as d1-omni-600M, at about 1.5× the model time.",
  multi: "Maps every form field correctly, but stops safely at Piko's page-action step and approves every claim it is shown.",
};

function updateModel() {
  $("model-note").textContent = isStamp() || isExtract() || isLab() ? "" : modelNotes[$("model").value] || "";
  if (isStamp()) { renderReliability(); updateVisionTitle(); return; }
  if (isExtract() || isLab()) return;
  $("model-title").textContent = `${modelName()} reads it`;
  document.querySelector(".model-icon").textContent = modelName()[0];
  const item = config.workflows.find((item) => item.id === workflow);
  $("browser-guide").innerHTML = item.guide.replaceAll("GLiNER", modelName());
}

function consume(record) {
  records.push(record);
  $("event-log").textContent = records.map((item) => JSON.stringify(item, null, 2)).join("\n\n");
  if (!isSimulation() && decisionEvents.includes(record.event)) {
    $("browser-latest").textContent = JSON.stringify(record, null, 2);
    $("browser-confidence").textContent = typeof record.confidence === "number" ? `${(record.confidence * 100).toFixed(1)}% confidence` : "Confidence not reported";
    $("run-count").textContent = `${records.filter((item) => decisionEvents.includes(item.event)).length} model decisions`;
  }
  if (record.event === "request_received") {
    clearFlow();
    currentTicket = record.ticket;
    $("subject").value = currentTicket.subject;
    $("message").value = currentTicket.message;
    const index = config.samples[workflow].findIndex((ticket) => ticket.id === currentTicket.id);
    $("sample").value = index >= 0 ? String(index) : "custom";
    $("request-id").textContent = currentTicket.id;
    updateMessageCount();
    stage(1);
    status(`Request ${record.index} received · ${currentTicket.id}`, "busy");
  } else if (record.event === "classification_started") {
    stage(2);
    status(`${modelName()} is reading the message…`, "busy");
  } else if (record.event === "business_decision" && isSimulation()) {
    predictions[record.task] = record;
    modelMs = record.model_ms;
    renderPredictions();
    status(`${modelName()} returned ${pretty(record.task)} · ${pretty(record.decision)}`, "busy");
  } else if (record.event === "routing_decided") {
    route = record;
    stage(3);
    renderRules();
    status("Applying queue mapping and review rules…", "busy");
  } else if (record.event === "ticket_routed" && isSimulation()) {
    stage(4);
    renderOutput(record);
    ledger.push({ ticket: currentTicket, predictions: { ...predictions }, output: record, modelMs });
    renderLedger(record.ticket_id);
    status(`Ticket ${record.ticket_id} produced · ${pretty(record.queue)}`, "busy");
  } else if (record.event === "run_failed") {
    fail(record.error);
  } else if (record.event === "run_finished") {
    finished = true;
    status(isSimulation() ? `Complete · ${ledger.length} request${ledger.length === 1 ? "" : "s"} routed` : "Complete · browser workflow passed");
  } else if (!isSimulation()) {
    status(`${pretty(record.event)}${record.decision ? ` · ${record.decision}` : ""}`, "busy");
  }
}

async function startRun(mode) {
  if (running) return;
  if (isSimulation() && mode === "single" && !$("request-form").reportValidity()) return;
  const input = { workflow, model: $("model").value, mode, subject: $("subject").value, message: $("message").value };
  const thisRun = ++generation;
  resetRun();
  setBusy(true);
  status("Connecting to the Gradio queue…", "busy");
  try {
    await getClient();
    if (thisRun !== generation) return;
    job = client.submit("/run_demo", input);
    let seen = 0;
    for await (const update of job) {
      if (thisRun !== generation) return;
      if (update.type === "status" && update.stage === "error") throw new Error(update.message || "The model run failed.");
      if (update.type === "status" && update.stage === "pending") status("Queued. Waiting for the previous run to finish…", "busy");
      if (update.type !== "data") continue;
      const payload = update.data[0];
      if (!Array.isArray(payload?.events)) throw new Error("The server returned an invalid event stream.");
      if (payload.screenshot && !isSimulation()) {
        $("screenshot").src = payload.screenshot;
        $("screenshot").hidden = false;
        $("browser-empty").hidden = true;
      }
      if (!isSimulation()) $("model-time").textContent = `Run time · ${payload.elapsed_s.toFixed(1)}s`;
      for (const record of payload.events.slice(seen)) {
        if (thisRun !== generation) return;
        consume(record);
        seen++;
        if (isSimulation() && $("pace").value === "guided" && ["request_received", "classification_started", "business_decision", "routing_decided", "ticket_routed"].includes(record.event)) {
          await delay(record.event === "ticket_routed" ? 1100 : 550);
        }
      }
    }
    if (!finished && !runFailed) throw new Error("The connection ended before the run finished. You can send the request again.");
  } catch (error) {
    if (thisRun === generation) fail(error);
  } finally {
    if (thisRun === generation) {
      job = null;
      setBusy(false);
    }
  }
}

// ---- Document stamp check -------------------------------------------------
let uploadData = null, stampLedger = [], stampRecords = [], stampDoc = null, stampChecks = {}, stampCalls = [];
const verdictWords = { accept: "Accepted", reject: "Rejected", review: "Human review" };
const statusWords = { pass: "pass", fail: "fail", uncertain: "unsure", unverified: "not trusted", skipped: "blind spot", waiting: "waiting", review: "review" };
const stampModelName = () => config.stamp.models.find((m) => m.id === $("model").value)?.name || "d1";
const sampleById = (id) => config.stamp.samples.find((s) => s.id === id);
const labelTitle = (label) => sampleById(`${label}-1`)?.title || pretty(label);
const allChecks = () => [...config.stamp.checks, { id: "anywhere", title: "Stamp anywhere on the page" }];

function stampStage(number) {
  document.querySelectorAll("[data-sstage]").forEach((el) => {
    const n = Number(el.dataset.sstage);
    el.classList.toggle("active", n === number);
    el.classList.toggle("done", n < number);
  });
  $("doc-card").classList.toggle("active", number === 1 || number === 2);
  $("vision-card").classList.toggle("active", number === 2 || number === 3);
  $("verdict-card").classList.toggle("active", number === 4);
}

function updateVisionTitle() {
  $("vision-title").textContent = `${stampModelName()} looks at the stamp`;
  $("vision-icon").textContent = $("model").value === "3b" ? "3B" : "0.6";
}

function renderReliability() {
  const models = config.stamp.models;
  const rows = config.stamp.checks.map((check) => `<tr><td>${escape(check.title)}</td>${models.map((m) => {
    const r = config.stamp.reliability[m.id][check.id];
    return `<td class="${r.trusted ? "trust" : "no-trust"}">${r.trusted ? "✓" : "—"} ${r.test_auroc.toFixed(2)}</td>`;
  }).join("")}</tr>`).join("");
  $("reliability").innerHTML = `<p>A check is trusted when its AUROC on a separate calibration set is at least ${config.stamp.reliable_auroc}. Numbers are AUROC on the held-out test set. Untrusted checks go to a person instead of deciding.</p>
    <table class="reliability-table"><thead><tr><th>Check</th>${models.map((m) => `<th>${escape(m.name)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
}

// Scores are drawn on a log-odds scale: thresholds such as 0.00004 and 0.75 both get room to separate.
const logOdds = (p) => Math.log(Math.min(Math.max(p, 1e-6), 1 - 1e-6) / (1 - Math.min(Math.max(p, 1e-6), 1 - 1e-6)));
const barPos = (p) => ((Math.max(-9, Math.min(9, logOdds(p))) + 9) / 18 * 100).toFixed(1);
const fmt = (p) => p < 0.001 ? p.toExponential(1) : p > 0.999 ? `1 − ${(1 - p).toExponential(1)}` : p.toFixed(3);

function checkState(check) {
  const r = stampChecks[check.id];
  const verdict = stampDoc?.verdict;
  if (r) return r.uncertain ? "uncertain" : r.passed ? "pass" : (check.advisory ? "review" : "fail");
  return verdict ? (verdict.statuses?.[check.id] || "unverified") : "waiting";
}

function renderChecks(rebuild = false) {
  const list = $("checks");
  const visible = allChecks().filter((c) => c.id !== "anywhere" || stampChecks.anywhere);
  if (rebuild || list.children.length !== visible.length) {
    list.innerHTML = visible.map((c, i) => `<li class="check" data-check="${escape(c.id)}" style="animation-delay:${i * 30}ms"></li>`).join("");
  }
  const model = stampDoc?.verdict?.model || stampDoc?.model || $("model").value;
  visible.forEach((check, i) => {
    const li = list.children[i];
    const r = stampChecks[check.id];
    const state = checkState(check);
    const reliability = config.stamp.reliability[model]?.[check.id];
    const name = config.stamp.models.find((m) => m.id === model)?.name || "this model";
    const note = r ? `score ${fmt(r.score)} · threshold ${fmt(r.threshold)} · calib AUROC ${r.calib_auroc.toFixed(2)}`
      : state === "skipped" ? `known blind spot · test AUROC ${reliability?.test_auroc.toFixed(2)}`
      : state === "unverified" ? `not trusted on ${name} · test AUROC ${reliability?.test_auroc.toFixed(2)}` : "";
    const bar = r ? `<div class="check-bar" title="log-odds scale"><span data-w="${barPos(r.score)}"></span><i style="left:${barPos(r.threshold)}%"></i></div>` : "";
    const html = `<div class="check-head"><span>${escape(check.title)}</span><span class="chip ${state}">${escape(statusWords[state] || state)}</span></div>${bar}${note ? `<div class="check-note">${escape(note)}</div>` : ""}`;
    li.className = `check ${state}`;
    if (li.dataset.html !== html) {
      li.innerHTML = html;
      li.dataset.html = html;
      const span = li.querySelector(".check-bar span");
      if (span) requestAnimationFrame(() => requestAnimationFrame(() => { span.style.width = `${span.dataset.w}%`; }));
    }
  });
}

function renderVerdict() {
  const v = stampDoc?.verdict;
  $("verdict-empty").hidden = Boolean(v);
  $("verdict-output").hidden = !v;
  if (!v) { $("verdict-output").innerHTML = ""; return; }
  let truth = "";
  if (v.expected) {
    const soft = !v.correct && v.verdict === "review";
    const cls = v.correct ? "" : soft ? "miss-soft" : "miss";
    const mark = v.correct ? "✓ Correct" : soft ? "Sent to a person" : "✗ Missed";
    truth = `<div class="verdict-truth ${cls}"><strong>${mark}.</strong> Known defect: ${escape(labelTitle(v.expected))}. Expected ${escape(verdictWords[v.expected_verdict].toLowerCase())}${v.expected_verdict === "reject" ? ` · ${escape(pretty(v.expected))}` : ""}.</div>`;
  }
  $("verdict-output").innerHTML = `<div class="verdict">
    <div class="verdict-stamp ${v.verdict}">${verdictWords[v.verdict]}</div>
    <p class="verdict-reason">${escape(v.reason)}</p>
    ${truth}
    <dl class="ticket-fields">
      <div><dt>Model</dt><dd>${escape(v.model_name)}</dd></div>
      <div><dt>Forward passes</dt><dd>${v.model_calls}</dd></div>
      <div><dt>Model time</dt><dd>${v.model_ms.toFixed(0)} ms</dd></div>
      <div><dt>End to end</dt><dd>${v.elapsed_ms.toFixed(0)} ms</dd></div>
    </dl></div>`;
}

function renderStampLedger(selected) {
  $("stamp-count").textContent = stampLedger.length;
  $("stamp-ledger-empty").hidden = stampLedger.length > 0;
  $("stamp-ledger-table").hidden = stampLedger.length === 0;
  const scored = stampLedger.filter((e) => e.verdict.expected);
  if (scored.length) {
    const right = scored.filter((e) => e.verdict.correct).length;
    const fraudIn = scored.filter((e) => e.verdict.expected !== "valid" && e.verdict.verdict === "accept").length;
    const validOut = scored.filter((e) => e.verdict.expected === "valid" && e.verdict.verdict === "reject").length;
    const reviewed = stampLedger.filter((e) => e.verdict.verdict === "review").length;
    $("stamp-score").textContent = `${right}/${scored.length} correct · ${fraudIn} bad stamps accepted · ${validOut} valid rejected · ${reviewed} sent to a person`;
  }
  $("stamp-rows").innerHTML = stampLedger.map((e, i) => {
    const v = e.verdict;
    const result = v.correct == null ? "—" : v.correct ? '<span class="result-ok">✓ correct</span>'
      : v.verdict === "review" ? '<span class="result-soft">to a person</span>' : '<span class="result-miss">✗ missed</span>';
    const verdict = v.verdict === "reject" ? `Rejected · ${pretty(v.label)}` : verdictWords[v.verdict];
    return `<tr class="${e.doc.doc_id === selected ? "selected" : ""}"><td><button class="ledger-request" data-stamp="${i}" ${running ? "disabled" : ""}><span>${escape(e.doc.doc_id)}</span>${escape(e.title)}</button></td><td>${escape(e.doc.capture || "upload")}</td><td>${escape(v.expected ? labelTitle(v.expected) : "unknown")}</td><td>${escape(verdict)}</td><td>${result}</td><td>${v.model_ms.toFixed(0)} ms</td></tr>`;
  }).join("");
}

function showSample() {
  const sample = sampleById($("stamp-sample").value);
  if (!sample) return;
  uploadData = null;
  $("stamp-upload").value = "";
  $("doc-image").src = `stamp-samples/${sample.file}`;
  $("doc-badge").textContent = "Sample";
  $("doc-capture").textContent = `Capture · ${sample.capture}`;
  $("doc-expected").textContent = `Known defect · ${sample.title}`;
}

function resetStamp(keepLedger = false) {
  stampDoc = null;
  stampChecks = {};
  stampCalls = [];
  if (!keepLedger) { stampLedger = []; stampRecords = []; }
  $("region-box").hidden = true;
  $("doc-scan").hidden = true;
  $("crop-scan").hidden = true;
  $("crop-image").hidden = true;
  $("crop-empty").hidden = false;
  $("pass-info").textContent = "Waiting for a document";
  stampStage(0);
  renderChecks(true);
  renderVerdict();
  renderStampLedger();
  $("run-count").textContent = `${stampLedger.length} document${stampLedger.length === 1 ? "" : "s"} checked`;
  $("model-time").textContent = "Model time · waiting";
  if (!uploadData) showSample();
  setBusy(running);
}

function placeRegion(box = config.stamp.region) {
  const [x0, y0, x1, y1] = box;
  Object.assign($("region-box").style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` });
  $("region-box").hidden = false;
}

let stampBatch = false;

function consumeStamp(record) {
  stampRecords.push(record);
  $("event-log").textContent = stampRecords.map((item) => JSON.stringify(item, null, 2)).join("\n\n");
  if (record.event === "check_started") {
    status(`Checking ${record.total} document${record.total === 1 ? "" : "s"} with ${record.model_name}…`, "busy");
  } else if (record.event === "document_received") {
    stampDoc = { ...record, verdict: null };
    stampChecks = {};
    stampCalls = [];
    const page = record.image_url;
    if (record.source === "sample") $("stamp-sample").value = record.doc_id;
    if (page) $("doc-image").src = page;
    $("doc-badge").textContent = record.source === "upload" ? "Upload" : "Sample";
    $("doc-capture").textContent = `Capture · ${record.capture || "your image"} · ${record.size.join("×")} px`;
    $("doc-expected").textContent = record.expected ? `Known defect · ${labelTitle(record.expected)}` : "Known defect · unknown";
    $("region-box").hidden = true;
    $("doc-scan").hidden = false;
    $("crop-image").hidden = true;
    $("crop-empty").hidden = false;
    stampStage(1);
    renderChecks(true);
    renderVerdict();
    status(`${record.doc_id} received · finding the stamp region`, "busy");
  } else if (record.event === "region_selected") {
    $("doc-scan").hidden = true;
    placeRegion(record.region);
    stampDoc.region = record.region;
    const crop = record.image_url;
    if (crop) { $("crop-image").src = crop; $("crop-image").hidden = false; $("crop-empty").hidden = true; }
    stampStage(2);
    status("Cropping the Official stamp box for a closer look", "busy");
  } else if (record.event === "page_found") {
    status("Found the page inside the photo · cropping away the background", "busy");
  } else if (record.event === "model_call_started") {
    $("crop-scan").hidden = false;
    stampStage(3);
    const n = Object.keys(record.questions).length;
    $("pass-info").innerHTML = `<strong>${escape(record.model_name)}</strong> · one forward pass · ${n} question${n === 1 ? "" : "s"} about the ${record.image === "page" ? "whole page" : "stamp region"}`;
    status(`${record.model_name} is reading the ${record.image === "page" ? "page" : "stamp"}…`, "busy");
  } else if (record.event === "check_scored") {
    stampChecks[record.check] = record;
    renderChecks();
  } else if (record.event === "model_call_finished") {
    $("crop-scan").hidden = true;
    stampCalls.push(record.model_ms);
    $("pass-info").innerHTML += ` · <strong>${record.model_ms.toFixed(0)} ms</strong>`;
  } else if (record.event === "verdict") {
    stampDoc.verdict = record;
    stampStage(4);
    renderChecks();
    renderVerdict();
    stampLedger.push({ doc: stampDoc, title: sampleById(record.doc_id)?.title || "Your upload", checks: { ...stampChecks }, verdict: record, region: $("crop-image").src, page: $("doc-image").src });
    renderStampLedger(record.doc_id);
    const total = stampLedger.reduce((sum, e) => sum + e.verdict.model_ms, 0);
    $("run-count").textContent = `${stampLedger.length} document${stampLedger.length === 1 ? "" : "s"} checked`;
    $("model-time").textContent = `Model time · ${total.toFixed(0)} ms`;
    status(`${record.doc_id} · ${verdictWords[record.verdict]} · ${record.reason}`, "busy");
  } else if (record.event === "completed") {
    finished = true;
    status(record.labelled ? `Complete · ${record.correct}/${record.labelled} correct · ${record.model_ms.toFixed(0)} ms of model time` : "Complete · verdict ready");
  } else if (record.event === "run_failed") {
    fail(record.error);
  }
}

const stampPause = { document_received: 650, region_selected: 750, model_call_started: 350, check_scored: 160, verdict: 1300 };

async function getClient() {
  if (!client) {
    const { Client } = await import("https://cdn.jsdelivr.net/npm/@gradio/client@2.7.1/dist/index.min.js");
    client = await Client.connect(new URL("./", location.href).href, { events: ["data", "status"] });
  }
  return client;
}

async function startStamp(mode) {
  if (running) return;
  stampBatch = mode === "batch";
  const input = { model: $("model").value, mode, sample: uploadData ? "" : $("stamp-sample").value, upload: mode === "single" && uploadData ? uploadData : "" };
  const thisRun = ++generation;
  runFailed = false;
  finished = false;
  $("error").hidden = true;
  if (mode === "batch") { uploadData = null; resetStamp(); } else resetStamp(true);
  setBusy(true);
  status("Connecting to the Gradio queue…", "busy");
  try {
    await getClient();
    if (thisRun !== generation) return;
    job = client.submit("/check_document", input);
    let seen = 0;
    for await (const update of job) {
      if (thisRun !== generation) return;
      if (update.type === "status" && update.stage === "error") throw new Error(update.message || "The check failed.");
      if (update.type === "status" && update.stage === "pending") status("Queued. Waiting for the previous run to finish…", "busy");
      if (update.type !== "data") continue;
      const payload = update.data[0];
      if (!Array.isArray(payload?.events)) throw new Error("The server returned an invalid event stream.");
      for (const record of payload.events.slice(seen)) {
        if (thisRun !== generation) return;
        consumeStamp(record);
        seen++;
        if ($("pace").value === "guided" && stampPause[record.event]) await delay(stampPause[record.event]);
      }
    }
    if (!finished && !runFailed) throw new Error("The connection ended before the check finished. Try again.");
  } catch (error) {
    if (thisRun === generation) fail(error);
  } finally {
    if (thisRun === generation) { job = null; stampBatch = false; $("doc-scan").hidden = true; $("crop-scan").hidden = true; setBusy(false); }
  }
}

function readUpload(file) {
  if (!file) return;
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { fail(new Error("Upload a JPEG, PNG or WebP image.")); return; }
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      // Phone photos are often 12 MP; 2,200 px on the long side keeps the stamp legible and the upload small.
      const scale = Math.min(1, 2200 / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      uploadData = canvas.toDataURL("image/jpeg", 0.92);
      $("doc-image").src = uploadData;
      $("stamp-sample").value = "";
      $("doc-badge").textContent = "Upload";
      $("doc-capture").textContent = `Your image · ${canvas.width}×${canvas.height} px`;
      $("doc-expected").textContent = "Known defect · unknown";
      $("error").hidden = true;
      resetStamp(true);
      status("Upload ready. Check it to see the verdict.");
    };
    img.onerror = () => fail(new Error("That image could not be read."));
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

$("stamp-sample").addEventListener("change", () => { uploadData = null; resetStamp(true); status("Ready. Check this document."); });
$("stamp-check").addEventListener("click", () => startStamp("single"));
$("stamp-batch").addEventListener("click", () => (running && stampBatch ? stopRun() : startStamp("batch")));
$("stamp-upload").addEventListener("change", (event) => readUpload(event.target.files[0]));
$("stamp-rows").addEventListener("click", (event) => {
  if (running) return;
  const button = event.target.closest("[data-stamp]");
  if (!button) return;
  const entry = stampLedger[Number(button.dataset.stamp)];
  stampDoc = { ...entry.doc, verdict: entry.verdict };
  stampChecks = entry.checks;
  $("doc-image").src = entry.page;
  $("crop-image").src = entry.region;
  $("crop-image").hidden = false;
  $("crop-empty").hidden = true;
  placeRegion(entry.doc.region);
  stampStage(4);
  renderChecks(true);
  renderVerdict();
  renderStampLedger(entry.doc.doc_id);
  status(`Inspecting ${entry.doc.doc_id} · saved result from this session`);
});
$("stamp-download").addEventListener("click", () => {
  const rows = stampLedger.map(({ doc, checks, verdict }) => ({ doc, checks, verdict }));
  const blob = new Blob([JSON.stringify({ workflow: "stamp", model: $("model").value, documents: rows, events: stampRecords }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "stamp-check.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// ---- Long-span extraction -------------------------------------------------
const xPalette = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#d97706", "#0891b2", "#db2777", "#65a30d", "#4f46e5", "#ea580c", "#0d9488", "#be123c"];
const xState = { docId: "", upload: null, page: 1, pageCount: null, results: {}, rows: [], hidden: new Set(), records: [], summary: null, longest: null };
const xLabelPattern = /^[a-z][a-z0-9_]{0,39}$/;
const xDoc = () => config.extract.documents.find((d) => d.id === xState.docId);
const xColor = (label) => xPalette[Math.max(0, xState.rows.findIndex((r) => r.label === label)) % xPalette.length];
const xSchema = () => Object.fromEntries(xState.rows.map((r) => [r.label.trim(), r.description.trim()]));
const pct = (v) => `${(v * 100).toFixed(3)}%`;

function xSetRows(schema) {
  xState.rows = Object.entries(schema || {}).map(([label, description]) => ({ label, description }));
  xState.hidden.clear();
  xRenderRows();
}

function xRenderRows() {
  $("x-label-count").textContent = xState.rows.length;
  $("x-labels").innerHTML = xState.rows.map((r, i) => `<div class="x-label-row" style="--c:${xPalette[i % xPalette.length]}"><span class="swatch"></span>
    <input data-row="${i}" data-field="label" value="${escape(r.label)}" maxlength="40" aria-label="Label ${i + 1} name" ${running ? "disabled" : ""}>
    <textarea data-row="${i}" data-field="description" maxlength="400" aria-label="Label ${i + 1} description" ${running ? "disabled" : ""}>${escape(r.description)}</textarea>
    <button type="button" data-remove="${i}" aria-label="Remove ${escape(r.label)}" ${running ? "disabled" : ""}>×</button></div>`).join("");
  $("x-add").disabled = running || xState.rows.length >= config.extract.max_labels;
}

function xInvalidate(message) {
  xState.results = {};
  xState.summary = null;
  $("x-pages").hidden = true;
  xRenderPage();
  if (message) status(message);
}

function xPagerLabel() {
  $("x-page-label").textContent = xState.pageCount ? `Page ${xState.page} of ${xState.pageCount}` : `Page ${xState.page}`;
  $("x-prev").disabled = running || xState.page <= 1;
  $("x-next").disabled = running || (xState.pageCount ? xState.page >= xState.pageCount : true);
}

function xSelectDocument() {
  const value = $("x-document").value;
  if (value === "upload") return;
  xState.docId = value;
  xState.upload = null;
  $("x-upload").value = "";
  const doc = xDoc();
  xState.page = doc.default_page || 1;
  xState.pageCount = doc.pages || null;
  const link = doc.source ? ` · <a href="${escape(doc.source)}" target="_blank" rel="noreferrer">source PDF ↗</a>` : "";
  $("x-source").innerHTML = `${escape(doc.subtitle)}${link} · ${escape(doc.license)}${doc.note ? ` · ${escape(doc.note)}` : ""}`;
  if (doc.model && $("model").value !== doc.model) $("model").value = doc.model;
  xSetRows(doc.schema);
  xInvalidate();
  xPagerLabel();
}

function xStats() {
  const r = xState.results[xState.page];
  const all = xState.summary;
  const pop = (id, text) => { const el = $(id); if (el.textContent !== text) { el.textContent = text; el.classList.remove("pop"); void el.offsetWidth; el.classList.add("pop"); } };
  if (all && all.pages.length > 1) {
    pop("xs-longest", all.longest ? `${all.longest.words} words` : "—");
    $("xs-longest-sub").textContent = all.longest ? `${pretty(all.longest.label)} · page ${all.longest.page} · ${all.longest.lines} lines` : "";
    pop("xs-beyond", String(all.beyond_cap));
    pop("xs-spans", String(all.spans));
    $("xs-spans-sub").textContent = `across ${all.pages.length} pages`;
    pop("xs-ms", `${all.model_ms.toFixed(0)} ms`);
    $("xs-ms-sub").textContent = `${(all.model_ms / all.pages.length).toFixed(0)} ms per page`;
    return;
  }
  if (!r?.spans) { ["xs-longest", "xs-beyond", "xs-spans", "xs-ms"].forEach((id) => { $(id).textContent = "—"; }); $("xs-longest-sub").textContent = "words"; return; }
  const longest = r.spans.reduce((a, s) => (!a || s.words > a.words ? s : a), null);
  pop("xs-longest", longest ? `${longest.words} words` : "—");
  $("xs-longest-sub").textContent = longest ? `${pretty(longest.label)} · ${longest.lines.length} lines` : "no spans";
  pop("xs-beyond", String(r.spans.filter((s) => s.legacy_cut).length));
  pop("xs-spans", String(r.spans.length));
  const unit = r.split === "rows" ? "table rows" : "blocks";
  $("xs-spans-sub").textContent = `${r.text_source === "ocr" ? "OCR · " : `page ${xState.page} · `}${r.words} words · ${r.blocks > 1 ? `${r.blocks} ${unit}` : "whole page"}`;
  pop("xs-ms", `${r.model_ms.toFixed(0)} ms`);
  $("xs-ms-sub").textContent = r.blocks > 1 ? `${r.blocks} ${unit} in one batch` : "one pass over the whole page";
}

function xRenderPage(animate = true) {
  const r = xState.results[xState.page];
  xPagerLabel();
  $("x-empty").hidden = Boolean(r?.image_key);
  $("x-page").hidden = !r?.image_key;
  if (r?.image_key && !$("x-page").src.endsWith(`${r.image_key}.jpg`)) $("x-page").src = `extract-pages/${r.image_key}.jpg`;
  $("x-viewer").style.aspectRatio = r?.image_size ? `${r.image_size[0]} / ${r.image_size[1]}` : "";
  if (!r) {
    $("x-empty").querySelector("h3").textContent = Object.keys(xState.results).length ? `Page ${xState.page} has not been extracted yet` : "Pick a page and extract it";
  }
  const spans = r?.spans || [];
  const order = spans.map((s, i) => i).sort((a, b) => spans[a].start - spans[b].start);
  const rank = new Map(order.map((i, k) => [i, k]));
  const step = Math.min(70, 2400 / Math.max(1, spans.length));
  const delay = (i, line) => (animate ? rank.get(i) * step + line * 45 : 0);
  const cap = $("x-cap").checked;
  const byLength = spans.map((s, i) => i).sort((a, b) => spans[b].words - spans[a].words);
  xState.longest = byLength.find((i) => spans[i].legacy_cut && !xState.hidden.has(spans[i].label)) ?? null;
  let html = (r?.block_marks || []).filter(Boolean).map(([x0, y0, , y1]) => `<div class="xmark" style="left:${pct(x0)};top:${pct(y0)};height:${pct(y1 - y0)}"></div>`).join("");
  for (const i of byLength) {
    const s = spans[i];
    const c = xColor(s.label);
    const off = xState.hidden.has(s.label) ? " off" : "";
    s.lines.forEach(([x0, y0, x1, y1], j) => {
      html += `<div class="xbox${s.words >= 8 ? " long" : ""}${off}" data-s="${i}" data-l="${escape(s.label)}" style="--c:${c};--d:${delay(i, j)}ms;left:${pct(x0 - 0.002)};top:${pct(y0 - 0.0015)};width:${pct(x1 - x0 + 0.004)};height:${pct(y1 - y0 + 0.003)}"></div>`;
    });
    if (s.words >= 8 && s.lines.length) {
      const [x0, y0] = s.lines[0];
      html += `<div class="xchip${off}" data-l="${escape(s.label)}" style="--c:${c};--d:${delay(i, 0)}ms;left:${pct(x0 - 0.002)};top:${pct(y0 - 0.002)}">${escape(pretty(s.label))}<b>${s.words}w</b></div>`;
    }
    if (cap && s.legacy_cut) {
      const [cx0, cy0, cx1, cy1] = s.legacy_cut;
      const show = i === xState.longest ? " show" : "";
      html += `<div class="xcut${off}${show}" data-s="${i}" data-l="${escape(s.label)}" style="left:${pct(cx1 + 0.002)};top:${pct(cy0 - 0.003)};height:${pct(cy1 - cy0 + 0.006)}"></div>`;
      for (const [x0, y0, x1, y1] of s.lines) {
        const sameLine = Math.min(y1, cy1) - Math.max(y0, cy0) > 0.3 * (y1 - y0);
        if (sameLine && x1 > cx1) html += `<div class="xhatch${off}${show}" data-s="${i}" data-l="${escape(s.label)}" style="left:${pct(cx1 + 0.002)};top:${pct(y0)};width:${pct(x1 - cx1)};height:${pct(y1 - y0)}"></div>`;
        else if (y0 > cy1 - 0.001) html += `<div class="xhatch${off}${show}" data-s="${i}" data-l="${escape(s.label)}" style="left:${pct(x0)};top:${pct(y0)};width:${pct(x1 - x0)};height:${pct(y1 - y0)}"></div>`;
      }
    }
  }
  $("x-boxes").innerHTML = html;
  $("x-boxes").classList.remove("focus");
  $("x-rail").innerHTML = spans.map((s, i) => {
    if (!s.lines.length || xState.hidden.has(s.label)) return "";
    const top = s.lines[0][1], bottom = s.lines[s.lines.length - 1][3];
    return `<i style="--c:${xColor(s.label)};--d:${delay(i, 0)}ms;top:${pct(top)};height:${pct(Math.max(0.004, bottom - top))}"></i>`;
  }).join("");
  const counts = {};
  spans.forEach((s) => { counts[s.label] = (counts[s.label] || 0) + 1; });
  $("x-legend").innerHTML = xState.rows.map((row) => `<button type="button" data-legend="${escape(row.label)}" class="${xState.hidden.has(row.label) ? "off" : ""}" style="--c:${xColor(row.label)}"><i></i>${escape(pretty(row.label))}<b>${counts[row.label] || 0}</b></button>`).join("");
  $("x-list").innerHTML = r?.note ? `<li class="x-item"><p>${escape(r.note)}</p></li>` : byLength.filter((i) => !xState.hidden.has(spans[i].label)).map((i, k) => {
    const s = spans[i];
    return `<li class="x-item" data-s="${i}" style="--c:${xColor(s.label)};--d:${animate ? Math.min(k * 30, 900) : 0}ms"><div class="x-item-head"><i></i><strong>${escape(pretty(s.label))}</strong>${s.score.toFixed(2)}<span class="len${s.legacy_cut ? " beyond" : ""}">${s.words} words · ${s.lines.length} line${s.lines.length === 1 ? "" : "s"}</span></div><p>${escape(s.text)}</p></li>`;
  }).join("") || (r ? '<li class="x-item"><p>No spans above the threshold on this page.</p></li>' : "");
  xStats();
  xRenderThumbs();
}

function xRenderThumbs() {
  const pages = Object.keys(xState.results).map(Number).filter((p) => xState.results[p].image_key).sort((a, b) => a - b);
  $("x-pages").hidden = pages.length < 2;
  $("x-pages").innerHTML = pages.map((p) => {
    const r = xState.results[p];
    return `<button type="button" class="x-thumb${p === xState.page ? " current" : ""}" data-page="${p}" aria-label="Show page ${p}"><img src="extract-pages/${r.image_key}.jpg" alt="" loading="lazy"><span><b>${p}</b>${r.spans ? r.spans.length : "…"}</span></button>`;
  }).join("");
}

function xFocus(i, on) {
  $("x-boxes").classList.toggle("focus", on);
  document.querySelectorAll("#x-boxes .xbox.on, #x-list .x-item.on").forEach((el) => el.classList.remove("on"));
  if (on) document.querySelectorAll(`#x-boxes .xbox[data-s="${i}"], #x-list .x-item[data-s="${i}"]`).forEach((el) => el.classList.add("on"));
  // The 8-word cap is drawn for one span at a time: the focused one, else the longest.
  const target = on && document.querySelector(`#x-boxes .xcut[data-s="${i}"]`) ? i : xState.longest;
  document.querySelectorAll("#x-boxes .xcut, #x-boxes .xhatch").forEach((el) => el.classList.toggle("show", String(el.dataset.s) === String(target)));
}

function consumeExtract(record) {
  xState.records.push(record);
  $("event-log").textContent = xState.records.map((item) => JSON.stringify(item, null, 2)).join("\n\n");
  if (record.event === "extraction_started") {
    status(`${record.model_name} is reading ${record.title}…`, "busy");
  } else if (record.event === "page_ready") {
    xState.pageCount = record.page_count;
    xState.results[record.page] = { ...record };
    xState.page = record.page;
    $("x-scan").hidden = false;
    xRenderPage(false);
    status(`Page ${record.page} of ${record.page_count} · ${record.words} words · finding spans…`, "busy");
  } else if (record.event === "page_extracted") {
    Object.assign(xState.results[record.page], { spans: record.spans, model_ms: record.model_ms, block_marks: record.block_marks, blocks: record.blocks, split: record.split, text_source: record.text_source });
    $("x-scan").hidden = true;
    if (record.page === xState.page) xRenderPage(true);
    status(`Page ${record.page} · ${record.spans.length} spans · longest ${record.longest_words} words · ${record.model_ms.toFixed(0)} ms`, "busy");
    const done = Object.values(xState.results).filter((r) => r.spans);
    $("run-count").textContent = `${done.reduce((n, r) => n + r.spans.length, 0)} spans · ${done.length} page${done.length === 1 ? "" : "s"}`;
    $("model-time").textContent = `Model time · ${done.reduce((n, r) => n + r.model_ms, 0).toFixed(0)} ms`;
  } else if (record.event === "completed") {
    finished = true;
    xState.summary = record;
    if (record.pages.length > 1 && record.longest) { xState.page = record.longest.page; xRenderPage(true); }
    else xStats();
    status(record.longest ? `Complete · ${record.spans} spans · longest ${record.longest.words} words (${pretty(record.longest.label)}, page ${record.longest.page})` : `Complete · ${record.spans} spans`);
  } else if (record.event === "run_failed") {
    fail(record.error);
  }
}

async function startExtract(mode) {
  if (running) return;
  const schema = xSchema();
  const bad = xState.rows.find((r) => !xLabelPattern.test(r.label.trim()) || !r.description.trim());
  if (!xState.rows.length || bad || Object.keys(schema).length !== xState.rows.length) {
    $("x-schema").open = true;
    fail(new Error(bad ? `Label "${bad.label}" needs a lowercase name (letters, digits, _) and a description.` : "Labels must be unique, and you need at least one."));
    return;
  }
  const input = { model: $("model").value, document: xState.upload ? "" : xState.docId, upload: xState.upload?.data || "", pages: mode, page: xState.page, schema: JSON.stringify(schema), threshold: Number($("x-threshold").value), segment: $("x-segment").checked };
  const thisRun = ++generation;
  runFailed = false;
  finished = false;
  $("error").hidden = true;
  xState.records = [];
  if (mode === "all") { xState.results = {}; xState.summary = null; }
  else xState.summary = null;
  setBusy(true);
  status("Connecting to the Gradio queue…", "busy");
  try {
    await getClient();
    if (thisRun !== generation) return;
    job = client.submit("/extract_document", input);
    let seen = 0;
    for await (const update of job) {
      if (thisRun !== generation) return;
      if (update.type === "status" && update.stage === "error") throw new Error(update.message || "The extraction failed.");
      if (update.type === "status" && update.stage === "pending") status("Queued. Waiting for the previous run to finish…", "busy");
      if (update.type !== "data") continue;
      const payload = update.data[0];
      if (!Array.isArray(payload?.events)) throw new Error("The server returned an invalid event stream.");
      for (const record of payload.events.slice(seen)) {
        if (thisRun !== generation) return;
        consumeExtract(record);
        seen++;
        if (record.event === "page_extracted" && mode === "all") await delay($("pace").value === "guided" ? 1100 : 120);
      }
    }
    if (!finished && !runFailed) throw new Error("The connection ended before the extraction finished. Try again.");
  } catch (error) {
    if (thisRun === generation) fail(error);
  } finally {
    if (thisRun === generation) { job = null; $("x-scan").hidden = true; setBusy(false); xRenderRows(); xPagerLabel(); }
  }
}

$("x-document").addEventListener("change", xSelectDocument);
$("x-prev").addEventListener("click", () => { if (xState.page > 1) { xState.page--; xRenderPage(true); } });
$("x-next").addEventListener("click", () => { if (!xState.pageCount || xState.page < xState.pageCount) { xState.page++; xRenderPage(true); } });
$("x-run").addEventListener("click", () => startExtract("one"));
$("x-run-all").addEventListener("click", () => startExtract("all"));
$("x-threshold").addEventListener("change", () => xInvalidate("Threshold changed. Extract again to apply it."));
$("x-cap").addEventListener("change", () => xRenderPage(false));
$("x-segment").addEventListener("change", () => xInvalidate($("x-segment").checked ? "Splitting at numbered items. Extract again to compare." : "Reading whole pages. Extract again to see spans run into the next item."));
$("x-labels").addEventListener("input", (event) => {
  const el = event.target.closest("[data-row]");
  if (!el) return;
  xState.rows[Number(el.dataset.row)][el.dataset.field] = el.value;
  if (el.dataset.field === "label") el.value = el.value.toLowerCase().replace(/[^a-z0-9_]/g, "_");
  xState.rows[Number(el.dataset.row)][el.dataset.field] = el.value;
  status("Labels edited. Extract again to see the new spans.");
});
$("x-labels").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (!button || running) return;
  xState.rows.splice(Number(button.dataset.remove), 1);
  xRenderRows();
  xRenderPage(false);
});
$("x-add").addEventListener("click", () => {
  if (xState.rows.length >= config.extract.max_labels) return;
  xState.rows.push({ label: `label_${xState.rows.length + 1}`, description: "describe what to extract, with a short example" });
  xRenderRows();
  $("x-labels").querySelector(`[data-row="${xState.rows.length - 1}"][data-field="label"]`)?.select();
});
$("x-reset").addEventListener("click", () => { xSetRows(xDoc()?.schema || {}); xRenderPage(false); status("Labels reset. Extract again to compare."); });
$("x-legend").addEventListener("click", (event) => {
  const button = event.target.closest("[data-legend]");
  if (!button) return;
  const label = button.dataset.legend;
  if (xState.hidden.has(label)) xState.hidden.delete(label); else xState.hidden.add(label);
  xRenderPage(false);
});
$("x-pages").addEventListener("click", (event) => {
  const thumb = event.target.closest("[data-page]");
  if (!thumb || running) return;
  xState.page = Number(thumb.dataset.page);
  xRenderPage(true);
});
for (const id of ["x-boxes", "x-list"]) {
  $(id).addEventListener("mouseover", (event) => { const el = event.target.closest("[data-s]"); if (el) xFocus(el.dataset.s, true); });
  $(id).addEventListener("mouseout", (event) => { if (!event.relatedTarget?.closest?.(`#${id} [data-s]`)) { xFocus(null, false); $("x-tip").hidden = true; } });
}
$("x-boxes").addEventListener("mousemove", (event) => {
  const el = event.target.closest(".xbox");
  const s = el && xState.results[xState.page]?.spans?.[Number(el.dataset.s)];
  if (!s) { $("x-tip").hidden = true; return; }
  $("x-tip").innerHTML = `<b style="color:${xColor(s.label)}">${escape(pretty(s.label))}</b> ${escape(s.text.length > 220 ? s.text.slice(0, 220) + "…" : s.text)}<span>confidence ${s.score.toFixed(2)} · ${s.words} words · ${s.lines.length} lines${s.legacy_cut ? " · past an 8-word cap" : ""}</span>`;
  $("x-tip").hidden = false;
  const x = Math.min(event.clientX + 14, innerWidth - 380), y = Math.min(event.clientY + 14, innerHeight - 140);
  Object.assign($("x-tip").style, { left: `${x}px`, top: `${y}px` });
});
$("x-list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-s]");
  if (!item) return;
  const box = $("x-boxes").querySelector(`.xbox[data-s="${item.dataset.s}"]`);
  box?.scrollIntoView({ behavior: "smooth", block: "center" });
  xFocus(item.dataset.s, true);
});
$("x-upload").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (!["application/pdf", "image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 25 * 1024 * 1024) { fail(new Error("Upload a PDF, or a JPEG, PNG or WebP scan, under 25 MB.")); return; }
  const reader = new FileReader();
  reader.onload = () => {
    xState.upload = { name: file.name, data: reader.result };
    xState.page = 1;
    xState.pageCount = null;
    if (!$("x-document").querySelector('option[value="upload"]')) $("x-document").insertAdjacentHTML("beforeend", '<option value="upload"></option>');
    $("x-document").querySelector('option[value="upload"]').textContent = `Your file · ${file.name}`;
    $("x-document").value = "upload";
    $("x-source").textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB · read on this machine${file.type.startsWith("image/") ? " with OCR" : ". Scanned PDFs need a text layer; upload a scan as an image"}.`;
    $("error").hidden = true;
    xInvalidate("PDF loaded. Edit the labels for your document, then extract a page.");
  };
  reader.readAsDataURL(file);
});

// ---- Lab report reader -----------------------------------------------------
const labColors = { title: "#7c3aed", test: "#2563eb", result: "#16a34a", slot: "#d97706", method: "#64748b", section: "#db2777" };
const bandColors = { test: "#2563eb", result: "#16a34a", unit: "#d97706", range: "#ea580c" };
const labState = { docId: "", upload: null, size: null, key: null, records: [], summary: null, scored: null, events: [] };
const labDoc = () => config.lab.documents.find((d) => d.id === labState.docId);
const box = ([x0, y0, x1, y1], pad = 0.002) => `left:${pct(x0 - pad)};top:${pct(y0 - pad)};width:${pct(x1 - x0 + 2 * pad)};height:${pct(y1 - y0 + 2 * pad)}`;
const fmtNum = (v) => (Number.isInteger(v) ? String(v) : String(+v.toFixed(3)));

function labStage(n) {
  document.querySelectorAll("[data-lstage]").forEach((el) => {
    const k = Number(el.dataset.lstage);
    el.classList.toggle("active", k === n);
    el.classList.toggle("done", k < n);
  });
}

function labReset(keepDoc = true) {
  Object.assign(labState, { size: null, key: null, records: [], summary: null, scored: null, events: [] });
  ["lab-bands", "lab-boxes"].forEach((id) => { $(id).innerHTML = ""; });
  $("lab-links").innerHTML = "";
  $("lab-page").hidden = true;
  $("lab-empty").hidden = false;
  $("lab-scan").hidden = true;
  $("lab-rows").innerHTML = '<tr><td colspan="6" class="lab-wait">Results appear here.</td></tr>';
  ["ls-tests", "ls-flags", "ls-agree", "ls-key", "ls-ms"].forEach((id) => { $(id).textContent = "—"; });
  $("ls-key-sub").textContent = labState.upload ? "no answer key for uploads" : "sample reports only";
  labStage(0);
  if (!keepDoc) labState.docId = "";
}

function labSelect() {
  const value = $("lab-document").value;
  if (value === "upload") return;
  labState.docId = value;
  labState.upload = null;
  $("lab-upload").value = "";
  const doc = labDoc();
  $("lab-source").textContent = `${doc.subtitle}${doc.kind === "real" ? "" : " · answer key from construction"}`;
  labReset();
}

function labPage(record) {
  labState.size = record.image_size;
  labState.key = record.image_key;
  $("lab-viewer").style.aspectRatio = `${record.image_size[0]} / ${record.image_size[1]}`;
  $("lab-page").src = `extract-pages/${record.image_key}.jpg`;
  $("lab-page").hidden = false;
  $("lab-empty").hidden = true;
  $("lab-links").setAttribute("viewBox", `0 0 ${record.image_size[0]} ${record.image_size[1]}`);
}

function labOcr(cells) {
  const step = Math.min(8, 900 / Math.max(1, cells.length));
  $("lab-boxes").innerHTML = cells.map((c, i) => `<div class="lbox ocr" style="--d:${(i * step).toFixed(0)}ms;${box(c.box)}"></div>`).join("");
}

function labBands(headers) {
  const kinds = Object.keys(headers).sort((a, b) => (headers[a].box[0] + headers[a].box[2]) - (headers[b].box[0] + headers[b].box[2]));
  const centres = kinds.map((k) => (headers[k].box[0] + headers[k].box[2]) / 2);
  const top = Math.min(...kinds.map((k) => headers[k].box[1])) - 0.004;
  $("lab-bands").innerHTML = kinds.map((k, i) => {
    const left = i ? (centres[i - 1] + centres[i]) / 2 : 0.01;
    const right = i < kinds.length - 1 ? (centres[i] + centres[i + 1]) / 2 : 0.99;
    return `<div class="lband" style="--c:${bandColors[k] || "#64748b"};left:${pct(left)};width:${pct(right - left)};top:${pct(top)}"><span>${escape(k)}</span></div>`;
  }).join("") + kinds.map((k) => `<div class="lbox" style="--c:${labColors.title};${box(headers[k].box)}"></div>`).join("");
}

function labRoleColor(cell) {
  if (cell.column === "test") return { test_name: labColors.test, method: labColors.method, section: labColors.section }[cell.role] || null;
  if (cell.column === "result") return labColors.result;
  return labColors.slot;
}

function labDraw(result) {
  const [w, h] = labState.size;
  $("lab-boxes").innerHTML = result.cells.map((c, i) => {
    const colour = labRoleColor(c);
    return colour ? `<div class="lbox" style="--c:${colour};--d:${Math.min(i * 6, 700)}ms;${box(c.box)}"></div>`
      : `<div class="lbox ignored" style="${box(c.box)}"></div>`;
  }).join("");
  const mid = (b, side) => [((side === "right" ? b[2] : b[0]) * w), ((b[1] + b[3]) / 2) * h];
  const curve = ([x1, y1], [x2, y2]) => `M${x1.toFixed(1)},${y1.toFixed(1)} C${((x1 + x2) / 2).toFixed(1)},${y1.toFixed(1)} ${((x1 + x2) / 2).toFixed(1)},${y2.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
  $("lab-links").innerHTML = result.records.map((r, i) => {
    if (!r.result_boxes) return "";
    const d = `${(i * 70).toFixed(0)}ms`;
    let paths = `<path class="result" data-r="${i}" pathLength="1" style="--len:1;--d:${d}" d="${curve(mid(r.test_box, "right"), mid(r.result_boxes[0], "left"))}"/>`;
    if (r.slot_boxes?.length) paths += `<path class="slot" data-r="${i}" style="--d:${d}" d="${curve(mid(r.result_boxes[0], "right"), mid(r.slot_boxes[0], "left"))}"/>`;
    return paths;
  }).join("");
  // Flag rings around out-of-range results.
  $("lab-boxes").insertAdjacentHTML("beforeend", result.records.filter((r) => r.flag === "low" || r.flag === "high")
    .map((r) => `<div class="lbox flag" style="--c:#dc2626;${box(r.result_boxes[0], 0.004)}"></div>`).join(""));
}

function labTable() {
  const records = labState.records;
  if (!records.length) { $("lab-rows").innerHTML = '<tr><td colspan="6" class="lab-wait">No results table was found.</td></tr>'; return; }
  let section = null;
  $("lab-rows").innerHTML = records.map((r, i) => {
    let head = "";
    if (r.section !== section) { section = r.section; head = `<tr class="sec"><td colspan="6">${escape(section || "Results")}</td></tr>`; }
    const check = r.check;
    const key = !check ? '<span class="lab-na">·</span>'
      : Object.entries(check).filter(([k]) => k !== "expected").every(([, ok]) => ok) ? '<span class="lab-ok" title="Matches the answer key">✓</span>'
      : `<span class="lab-miss" title="Expected ${escape(JSON.stringify(check.expected))}">✗</span>`;
    if ("finding" in r) {
      return `${head}<tr class="rec" data-r="${i}" style="--d:${i * 25}ms"><td class="test">${escape(r.test)}</td><td colspan="3">${escape(r.finding)}</td><td class="mark"><span class="lab-na">·</span></td><td class="mark">${key}</td></tr>`;
    }
    const flag = r.flag === "high" ? '<span class="lab-flag high">↑ HIGH</span>' : r.flag === "low" ? '<span class="lab-flag low">↓ LOW</span>' : "";
    const lab = r.printed_box ? '<span class="lab-ok" title="The lab printed this value in a shaded box">▣</span>'
      : r.printed_flag ? `<span class="lab-ok" title="The lab printed a ${escape(r.printed_flag)} marker">${escape(r.printed_flag)}</span>` : '<span class="lab-na" title="No box or marker from the lab">·</span>';
    const bounds = r.low != null && r.high != null ? `${fmtNum(r.low)} – ${fmtNum(r.high)}` : r.high != null ? `&lt; ${fmtNum(r.high)}` : r.low != null ? `&gt; ${fmtNum(r.low)}` : null;
    const range = bounds ? `${bounds}${r.qualifier ? ` <small>(${escape(r.qualifier)})</small>` : ""}` : '<span class="lab-na">none printed</span>';
    const unit = r.unit ? `${escape(r.unit)}${r.unit_inferred ? ' <small class="lab-na" title="Not printed on this line: the unit printed above, confirmed by d1">↑</small>' : ""}`
      : r.unit === "" ? '<span class="lab-na" title="d1: a ratio or index, no unit">none</span>' : '<span class="lab-na">—</span>';
    return `${head}<tr class="rec" data-r="${i}" style="--d:${i * 25}ms"><td class="test">${escape(r.test)}</td><td class="val">${fmtNum(r.value)}${flag}</td><td>${unit}</td><td>${range}</td><td class="mark">${lab}</td><td class="mark">${key}</td></tr>`;
  }).join("");
}

function labStats() {
  const s = labState.summary;
  if (!s) return;
  const pop = (id, text) => { const el = $(id); el.textContent = text; el.classList.remove("pop"); void el.offsetWidth; el.classList.add("pop"); };
  pop("ls-tests", String(s.tests));
  $("ls-tests-sub").textContent = `${s.findings ? `plus ${s.findings} written findings · ` : ""}values, units and ranges`;
  pop("ls-flags", String(s.out_of_range));
  $("ls-flags-sub").textContent = s.out_of_range ? labState.records.filter((r) => r.flag === "low" || r.flag === "high").map((r) => r.test).slice(0, 3).join(", ") : "everything within range";
  pop("ls-agree", s.compared ? `${s.agree}/${s.compared}` : "none");
  $("ls-agree-sub").textContent = s.compared ? `our flags vs the ${s.lab_boxed} values the lab boxed or starred` : "no boxes or * marks to compare (bold is not detected)";
  pop("ls-ms", `${((s.ocr_ms + s.model_ms) / 1000).toFixed(1)} s`);
  $("ls-ms-sub").textContent = `OCR ${(s.ocr_ms / 1000).toFixed(1)} s · d1 ${(s.model_ms / 1000).toFixed(1)} s`;
  const stages = Object.entries(s.stage_ms).map(([k, v]) => `<b>${escape(pretty(k))}</b> ${v.toFixed(0)} ms`).join(" · ");
  $("lab-how").innerHTML = `<ol><li><b>OCR</b> (docTR) finds each cell and its box.</li><li><b>d1</b> decides which title names each column, then whether each line in the test column is a test, a method note or a section heading (it sees the crop, so bold underlined headings count).</li><li><b>Code</b> pairs each test with the number on its line, then lines up units and ranges in order, because many reports print them a line low.</li><li><b>d1</b> maps each printed unit to a standard one, and its <b>vision</b> checks whether the lab boxed the value.</li><li><b>Code</b> flags values outside their range and compares with the lab's marks.</li></ol><p>${stages}</p>`;
}

function labFocus(i, on) {
  $("lab-links").classList.toggle("focus", on);
  $("lab-boxes").classList.toggle("focus", on);
  document.querySelectorAll("#lab-links path.on, #lab-rows tr.on").forEach((el) => el.classList.remove("on"));
  document.querySelectorAll("#lab-boxes .lbox.hl").forEach((el) => el.remove());
  if (!on) return;
  document.querySelectorAll(`#lab-links path[data-r="${i}"], #lab-rows tr[data-r="${i}"]`).forEach((el) => el.classList.add("on"));
  const r = labState.records[i];
  const boxes = [[r.test_box, labColors.test], ...(r.result_boxes || []).map((b) => [b, labColors.result]), ...(r.slot_boxes || []).map((b) => [b, labColors.slot])];
  $("lab-boxes").insertAdjacentHTML("beforeend", boxes.map(([b, c]) => `<div class="lbox hl on" style="--c:${c};${box(b, 0.004)}"></div>`).join(""));
}

function consumeLab(record) {
  labState.events.push(record);
  $("event-log").textContent = labState.events.map((item) => JSON.stringify(item, null, 2)).join("\n\n");
  const ev = record.event;
  if (ev === "report_received") { status(`Reading ${record.title} with ${record.model === "3b" ? "d1-3B" : "d1-omni-600M"}…`, "busy"); $("lab-scan").hidden = false; }
  else if (ev === "ocr_done") { labPage(record); labOcr(record.cells); labStage(1); status(`OCR found ${record.cells.length} cells in ${(record.ocr_ms / 1000).toFixed(1)} s`, "busy"); }
  else if (ev === "columns_found") { labBands(record.headers); labStage(2); status(`d1 found the columns: ${Object.values(record.headers).map((h) => `“${h.text}”`).join(", ")}`, "busy"); }
  else if (ev === "names_read") { labStage(3); status(`Test column read: ${record.roles.test_name} tests, ${record.roles.method} method notes, ${record.roles.section} section headings`, "busy"); }
  else if (ev === "table_found") { labStage(3); }
  else if (ev === "records_paired") { labStage(4); status(`Paired ${record.records} results; ${record.with_range} have a reference range`, "busy"); }
  else if (ev === "units_read") { labStage(5); status(`Units: ${record.units.join(", ")}`, "busy"); }
  else if (ev === "report_read") {
    $("lab-scan").hidden = true;
    labStage(6);
    labState.records = record.records;
    labState.summary = record.summary;
    labDraw(record);
    labTable();
    labStats();
    $("run-count").textContent = `${record.summary.tests} tests read`;
    $("model-time").textContent = `Model time · ${(record.summary.model_ms / 1000).toFixed(1)} s`;
    status(`Read ${record.summary.tests} tests · ${record.summary.out_of_range} out of range · lab's marks agree on ${record.summary.agree}/${record.summary.compared}`, "busy");
  } else if (ev === "report_scored") {
    labState.records = record.records;
    labTable();
    const t = record.totals;
    const fields = ["value", "unit", "range", "flag"];
    const right = fields.reduce((n, k) => n + t[k][0], 0), all = fields.reduce((n, k) => n + t[k][1], 0);
    $("ls-key").textContent = `${right}/${all}`;
    $("ls-key-sub").textContent = `fields right · ${t.found[0]}/${t.found[1]} tests found${t.findings[1] ? ` · findings ${t.findings[0]}/${t.findings[1]}` : ""}`;
  } else if (ev === "run_finished") {
    finished = true;
    status(`Complete · ${labState.summary ? `${labState.summary.tests} tests read` : "done"}`);
  } else if (ev === "run_failed") {
    $("lab-scan").hidden = true;
    fail(record.error);
  }
}

async function startLab() {
  if (running) return;
  const input = { model: $("model").value, document: labState.upload ? "" : labState.docId, upload: labState.upload || "" };
  const thisRun = ++generation;
  runFailed = false;
  finished = false;
  $("error").hidden = true;
  labReset();
  setBusy(true);
  status("Connecting to the Gradio queue…", "busy");
  try {
    await getClient();
    if (thisRun !== generation) return;
    job = client.submit("/read_lab_report", input);
    let seen = 0;
    for await (const update of job) {
      if (thisRun !== generation) return;
      if (update.type === "status" && update.stage === "error") throw new Error(update.message || "Reading the report failed.");
      if (update.type === "status" && update.stage === "pending") status("Queued. Waiting for the previous run to finish…", "busy");
      if (update.type !== "data") continue;
      const payload = update.data[0];
      if (!Array.isArray(payload?.events)) throw new Error("The server returned an invalid event stream.");
      for (const record of payload.events.slice(seen)) {
        if (thisRun !== generation) return;
        consumeLab(record);
        seen++;
        if ($("pace").value === "guided" && ["ocr_done", "columns_found", "names_read", "records_paired", "units_read"].includes(record.event)) await delay(650);
      }
    }
    if (!finished && !runFailed) throw new Error("The connection ended before the report was read. Try again.");
  } catch (error) {
    if (thisRun === generation) fail(error);
  } finally {
    if (thisRun === generation) { job = null; $("lab-scan").hidden = true; setBusy(false); }
  }
}

$("lab-document").addEventListener("change", labSelect);
$("lab-run").addEventListener("click", startLab);
$("lab-rows").addEventListener("mouseover", (event) => { const row = event.target.closest("tr[data-r]"); if (row) labFocus(row.dataset.r, true); });
$("lab-rows").addEventListener("mouseleave", () => labFocus(null, false));
$("lab-upload").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 25 * 1024 * 1024) { fail(new Error("Upload a JPEG, PNG or WebP photo of the report, under 25 MB.")); return; }
  const reader = new FileReader();
  reader.onload = () => {
    labState.upload = reader.result;
    if (!$("lab-document").querySelector('option[value="upload"]')) $("lab-document").insertAdjacentHTML("beforeend", '<option value="upload"></option>');
    $("lab-document").querySelector('option[value="upload"]').textContent = `Your report · ${file.name}`;
    $("lab-document").value = "upload";
    $("lab-source").textContent = `${file.name} · read on this machine · no answer key, so results are not scored`;
    $("error").hidden = true;
    labReset();
    status("Report loaded. Read it to see the results.");
  };
  reader.readAsDataURL(file);
});

$("request-form").addEventListener("submit", (event) => { event.preventDefault(); startRun("single"); });
$("inbox").addEventListener("click", () => startRun("inbox"));
$("run-browser").addEventListener("click", () => startRun("single"));
$("sample").addEventListener("change", selectSample);
$("model").addEventListener("change", () => { resetRun(); if (isStamp()) resetStamp(); if (isExtract()) xInvalidate(); if (isLab()) labReset(); updateModel(); status("Model changed. Ready for a new run."); });
$("message").addEventListener("input", updateMessageCount);
for (const id of ["subject", "message"]) {
  $(id).addEventListener("input", () => {
    if (running) return;
    $("sample").value = "custom";
    if (currentStage || records.length) resetRun();
    status("Draft edited. Send it to get new predictions.");
  });
}
$("workflows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-workflow]");
  if (button) selectWorkflow(button.dataset.workflow);
});
window.addEventListener("hashchange", () => { if (config) selectWorkflow(location.hash.slice(1)); });
async function stopRun() {
  generation++;
  const activeJob = job;
  job = null;
  stampBatch = false;
  ["doc-scan", "crop-scan", "x-scan", "lab-scan"].forEach((id) => { $(id).hidden = true; });
  status("Playback stopped. Any active call will finish before the next run.");
  setBusy(false);
  try { await activeJob?.cancel(); } catch { /* The connection may already be closed. */ }
}
$("stop").addEventListener("click", stopRun);
$("ledger-rows").addEventListener("click", (event) => {
  if (running) return;
  const button = event.target.closest("[data-index]");
  if (!button) return;
  const entry = ledger[Number(button.dataset.index)];
  currentTicket = entry.ticket;
  predictions = entry.predictions;
  route = entry.output;
  modelMs = entry.modelMs;
  $("subject").value = currentTicket.subject;
  $("message").value = currentTicket.message;
  $("request-id").textContent = currentTicket.id;
  const index = config.samples[workflow].findIndex((ticket) => ticket.id === currentTicket.id);
  $("sample").value = index >= 0 ? String(index) : "custom";
  updateMessageCount();
  stage(4);
  renderPredictions();
  renderRules();
  renderOutput(entry.output);
  renderLedger(entry.output.ticket_id);
  status(`Inspecting ${currentTicket.id} · saved result from this run`);
});
$("download").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify({ workflow, model: $("model").value, tickets: ledger, events: records }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${workflow}-routing.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

try {
  const response = await fetch("demo-config");
  if (!response.ok) throw new Error("Could not load the demo configuration. Reload the page to try again.");
  config = await response.json();
  const group = (name, entries) => `<div class="nav-group">${name}</div>` + entries.map((item) => `<button class="nav-button" data-workflow="${escape(item.id)}"><span class="nav-icon">${item.id === "lab" ? "⚕" : item.id === "extract" ? "¶" : item.id === "stamp" ? "◎" : ["clinic", "helpdesk"].includes(item.id) ? "↗" : "▣"}</span>${escape(item.name)}</button>`).join("");
  const simulated = ["clinic", "helpdesk"];
  $("workflows").innerHTML = group("Request simulations", config.workflows.filter((item) => simulated.includes(item.id)))
    + group("Live browser", config.workflows.filter((item) => !simulated.includes(item.id) && !["stamp", "extract", "lab"].includes(item.id)))
    + group("Document vision", config.workflows.filter((item) => ["stamp", "extract", "lab"].includes(item.id)));
  $("lab-document").innerHTML = config.lab.documents.map((d) => `<option value="${escape(d.id)}">${escape(d.title)}</option>`).join("");
  $("x-document").innerHTML = config.extract.documents.map((d) => `<option value="${escape(d.id)}">${escape(d.title)} · ${d.pages} page${d.pages === 1 ? "" : "s"}</option>`).join("");
  $("stamp-sample").innerHTML = config.stamp.samples.map((s) => `<option value="${escape(s.id)}">${escape(s.title)} · ${escape(s.capture)}</option>`).join("") + '<option value="" hidden>Your upload</option>';
  fillModels();
  const requested = location.hash.slice(1);
  selectWorkflow(config.workflows.some((item) => item.id === requested) ? requested : "clinic");
  setBusy(false);
} catch (error) { fail(error); }
