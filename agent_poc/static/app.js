// Demo console: sign in, chat or replay scenarios, and show each turn's path through the six layers.

const LAYER_NAMES = ["Session token", "ServiceNow account", "Per-user graph", "d1 screen + router", "Scoped tools", "Qwen agent"];
const STEP_LABEL = { blocked: "blocked by d1", clarify: "clarifying question", denied: "not in plan" };
const state = {
  status: null, persona: null, token: null, session: null, chat: [],
  scenarios: [], results: {}, viewing: null, selected: null, running: false,
};
const $ = (id) => document.getElementById(id);
// Replies use light markdown: bold, inline code and dashed lists.
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/^\s*[-*] /gm, "• ");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

async function stream(url, body, onEvent) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error(await response.text());
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 1);
      if (line.trim()) onEvent(JSON.parse(line));
    }
  }
}

// A turn collects its trace events as they stream in.
function newTurn(text) { return { text, auth: null, d1: null, tools: [], reply: null, error: null }; }
function addEvent(turn, event) {
  if (event.type === "auth") turn.auth = event;
  else if (event.type === "d1") turn.d1 = event;
  else if (event.type === "tool_call") turn.tools.push({ name: event.name, args: event.args });
  else if (event.type === "tool_result") {
    const call = turn.tools.find((t) => t.name === event.name && t.result === undefined);
    if (call) call.result = event.content;
  } else if (event.type === "reply") turn.reply = event;
  else if (event.type === "error") turn.error = event.error;
}
function stopLayer(turn) {
  if (turn.auth && !turn.auth.ok) return turn.auth.error.startsWith("Invalid session") ? 1 : 2;
  const step = turn.d1?.step;
  if (step === "denied") return 3;
  if (step === "blocked" || step === "clarify") return 4;
  return null;
}

// ---- Header: live readiness checks
async function loadStatus() {
  state.status = await (await fetch("/api/status")).json();
  const short = { d1: (d) => `d1-${d.split(" ")[0].toUpperCase()}`, servicenow: (d) => `ServiceNow ${d.split(" ")[0]}`,
    llm: (d) => d.split("/").pop() };
  $("checks").innerHTML = Object.entries(state.status.checks).map(([key, c]) =>
    `<span class="check ${c.ok ? "ok" : "bad"}" title="${esc(c.detail)}">${esc(c.ok ? short[key](c.detail) : `${key}: ${c.detail}`)}</span>`).join("");
}

// ---- Left: personas and the session they produce
async function loadPersonas() {
  const personas = await (await fetch("/api/personas")).json();
  $("personas").innerHTML = personas.map((p, i) =>
    `<button class="persona ${i > 2 ? "bad" : ""}" data-id="${p.id}"><b>${esc(p.name)}</b><small>${esc(p.note)}</small></button>`).join("");
  $("personas").querySelectorAll(".persona").forEach((el) => el.addEventListener("click", () => signIn(el.dataset.id)));
}

async function signIn(persona) {
  const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ persona }) });
  const info = await response.json();
  Object.assign(state, { persona, token: info.token, session: crypto.randomUUID(), chat: [], viewing: null, selected: null });
  document.querySelectorAll(".persona").forEach((el) => el.classList.toggle("active", el.dataset.id === persona));
  renderSession(info);
  showTab("chat");
  renderTranscript();
  renderTrace();
  $("message").disabled = $("send").disabled = false;
  $("message").placeholder = `Message as ${info.claims.sub}…`;
  $("message").focus();
}

function renderSession(info) {
  const [h, p, s] = info.token.split(".");
  const exp = info.claims.exp ? Math.round((info.claims.exp * 1000 - Date.now()) / 60000) : null;
  const expiry = exp === null ? "none" : exp >= 0 ? `in ${exp} min` : `${-exp} min ago`;
  const auth = info.auth;
  const access = state.status?.access || {};
  $("session").innerHTML = `
    <h2>Session token</h2>
    <div class="token"><span class="h">${esc(h)}</span>.<span class="p">${esc(p)}</span>.<span class="s">${esc(s || "(no signature)")}</span></div>
    <dl class="kv"><dt>alg</dt><dd>${esc(info.header.alg)}</dd><dt>sub</dt><dd>${esc(info.claims.sub)}</dd><dt>expires</dt><dd>${expiry}</dd></dl>
    <div class="verdict ${auth.ok ? "ok" : "bad"}">${auth.ok ? `Accepted · ${esc(auth.name)}` : `Rejected · ${esc(auth.error)}`}</div>
    ${auth.ok ? `
      <dl class="kv"><dt>Premium Support</dt><dd>${auth.groups.includes("Premium Support") ? "member" : "not a member"}</dd></dl>
      <div class="chips">${Object.keys(access).map((a) => `<span class="chip ${auth.permissions.includes(a) ? "on" : "off"}">${a}</span>`).join("")}</div>
      <p class="muted" style="font-size:12px">Read live from ServiceNow on every message.</p>` : ""}`;
}

