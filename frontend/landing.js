// "Get started": signed-in users go straight to the app; everyone else signs in with a wallet first.
document.querySelectorAll("[data-start]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    closeMenu();
    if (await XerienWallet.me()) return location.assign("/app");
    try {
      await XerienWallet.signIn();
      location.assign("/app");
    } catch { /* modal closed */ }
  })
);
XerienWallet.me().then((m) => {
  if (!m) return;
  document.querySelectorAll("[data-start]").forEach((b) => (b.textContent = b.textContent.includes("→") ? "Open Scout →" : "Open Scout"));
});

// Mobile menu
const toggle = document.getElementById("nav-toggle");
const menu = document.getElementById("nav-menu");
function closeMenu() {
  menu.hidden = true;
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-label", "Open menu");
}
toggle.addEventListener("click", () => {
  const open = menu.hidden;
  menu.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
});
menu.querySelectorAll("a").forEach((a) => a.addEventListener("click", closeMenu));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
matchMedia("(min-width: 901px)").addEventListener("change", (e) => { if (e.matches) closeMenu(); });

// Hairline under the nav once the page scrolls
const nav = document.getElementById("nav");
const onScroll = () => nav.classList.toggle("scrolled", scrollY > 4);
addEventListener("scroll", onScroll, { passive: true });
onScroll();
