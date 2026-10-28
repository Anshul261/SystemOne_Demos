// Chart entry console: read a doctor's note, review the screens Jev fills from it, watch them being entered
// into the EMR, then save. Each step stays locked until the one before it is finished.

const $ = (id) => document.getElementById(id);
const state = { session: null, screens: [], ehr: "", stage: "start", entered: new Map(), marks: new Map() };
const id = (row) => `${row.screen}:${row.index}`;
const allRows = () => state.screens.flatMap((screen) => screen.rows);
const keptRows = () => state.screens.filter((screen) => !screen.skip).flatMap((screen) => screen.rows);
const openFlags = (rows = keptRows()) => rows.filter((row) => row.flag && !row.resolved).length;

// "Weight (LOINC:29463-7) lbs (Value) - Decimal pounds…" reads as "Weight lbs"; a lab row's leading order
// date goes; "… (Abn)" keeps its column.
const fieldName = (label) => label.replace(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d /, "").replace(/\s*\(LOINC:[^)]*\)/, "")
  .replace(/ \(Value\)/, "").replace(/ - (Decimal pounds|Units applicable|Indicates|Reference range|Date and time).*$/, "")
  .replace(/\s*\*.*$/, "").trim();
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

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
  const { targets } = await api("/api/targets");
  for (const target of targets) {
    const option = new Option(target.name.replace(/^the /, "").replace(/^./, (c) => c.toUpperCase()), target.id);
    option.dataset.ehr = target.name;
    $("target").append(option);
  }
  $("target").value = targets.some((t) => t.id === "openemr") ? "openemr" : targets[0]?.id;
}

// Mark the first whole-word match of each value in the note, so a reviewer can see where it came from.
function renderNote(note) {
  const found = [];
  for (const row of allRows()) {
    if (!row.value) continue;
    const match = new RegExp(`(?<![\\w.])${escape(row.value)}(?![\\w])`, "i").exec(note);
    if (!match) continue;
    const end = match.index + match[0].length;
    const same = found.find((f) => f.start === match.index && f.end === end);
    if (same) same.rows.push(row);  // fields with one value share a mark (First Name, Birth First Name)
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
    for (const row of rows) state.marks.set(id(row), mark);
    at = end;
  }
  view.append(note.slice(at));
}

function button(text, onClick, className = "") {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = text;
  element.className = className;
  element.addEventListener("click", onClick);
  return element;
}