// ---- Center: chat
$("composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("message").value.trim();
  if (!text || !state.token) return;
  $("message").value = "";
  const turn = newTurn(text);
  state.chat.push(turn);
  state.selected = turn;
  $("send").disabled = true;
  renderTranscript();
  try {
    await stream("/api/turn", { token: state.token, session: state.session, text }, (event) => {
      addEvent(turn, event);
      renderTranscript();
      if (state.selected === turn) renderTrace();
    });
  } catch (error) {
    turn.error = String(error);
  }
  $("send").disabled = false;
  renderTranscript();
  renderTrace();
});

function renderTranscript() {
  const turns = state.viewing ? state.results[state.viewing]?.turns || [] : state.chat;
  const box = $("transcript");
  if (!turns.length && !state.viewing) {
    box.innerHTML = `<p class="muted empty">${state.token ? "Ask for help with an IT issue or a call with an engineer." : "Sign in, then ask for help with an IT issue or a call with an engineer."}</p>`;
    return;
  }
  const parts = [];
  if (state.viewing) {
    const s = state.scenarios.find((x) => x.name === state.viewing);
    parts.push(`<div class="banner" style="background:var(--surface)">Scenario · ${esc(s.name)} · as ${esc(s.user)}${s.bad_token ? " with a bad token" : ""}</div>`);
  }
  turns.forEach((turn, i) => {
    parts.push(`<div class="msg user">${esc(turn.text)}</div>`);
    const selected = state.selected === turn ? "selected" : "";
    const layer = stopLayer(turn);
    if (turn.auth && !turn.auth.ok) {
      parts.push(`<div class="msg system ${selected}" data-i="${i}">Session rejected · ${esc(turn.auth.error)}</div>`);
    } else if (turn.reply) {
      const r = turn.reply;
      const meta = [STEP_LABEL[r.step] || r.step, `${r.llm_calls} LLM call${r.llm_calls === 1 ? "" : "s"}`, `${(r.ms / 1000).toFixed(1)} s`];
      parts.push(`<div class="msg bot ${layer ? "stopped" : ""} ${selected}" data-i="${i}">${md(r.reply)}<div class="meta">${meta.join(" · ")}</div></div>`);
    } else if (turn.error) {
      parts.push(`<div class="msg system">${esc(turn.error)}</div>`);
    } else {
      const doing = turn.tools.length ? `calling ${turn.tools.at(-1).name}` : turn.d1 ? `d1 → ${turn.d1.step}` : turn.auth ? "d1 deciding" : "checking session";
      parts.push(`<div class="msg bot pending ${selected}" data-i="${i}">${doing}…</div>`);
    }
  });
  const result = state.viewing && state.results[state.viewing];
  if (result?.verdict) {
    const v = result.verdict;
    const stop = v.stopped_by ? ` · stopped by ${state.status.layers[v.stopped_by]}` : "";
    parts.push(`<div class="banner ${v.passed ? "ok" : "bad"}">${v.passed ? "PASS" : "FAIL"}${stop}${v.failures.length ? " · " + esc(v.failures.join("; ")) : ""}</div>`);
  }
  box.innerHTML = parts.join("");
  box.querySelectorAll("[data-i]").forEach((el) => el.addEventListener("click", () => {
    state.selected = turns[Number(el.dataset.i)];
    renderTranscript();
    renderTrace();
  }));
  box.scrollTop = box.scrollHeight;
}

// ---- Center: scenarios
async function loadScenarios() {
  state.scenarios = await (await fetch("/api/scenarios")).json();
  renderScenarios();
}

function renderScenarios() {
  const row = (s) => {
    const r = state.results[s.name];
    const status = r ? r.status : "idle";
    const label = { idle: "run", running: "running", pass: "pass", fail: "fail" }[status];
    const stop = r?.verdict?.stopped_by ? state.status.layers[r.verdict.stopped_by] : s.kind === "attack" && s.stops ? `expects ${state.status.layers[s.stops]}` : "";
    return `<div class="row" data-name="${s.name}">
      <span class="state ${status}">${label}</span>
      <div><div class="name">${esc(s.name)} <span class="who">${esc(s.user)}${s.bad_token ? " · bad token" : ""}</span></div>
        <div class="said">${esc(s.turns.map((t) => t.length > 160 ? t.slice(0, 160) + "…" : t).join("  →  "))}</div></div>
      <span class="stop">${esc(stop)}</span></div>`;
  };
  const genuine = state.scenarios.filter((s) => s.kind === "genuine");
  const attacks = state.scenarios.filter((s) => s.kind === "attack");
  $("scenario-list").innerHTML = `<div class="group">Genuine requests · ${genuine.length}</div>${genuine.map(row).join("")}
    <div class="group">Attacks · ${attacks.length}</div>${attacks.map(row).join("")}`;
  $("scenario-list").querySelectorAll(".row").forEach((el) => el.addEventListener("click", () => {
    const name = el.dataset.name;
    if (state.results[name] && state.results[name].status !== "idle") { view(name); return; }
    if (!state.running) playScenario(name, true);
  }));
  renderScore();
}

