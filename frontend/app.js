const $ = (s) => document.querySelector(s);
const form = $("#ask"), q = $("#q"), go = $("#go");
const timeline = $("#timeline"), sourcesEl = $("#sources"), report = $("#report");
const live = $("#live"), liveText = $("#live-text");

const ICONS = { search: "🔍", read: "📄", analyze: "🧮", thinking: "🧠", note: "💬", done: "✅", error: "⚠️", status: "🛰️" };
const LABELS = { search: "Search", read: "Read", analyze: "Analyze", thinking: "Thinking", note: "Note", done: "Done", error: "Error", status: "Status" };

let state;

fetch("/api/health").then((r) => r.json()).then((h) => {
  const m = $("#mode");
  m.textContent = h.mode === "live" ? `● live · ${h.model}` : "● demo mode";
  m.className = `pill ${h.mode}`;
}).catch(() => {});

document.querySelectorAll("#examples button").forEach((b) =>
  b.addEventListener("click", () => { q.value = b.textContent; form.requestSubmit(); })
);
q.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = q.value.trim();
  if (!question || state?.running) return;
  start(question, new FormData(form).get("depth"));
});

function start(question, depth) {
  state = { running: true, buffer: "", sources: new Map(), searches: 0, reads: 0, t0: performance.now(), pending: null, raf: 0 };
  document.body.classList.add("running");
  $("#run").classList.remove("hidden");
  $("#report-title").textContent = question;
  timeline.innerHTML = ""; sourcesEl.innerHTML = ""; report.innerHTML = "";
  $("#confidence").classList.add("hidden");
  $("#copy").disabled = $("#download").disabled = true;
  go.disabled = true; go.textContent = "Scouting…";
  setLive("Planning the research…");
  state.timer = setInterval(() => ($("#st-time").textContent = elapsed()), 100);
  updateStats();
  stream(question, depth).catch((err) => onEvent({ type: "error", text: String(err.message || err) })).finally(finish);
}

async function stream(question, depth) {
  const res = await fetch("/api/research", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, depth }),
  });
  if (!res.ok) throw new Error(`Server returned ${res.status}`);
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}

function onEvent(ev) {
  switch (ev.type) {
    case "status": addStep("status", ev.text); break;
    case "token":
      state.buffer += ev.text;
      setLive("Writing…");
      scheduleRender();
      break;
    case "thinking": addStep("thinking", clip(ev.text, 280)); setLive("Thinking…"); break;
    case "tool_pending":
      flushNote();
      state.pending = addStep(ev.tool === "web_fetch" ? "read" : ev.tool === "web_search" ? "search" : "analyze", "…", true);
      setLive(ev.tool === "web_fetch" ? "Reading a source…" : ev.tool === "web_search" ? "Searching the web…" : "Analyzing…");
      break;
    case "step":
      if (ev.kind === "search") state.searches++;
      if (ev.kind === "read") state.reads++;
      resolvePending(ev.kind, ev.label);
      updateStats();
      break;
    case "sources":
      ev.sources.forEach((s) => addSource(s.url, s.title));
      setLive("Weighing the results…");
      break;
    case "fetched": addSource(ev.url, ev.title, true); break;
    case "error":
      if (state.pending) resolvePending("error", ev.text);
      else addStep("error", ev.text);
      report.insertAdjacentHTML("beforeend", `<div class="errbox">${esc(ev.text)}</div>`);
      break;
    case "done": {
      const u = ev.usage || {};
      const tokens = (u.input || 0) + (u.output || 0);
      addStep("done", `Report ready in ${elapsed()}` + (tokens ? ` · ${fmt(tokens)} tokens` : ""));
      break;
    }
  }
}

function finish() {
  clearInterval(state.timer);
  state.running = false;
  cancelAnimationFrame(state.raf);
  renderReport(true);
  live.classList.add("hidden");
  go.disabled = false; go.textContent = "Deploy Scout →";
  const hasReport = state.buffer.trim().length > 0;
  $("#copy").disabled = $("#download").disabled = !hasReport;
}

