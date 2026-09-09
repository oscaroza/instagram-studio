// Instagram Studio — Sync widget for Scriptable
// Small widget: tap it to launch a full Instagram analytics sync.
// Uses the same Studio access code stored in iOS Keychain as the other widgets.

const STUDIO_URL = "https://TON-SERVICE.onrender.com"; // replace with your Render URL
const KEYCHAIN_KEY = "instagram-studio-access-code";
const LAST_SYNC_KEY = "instagram-studio-last-sync";

async function login() {
  let code = Keychain.contains(KEYCHAIN_KEY) ? Keychain.get(KEYCHAIN_KEY) : "";
  if (!code && config.runsInApp) {
    const a = new Alert();
    a.title = "Instagram Studio";
    a.message = "Code d’accès du Studio (stocké uniquement dans le trousseau iOS).";
    a.addSecureTextField("Code d’accès");
    a.addAction("Enregistrer");
    a.addCancelAction("Annuler");
    if (await a.present() === -1) throw new Error("Code manquant");
    code = a.textFieldValue(0);
    Keychain.set(KEYCHAIN_KEY, code);
  }
  if (!code) throw new Error("Lance d’abord le script dans Scriptable pour enregistrer le code.");

  const r = new Request(`${STUDIO_URL}/login`);
  r.method = "POST";
  r.headers = { "Content-Type": "application/x-www-form-urlencoded" };
  r.body = `access_code=${encodeURIComponent(code)}&next=${encodeURIComponent("/api/analytics/sync")}`;
  await r.load();

  const cookies = r.response.cookies || [];
  const cookie = cookies.map(c => `${c.name}=${c.value}`).join("; ");
  if (!cookie) throw new Error("Connexion impossible. Vérifie STUDIO_URL et le code.");
  return cookie;
}

function time(value = new Date()) {
  const df = new DateFormatter();
  df.locale = "fr_FR";
  df.dateFormat = "HH:mm";
  return df.string(value);
}

async function syncInstagram(cookie) {
  const r = new Request(`${STUDIO_URL}/api/analytics/sync`);
  r.method = "POST";
  r.headers = { Cookie: cookie, Accept: "application/json" };
  r.timeoutInterval = 120;
  const data = await r.loadJSON();
  if (!data?.ok) throw new Error(data?.error || "Synchronisation impossible");
  return data.sync || {};
}

function makeWidget(state = "ready", message = "") {
  const w = new ListWidget();
  w.setPadding(14, 14, 14, 14);
  w.refreshAfterDate = new Date(Date.now() + 30 * 60 * 1000);

  const spacer = w.addSpacer();

  const icon = w.addText(state === "success" ? "✓" : state === "error" ? "!" : "↻");
  icon.font = Font.boldSystemFont(30);
  icon.centerAlignText();

  w.addSpacer(5);

  const title = w.addText(state === "success" ? "SYNCHRONISÉ" : state === "error" ? "ERREUR" : "SYNC INSTAGRAM");
  title.font = Font.boldSystemFont(12);
  title.centerAlignText();
  title.lineLimit = 1;

  w.addSpacer(4);

  let detail = message;
  if (!detail && Keychain.contains(LAST_SYNC_KEY)) {
    const d = new Date(Keychain.get(LAST_SYNC_KEY));
    if (!Number.isNaN(d.getTime())) detail = `Dernière sync • ${time(d)}`;
  }
  if (!detail) detail = "Appuie pour actualiser";

  const subtitle = w.addText(detail);
  subtitle.font = Font.mediumSystemFont(8);
  subtitle.textOpacity = 0.5;
  subtitle.centerAlignText();
  subtitle.lineLimit = 2;

  w.addSpacer();
  return w;
}

// Home Screen widget: tapping it launches this same script in Scriptable.
// In the app, the script performs the sync.
const scriptName = Script.name();
const runURL = `scriptable:///run?scriptName=${encodeURIComponent(scriptName)}`;

if (config.runsInWidget) {
  const widget = makeWidget("ready");
  widget.url = runURL;
  Script.setWidget(widget);
  Script.complete();
} else {
  let widget;
  try {
    const cookie = await login();
    const progress = new Alert();
    progress.title = "Instagram Studio";
    progress.message = "Synchronisation Instagram en cours…";

    const result = await syncInstagram(cookie);
    const now = new Date();
    Keychain.set(LAST_SYNC_KEY, now.toISOString());

    widget = makeWidget("success", `Terminée • ${time(now)}`);
    widget.url = runURL;
    Script.setWidget(widget);
    await widget.presentSmall();
  } catch (e) {
    widget = makeWidget("error", String(e.message || e));
    widget.url = runURL;
    Script.setWidget(widget);
    await widget.presentSmall();
  }
  Script.complete();
}
