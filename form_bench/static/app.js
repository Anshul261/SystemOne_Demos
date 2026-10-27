// Chart entry console: fill a screen from a note, show each value beside its words in the note, and keep
// Save locked until every flagged value has been kept or corrected.

const $ = (id) => document.getElementById(id);
const state = { session: null, rows: [], targetName: "", marks: new Map() };

// "Weight (LOINC:29463-7) lbs * - Decimal pounds…" reads as "Weight lbs".
const fieldName = (label) => label.replace(/\s*\(LOINC:[^)]*\)/, "").replace(/\s*\*.*$/, "").trim();
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

async function api(path, body) {
  const response = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) } : undefined);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `The server answered ${response.status}.`);
  return data;
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

async function loadTargets() {
  const { targets, system_one, llm } = await api("/api/targets");
  const checker = system_one === "jev" ? "Jev" : "d1";
  const model = { "qwen-3.8-27b": "Qwen3.8-27B", "gpt-oss-120b": "gpt-oss-120b" }[llm] || llm;
  $("engine").textContent = `${model} proposes each value and ${checker} checks it against the note before you see it.`;
  for (const target of targets) {
    const group = document.createElement("optgroup");
    group.label = target.name;
    for (const screen of target.screens) {
      group.append(new Option(screen.title, `${target.id}:${screen.index}`));
    }
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

function rowElement(row) {
  const li = document.createElement("li");
  li.classList.toggle("flagged", Boolean(row.flag));
  li.classList.toggle("resolved", row.resolved);

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

  const status = document.createElement("div");
  status.className = "status";
  const message = document.createElement("span");
  const actions = document.createElement("span");
  actions.className = "actions";
  if (row.flag && !row.resolved) {
    message.textContent = row.flag;
    actions.append(button("Keep this value", () => keep(row)));
  } else {
    message.textContent = row.resolved ? "Reviewed by you" : "Checked";
    message.title = row.resolved ? "" : "The decision model confirmed this value is stated in the note.";
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
  control.addEventListener("input", () => {
    if (!useMine.isConnected) actions.prepend(useMine);
  });
  control.addEventListener("change", () => {
    if (!useMine.isConnected) actions.prepend(useMine);
  });

  const light = (on) => state.marks.get(row.index)?.classList.toggle("lit", on);
  li.addEventListener("mouseenter", () => light(true));
  li.addEventListener("mouseleave", () => light(false));
  li.addEventListener("focusin", () => light(true));
  li.addEventListener("focusout", () => light(false));

  li.append(name, control, status);
  return li;
}

function button(text, onClick) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = text;
  element.addEventListener("click", onClick);
  return element;
}

function render() {
  renderNote($("note").value);
  $("rows").replaceChildren(...state.rows.map(rowElement));
  $("empty").hidden = state.rows.length > 0;
  const open = state.rows.filter((row) => row.flag && !row.resolved).length;
  $("tally").innerHTML = state.rows.length
    ? (open ? `<strong>${open} to review</strong> of ${state.rows.length} fields` : `${state.rows.length} fields, all checked`)
    : "";
  $("save").disabled = !state.session || open > 0;
  $("save").textContent = `Save to ${state.targetName}`;
  $("save-status").className = "";
  $("save-status").textContent = !state.session ? "No form filled yet."
    : open ? `Check the ${open} flagged field${open === 1 ? "" : "s"} to unlock saving.`
    : `Every value is checked. Saving writes this screen to ${state.targetName}.`;
}

function showScreenshot(data) {
  $("shot").src = `data:image/jpeg;base64,${data}`;
  $("shot-box").hidden = false;
}

function updateRow(updated, screenshot) {
  state.rows = state.rows.map((row) => (row.index === updated.index ? updated : row));
  showScreenshot(screenshot);
  render();
}

async function keep(row) {
  try {
    const { row: updated, screenshot } = await api("/api/confirm", { session: state.session, index: row.index });
    updateRow(updated, screenshot);
  } catch (error) { showError(error.message); }
}

async function correct(row, value) {
  try {
    const { row: updated, screenshot } = await api("/api/edit", { session: state.session, index: row.index, value });
    updateRow(updated, screenshot);
  } catch (error) { showError(error.message); }
}

function setStep(name, status, ms) {
  const item = $("steps").querySelector(`[data-step="${name}"]`);
  item.className = status;
  item.querySelector(".took")?.remove();
  if (ms !== undefined) {
    const took = document.createElement("span");
    took.className = "took";
    took.textContent = `${(ms / 1000).toFixed(1)} s`;
    item.append(took);
  }
}

async function fill() {
  const [target, screen] = $("screen").value.split(":");
  const note = $("note").value;
  if (!note.trim()) return showError("Paste a visit note first.");
  showError("");
  Object.assign(state, { session: null, rows: [], targetName: $("screen").selectedOptions[0].parentElement.label });
  $("fill").disabled = true;
  $("steps").hidden = false;
  for (const item of $("steps").children) item.className = "";
  setStep("open", "running");
  render();

  const order = ["open", "propose", "enter"];
  let previous = 0;
  try {
    const response = await fetch("/api/fill", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, screen: Number(screen), note }) });
    if (!response.ok) throw new Error((await response.json()).detail);
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines.filter(Boolean)) {
        const event = JSON.parse(line);
        if (event.step === "error") throw new Error(event.message);
        if (order.includes(event.step)) {
          setStep(event.step, "done", event.ms - previous);
          previous = event.ms;
          const next = order[order.indexOf(event.step) + 1];
          if (next) setStep(next, "running");
        }
        if (event.step === "done") {
          Object.assign(state, { session: event.session, rows: event.rows });
          showScreenshot(event.screenshot);
          $("note").hidden = true;
          $("note-view").hidden = false;
          $("edit-note").hidden = false;
          render();
        }
      }
    }
  } catch (error) {
    showError(`The form was not filled. ${error.message}`);
  } finally {
    $("fill").disabled = false;
  }
}

async function save() {
  $("save").disabled = true;
  try {
    const { saved } = await api("/api/save", { session: state.session });
    state.session = null;
    $("save-status").textContent = saved;
    $("save-status").className = "saved";
  } catch (error) {
    showError(error.message);
    render();
  }
}

$("sample").addEventListener("click", async () => {
  const { note } = await api("/api/sample");
  $("note").value = note;
  $("note").hidden = false;
  $("note-view").hidden = true;
  $("edit-note").hidden = true;
});
$("edit-note").addEventListener("click", () => {
  $("note").hidden = false;
  $("note-view").hidden = true;
  $("edit-note").hidden = true;
  $("note").focus();
});
$("fill").addEventListener("click", fill);
$("save").addEventListener("click", save);
loadTargets().catch((error) => showError(`Could not load the EHR screens. ${error.message}`));
render();
