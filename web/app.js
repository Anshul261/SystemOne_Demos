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
  document.querySelectorAll(".nav-button, #model, #sample, #send, #inbox, #run-browser").forEach((el) => { el.disabled = value; });
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

function updateModel() {
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
    if (!client) {
      const { Client } = await import("https://cdn.jsdelivr.net/npm/@gradio/client@2.7.1/dist/index.min.js");
      client = await Client.connect(new URL("./", location.href).href, { events: ["data", "status"] });
    }
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

$("request-form").addEventListener("submit", (event) => { event.preventDefault(); startRun("single"); });
$("inbox").addEventListener("click", () => startRun("inbox"));
$("run-browser").addEventListener("click", () => startRun("single"));
$("sample").addEventListener("change", selectSample);
$("model").addEventListener("change", () => { resetRun(); updateModel(); status("Model changed. Ready for a new run."); });
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
  const group = (name, entries) => `<div class="nav-group">${name}</div>` + entries.map((item) => `<button class="nav-button" data-workflow="${escape(item.id)}"><span class="nav-icon">${["clinic", "helpdesk"].includes(item.id) ? "↗" : "▣"}</span>${escape(item.name)}</button>`).join("");
  $("workflows").innerHTML = group("Request simulations", config.workflows.filter((item) => ["clinic", "helpdesk"].includes(item.id))) + group("Live browser", config.workflows.filter((item) => !["clinic", "helpdesk"].includes(item.id)));
  $("model").innerHTML = config.models.map((model) => `<option value="${escape(model)}">${model === "gliner" ? "GLiNER2.5 Decide" : "JPT 0.8B"}</option>`).join("");
  const requested = location.hash.slice(1);
  selectWorkflow(config.workflows.some((item) => item.id === requested) ? requested : "clinic");
  setBusy(false);
} catch (error) { fail(error); }
