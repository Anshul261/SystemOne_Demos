const $ = (id) => document.getElementById(id);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const pretty = (value) => String(value ?? "").replaceAll("_", " ");
const modelName = () => $("model").value === "jpt" ? "JPT" : "GLiNER";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const titles = { request: "Request type", queue: "Team", priority: "Priority", needs_human: "Staff review", topics: "Topics" };
const decisionEvents = ["business_decision", "field_classification", "browser_action"];
let config, client, workflow = "clinic", running = false, job, generation = 0;
let records = [], ledger = [], predictions = {}, currentTicket, route, modelMs = null;
let runFailed = false, finished = false, currentStage = 0;

function isSimulation() { return workflow === "clinic" || workflow === "helpdesk"; }
function isStamp() { return workflow === "stamp"; }

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
  document.querySelectorAll(".nav-button, #model, #sample, #send, #inbox, #run-browser, #stamp-sample, #stamp-batch, #stamp-upload").forEach((el) => { el.disabled = value; });
  $("stamp-check").disabled = value || (!uploadData && !$("stamp-sample").value);
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
  const options = isStamp()
    ? config.stamp.models.map((m) => [m.id, m.name])
    : config.models.map((m) => [m, m === "gliner" ? "GLiNER2.5 Decide" : "JPT 0.8B"]);
  if ([...$("model").options].map((o) => o.value).join() === options.map(([id]) => id).join()) return;
  $("model").innerHTML = options.map(([id, name]) => `<option value="${escape(id)}">${escape(name)}</option>`).join("");
}

function updateModel() {
  if (isStamp()) { renderReliability(); updateVisionTitle(); return; }
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

function consumeStamp(record, images) {
  stampRecords.push(record);
  $("event-log").textContent = stampRecords.map((item) => JSON.stringify(item, null, 2)).join("\n\n");
  const imagesFor = (id) => (images?.doc_id === id ? images : {});
  if (record.event === "check_started") {
    status(`Checking ${record.total} document${record.total === 1 ? "" : "s"} with ${record.model_name}…`, "busy");
  } else if (record.event === "document_received") {
    stampDoc = { ...record, verdict: null };
    stampChecks = {};
    stampCalls = [];
    const page = imagesFor(record.doc_id).page;
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
    const crop = imagesFor(record.doc_id).region;
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
        consumeStamp(record, payload.doc_images);
        seen++;
        if ($("pace").value === "guided" && stampPause[record.event]) await delay(stampPause[record.event]);
      }
    }
    if (!finished && !runFailed) throw new Error("The connection ended before the check finished. Try again.");
  } catch (error) {
    if (thisRun === generation) fail(error);
  } finally {
    if (thisRun === generation) { job = null; $("doc-scan").hidden = true; $("crop-scan").hidden = true; setBusy(false); }
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
$("stamp-batch").addEventListener("click", () => startStamp("batch"));
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

$("request-form").addEventListener("submit", (event) => { event.preventDefault(); startRun("single"); });
$("inbox").addEventListener("click", () => startRun("inbox"));
$("run-browser").addEventListener("click", () => startRun("single"));
$("sample").addEventListener("change", selectSample);
$("model").addEventListener("change", () => { resetRun(); if (isStamp()) resetStamp(); updateModel(); status("Model changed. Ready for a new run."); });
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
$("stop").addEventListener("click", async () => {
  generation++;
  const activeJob = job;
  job = null;
  status("Playback stopped. Any active call will finish before the next run.");
  setBusy(false);
  try { await activeJob?.cancel(); } catch { /* The connection may already be closed. */ }
});
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
  const group = (name, entries) => `<div class="nav-group">${name}</div>` + entries.map((item) => `<button class="nav-button" data-workflow="${escape(item.id)}"><span class="nav-icon">${item.id === "stamp" ? "◎" : ["clinic", "helpdesk"].includes(item.id) ? "↗" : "▣"}</span>${escape(item.name)}</button>`).join("");
  const simulated = ["clinic", "helpdesk"];
  $("workflows").innerHTML = group("Request simulations", config.workflows.filter((item) => simulated.includes(item.id)))
    + group("Live browser", config.workflows.filter((item) => !simulated.includes(item.id) && item.id !== "stamp"))
    + group("Document vision", config.workflows.filter((item) => item.id === "stamp"));
  $("stamp-sample").innerHTML = config.stamp.samples.map((s) => `<option value="${escape(s.id)}">${escape(s.title)} · ${escape(s.capture)}</option>`).join("") + '<option value="" hidden>Your upload</option>';
  fillModels();
  const requested = location.hash.slice(1);
  selectWorkflow(config.workflows.some((item) => item.id === requested) ? requested : "clinic");
  setBusy(false);
} catch (error) { fail(error); }
