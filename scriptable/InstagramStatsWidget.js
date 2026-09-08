// Instagram Studio — Stats widget for Scriptable
// Uses the existing /api/analytics/dashboard endpoint.
// Set STUDIO_URL, run once in Scriptable, then add as a medium widget.

const STUDIO_URL = "https://TON-SERVICE.onrender.com"; // no trailing slash
const KEYCHAIN_KEY = "instagram-studio-access-code"; // shared with the first widget
const PERIOD_DAYS = 30;

async function login() {
  let code = Keychain.contains(KEYCHAIN_KEY) ? Keychain.get(KEYCHAIN_KEY) : "";
  if (!code && config.runsInApp) {
    const a = new Alert();
    a.title = "Instagram Studio Stats";
    a.message = "Code d’accès du Studio (stocké dans le trousseau iOS).";
    a.addSecureTextField("Code d’accès");
    a.addAction("Enregistrer");
    a.addCancelAction("Annuler");
    if (await a.present() === -1) throw new Error("Code manquant");
    code = a.textFieldValue(0);
    Keychain.set(KEYCHAIN_KEY, code);
  }
  if (!code) throw new Error("Lance d’abord le script dans Scriptable.");

  const r = new Request(`${STUDIO_URL}/login`);
  r.method = "POST";
  r.headers = { "Content-Type": "application/x-www-form-urlencoded" };
  r.body = `access_code=${encodeURIComponent(code)}&next=${encodeURIComponent("/api/analytics/dashboard")}`;
  await r.load();
  const cookies = r.response.cookies || [];
  const cookie = cookies.map(c => `${c.name}=${c.value}`).join("; ");
  if (!cookie) throw new Error("Connexion impossible. Vérifie l’URL et le code.");
  return cookie;
}

async function getJSON(path, cookie) {
  const r = new Request(`${STUDIO_URL}${path}`);
  r.headers = { Cookie: cookie, Accept: "application/json" };
  const data = await r.loadJSON();
  if (!data?.ok) throw new Error(data?.error || "Statistiques indisponibles");
  return data;
}

function compact(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1000000) return `${(n / 1000000).toFixed(n >= 10000000 ? 0 : 1)} M`;
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)} k`;
  return Math.round(n).toLocaleString("fr-FR");
}

function refreshTime() {
  const df = new DateFormatter();
  df.locale = "fr_FR";
  df.dateFormat = "HH:mm";
  return df.string(new Date());
}

function metric(stack, value, label, suffix = "") {
  const v = stack.addText(`${value}${suffix}`);
  v.font = Font.boldSystemFont(19);
  v.lineLimit = 1;
  const l = stack.addText(label);
  l.font = Font.mediumSystemFont(8);
  l.textOpacity = 0.48;
}

function postLabel(post) {
  const text = String(post?.hook || post?.caption || post?.title || "Meilleure publication")
    .replace(/\s+/g, " ").trim();
  return text.length > 32 ? `${text.slice(0, 31)}…` : text;
}

async function buildWidget() {
  const w = new ListWidget();
  w.url = `${STUDIO_URL}/#stats`;
  w.refreshAfterDate = new Date(Date.now() + 30 * 60 * 1000);
  w.setPadding(13, 14, 9, 14);

  try {
    const cookie = await login();
    const data = await getJSON(`/api/analytics/dashboard?period_days=${PERIOD_DAYS}`, cookie);
    const account = data.account || {};
    const profile = account.profile || {};
    const period = account.period || {};
    const m = period.metrics || {};
    const available = new Set(Array.isArray(period.available_metrics) ? period.available_metrics : []);
    const exact = new Set(Array.isArray(period.exact_metrics) ? period.exact_metrics : period.available_metrics || []);
    const has = name => available.has(name) && exact.has(name);

    const header = w.addStack();
    const title = header.addText("INSTAGRAM");
    title.font = Font.boldSystemFont(12);
    header.addSpacer();
    const range = header.addText(`${period.days || PERIOD_DAYS} JOURS`);
    range.font = Font.semiboldSystemFont(9);
    range.textOpacity = 0.5;

    w.addSpacer(9);

    const row = w.addStack();
    row.layoutHorizontally();

    const followers = row.addStack();
    followers.layoutVertically();
    metric(followers, compact(profile.followers_count), "FOLLOWERS");
    row.addSpacer();

    const views = row.addStack();
    views.layoutVertically();
    metric(views, has("views") ? compact(m.views) : "—", "VUES");
    row.addSpacer();

    const reach = row.addStack();
    reach.layoutVertically();
    metric(reach, has("reach") ? compact(m.reach) : "—", "PORTÉE");
    row.addSpacer();

    const engagement = row.addStack();
    engagement.layoutVertically();
    const engagementAvailable = has("total_interactions") && (has("reach") || has("views"));
    metric(engagement, engagementAvailable ? Number(period.engagement_rate || 0).toFixed(1) : "—", "ENGAGEMENT", engagementAvailable ? "%" : "");

    w.addSpacer(7);

    const bottom = w.addStack();
    bottom.layoutHorizontally();

    const net = bottom.addStack();
    net.layoutVertically();
    const netValue = has("net_follows") ? Number(m.net_follows || 0) : null;
    const netText = netValue === null ? "—" : `${netValue > 0 ? "+" : ""}${compact(netValue)}`;
    const netNumber = net.addText(netText);
    netNumber.font = Font.boldSystemFont(13);
    const netLabel = net.addText("ABONNÉS NETS");
    netLabel.font = Font.mediumSystemFont(7);
    netLabel.textOpacity = 0.45;

    const posts = Array.isArray(data.top_posts) ? data.top_posts : [];
    const best = [...posts].sort((a, b) => Number(b.views || 0) - Number(a.views || 0))[0];
    if (best) {
      bottom.addSpacer(14);
      const bestStack = bottom.addStack();
      bestStack.layoutVertically();
      const bestTitle = bestStack.addText(`🔥 ${postLabel(best)}`);
      bestTitle.font = Font.semiboldSystemFont(10);
      bestTitle.lineLimit = 1;
      const bestMeta = bestStack.addText(`${compact(best.views)} vues • ${Number(best.engagement_rate || 0).toFixed(1)} % engagement`);
      bestMeta.font = Font.systemFont(8);
      bestMeta.textOpacity = 0.55;
    }

    w.addSpacer(5);
    const refreshRow = w.addStack();
    refreshRow.addSpacer();
    const refreshed = refreshRow.addText(`↻ Widget actualisé à ${refreshTime()}`);
    refreshed.font = Font.mediumSystemFont(7);
    refreshed.textOpacity = 0.38;
  } catch (e) {
    const title = w.addText("INSTAGRAM STATS");
    title.font = Font.boldSystemFont(13);
    w.addSpacer(10);
    const err = w.addText("Stats indisponibles");
    err.font = Font.boldSystemFont(15);
    const detail = w.addText(String(e.message || e));
    detail.font = Font.systemFont(9);
    detail.textOpacity = 0.55;
    detail.lineLimit = 3;

    w.addSpacer();
    const refreshRow = w.addStack();
    refreshRow.addSpacer();
    const refreshed = refreshRow.addText(`↻ Tentative à ${refreshTime()}`);
    refreshed.font = Font.mediumSystemFont(7);
    refreshed.textOpacity = 0.38;
  }
  return w;
}

const widget = await buildWidget();
Script.setWidget(widget);
if (!config.runsInWidget) await widget.presentMedium();
Script.complete();
