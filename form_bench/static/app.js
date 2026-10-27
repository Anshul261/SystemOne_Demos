// Chart entry console: extract values from a note, review them beside their evidence, watch them being
// entered into the EHR, then save. Each step stays locked until the one before it is finished.

const $ = (id) => document.getElementById(id);
const state = { session: null, rows: [], ehr: "", stage: "start", entered: new Map(), marks: new Map() };

// "Weight (LOINC:29463-7) lbs (Value) - Decimal pounds…" reads as "Weight lbs"; "… (Abn)" keeps its column.
const fieldName = (label) => label.replace(/\s*\(LOINC:[^)]*\)/, "").replace(/ \(Value\)/, "")
  .replace(/ - Decimal pounds.*$/, "").replace(/\s*\*.*$/, "").trim();
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const openFlags = () => state.rows.filter((row) => row.flag && !row.resolved).length;

async function api(path, body) {
  const response = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) } : undefined);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `The server answered ${response.status}.`);
  return data;
}

// Read a streamed answer: one JSON event per line.
async function stream(path, body, onEvent) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `The server answered ${response.status}.`);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines.filter(Boolean)) {
      const event = JSON.parse(line);
      if (event.step === "error") throw new Error(event.message);
      onEvent(event);
    }
  }
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

async function loadTargets() {
  const { targets, system_one, llm } = await api("/api/targets");
  const model = { "qwen-3.8-27b": "Qwen3.8-27B", "gpt-oss-120b": "gpt-oss-120b" }[llm] || llm;
  const checker = system_one === "jev" ? "Jev" : "d1";
  $("engine").textContent = `${model} pulls each value from the note and ${checker} checks it before you see it.`;
  for (const target of targets) {
    const group = document.createElement("optgroup");
    group.label = target.name.replace(/^the /, "").replace(/^./, (c) => c.toUpperCase());
    group.dataset.ehr = target.name;
    for (const screen of target.screens) group.append(new Option(screen.title, `${target.id}:${screen.index}`));
    $("screen").append(group);
  }
}

// Mark the first whole-word match of each value in the note, so a reviewer can see where it came from.
function renderNote(note) {
  const found = [];
  for (const row of state.rows) {
    if (!row.value) continue;
    const match = new RegExp(`(?<![\\w])${escape(row.value)}(?![\\w])`, "i").exec(note);
    if (!match) continue;
    const end = match.index + match[0].length;
    const same = found.find((f) => f.start === match.index && f.end === end);
    if (same) same.rows.push(row);  // two fields with one value share a mark (First Name, Birth First Name)
    else if (!found.some((f) => match.index < f.end && f.start < end)) found.push({ start: match.index, end, rows: [row] });
  }
  found.sort((a, b) => a.start - b.start);
  const view = $("note-view");
  view.replaceChildren();
  state.marks.clear();
  let at = 0;
  for (const { start, end, rows } of found) {
    view.append(note.slice(at, start));
    const mark = document.createElement("mark");
    mark.textContent = note.slice(start, end);
    mark.classList.toggle("flagged", rows.some((row) => row.flag && !row.resolved));
    view.append(mark);
    for (const row of rows) state.marks.set(row.index, mark);
    at = end;
  }
  view.append(note.slice(at));
}

function button(text, onClick) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = text;
  element.addEventListener("click", onClick);
  return element;
}