/* ---------- timeline ---------- */
function addStep(kind, text, pending = false) {
  const li = document.createElement("li");
  li.className = kind + (pending ? " pending" : "");
  li.innerHTML = `<div class="ic">${ICONS[kind] || "•"}</div><div class="k">${LABELS[kind] || kind}</div><div class="v"></div>`;
  li.querySelector(".v").textContent = text;
  timeline.appendChild(li);
  li.scrollIntoView({ block: "nearest" });
  return li;
}
function resolvePending(kind, label) {
  const li = state.pending;
  if (!li) { if (kind !== "error") addStep(kind, label); return; }
  li.className = kind;
  li.querySelector(".ic").textContent = ICONS[kind] || "•";
  li.querySelector(".k").textContent = LABELS[kind] || kind;
  li.querySelector(".v").textContent = kind === "read" ? prettyUrl(label) : label;
  state.pending = null;
}
// Text written between tool calls is the agent narrating - move it to the trace.
function flushNote() {
  const text = state.buffer.trim();
  if (text) addStep("note", clip(text.replace(/[#*`>]/g, ""), 320));
  state.buffer = "";
  renderReport();
}

/* ---------- sources ---------- */
function addSource(url, title, read = false) {
  if (!url) return;
  const existing = state.sources.get(url);
  if (existing) {
    if (read && !existing.querySelector(".read-badge")) existing.querySelector("a").insertAdjacentHTML("beforeend", `<span class="read-badge">READ</span>`);
    return;
  }
  let host = ""; try { host = new URL(url).hostname; } catch {}
  const li = document.createElement("li");
  li.innerHTML = `<a target="_blank" rel="noopener"><img alt="" loading="lazy"><span class="t"></span>${read ? '<span class="read-badge">READ</span>' : ""}</a>`;
  const a = li.querySelector("a"); a.href = url; a.title = url;
  li.querySelector("img").src = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=32`;
  li.querySelector(".t").textContent = title || host;
  sourcesEl.appendChild(li);
  state.sources.set(url, li);
  $("#st-src").textContent = state.sources.size;
}

/* ---------- report ---------- */
function scheduleRender() {
  if (state.raf) return;
  state.raf = requestAnimationFrame(() => { state.raf = 0; renderReport(); });
}
function renderReport(final = false) {
  const md = state.buffer;
  report.innerHTML = md ? DOMPurify.sanitize(marked.parse(md)) : "";
  report.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
  report.classList.toggle("writing", !final && md.length > 0);
  if (final) enhance();
}
function enhance() {
  const h2s = [...report.querySelectorAll("h2")];
  const tldr = h2s.find((h) => /tl;?dr/i.test(h.textContent));
  if (tldr) {
    const box = document.createElement("div"); box.className = "tldr";
    let n = tldr.nextElementSibling;
    while (n && n.tagName !== "H2") { const next = n.nextElementSibling; box.appendChild(n); n = next; }
    tldr.after(box);
  }
  const m = state.buffer.match(/Confidence:\s*\**\s*(\d{1,3})\s*\/\s*100/i);
  if (m) {
    const p = Math.min(100, +m[1]);
    const ring = $("#confidence .ring");
    ring.style.setProperty("--p", p);
    ring.style.setProperty("--ring", p >= 70 ? "var(--ok)" : p >= 40 ? "var(--warn)" : "var(--err)");
    ring.querySelector("span").textContent = p;
    $("#confidence").classList.remove("hidden");
  }
  const fu = h2s.find((h) => /follow[- ]?up/i.test(h.textContent));
  const list = fu?.nextElementSibling;
  if (list && list.tagName === "UL") {
    const wrap = document.createElement("div"); wrap.className = "followups";
    [...list.querySelectorAll("li")].forEach((li) => {
      const b = document.createElement("button"); b.type = "button"; b.textContent = "↳ " + li.textContent.trim();
      b.onclick = () => { q.value = li.textContent.trim(); window.scrollTo({ top: 0, behavior: "smooth" }); form.requestSubmit(); };
      wrap.appendChild(b);
    });
    list.replaceWith(wrap);
  }
}

$("#copy").onclick = async () => {
  await navigator.clipboard.writeText(state.buffer);
  $("#copy").textContent = "Copied ✓"; setTimeout(() => ($("#copy").textContent = "Copy"), 1500);
};
$("#download").onclick = () => {
  const blob = new Blob([`# ${$("#report-title").textContent}\n\n${state.buffer}`], { type: "text/markdown" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "xerien-scout-report.md" });
  a.click(); URL.revokeObjectURL(a.href);
};

/* ---------- utils ---------- */
function setLive(t) { live.classList.remove("hidden"); liveText.textContent = t; }
function updateStats() { $("#st-search").textContent = state.searches; $("#st-read").textContent = state.reads; }
function elapsed() { return ((performance.now() - state.t0) / 1000).toFixed(1) + "s"; }
function clip(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function fmt(n) { return Number.isFinite(n) ? n.toLocaleString() : "0"; }
function esc(s) { return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
function prettyUrl(u) { try { const x = new URL(u); return x.hostname + x.pathname.replace(/\/$/, ""); } catch { return u; } }
