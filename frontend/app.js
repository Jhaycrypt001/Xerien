const $ = (s) => document.querySelector(s);
const form = $("#ask"), q = $("#q"), go = $("#go");
const traceEl = $("#trace"), sourcesEl = $("#sources"), report = $("#report");

const KIND = {
  status: ["·", "Agent", "muted"], note: ["·", "Note", "muted"], thinking: ["·", "Think", "muted"],
  search: ["◆", "Search", ""], read: ["◆", "Read", ""], analyze: ["◆", "Analyze", ""], market: ["◆", "Data", ""],
  fetched: ["✓", "Read", "ok"], done: ["✓", "Done", "ok"], error: ["×", "Error", "err"],
};

const api = (path, opts = {}) => fetch(path, opts);

let state = null;
let account = null; // { account, chain, address } when signed in

/* ---------- boot ---------- */
api("/api/health").then((r) => r.json()).then((h) => {
  const s = $("#status");
  s.className = "tag " + (h.configured ? "ok" : "bad");
  s.lastElementChild.textContent = h.configured ? "Online" : "Not configured";
  s.title = h.configured ? `${h.provider === "gemini" ? "Gemini" : "Claude"} · ${h.model}` : "The server has no model API key";
}).catch(() => {});

XerienWallet.me().then((m) => { setAccount(m); route(); });
window.addEventListener("popstate", route);

function route() {
  const m = location.pathname.match(/^\/r\/([\w-]+)/);
  if (m) openReport(m[1]);
  else if (!account) showGate();
  else if (location.pathname === "/history") showHistoryPage();
  else showComposer();
}
function navigate(path) {
  if (state?.running) return toast("Wait for the current run to finish");
  if (location.pathname !== path) history.pushState({}, "", path);
  route();
}
const VIEWS = ["#gate", "#composer", "#run", "#history-page"];
function showView(sel) {
  VIEWS.forEach((v) => $(v).classList.toggle("hidden", v !== sel));
  $("#history-link").classList.toggle("active", sel === "#history-page");
  window.scrollTo({ top: 0 });
}

/* ---------- account ---------- */
function setAccount(m) {
  account = m;
  $("#signin").classList.toggle("hidden", Boolean(m));
  $("#history-link").classList.toggle("hidden", !m);
  $("#history-all").classList.toggle("hidden", !m);
  $("#account").classList.toggle("hidden", !m);
  $("#new").classList.toggle("hidden", !m);
  if (m) {
    $("#acct-addr").textContent = XerienWallet.short(m.address);
    $("#acct-full").textContent = m.address;
    $("#acct-chain").textContent = m.chain === "solana" ? "Solana wallet" : "EVM wallet";
  }
  $("#scan").classList.toggle("hidden", !m);
  $("#history-locked").classList.toggle("hidden", Boolean(m));
  if (m) loadHistory(); else { $("#history").innerHTML = ""; $("#history-empty").classList.add("hidden"); }
}
async function signIn() {
  try {
    setAccount(await XerienWallet.signIn());
    return true;
  } catch { return false; }
}
$("#signin").onclick = $("#gate-btn").onclick = async () => {
  if (await signIn() && !location.pathname.startsWith("/r/")) route();
};
$("#acct-btn").onclick = (e) => {
  e.stopPropagation();
  const menu = $("#acct-menu"), open = menu.classList.toggle("hidden") === false;
  $("#acct-btn").setAttribute("aria-expanded", String(open));
};
document.addEventListener("click", (e) => { if (!e.target.closest("#account")) $("#acct-menu").classList.add("hidden"); });
$("#acct-copy").onclick = () => { $("#acct-menu").classList.add("hidden"); copyText(account.address, "Address copied"); };
$("#acct-out").onclick = async () => {
  $("#acct-menu").classList.add("hidden");
  await XerienWallet.signOut().catch(() => {});
  setAccount(null);
  if (state) state.owner = false;
  if (location.pathname.startsWith("/r/")) setActions(Boolean(state && state.buffer.trim()));
  else showGate();
  toast("Signed out");
};

function showGate() {
  showView("#gate");
  document.title = "Scout Dashboard";
}