function rowElement(row) {
  const li = document.createElement("li");
  const entered = state.entered.get(row.index);
  li.classList.toggle("flagged", Boolean(row.flag));
  li.classList.toggle("resolved", row.resolved);
  li.classList.toggle("entered", entered === "kept");
  li.classList.toggle("refused", entered === "refused");

  const name = document.createElement("label");
  name.className = "field-name";
  name.textContent = fieldName(row.label);
  name.title = row.label;
  name.htmlFor = `field-${row.index}`;

  let control;
  if (row.kind === "choice") {
    control = document.createElement("select");
    for (const option of row.options) control.append(new Option(option || "(blank)", option));
  } else {
    control = document.createElement("input");
  }
  control.id = `field-${row.index}`;
  control.value = row.value;
  control.disabled = state.stage === "entering" || state.stage === "saved";

  const status = document.createElement("div");
  status.className = "status";
  const message = document.createElement("span");
  const actions = document.createElement("span");
  actions.className = "actions";
  if (row.flag && !row.resolved) {
    message.textContent = row.flag;
    actions.append(button("Keep this value", () => keep(row)));
  } else if (entered) {
    message.textContent = entered === "kept" ? "Entered and read back" : "The page refused this value";
  } else if (row.resolved) {
    message.textContent = "Reviewed by you";
  } else {
    message.textContent = row.confidence == null ? "Checked" : `Jev is ${Math.round(row.confidence * 100)}% sure`;
    message.title = "How sure the decision model is that the note gives this value for this field.";
  }
  status.append(message);
  if (row.value && !state.marks.has(row.index) && !row.resolved) {
    const hint = document.createElement("span");
    hint.className = "no-evidence";
    hint.textContent = "Not word for word in the note";
    status.append(hint);
  }
  status.append(actions);

  const useMine = button("Use my value", () => correct(row, control.value));
  const offer = () => { if (!useMine.isConnected) actions.prepend(useMine); };
  control.addEventListener("input", offer);
  control.addEventListener("change", offer);

  const light = (on) => state.marks.get(row.index)?.classList.toggle("lit", on);
  li.addEventListener("mouseenter", () => light(true));
  li.addEventListener("mouseleave", () => light(false));
  li.addEventListener("focusin", () => light(true));
  li.addEventListener("focusout", () => light(false));

  li.append(name, control, status);
  return li;
}

function setStage(stage) {
  state.stage = stage;
  const order = ["extract", "review", "enter", "save"];
  const current = { start: -1, extracting: 0, review: 1, entering: 2, entered: 3, saved: 4 }[stage];
  for (const item of $("stages").children) {
    const at = order.indexOf(item.dataset.stage);
    item.classList.toggle("done", at < current);
    item.classList.toggle("current", at === current);
  }
}

function took(stage, ms) {
  const item = $("stages").querySelector(`[data-stage="${stage}"]`);
  item.querySelector(".took")?.remove();
  const span = document.createElement("span");
  span.className = "took";
  span.textContent = `${(ms / 1000).toFixed(1)} s`;
  item.append(span);
}

function render() {
  renderNote($("note").value);
  $("rows").replaceChildren(...state.rows.map(rowElement));
  $("empty").hidden = state.rows.length > 0;
  const open = openFlags();
  $("tally").innerHTML = !state.rows.length ? ""
    : open ? `<strong>${open} to review</strong> of ${state.rows.length} values` : `${state.rows.length} values, all checked`;
  const action = $("action");
  const ehr = state.ehr || "the EHR";
  if (state.stage === "review") {
    action.textContent = `Enter into ${ehr}`;
    action.disabled = open > 0;
    $("status").textContent = open ? `Review the ${open} flagged value${open === 1 ? "" : "s"} to unlock entry.`
      : `Every value is checked. Entering types them into ${ehr}; nothing is saved yet.`;
  } else if (state.stage === "entered") {
    action.textContent = `Save to ${ehr}`;
    action.disabled = open > 0;
    $("status").textContent = open ? `The page refused ${open} value${open === 1 ? "" : "s"}. Fix or keep them to unlock saving.`
      : `Check the entry above, then save it to ${ehr}.`;
  } else if (state.stage === "entering") {
    action.disabled = true;
    $("status").textContent = `Entering into ${ehr}…`;
  } else if (state.stage !== "saved") {
    action.textContent = "Enter";
    action.disabled = true;
    $("status").textContent = state.stage === "extracting" ? "Reading the note…" : "Paste a note to begin.";
  }
}