function rowElement(row, screen) {
  const li = document.createElement("li");
  const entered = state.entered.get(id(row));
  li.classList.toggle("flagged", Boolean(row.flag));
  li.classList.toggle("resolved", row.resolved);
  li.classList.toggle("entered", entered === "kept");
  li.classList.toggle("refused", entered === "refused");

  const name = document.createElement("label");
  name.className = "field-name";
  name.textContent = fieldName(row.label);
  name.title = row.label;
  name.htmlFor = `field-${row.screen}-${row.index}`;

  let control;
  if (row.kind === "choice") {
    control = document.createElement("select");
    for (const option of row.options) control.append(new Option(option || "(blank)", option));
  } else {
    control = document.createElement("input");
  }
  control.id = `field-${row.screen}-${row.index}`;
  control.value = row.value;
  control.disabled = screen.skip || screen.saved || state.stage === "entering" || state.stage === "saved";

  const status = document.createElement("div");
  status.className = "status";
  const message = document.createElement("span");
  const actions = document.createElement("span");
  actions.className = "actions";
  if (row.flag && !row.resolved) {
    message.textContent = row.flag;
    if (row.value) actions.append(button("Keep this value", () => keep(row)));
    else actions.append(button("Leave it blank", () => keep(row)));
  } else if (entered) {
    message.textContent = entered === "kept" ? "Entered and read back" : "The page refused this value";
  } else if (row.resolved) {
    message.textContent = "Reviewed by you";
  } else {
    message.textContent = row.confidence == null ? "Checked" : `Jev is ${Math.round(row.confidence * 100)}% sure`;
    message.title = "How sure Jev is of this value and of what the field holds.";
  }
  status.append(message);
  if (row.value && !state.marks.has(id(row)) && !row.resolved && row.kind === "text") {
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

  const light = (on) => state.marks.get(id(row))?.classList.toggle("lit", on);
  li.addEventListener("mouseenter", () => light(true));
  li.addEventListener("mouseleave", () => light(false));
  li.addEventListener("focusin", () => light(true));
  li.addEventListener("focusout", () => light(false));

  li.append(name, control, status);
  return li;
}

function screenElement(screen) {
  const section = document.createElement("section");
  section.className = "screen";
  section.classList.toggle("skipped", screen.skip);
  const head = document.createElement("div");
  head.className = "screen-head";
  const title = document.createElement("h3");
  title.textContent = screen.title;
  const open = openFlags(screen.rows);
  const tally = document.createElement("p");
  tally.className = "screen-tally";
  tally.textContent = screen.skip ? "Left out: nothing here will be entered"
    : screen.saved ? "Saved" : screen.entered ? "Entered"
    : `${screen.rows.length} fields${open ? `, ${open} to review` : ""}`;
  head.append(title, tally);
  if (!screen.entered && state.stage === "review") {
    head.append(button(screen.skip ? "Put this screen back" : "Leave this screen out", () => skip(screen), "quiet"));
  }
  const list = document.createElement("ul");
  list.className = "rows";
  if (!screen.skip) list.append(...screen.rows.map((row) => rowElement(row, screen)));
  section.append(head, list);
  return section;
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
  $("screens").replaceChildren(...state.screens.map(screenElement));
  $("empty").hidden = state.screens.length > 0 || state.stage === "extracting";
  const kept = keptRows();
  const open = openFlags();
  const count = state.screens.filter((s) => !s.skip).length;
  $("tally").innerHTML = !state.screens.length ? ""
    : `${count} screen${count === 1 ? "" : "s"}, ${kept.length} values` + (open ? `, <strong>${open} to review</strong>` : ", all checked");
  const action = $("action");
  const ehr = state.ehr || "the EMR";
  if (state.stage === "review") {
    action.textContent = `Enter into ${ehr}`;
    action.disabled = open > 0 || count === 0;
    $("status").textContent = !count ? "Every screen is left out." : open ? `Review the ${open} flagged value${open === 1 ? "" : "s"} to unlock entry.`
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
    $("status").textContent = state.stage === "extracting" ? "Reading the note…" : "Write or paste a note to begin.";
  }
}

function replaceRow(updated) {
  for (const screen of state.screens) {
    if (screen.screen === updated.screen) screen.rows = screen.rows.map((row) => (row.index === updated.index ? updated : row));
  }
  render();
}

function replaceScreen(updated) {
  state.screens = state.screens.map((screen) => (screen.screen === updated.screen ? updated : screen));
  render();
}

async function keep(row) {
  try {
    replaceRow((await api("/api/confirm", { session: state.session, screen: row.screen, index: row.index })).row);
  } catch (error) { showError(error.message); }
}

async function correct(row, value) {
  try {
    replaceRow((await api("/api/edit", { session: state.session, screen: row.screen, index: row.index, value })).row);
  } catch (error) { showError(error.message); }
}

async function skip(screen) {
  try {
    replaceScreen((await api("/api/skip", { session: state.session, screen: screen.screen, skip: !screen.skip })).screen);
  } catch (error) { showError(error.message); }
}

function showNoteView(on) {
  $("note").hidden = on;
  $("note-view").hidden = !on;
  $("edit-note").hidden = !on;
}

function log(text) {
  $("read-log").hidden = !text;
  $("read-log").textContent = text;
}

async function readNote() {
  const note = $("note").value;
  if (!note.trim()) return showError("Write or paste a note first.");
  showError("");
  Object.assign(state, { session: null, screens: [], ehr: $("target").selectedOptions[0].dataset.ehr });
  state.entered.clear();
  for (const element of $("stages").querySelectorAll(".took")) element.remove();
  $("live").hidden = true;
  $("extract").disabled = true;
  setStage("extracting");
  render();
  const found = [];
  try {
    await stream("/api/read", { target: $("target").value, note }, (event) => {
      if (event.step === "read") log(`Read ${event.values} values from the note. Looking at the screens…`);
      if (event.step === "looking") log(`Read the note. Looking at ${event.title}…`);
      if (event.step === "screen") found.push(`${event.title} (${event.values})`);
      if (event.step === "done") {
        took("extract", event.ms);
        log(`${found.length ? `Needs ${found.join(", ")}.` : "No screen needs anything from this note."} `
          + `${event.checks} Jev requests, ${(event.ms / 1000).toFixed(1)} s.`);
        Object.assign(state, { session: event.session, screens: event.screens });
        showNoteView(true);
        setStage("review");
        render();
      }
    });
  } catch (error) {
    showError(`The note was not read. ${error.message}`);
    log("");
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
  let title = "";
  try {
    await stream("/api/enter", { session: state.session }, (event) => {
      if (event.step === "screen") title = event.title;
      if (event.step === "field") {
        $("live-frame").src = `data:image/jpeg;base64,${event.frame}`;
        const name = fieldName(event.label);
        const caption = $("live-caption");
        caption.className = `live-caption ${event.status === "refused" ? "refused" : ""}`;
        caption.textContent = `${title}: ` + (event.status === "typing" ? `entering ${name}: ${event.value}`
          : event.status === "kept" ? `${name}: ${event.value}, read back` : `${name}: the page refused ${event.value}`);
        if (event.status !== "typing") {
          state.entered.set(`${event.screen}:${event.index}`, event.status);
          render();
        }
      }
      if (event.step === "entered") replaceScreen(event.screen);
      if (event.step === "done") {
        took("enter", event.ms);
        state.screens = event.screens;
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
    for (const screen of state.screens) if (!screen.skip) screen.saved = true;
    setStage("saved");
    $("status").textContent = saved.join(". ");
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
$("extract").addEventListener("click", readNote);
$("action").addEventListener("click", () => (state.stage === "review" ? enterValues() : save()));
loadTargets().catch((error) => showError(`Could not load the EMRs. ${error.message}`));
setStage("start");
render();