/* ---------- composer ---------- */
document.querySelectorAll("#examples .chip").forEach((b) =>
  b.addEventListener("click", () => { q.value = b.textContent; form.requestSubmit(); })
);
q.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const question = q.value.trim();
  if (question.length < 3 || state?.running) return;
  runResearch(question, new FormData(form).get("depth"));
});
$("#scan").onclick = () => {
  if (state?.running) return;
  runResearch(q.value.trim(), new FormData(form).get("depth"), true);
};
$("#new").onclick = async () => {
  if (state?.running) return;
  if (!account && !(await signIn())) return;
  navigate("/app"); q.focus();
};
$("#hp-new").onclick = $("#hp-empty-new").onclick = () => { navigate("/app"); q.focus(); };
[$("#history-link"), $("#history-all")].forEach((a) => a.addEventListener("click", (e) => {
  e.preventDefault();
  navigate("/history");
}));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#acct-menu").classList.add("hidden"); });

function showComposer() {
  if (!account) return showGate();
  showView("#composer");
  document.title = "Scout Dashboard";
  markActive(null);
}

/* ---------- run view ---------- */
function resetRun(question, meta) {
  state = { running: false, id: null, owner: false, buffer: "", sources: new Map(), searches: 0, reads: 0, pending: null, raf: 0, t0: performance.now(), question };
  showView("#run");
  $("#run-q").textContent = question;
  $("#run-meta").textContent = meta;
  document.title = `${question.slice(0, 60)} · Scout`;
  traceEl.innerHTML = ""; sourcesEl.innerHTML = ""; report.innerHTML = "";
  $("#src-n").textContent = "0";
  $("#error").classList.add("hidden");
  $("#confidence").classList.add("hidden");
  $("#market").classList.add("hidden"); $("#market-list").innerHTML = "";
  $("#holdings").classList.add("hidden"); $("#hold-list").innerHTML = "";
  $("#delete").classList.add("hidden");
  setActions(false);
  updateStats("0.0s");
}

async function runResearch(question, depth, scanWallet = false) {
  resetRun(question || "Wallet scan: risks in my holdings", `${depth} ${scanWallet ? "wallet scan" : "research"} · running`);
  state.running = true;
  go.disabled = true; $("#new").disabled = true;
  state.timer = setInterval(() => updateStats(elapsed()), 100);
  try {
    const res = await api("/api/research", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, depth, scan_wallet: scanWallet }),
    });
    if (res.status === 401) { setAccount(null); throw new Error("Your session expired. Connect your wallet again."); }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Server returned ${res.status}`);
    }
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).split("\n").find((l) => l.startsWith("data: "));
        buf = buf.slice(i + 2);
        if (line) onEvent(JSON.parse(line.slice(6)));
      }
    }
  } catch (err) {
    onEvent({ type: "error", text: String(err.message || err) });
  } finally {
    clearInterval(state.timer);
    state.running = false;
    go.disabled = false; $("#new").disabled = false;
    cancelAnimationFrame(state.raf); state.raf = 0;
    renderReport(true);
    $("#run-meta").textContent = `${depth} research · ${state.id ? "saved" : "not saved"} · ${elapsed()}`;
    setActions(Boolean(state.buffer.trim()));
  }
}

async function openReport(id) {
  if (state?.running) return;
  resetRun("Loading report…", "");
  markActive(id);
  const res = await api(`/api/reports/${encodeURIComponent(id)}`).catch(() => null);
  if (!res || !res.ok) {
    $("#run-q").textContent = "Report not found";
    showError("This report doesn't exist or was deleted.");
    return;
  }
  const r = await res.json();
  resetRun(r.question, `${r.depth} research · ${fmtDate(r.created_at)} · ${(r.duration_ms / 1000).toFixed(1)}s`);
  Object.assign(state, { id: r.id, owner: r.owner });
  markActive(r.id);
  r.trace.forEach((ev) => onEvent(ev, true));
  state.buffer = r.report;
  renderReport(true);
  updateStats(`${(r.duration_ms / 1000).toFixed(1)}s`);
  setActions(true);
}

/* ---------- agent events ---------- */
function onEvent(ev, replay = false) {
  switch (ev.type) {
    case "status": line("status", ev.text); break;
    case "note": line("note", clip(ev.text, 280)); break;
    case "thinking": line("thinking", clip(ev.text, 280)); break;
    case "token":
      state.buffer += ev.text;
      scheduleRender();
      break;
    case "tool_pending": {
      const text = state.buffer.trim();
      if (text) line("note", clip(text.replace(/[#*`>_]/g, ""), 280));
      state.buffer = "";
      renderReport();
      const kind = ev.tool === "web_fetch" ? "read" : ev.tool === "web_search" ? "search" : "analyze";
      state.pending = line(kind, "…", true);
      break;
    }
    case "step":
      if (ev.kind === "search") state.searches++;
      if (ev.kind === "read") state.reads++;
      if (state.pending) resolve(state.pending, ev.kind, ev.kind === "read" ? prettyUrl(ev.label) : ev.label);
      else line(ev.kind, ev.kind === "read" ? prettyUrl(ev.label) : ev.label);
      state.pending = null;
      if (!replay) updateStats(elapsed());
      break;
    case "sources": ev.sources.forEach((s) => addSource(s.url, s.title, false, s.domain)); break;
    case "market": renderMarket(ev.items, replay); break;
    case "holdings": renderHoldings(ev); break;
    case "report": state.buffer = ev.text; scheduleRender(); break;
    case "fetched": addSource(ev.url, ev.title, true); break;
    case "error":
      if (state.pending) { resolve(state.pending, "error", ev.text); state.pending = null; }
      else line("error", ev.text);
      showError(ev.text);
      break;
    case "done": {
      const u = ev.usage || {};
      const tokens = (u.input || 0) + (u.output || 0);
      line("done", `${state.sources.size} sources` + (tokens ? ` · ${tokens.toLocaleString()} tokens` : ""));
      break;
    }
    case "saved":
      state.id = ev.id; state.owner = true;
      history.pushState({}, "", `/r/${ev.id}`);
      loadHistory().then(() => markActive(ev.id));
      break;
  }
}

