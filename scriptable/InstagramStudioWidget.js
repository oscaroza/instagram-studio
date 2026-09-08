// Instagram Studio — Scriptable iOS widget
// 1) Install Scriptable on iOS
// 2) Create a script and paste this file
// 3) Set STUDIO_URL below
// 4) Run once inside Scriptable: it will ask for the Studio access code
//    and store it in the iOS Keychain (not in this file / GitHub).
// 5) Add a Scriptable widget to the Home Screen and select this script.

const STUDIO_URL = "https://TON-SERVICE.onrender.com"; // no trailing slash
const KEYCHAIN_KEY = "instagram-studio-access-code";

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
  r.body = `access_code=${encodeURIComponent(code)}&next=${encodeURIComponent("/api/v2/status")}`;
  await r.load();

  const cookies = r.response.cookies || [];
  const cookie = cookies.map(c => `${c.name}=${c.value}`).join("; ");
  if (!cookie) throw new Error("Connexion impossible. Vérifie STUDIO_URL et le code.");
  return cookie;
}

async function json(path, cookie) {
  const r = new Request(`${STUDIO_URL}${path}`);
  r.headers = { Cookie: cookie, Accept: "application/json" };
  const data = await r.loadJSON();
  if (!data?.ok) throw new Error(data?.error || `Erreur ${path}`);
  return data;
}

function gb(bytes) {
  return (Number(bytes || 0) / 1024 / 1024 / 1024).toFixed(1);
}

function formatDate(value) {
  if (!value) return "";
  const d = new Date(value);
  const df = new DateFormatter();
  df.locale = "fr_FR";
  df.dateFormat = "EEE d MMM • HH:mm";
  return df.string(d);
}

function icon(kind) {
  return ({ reel: "🎬", photo: "📷", carousel: "▣", story: "◉" })[kind] || "●";
}

async function buildWidget() {
  const w = new ListWidget();
  w.url = STUDIO_URL;
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  w.setPadding(14, 14, 12, 14);

  try {
    const cookie = await login();
    const now = new Date();
    const end = new Date(Date.now() + 90 * 24 * 3600 * 1000);
    const [status, calendar] = await Promise.all([
      json("/api/v2/status", cookie),
      json(`/api/publications/calendar?start=${encodeURIComponent(now.toISOString())}&end=${encodeURIComponent(end.toISOString())}`, cookie),
    ]);

    const upcoming = (calendar.items || [])
      .filter(x => x.status === "scheduled" && x.scheduled_for && new Date(x.scheduled_for) >= now)
      .sort((a, b) => new Date(a.scheduled_for) - new Date(b.scheduled_for));
    const next = upcoming[0];

    const head = w.addStack();
    const title = head.addText("INSTAGRAM STUDIO");
    title.font = Font.boldSystemFont(13);
    head.addSpacer();
    const healthy = status.mongodb_ready && status.media_storage_ready;
    const state = head.addText(healthy ? "● OK" : "● !");
    state.font = Font.semiboldSystemFont(10);

    w.addSpacer(12);

    if (next) {
      const label = w.addText("PROCHAINE PUBLICATION");
      label.font = Font.mediumSystemFont(9);
      label.textOpacity = 0.5;
      w.addSpacer(3);
      const name = w.addText(`${icon(next.media_kind)} ${next.title || "Publication Instagram"}`);
      name.font = Font.boldSystemFont(15);
      name.lineLimit = 1;
      const when = w.addText(formatDate(next.scheduled_for));
      when.font = Font.systemFont(11);
      when.textOpacity = 0.65;
    } else {
      const empty = w.addText("Aucune publication programmée");
      empty.font = Font.semiboldSystemFont(13);
    }

    w.addSpacer();

    const footer = w.addStack();
    footer.layoutHorizontally();
    const count = footer.addText(`${upcoming.length} programmée${upcoming.length > 1 ? "s" : ""}`);
    count.font = Font.systemFont(10);
    count.textOpacity = 0.65;
    footer.addSpacer();
    const storage = footer.addText(`${status.media_storage_label || "Stockage"} ${gb(status.media_storage_usage_bytes)} / ${gb(status.media_storage_limit_bytes)} Go`);
    storage.font = Font.systemFont(10);
    storage.textOpacity = 0.65;
  } catch (e) {
    const title = w.addText("INSTAGRAM STUDIO");
    title.font = Font.boldSystemFont(13);
    w.addSpacer(12);
    const err = w.addText("Widget indisponible");
    err.font = Font.boldSystemFont(15);
    const detail = w.addText(String(e.message || e));
    detail.font = Font.systemFont(10);
    detail.textOpacity = 0.6;
    detail.lineLimit = 3;
  }
  return w;
}

const widget = await buildWidget();
Script.setWidget(widget);
if (!config.runsInWidget) await widget.presentMedium();
Script.complete();