function renderScore() {
  const done = Object.values(state.results).filter((r) => r.verdict);
  $("score").textContent = done.length ? `${done.filter((r) => r.verdict.passed).length}/${done.length}` : "";
  const tally = {};
  done.forEach((r) => { if (r.verdict.stopped_by) tally[r.verdict.stopped_by] = (tally[r.verdict.stopped_by] || 0) + 1; });
  $("layer-tally").innerHTML = Object.keys(tally).length
    ? "Attacks stopped by: " + Object.entries(tally).map(([l, n]) => `<span class="chip">${esc(state.status.layers[l])} · ${n}</span>`).join("")
    : `<span class="muted">Each scenario runs live against d1, Qwen, ServiceNow and the calendar, then the eval's checks decide pass or fail.</span>`;
}

function view(name) {
  state.viewing = name;
  const turns = state.results[name].turns;
  state.selected = turns.at(-1) || null;
  $("message").disabled = $("send").disabled = true;
  $("message").placeholder = "Viewing a scenario. Pick a person on the left to chat.";
  document.querySelectorAll(".persona").forEach((el) => el.classList.remove("active"));
  const s = state.scenarios.find((x) => x.name === name);
  $("session").innerHTML = `<h2>Scenario session</h2><p>This scenario signs in as <b>${esc(s.user)}</b>${s.bad_token
    ? " with a deliberately bad token" : " with a fresh valid token"}. Its trace on the right shows what the server made of it.</p>`;
  state.token = null;
  showTab("chat");
  renderTranscript();
  renderTrace();
}

async function playScenario(name, show) {
  const result = { status: "running", turns: [], verdict: null };
  state.results[name] = result;
  if (show) view(name);
  renderScenarios();
  let turn = null;
  try {
    await stream("/api/scenario", { name, run_id: String(Date.now()) }, (event) => {
      if (event.type === "user") { turn = newTurn(event.text); result.turns.push(turn); if (state.viewing === name) state.selected = turn; }
      else if (event.type === "verdict") { result.verdict = event; result.status = event.passed ? "pass" : "fail"; }
      else if (turn) addEvent(turn, event);
      else if (event.type === "error") { result.verdict = { passed: false, failures: [event.error] }; result.status = "fail"; }
      if (state.viewing === name) { renderTranscript(); renderTrace(); }
    });
  } catch (error) {
    result.verdict = { passed: false, failures: [String(error)] };
    result.status = "fail";
  }
  renderScenarios();
  if (state.viewing === name) renderTranscript();
}

$("run-all").addEventListener("click", async () => {
  if (state.running) return;
  state.running = true;
  $("run-all").disabled = true;
  await fetch("/api/reset", { method: "POST" });
  state.results = {};
  renderScenarios();
  for (const s of state.scenarios) await playScenario(s.name, false);
  state.running = false;
  $("run-all").disabled = false;
});

$("reset").addEventListener("click", async () => {
  await fetch("/api/reset", { method: "POST" });
  $("reset").textContent = "Calendar reset ✓";
  setTimeout(() => ($("reset").textContent = "Reset calendar"), 1500);
});

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("chat").classList.toggle("hidden", name !== "chat");
  $("scenarios").classList.toggle("hidden", name !== "scenarios");
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

// ---- Right: the trace of the selected turn, one block per layer
function bars(answer, order, flagged) {
  const probabilities = answer.probabilities || {};
  return `<div class="bars">${order.map((key) => {
    const p = probabilities[key] ?? 0;
    const win = key === answer.choice;
    return `<div class="bar ${win ? "win" : ""} ${win && flagged ? "flag" : ""}"><span>${esc(key)}</span>
      <span class="track"><span class="fill" style="width:${(p * 100).toFixed(1)}%;display:block"></span></span><span>${p.toFixed(2)}</span></div>`;
  }).join("")}</div>`;
}