function line(kind, text, pending = false) {
  const [glyph, label, cls] = KIND[kind] || ["·", kind, "muted"];
  const el = document.createElement("div");
  el.className = `cli-line ${cls}${pending ? " pending" : ""}`;
  el.innerHTML = `<span class="g"></span><span class="k"></span><span class="v"></span>`;
  el.children[0].textContent = glyph; el.children[1].textContent = label; el.children[2].textContent = text;
  traceEl.appendChild(el);
  traceEl.scrollTop = traceEl.scrollHeight;
  return el;
}
function resolve(el, kind, text) {
  const [glyph, label, cls] = KIND[kind] || ["·", kind, ""];
  el.className = `cli-line ${cls}`;
  el.children[0].textContent = glyph; el.children[1].textContent = label; el.children[2].textContent = text;
}

function addSource(url, title, read = false, domain = "") {
  if (!/^https?:\/\//i.test(url || "")) return;
  const found = state.sources.get(url);
  if (found) { if (read) markRead(found); return; }
  const li = document.createElement("li");
  li.innerHTML = `<a target="_blank" rel="noopener"><span class="host"></span><span class="t"></span></a>`;
  const a = li.firstElementChild; a.href = url; a.title = title ? `${title}\n${url}` : url;
  a.querySelector(".host").textContent = domain || hostOf(url);
  a.querySelector(".t").textContent = title || url;
  if (read) markRead(li);
  sourcesEl.appendChild(li);
  state.sources.set(url, li);
  $("#src-n").textContent = state.sources.size;
}
function markRead(li) {
  if (li.querySelector(".badge")) return;
  li.firstElementChild.insertAdjacentHTML("beforeend", `<span class="badge">READ</span>`);
  sourcesEl.prepend(li);
}

/* ---------- market data ---------- */
function dataRow(rawUrl, name, tag, value, sub, change) {
  const url = /^https:\/\//.test(rawUrl || "") ? rawUrl : null;
  const li = document.createElement("li");
  const el = document.createElement(url ? "a" : "div");
  el.className = "data-row";
  if (url) { el.href = url; el.target = "_blank"; el.rel = "noopener noreferrer"; }
  el.innerHTML = `<span class="data-name"></span><span class="data-val"></span><span class="data-sub"></span>`;
  const n = el.children[0]; n.textContent = name;
  if (tag) { const t = document.createElement("small"); t.textContent = tag; n.appendChild(t); }
  el.children[1].textContent = value;
  if (change != null) {
    const c = document.createElement("span");
    c.className = change >= 0 ? "up" : "down";
    c.textContent = ` ${change >= 0 ? "+" : "−"}${Math.abs(change).toFixed(1)}%`;
    el.children[1].appendChild(c);
  }
  el.children[2].textContent = sub;
  li.appendChild(el);
  return li;
}
function renderMarket(items, replay = false) {
  const ul = $("#market-list"); ul.innerHTML = "";
  items.forEach((m) => {
    if (m.kind === "token") {
      const age = m.pairAgeDays != null ? ` · ${m.pairAgeDays}d old` : "";
      ul.appendChild(dataRow(m.url, m.symbol, m.chain, usd(m.priceUsd), `Liq ${usd(m.liquidityUsd)} · Vol ${usd(m.volume24h)} · FDV ${usd(m.fdv)}${age}`, m.change24h));
    } else {
      ul.appendChild(dataRow(m.url, m.name, m.category, usd(m.tvl), `TVL · 7d ${m.change7d != null ? m.change7d.toFixed(1) + "%" : "n/a"} · ${m.chains.join(", ")}`, m.change1d));
    }
  });
  $("#market-time").textContent = replay ? "at run time" : "live";
  $("#market").classList.toggle("hidden", !items.length);
}
function renderHoldings(h) {
  const ul = $("#hold-list"); ul.innerHTML = "";
  (h.items || []).forEach((x) =>
    ul.appendChild(dataRow(x.url, x.symbol, x.chain || "solana", usd(x.valueUsd), `${fmtAmount(x.amount)} ${x.name} · Liq ${usd(x.liquidityUsd)}`, x.change24h))
  );
  $("#hold-total").textContent = usd(h.totalUsd);
  $("#hold-src").textContent = h.source || "";
  $("#holdings").classList.remove("hidden");
}

/* ---------- report rendering ---------- */
function scheduleRender() {
  if (state.raf) return;
  state.raf = requestAnimationFrame(() => { state.raf = 0; renderReport(); });
}
function renderReport(final = false) {
  const md = state.buffer;
  report.innerHTML = md ? DOMPurify.sanitize(marked.parse(md)) : "";
  report.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
  report.classList.toggle("writing", !final && md.length > 0);
  if (final && md) enhance();
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
  const m = state.buffer.match(/Confidence:?\s*\**\s*(\d{1,3})\s*\/\s*100/i);
  if (m) {
    const p = Math.min(100, +m[1]);
    $("#conf-n").textContent = p;
    $("#confidence").classList.remove("hidden");
    requestAnimationFrame(() => ($("#conf-bar").style.width = p + "%"));
  }
  const fu = h2s.find((h) => /follow[- ]?up/i.test(h.textContent));
  const list = fu?.nextElementSibling;
  if (list && (list.tagName === "UL" || list.tagName === "OL")) {
    const wrap = document.createElement("div"); wrap.className = "followups";
    [...list.querySelectorAll("li")].forEach((li) => {
      const text = li.textContent.trim();
      const b = document.createElement("button"); b.type = "button"; b.className = "chip"; b.textContent = text;
      b.onclick = async () => { if (state.running || (!account && !(await signIn()))) return; history.pushState({}, "", "/app"); showComposer(); q.value = text; form.requestSubmit(); };
      wrap.appendChild(b);
    });
    list.replaceWith(wrap);
  }
}

/* ---------- history ---------- */
let historyItems = [];
async function loadHistory() {
  if (!account) return;
  const res = await api("/api/reports").catch(() => null);
  const items = res && res.ok ? await res.json() : [];
  historyItems = items;
  if (!$("#history-page").classList.contains("hidden")) renderHistoryPage();
  const ul = $("#history");
  ul.innerHTML = "";
  items.forEach((r) => {
    const li = document.createElement("li");
    li.innerHTML = `<a><span class="q"></span><span class="d"></span></a>`;
    const a = li.firstElementChild;
    a.href = `/r/${r.id}`; a.dataset.id = r.id;
    a.querySelector(".q").textContent = r.question;
    a.querySelector(".d").textContent = `${r.depth} · ${fmtDate(r.created_at)}`;
    a.onclick = (e) => { e.preventDefault(); navigate(`/r/${r.id}`); };
    ul.appendChild(li);
  });
  $("#history-empty").classList.toggle("hidden", items.length > 0 || !account);
  if (state?.id) markActive(state.id);
}
/* ---------- history page ---------- */
async function showHistoryPage() {
  showView("#history-page");
  document.title = "History · Scout";
  markActive(null);
  renderHistoryPage();
  await loadHistory();
}
function renderHistoryPage() {
  const needle = $("#hp-search").value.trim().toLowerCase();
  const items = historyItems.filter((r) => !needle || r.question.toLowerCase().includes(needle));
  const ul = $("#hp-list");
  ul.innerHTML = "";
  items.forEach((r) => {
    const li = document.createElement("li");
    li.className = "hp-item card";
    li.innerHTML = `<a class="hp-open"><span class="hp-q"></span><span class="hp-meta mono"></span></a>
      <div class="hp-actions"><button type="button" class="btn btn-ghost btn-sm" data-act="share">Share</button><button type="button" class="btn btn-ghost btn-sm" data-act="delete">Delete</button></div>`;
    const a = li.querySelector(".hp-open");
    a.href = `/r/${r.id}`;
    a.querySelector(".hp-q").textContent = r.question;
    a.querySelector(".hp-meta").textContent = `${r.depth} · ${fmtDate(r.created_at)}`;
    a.onclick = (e) => { e.preventDefault(); navigate(`/r/${r.id}`); };
    li.querySelector('[data-act="share"]').onclick = () => copyText(`${location.origin}/r/${r.id}`, "Share link copied");
    li.querySelector('[data-act="delete"]').onclick = async () => {
      if (!confirm("Delete this report? The share link will stop working.")) return;
      const res = await api(`/api/reports/${encodeURIComponent(r.id)}`, { method: "DELETE" });
      if (!res.ok) return toast("Couldn't delete the report");
      toast("Report deleted");
      historyItems = historyItems.filter((x) => x.id !== r.id);
      renderHistoryPage();
      loadHistory();
    };
    ul.appendChild(li);
  });
  $("#hp-count").textContent = historyItems.length ? `${items.length} of ${historyItems.length}` : "";
  $("#hp-empty").classList.toggle("hidden", historyItems.length > 0);
  $("#hp-nomatch").classList.toggle("hidden", !(historyItems.length && !items.length));
}
$("#hp-search").addEventListener("input", renderHistoryPage);

function markActive(id) {
  document.querySelectorAll("#history a").forEach((a) => a.classList.toggle("active", a.dataset.id === id));
}

/* ---------- actions ---------- */
function setActions(on) {
  $("#copy").disabled = $("#download").disabled = !on;
  $("#share").disabled = !(on && state.id);
  $("#delete").classList.toggle("hidden", !(state.id && state.owner));
}
$("#share").onclick = () => copyText(`${location.origin}/r/${state.id}`, "Share link copied");
$("#copy").onclick = () => copyText(state.buffer, "Report copied");
$("#download").onclick = () => {
  const md = `# ${state.question}\n\n${state.buffer}\n`;
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob([md], { type: "text/markdown" })),
    download: `${slug(state.question)}.md`,
  });
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
$("#delete").onclick = async () => {
  if (!state.id || !confirm("Delete this report? The share link will stop working.")) return;
  const res = await api(`/api/reports/${state.id}`, { method: "DELETE" });
  if (!res.ok) return toast("Couldn't delete the report");
  toast("Report deleted");
  history.pushState({}, "", "/app"); showComposer(); loadHistory();
};

async function copyText(text, msg) {
  try { await navigator.clipboard.writeText(text); toast(msg); } catch { toast("Copy failed"); }
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), 1800);
}
function showError(text) { const e = $("#error"); e.textContent = text; e.classList.remove("hidden"); }

/* ---------- utils ---------- */
function updateStats(time) { $("#stats").textContent = `${state.searches} search · ${state.reads} read · ${time}`; }
function elapsed() { return ((performance.now() - state.t0) / 1000).toFixed(1) + "s"; }
function clip(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } }
function prettyUrl(u) { try { const x = new URL(u); return x.hostname.replace(/^www\./, "") + x.pathname.replace(/\/$/, ""); } catch { return u; } }
function fmtDate(ts) { return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
function usd(v) {
  if (v == null || !Number.isFinite(v)) return "n/a";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return a < 1 ? `$${v.toPrecision(3)}` : `$${v.toFixed(2)}`;
}
function fmtAmount(n) { return n >= 1000 ? Math.round(n).toLocaleString() : n.toPrecision(4); }
function slug(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "report"; }
