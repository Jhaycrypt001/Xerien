// "Get started": signed-in users go straight to the app; everyone else signs in with a wallet first.
document.querySelectorAll("[data-start]").forEach((btn) =>
  btn.addEventListener("click", async () => {
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