function renderTrace() {
  const turn = state.selected;
  if (!turn) {
    $("trace-label").textContent = "";
    $("trace").innerHTML = `<p class="muted">Each turn passes through six layers. Select a reply to see its trace.</p>`;
    return;
  }
  $("trace-label").textContent = `· "${turn.text.length > 34 ? turn.text.slice(0, 34) + "…" : turn.text}"`;
  const stop = stopLayer(turn);
  const blocks = [];
  const layer = (n, body, ms) => {
    const cls = stop === n ? "stopped" : stop && n > stop && !(stop === 3 && n === 4) ? "skipped" : (n === 1 && turn.auth) || (n === 2 && turn.auth?.ok) || (n === 3 && turn.d1) || (n === 4 && turn.d1) || (n === 5 && turn.reply) || (n === 6 && turn.reply) ? "pass" : "";
    blocks.push(`<div class="layer ${cls}"><h3><span><span class="n">${n}</span>${LAYER_NAMES[n - 1]}</span>${ms !== undefined ? `<span class="t">${ms} ms</span>` : ""}</h3><div class="what">${body}</div></div>`);
  };
  const auth = turn.auth;
  const access = state.status?.access || {};
  // 1 and 2: the session
  if (!auth) layer(1, `<span class="muted">checking…</span>`);
  else if (!auth.ok && stop === 1) layer(1, `Rejected: ${esc(auth.error)}. No model ran.`, auth.ms);
  else layer(1, "Signature and expiry verified. Identity comes from the token, never the chat.");
  if (auth && !auth.ok && stop === 2) layer(2, `Rejected: ${esc(auth.error)}. No model ran.`, auth.ms);
  else if (auth?.ok) layer(2, `Read live from ServiceNow: ${esc(auth.name)} · active · <span class="chips">${auth.groups.includes("Premium Support") ? `<span class="chip on">Premium Support</span>` : `<span class="chip off">Premium Support</span>`}</span>`, auth.ms);
  else layer(2, `<span class="muted">not reached</span>`);
  // 3: which agents exist in this user's graph
  if (auth?.ok) {
    const chips = Object.keys(access).map((a) => `<span class="chip ${auth.permissions.includes(a) ? "on" : "off"}">${a}</span>`).join(" ");
    layer(3, `${chips}${stop === 3 ? `<div class="rule">d1 picked ${esc(turn.d1.agent.choice)}, which isn't in this graph. Fixed refusal, no LLM call.</div>` : ""}`);
  } else layer(3, `<span class="muted">not reached</span>`);
  // 4: d1, both questions from one forward pass
  if (turn.d1) {
    const d = turn.d1;
    const flagged = d.intent.choice !== "normal";
    const t = state.status.thresholds;
    layer(4, `<div class="q">What is the user doing? (screen, blocks at ≥ ${t.screen})</div>
      ${bars(d.intent, ["normal", "other_person", "override", "payload", "harmful"], flagged)}
      <div class="q">Which agent should answer? (start ≥ ${t.start}, switch ≥ ${t.switch})</div>
      ${bars(d.agent, ["support", "appointments", "unclear"], false)}
      <div class="rule">→ ${esc(d.rule)} · <b>${esc(STEP_LABEL[d.step] || d.step)}</b></div>
      <div class="q">${esc(d.model)} · one forward pass · 0 output tokens</div>`, d.model_ms ? Math.round(d.model_ms) : d.ms);
  } else layer(4, `<span class="muted">${auth?.ok ? "deciding…" : "not reached"}</span>`);
  // 5: the tools, built for this user only
  if (stop) layer(5, `<span class="muted">not reached</span>`);
  else if (turn.tools.length) layer(5, turn.tools.map((t) => `<div><b>${esc(t.name)}</b>(${esc(JSON.stringify(t.args))})</div>
      <pre class="tool">${esc(t.result === undefined ? "…" : t.result.length > 900 ? t.result.slice(0, 900) + "…" : t.result)}</pre>`).join("")
      + `<div class="q">Every call runs as ${esc(auth.user)}. No tool takes a user argument.</div>`);
  else layer(5, turn.reply ? `No tools called.` : `<span class="muted">waiting…</span>`);
  // 6: Qwen
  if (stop) layer(6, `Not called. Saved an LLM call.`);
  else if (turn.reply) layer(6, `${turn.reply.llm_calls} call${turn.reply.llm_calls === 1 ? "" : "s"} to Qwen3.8-27B · turn total ${(turn.reply.ms / 1000).toFixed(1)} s`);
  else layer(6, `<span class="muted">waiting…</span>`);
  $("trace").innerHTML = blocks.join("");
}

loadStatus().then(() => Promise.all([loadPersonas(), loadScenarios()]));
