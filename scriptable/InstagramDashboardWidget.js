// Instagram Studio — Extra Large iPad Dashboard for Scriptable
// Shows upcoming publications, analytics, service/storage status and a Sync button.

const STUDIO_URL = "https://TON-SERVICE.onrender.com"; // replace with your Render URL
const KEYCHAIN_KEY = "instagram-studio-access-code";
const LAST_SYNC_KEY = "instagram-studio-last-sync";
const PERIOD_DAYS = 30;

async function login() {
  let code = Keychain.contains(KEYCHAIN_KEY) ? Keychain.get(KEYCHAIN_KEY) : "";
  if (!code && config.runsInApp) {
    const a = new Alert();
    a.title = "Instagram Studio Dashboard";
    a.message = "Code d’accès du Studio";
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

async function json(path, cookie, method = "GET") {
  const r = new Request(`${STUDIO_URL}${path}`);
  r.method = method;
  r.headers = { Cookie: cookie, Accept: "application/json" };
  r.timeoutInterval = 120;
  const data = await r.loadJSON();
  if (!data?.ok) throw new Error(data?.error || `Erreur ${path}`);
  return data;
}

function compact(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1000000) return `${(n / 1000000).toFixed(Math.abs(n) >= 10000000 ? 0 : 1)} M`;
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(Math.abs(n) >= 10000 ? 0 : 1)} k`;
  return Math.round(n).toLocaleString("fr-FR");
}

function gb(bytes) {
  return (Number(bytes || 0) / 1024 / 1024 / 1024).toFixed(1);
}

function clock(date = new Date()) {
  const df = new DateFormatter();
  df.locale = "fr_FR";
  df.dateFormat = "HH:mm";
  return df.string(date);
}

function publicationDate(value) {
  if (!value) return "";
  const df = new DateFormatter();
  df.locale = "fr_FR";
  df.dateFormat = "EEE d MMM • HH:mm";
  return df.string(new Date(value));
}

function kindIcon(kind) {
  return ({ reel: "🎬", photo: "📷", carousel: "▣", story: "◉" })[kind] || "●";
}

function addSectionTitle(stack, text) {
  const t = stack.addText(text);
  t.font = Font.semiboldSystemFont(9);
  t.textOpacity = 0.45;
  return t;
}

function addMetric(parent, value, label) {
  const s = parent.addStack();
  s.layoutVertically();
  const v = s.addText(value);
  v.font = Font.boldSystemFont(23);
  v.lineLimit = 1;
  const l = s.addText(label);
  l.font = Font.mediumSystemFont(8);
  l.textOpacity = 0.45;
  return s;
}

function postLabel(post) {
  const text = String(post?.hook || post?.caption || post?.title || "Meilleure publication").replace(/\s+/g, " ").trim();
  return text.length > 48 ? `${text.slice(0, 47)}…` : text;
}

function syncURL() {
  // This expects the third Scriptable script to be named exactly "Instagram Sync".
  return `scriptable:///run?scriptName=${encodeURIComponent("Instagram Sync")}`;
}