async function keep(row) {
  try {
    const { row: updated } = await api("/api/confirm", { session: state.session, index: row.index });
    replaceRow(updated);
  } catch (error) { showError(error.message); }
}

async function correct(row, value) {
  try {
    const { row: updated } = await api("/api/edit", { session: state.session, index: row.index, value });
    replaceRow(updated);
  } catch (error) { showError(error.message); }
}

function replaceRow(updated) {
  state.rows = state.rows.map((row) => (row.index === updated.index ? updated : row));
  render();
}

function showNoteView(on) {
  $("note").hidden = on;
  $("note-view").hidden = !on;
  $("edit-note").hidden = !on;
}

async function extract() {
  const [target, screen] = $("screen").value.split(":");
  const note = $("note").value;
  if (!note.trim()) return showError("Paste a visit note first.");
  showError("");
  Object.assign(state, { session: null, rows: [], ehr: $("screen").selectedOptions[0].parentElement.dataset.ehr });
  state.entered.clear();
  for (const took of $("stages").querySelectorAll(".took")) took.remove();
  $("live").hidden = true;
  $("shot-box").hidden = true;
  $("extract").disabled = true;
  setStage("extracting");
  render();
  try {
    await stream("/api/extract", { target, screen: Number(screen), note }, (event) => {
      if (event.step === "extract") took("extract", event.ms);
      if (event.step === "done") {
        Object.assign(state, { session: event.session, rows: event.rows });
        showNoteView(true);
        setStage("review");
        render();
      }
    });
  } catch (error) {
    showError(`The note was not extracted. ${error.message}`);
    setStage("start");
    render();
  } finally {
    $("extract").disabled = false;
  }
}

async function enterValues() {
  setStage("entering");
  $("live").hidden = false;
  $("extract").disabled = true;
  render();
  $("live").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    await stream("/api/enter", { session: state.session }, (event) => {
      if (event.step === "field") {
        $("live-frame").src = `data:image/jpeg;base64,${event.frame}`;
        const name = fieldName(event.label);
        const caption = $("live-caption");
        caption.className = `live-caption ${event.status === "refused" ? "refused" : ""}`;
        caption.textContent = event.status === "typing" ? `Entering ${name}: ${event.value}`
          : event.status === "kept" ? `${name}: ${event.value}, read back` : `${name}: the page refused ${event.value}`;
        if (event.status !== "typing") {
          state.entered.set(event.index, event.status);
          render();
        }
      }
      if (event.step === "done") {
        took("enter", event.ms);
        state.rows = event.rows;
        $("shot").src = `data:image/jpeg;base64,${event.screenshot}`;
        $("shot-box").hidden = false;
        $("live-caption").textContent = `${state.entered.size} values entered. Nothing is saved yet.`;
        setStage("entered");
        render();
      }
    });
  } catch (error) {
    showError(`Entry stopped. ${error.message}`);
    setStage("review");
    render();
  } finally {
    $("extract").disabled = false;
  }
}

async function save() {
  $("action").disabled = true;
  try {
    const { saved } = await api("/api/save", { session: state.session });
    state.session = null;
    setStage("saved");
    $("status").textContent = saved;
    $("status").className = "saved";
    render();
  } catch (error) {
    showError(error.message);
    render();
  }
}

$("sample").addEventListener("click", async () => {
  try {
    const { note } = await api("/api/sample");
    $("note").value = note;
    showNoteView(false);
  } catch (error) { showError(error.message); }
});
$("edit-note").addEventListener("click", () => {
  showNoteView(false);
  $("note").focus();
});
$("extract").addEventListener("click", extract);
$("action").addEventListener("click", () => (state.stage === "review" ? enterValues() : save()));
loadTargets().catch((error) => showError(`Could not load the EHR screens. ${error.message}`));
setStage("start");
render();
