/* Xerien wallet sign-in.
 * Discovers installed wallets (Solana Wallet Standard, EIP-6963, legacy injected
 * providers), connects, signs the server's challenge and opens a session. */
(() => {
  const found = new Map(); // key -> { key, name, icon, chain, connect() -> {address, sign(msg)} }
  const listeners = new Set();
  const enc = new TextEncoder();

  const add = (w) => {
    if (!found.has(w.key)) { found.set(w.key, w); listeners.forEach((fn) => fn()); }
  };
  const safeIcon = (src) => (typeof src === "string" && /^data:image\/(svg\+xml|png|webp|jpeg|gif)[;,]/.test(src) ? src : "");

  /* ---- Solana: Wallet Standard ---- */
  function registerStandard(wallet) {
    const f = wallet.features || {};
    const solana = (wallet.chains || []).some((c) => c.startsWith("solana:"));
    if (!solana || !f["standard:connect"] || !f["solana:signMessage"]) return;
    add({
      key: `sol:${wallet.name}`, name: wallet.name, icon: safeIcon(wallet.icon), chain: "solana",
      async connect() {
        const res = await f["standard:connect"].connect();
        const account = (res && res.accounts && res.accounts[0]) || wallet.accounts[0];
        if (!account) throw new Error("No account was shared by the wallet");
        return {
          address: account.address,
          async sign(message) {
            const [out] = await f["solana:signMessage"].signMessage({ account, message: enc.encode(message) });
            return b64(out.signature);
          },
        };
      },
    });
  }
  const standardApi = { register: (...ws) => { ws.forEach(registerStandard); return () => {}; } };
  window.addEventListener("wallet-standard:register-wallet", (e) => { try { e.detail(standardApi); } catch {} });
  try { window.dispatchEvent(new CustomEvent("wallet-standard:app-ready", { detail: standardApi })); } catch {}

  /* ---- Solana: legacy injected providers ---- */
  function legacySolana() {
    const candidates = [
      ["Phantom", window.phantom && window.phantom.solana],
      ["Solflare", window.solflare],
      ["Backpack", window.backpack],
    ];
    for (const [name, p] of candidates) {
      if (!p || typeof p.connect !== "function" || found.has(`sol:${name}`)) continue;
      add({
        key: `sol:${name}`, name, icon: "", chain: "solana",
        async connect() {
          const r = await p.connect();
          const pk = (r && r.publicKey) || p.publicKey;
          return {
            address: pk.toString(),
            async sign(message) {
              const out = await p.signMessage(enc.encode(message), "utf8");
              return b64(out.signature || out);
            },
          };
        },
      });
    }
  }

  /* ---- EVM: EIP-6963 + legacy window.ethereum ---- */
  function registerEvm(info, provider) {
    add({
      key: `evm:${info.rdns || info.name}`, name: info.name, icon: safeIcon(info.icon), chain: "ethereum",
      async connect() {
        const [address] = await provider.request({ method: "eth_requestAccounts" });
        if (!address) throw new Error("No account was shared by the wallet");
        return {
          address,
          sign: (message) => provider.request({ method: "personal_sign", params: [hex(message), address] }),
        };
      },
    });
  }
  window.addEventListener("eip6963:announceProvider", (e) => {
    if (e.detail && e.detail.info && e.detail.provider) registerEvm(e.detail.info, e.detail.provider);
  });
  window.dispatchEvent(new Event("eip6963:requestProvider"));

  function legacyEvm() {
    const eth = window.ethereum;
    if (!eth || [...found.values()].some((w) => w.chain === "ethereum")) return;
    registerEvm({ name: eth.isMetaMask ? "MetaMask" : "Browser wallet", rdns: "injected" }, eth);
  }

  // Legacy providers inject late; sweep a few times after load.
  const sweep = () => { legacySolana(); legacyEvm(); };
  window.addEventListener("load", () => { sweep(); setTimeout(sweep, 400); setTimeout(sweep, 1200); });

  /* ---- server session ---- */
  async function post(path, body) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    return data;
  }
  async function me() {
    const res = await fetch("/api/auth/me").catch(() => null);
    return res && res.ok ? res.json() : null;
  }
  async function signOut() { await post("/api/auth/logout"); }

  async function signInWith(wallet, setStatus) {
    setStatus(`Approve the connection in ${wallet.name}…`);
    const session = await wallet.connect();
    setStatus("Preparing a sign-in request…");
    const { nonce, message } = await post("/api/auth/challenge", { chain: wallet.chain, address: session.address });
    setStatus(`Sign the message in ${wallet.name}. It's free and sends no transaction.`);
    const signature = await session.sign(message);
    setStatus("Verifying signature…");
    return post("/api/auth/verify", { nonce, signature });
  }

  /* ---- modal ---- */
  const INSTALL = [
    { name: "Phantom", url: "https://phantom.com/download", chain: "Solana · EVM" },
    { name: "Solflare", url: "https://solflare.com/download", chain: "Solana" },
    { name: "MetaMask", url: "https://metamask.io/download/", chain: "EVM" },
  ];
  let modal;

  function buildModal() {
    modal = document.createElement("div");
    modal.className = "wm-backdrop hidden";
    modal.innerHTML = `
      <div class="wm" role="dialog" aria-modal="true" aria-labelledby="wm-title">
        <div class="wm-head">
          <div>
            <p class="eyebrow muted" style="margin:0 0 8px">Sign in</p>
            <h2 id="wm-title" class="wm-title">Connect a wallet</h2>
          </div>
          <button class="wm-x" type="button" aria-label="Close">×</button>
        </div>
        <p class="body wm-sub">Your wallet is your account. Signing in is free and never sends a transaction.</p>
        <ul class="wm-list"></ul>
        <p class="wm-status mono" aria-live="polite"></p>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
    modal.querySelector(".wm-x").onclick = close;
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.classList.contains("hidden")) close(); });
  }

  let pending = null; // { resolve, reject, busy }
  function renderList() {
    const ul = modal.querySelector(".wm-list");
    ul.innerHTML = "";
    const wallets = [...found.values()].sort((a, b) => a.name.localeCompare(b.name) || a.chain.localeCompare(b.chain));
    wallets.forEach((w) => {
      const li = document.createElement("li");
      li.innerHTML = `<button type="button" class="wm-row"><span class="wm-icon"></span><span class="wm-name"></span><span class="wm-chain mono"></span><span class="wm-tag mono">Detected</span></button>`;
      const icon = li.querySelector(".wm-icon");
      if (w.icon) { const img = new Image(); img.src = w.icon; img.alt = ""; icon.appendChild(img); } else icon.textContent = w.name[0];
      li.querySelector(".wm-name").textContent = w.name;
      li.querySelector(".wm-chain").textContent = w.chain === "solana" ? "Solana" : "EVM";
      li.firstElementChild.onclick = () => choose(w);
      ul.appendChild(li);
    });
    INSTALL.filter((i) => !wallets.some((w) => w.name.toLowerCase().includes(i.name.toLowerCase()))).forEach((i) => {
      const li = document.createElement("li");
      li.innerHTML = `<a class="wm-row install" target="_blank" rel="noopener"><span class="wm-icon"></span><span class="wm-name"></span><span class="wm-chain mono"></span><span class="wm-tag mono">Install ↗</span></a>`;
      li.firstElementChild.href = i.url;
      li.querySelector(".wm-icon").textContent = i.name[0];
      li.querySelector(".wm-name").textContent = i.name;
      li.querySelector(".wm-chain").textContent = i.chain;
      ul.appendChild(li);
    });
  }
  listeners.add(() => { if (modal && !modal.classList.contains("hidden") && !(pending && pending.busy)) renderList(); });

  function status(text, isError = false) {
    const s = modal.querySelector(".wm-status");
    s.textContent = text; s.classList.toggle("err", isError);
  }

  async function choose(wallet) {
    if (!pending || pending.busy) return;
    pending.busy = true;
    modal.querySelectorAll(".wm-row").forEach((b) => (b.disabled = true));
    try {
      const account = await signInWith(wallet, (t) => status(t));
      status("Signed in.");
      const p = pending; pending = null;
      modal.classList.add("hidden");
      p.resolve(account);
    } catch (err) {
      pending.busy = false;
      modal.querySelectorAll(".wm-row").forEach((b) => (b.disabled = false));
      const msg = String((err && err.message) || err);
      status(/reject|denied|cancel|4001/i.test(msg) ? "Request cancelled in the wallet." : msg, true);
    }
  }

  function close() {
    if (pending && pending.busy) return;
    modal.classList.add("hidden");
    if (pending) { pending.reject(new Error("closed")); pending = null; }
  }

  /** Opens the picker. If exactly one wallet is installed, its popup opens right away. */
  function signIn() {
    if (!modal) buildModal();
    sweep();
    return new Promise((resolve, reject) => {
      pending = { resolve, reject, busy: false };
      status("");
      renderList();
      modal.classList.remove("hidden");
      setTimeout(() => {
        // One wallet brand installed (e.g. Phantom announces Solana + EVM): open it directly.
        const wallets = [...found.values()];
        const brands = new Set(wallets.map((w) => w.name));
        if (pending && !pending.busy && brands.size === 1) choose(wallets.find((w) => w.chain === "solana") || wallets[0]);
      }, 250); // give late-injecting wallets a moment to announce
    });
  }

  /* ---- utils ---- */
  function b64(bytes) {
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes.data || bytes);
    let s = ""; u8.forEach((b) => (s += String.fromCharCode(b)));
    return btoa(s);
  }
  function hex(str) {
    return "0x" + Array.from(enc.encode(str), (b) => b.toString(16).padStart(2, "0")).join("");
  }
  function short(addr) { return addr.length > 12 ? `${addr.slice(0, 4)}…${addr.slice(-4)}` : addr; }

  window.XerienWallet = { signIn, me, signOut, short };
})();