async function buildDashboard() {
  const w = new ListWidget();
  w.url = STUDIO_URL;
  w.refreshAfterDate = new Date(Date.now() + 30 * 60 * 1000);
  w.setPadding(18, 20, 16, 20);

  try {
    const cookie = await login();
    const now = new Date();
    const end = new Date(Date.now() + 90 * 24 * 3600 * 1000);

    const [status, analytics, calendar] = await Promise.all([
      json("/api/v2/status", cookie),
      json(`/api/analytics/dashboard?period_days=${PERIOD_DAYS}`, cookie),
      json(`/api/publications/calendar?start=${encodeURIComponent(now.toISOString())}&end=${encodeURIComponent(end.toISOString())}`, cookie),
    ]);

    const account = analytics.account || {};
    const profile = account.profile || {};
    const period = account.period || {};
    const m = period.metrics || {};
    const available = new Set(Array.isArray(period.available_metrics) ? period.available_metrics : []);
    const exact = new Set(Array.isArray(period.exact_metrics) ? period.exact_metrics : period.available_metrics || []);
    const has = name => available.has(name) && exact.has(name);

    const upcoming = (calendar.items || [])
      .filter(x => x.status === "scheduled" && x.scheduled_for && new Date(x.scheduled_for) >= now)
      .sort((a, b) => new Date(a.scheduled_for) - new Date(b.scheduled_for));

    // HEADER
    const header = w.addStack();
    header.centerAlignContent();
    const brand = header.addText("INSTAGRAM STUDIO");
    brand.font = Font.boldSystemFont(17);
    header.addSpacer();

    const healthy = status.mongodb_ready && status.media_storage_ready;
    const service = header.addText(healthy ? "● SERVICES OK" : "● SERVICE !");
    service.font = Font.semiboldSystemFont(9);
    service.textOpacity = healthy ? 0.65 : 1;

    header.addSpacer(14);
    const sync = header.addText("↻  SYNC INSTAGRAM");
    sync.font = Font.boldSystemFont(10);
    sync.url = syncURL();

    w.addSpacer(15);

    // METRICS ROW
    const metrics = w.addStack();
    metrics.layoutHorizontally();
    addMetric(metrics, compact(profile.followers_count), "FOLLOWERS");
    metrics.addSpacer();
    addMetric(metrics, has("views") ? compact(m.views) : "—", `VUES · ${period.days || PERIOD_DAYS}J`);
    metrics.addSpacer();
    addMetric(metrics, has("reach") ? compact(m.reach) : "—", "PORTÉE");
    metrics.addSpacer();

    const engagementAvailable = has("total_interactions") && (has("reach") || has("views"));
    addMetric(metrics, engagementAvailable ? `${Number(period.engagement_rate || 0).toFixed(1)}%` : "—", "ENGAGEMENT");
    metrics.addSpacer();

    const netValue = has("net_follows") ? Number(m.net_follows || 0) : null;
    addMetric(metrics, netValue === null ? "—" : `${netValue > 0 ? "+" : ""}${compact(netValue)}`, "ABONNÉS NETS");

    w.addSpacer(18);

    // TWO-COLUMN BODY
    const body = w.addStack();
    body.layoutHorizontally();

    // LEFT: UPCOMING PUBLICATIONS
    const left = body.addStack();
    left.layoutVertically();
    addSectionTitle(left, `PROCHAINES PUBLICATIONS · ${upcoming.length} PROGRAMMÉE${upcoming.length > 1 ? "S" : ""}`);
    left.addSpacer(8);

    if (!upcoming.length) {
      const empty = left.addText("Aucune publication programmée");
      empty.font = Font.semiboldSystemFont(13);
      empty.textOpacity = 0.65;
    } else {
      for (const item of upcoming.slice(0, 4)) {
        const line = left.addStack();
        line.layoutVertically();
        const name = line.addText(`${kindIcon(item.media_kind)}  ${item.title || "Publication Instagram"}`);
        name.font = Font.semiboldSystemFont(12);
        name.lineLimit = 1;
        const when = line.addText(publicationDate(item.scheduled_for));
        when.font = Font.systemFont(9);
        when.textOpacity = 0.5;
        left.addSpacer(7);
      }
    }

    body.addSpacer(30);

    // RIGHT: BEST POST + SYSTEM
    const right = body.addStack();
    right.layoutVertically();
    addSectionTitle(right, "MEILLEURE PUBLICATION");
    right.addSpacer(8);

    const posts = Array.isArray(analytics.top_posts) ? analytics.top_posts : [];
    const best = [...posts].sort((a, b) => Number(b.views || 0) - Number(a.views || 0))[0];
    if (best) {
      const bestTitle = right.addText(`🔥 ${postLabel(best)}`);
      bestTitle.font = Font.boldSystemFont(13);
      bestTitle.lineLimit = 2;
      right.addSpacer(3);
      const bestStats = right.addText(`${compact(best.views)} vues  •  ${compact(best.likes)} j’aime  •  ${Number(best.engagement_rate || 0).toFixed(1)}% engagement`);
      bestStats.font = Font.systemFont(9);
      bestStats.textOpacity = 0.55;
    } else {
      const none = right.addText("Pas encore de données");
      none.font = Font.semiboldSystemFont(12);
      none.textOpacity = 0.6;
    }

    right.addSpacer(17);
    addSectionTitle(right, "STUDIO");
    right.addSpacer(6);
    const storage = right.addText(`${status.media_storage_label || "Stockage"}   ${gb(status.media_storage_usage_bytes)} / ${gb(status.media_storage_limit_bytes)} Go`);
    storage.font = Font.semiboldSystemFont(10);
    const db = right.addText(`MongoDB ${status.mongodb_ready ? "✓" : "!"}   •   Médias ${status.media_storage_ready ? "✓" : "!"}`);
    db.font = Font.systemFont(9);
    db.textOpacity = 0.55;

    if (Keychain.contains(LAST_SYNC_KEY)) {
      const last = new Date(Keychain.get(LAST_SYNC_KEY));
      if (!Number.isNaN(last.getTime())) {
        const lastSync = right.addText(`Dernière sync manuelle : ${clock(last)}`);
        lastSync.font = Font.systemFont(9);
        lastSync.textOpacity = 0.55;
      }
    }

    w.addSpacer();

    // FOOTER
    const footer = w.addStack();
    const hint = footer.addText("Touchez ↻ SYNC INSTAGRAM pour actualiser les données Meta");
    hint.font = Font.mediumSystemFont(8);
    hint.textOpacity = 0.38;
    footer.addSpacer();
    const refreshed = footer.addText(`Widget actualisé à ${clock()}`);
    refreshed.font = Font.mediumSystemFont(8);
    refreshed.textOpacity = 0.38;
  } catch (e) {
    const title = w.addText("INSTAGRAM STUDIO");
    title.font = Font.boldSystemFont(17);
    w.addSpacer(14);
    const err = w.addText("Dashboard indisponible");
    err.font = Font.boldSystemFont(18);
    const detail = w.addText(String(e.message || e));
    detail.font = Font.systemFont(10);
    detail.textOpacity = 0.55;
    detail.lineLimit = 4;
  }

  return w;
}

const widget = await buildDashboard();
Script.setWidget(widget);
if (!config.runsInWidget) await widget.presentExtraLarge();
Script.complete();
