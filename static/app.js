/* BitcoinTalk : carnet d'analyse
   Interface client. Aucun framework, aucune dépendance externe.
   Les garde-fous d'échappement vivent ici et nulle part ailleurs. */

let CATALOG = {};
/* La langue et la section par defaut sont lues dans le DOM, dont les
   placeholders ont deja ete substitues par le serveur. Lire le HTML plutot
   qu'un placeholder ici evite un double source de verite et garantit une
   valeur valide pour localeCompare(). */
let LANG = document.documentElement.lang || "fr";
let allProjects = [];
let showInactive = false;
let query = "";
let currentPeriod = "month";
let activeTab = "overview";
let scanTimer = null;
let lastFocus = null;

const $ = (id) => document.getElementById(id);

/* ---------- i18n ---------- */

function detectLang() {
  try {
    const stored = localStorage.getItem("bt_lang");
    if (stored && stored.split("-")[0]) return stored.split("-")[0].toLowerCase();
  } catch (e) {
    /* storage indisponible */
  }
  /* Repli sur la langue injectee par le serveur dans <html lang>. */
  const serverDefault = (document.documentElement.lang || "").trim().toLowerCase();
  return serverDefault || "fr";
}

function t(key) {
  let value = CATALOG;
  for (const part of key.split(".")) {
    value = value == null ? null : value[part];
  }
  return value == null ? key : String(value);
}

function tfmt(key, params) {
  let out = t(key);
  if (params) {
    for (const name of Object.keys(params)) {
      out = out.split("{" + name + "}").join(params[name]);
    }
  }
  return out;
}

/* ---------- Garde-fous ---------- */

function esc(value) {
  const holder = document.createElement("div");
  holder.textContent = value == null ? "" : String(value);
  return holder.innerHTML;
}

function escAttr(value) {
  return esc(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* Garde-fou d'affichage des liens externes. Une URL relative est refusee :
   `new URL()` la transformerait silencieusement en lien vers la page
   courante, ce qui n'est jamais l'intention d'un lien de projet. */
function safeUrl(value) {
  const raw = value == null ? "" : String(value).trim();
  if (!raw) return "";
  if (!/^https?:\/\//i.test(raw)) return "";
  let parsed;
  try {
    parsed = new URL(raw);
  } catch (err) {
    return "";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
  return parsed.href;
}

/* ---------- Rendu des chaînes statiques ---------- */

function applyI18n() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = tfmt(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    el.setAttribute("placeholder", t(el.getAttribute("data-i18n-ph")));
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria")));
  });
  document.title = t("dashboard.title");
  document.documentElement.lang = LANG;
  paintTabChrome();
}

/* ---------- Réseau ---------- */

/* `detail` peut etre un tableau d'erreurs de validation : un `[object Object]`
   affiche a l'utilisateur ne sert a rien. */
function errorDetail(detail) {
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && d.msg ? d.msg : JSON.stringify(d)))
      .join("; ");
  }
  return String(detail);
}

async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = "HTTP " + response.status;
    let body = null;
    try {
      body = await response.json();
      if (body && body.detail) detail = errorDetail(body.detail);
      else if (body && body.error) detail = String(body.error);
    } catch (e) {
      /* corps d'erreur non JSON */
    }
    const error = new Error(detail);
    error.status = response.status;
    if (body && body.reason) error.reason = String(body.reason);
    throw error;
  }
  return response.json();
}

async function loadCatalog(lang) {
  CATALOG = await fetchJSON("/api/i18n/" + lang);
}

function toast(message, isError) {
  let el = $("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.className = "toast" + (isError ? " is-error" : "");
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

/* ---------- Format ---------- */

const NONE = () => t("dashboard.list_empty");

function scoreTone(score) {
  if (score == null) return "is-muted";
  const n = Number(score);
  if (n >= 75) return "is-green";
  if (n >= 50) return "is-accent";
  return "is-risk";
}

function formatNumber(value) {
  if (value == null) return NONE();
  return Number(value).toLocaleString("fr-FR");
}

function formatUsd(value) {
  if (value == null) return NONE();
  const n = Number(value);
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
  return "$" + n.toFixed(2);
}

function compact(value) {
  if (!value) return null;
  const n = Number(value);
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}

function dateOnly(value) {
  return String(value || "").slice(0, 10);
}

function shortDate(value) {
  return String(value || "").slice(0, 10);
}

/* ---------- Fragments ---------- */

function kpi(label, value, cls) {
  return (
    '<div class="kpi"><p class="kpi-label">' + esc(label) + "</p>" +
    '<p class="kpi-value ' + (cls || "") + '">' + esc(value) + "</p></div>"
  );
}

function meterRow(label, value, display) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    '<div class="meter-row"><div class="meter-head">' +
    '<span class="meter-name">' + esc(label) + "</span>" +
    '<span class="meter-value">' + esc(display != null ? display : v) + "</span></div>" +
    '<div class="meter-track"><div class="meter-fill" style="width:' + v + '%"></div></div></div>'
  );
}

function markCell(on, title) {
  return '<span class="mark' + (on ? " is-on" : "") + '" title="' + escAttr(title) + '">' +
    (on ? "&#10003;" : "&minus;") + "</span>";
}

function emptyState(title, note, icon) {
  return (
    '<div class="empty">' +
    (icon ? '<svg class="ic" aria-hidden="true"><use href="#' + icon + '"/></svg>' : "") +
    '<p class="empty-title">' + esc(title) + "</p>" +
    (note ? '<p class="empty-note">' + esc(note) + "</p>" : "") +
    "</div>"
  );
}

function notice(text) {
  return '<div class="notice"><svg class="ic" aria-hidden="true"><use href="#i-warn"/></svg>' +
    "<span>" + esc(text) + "</span></div>";
}

function skeletonGrid(id, count) {
  const host = $(id);
  if (!host) return;
  host.innerHTML = Array.from({ length: count })
    .map(() => '<div class="skeleton" style="height:150px"></div>')
    .join("");
}

/* ---------- Onglets ---------- */

const TAB_KEYS = {
  overview: { title: "nav.overview", note: "nav.note_overview" },
  projects: { title: "nav.projects", note: "nav.note_projects" },
  enrichment: { title: "nav.enrichment", note: "nav.note_enrichment" },
  tokenomics: { title: "nav.tokenomics", note: "nav.note_tokenomics" },
  mining: { title: "nav.mining", note: "nav.note_mining" },
};

function paintTabChrome() {
  document.querySelectorAll("[data-tab]").forEach((el) => {
    const on = el.dataset.tab === activeTab;
    el.classList.toggle("is-active", on);
    if (el.classList.contains("tab")) el.setAttribute("aria-selected", String(on));
    else if (el.classList.contains("nav-item")) {
      if (on) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    }
  });
  document.querySelectorAll("[data-leaf]").forEach((el) => {
    const on = el.dataset.leaf === activeTab;
    el.hidden = !on;
    el.classList.toggle("is-active", on);
  });

  const meta = TAB_KEYS[activeTab] || TAB_KEYS.overview;
  const title = $("leaf-title");
  if (title) title.textContent = t(meta.title);
  const note = $("leaf-note");
  if (note) {
    note.textContent =
      activeTab === "projects"
        ? tfmt(meta.note, { n: allProjects.length })
        : t(meta.note);
  }
}

function switchTab(name) {
  if (!TAB_KEYS[name]) return;
  activeTab = name;
  paintTabChrome();
  closeRail();
  try {
    localStorage.setItem("bt_tab", name);
  } catch (e) {
    /* storage indisponible */
  }
}

function openRail() {
  $("rail").classList.add("is-open");
  $("scrim").hidden = false;
  $("rail-toggle").setAttribute("aria-expanded", "true");
}

function closeRail() {
  const rail = $("rail");
  if (!rail.classList.contains("is-open")) return;
  rail.classList.remove("is-open");
  $("scrim").hidden = true;
  $("rail-toggle").setAttribute("aria-expanded", "false");
}

/* ---------- Vue d'ensemble ---------- */

async function renderStats() {
  const host = $("kpis");
  try {
    const s = await fetchJSON("/api/stats");
    host.innerHTML =
      kpi(t("dashboard.stat_total"), formatNumber(s.total_projects)) +
      kpi(t("dashboard.stat_active"), formatNumber(s.active_count), "is-green") +
      kpi(t("dashboard.stat_promising"), formatNumber(s.promising_count), "is-accent") +
      kpi(t("dashboard.stat_avg"),
          s.avg_final_score != null ? Number(s.avg_final_score).toFixed(1) : NONE()) +
      kpi(t("dashboard.stat_forks"), formatNumber(s.fork_count)) +
      kpi(t("dashboard.stat_max"), formatNumber(s.max_final_score));
  } catch (e) {
    host.innerHTML = notice(tfmt("dashboard.error_stats", { error: e.message }));
  }
}

async function renderTimeseries(period) {
  currentPeriod = period;
  document.querySelectorAll("[data-period]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.period === period));
  });
  const chart = $("chart");
  const axis = $("chart-axis");
  try {
    const data = await fetchJSON(
      "/api/stats/timeseries?period=" + period + "&include_inactive=" + (showInactive ? 1 : 0)
    );
    const buckets = data.buckets || [];
    if (!buckets.length) {
      chart.innerHTML = emptyState(t("ts.empty"), t("ts.empty_note"));
      axis.innerHTML = "";
      return;
    }
    const values = buckets.map((b) => b.count || 0);
    const max = Math.max.apply(null, values.concat([1]));
    chart.innerHTML = values
      .map((v) => {
        const h = v > 0 ? Math.max(3, Math.round((v / max) * 100)) : 100;
        return '<div class="bar" data-empty="' + (v > 0 ? "0" : "1") +
          '" style="height:' + h + '%" title="' + escAttr(v + " analyse(s)") + '"></div>';
      })
      .join("");
    axis.innerHTML = buckets
      .map((b) => "<span>" + esc(b.label) + "</span>")
      .join("");
  } catch (e) {
    chart.innerHTML = notice(e.message);
    axis.innerHTML = "";
  }
}

async function renderAlgorithms() {
  const host = $("meters");
  try {
    const data = await fetchJSON(
      "/api/stats/by_algorithm?top=8&include_inactive=" + (showInactive ? 1 : 0)
    );
    const items = data.algorithms || [];
    $("algo-total").textContent = tfmt("dashboard.algo_total", { n: data.total || 0 });
    if (!items.length) {
      host.innerHTML = emptyState(t("algo.empty"), t("algo.empty_note"));
      return;
    }
    const max = Math.max.apply(null, items.map((i) => i.count || 0).concat([1]));
    host.innerHTML = items
      .map((a) => {
        const unknown = !a.algorithm || a.algorithm === "unknown";
        const label = unknown ? t("dashboard.algo_unknown") : a.algorithm;
        return '<div class="meter-row"><div class="meter-head">' +
          '<span class="meter-name">' + esc(label) + "</span>" +
          '<span class="meter-value">' + esc(a.count) + "</span></div>" +
          '<div class="meter-track"><div class="meter-fill" style="width:' +
          Math.round((a.count / max) * 100) + '%"></div></div></div>';
      })
      .join("");
  } catch (e) {
    host.innerHTML = notice(e.message);
  }
}

/* ---------- Projets ---------- */

function filteredProjects() {
  let rows = allProjects.slice();
  if (!showInactive) rows = rows.filter((p) => p.is_active !== false);
  if (query) {
    const q = query.toLowerCase();
    rows = rows.filter((p) =>
      [p.title, p.author, p.mining_algorithm, p.consensus_mechanism, p.coingecko_symbol]
        .some((f) => String(f || "").toLowerCase().includes(q))
    );
  }
  return rows;
}

function sortProjects(rows) {
  const select = $("sort");
  const spec = (select && select.value) || "final_score:desc";
  const idx = spec.indexOf(":");
  const key = spec.slice(0, idx);
  const dir = spec.slice(idx + 1) === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    let va = a[key];
    let vb = b[key];
    if (typeof va === "string") va = va.toLowerCase();
    if (typeof vb === "string") vb = vb.toLowerCase();
    if (va == null) va = "";
    if (vb == null) vb = "";
    if (va === vb) return 0;
    // localeCompare place les accents et les ligatures correctement ;
    // une comparaison par unites de code les rejetterait apres "z".
    const cmp = typeof va === "string"
      ? va.localeCompare(vb, LANG, { sensitivity: "base", numeric: true })
      : (va < vb ? -1 : 1);
    return cmp * dir;
  });
  return rows;
}

function projectCard(p) {
  const chips = [];
  if (p.is_promising) {
    chips.push('<span class="chip is-pos">' + esc(t("dashboard.badge_promising")) + "</span>");
  }
  if (p.is_fork) {
    chips.push('<span class="chip is-muted">' +
      esc(tfmt("dashboard.badge_fork", { base: p.fork_base || "?" })) + "</span>");
  }
  if (p.tokenomics_score) {
    chips.push('<span class="chip is-accent">' + esc(t("dashboard.tok_score")) + " " +
      esc(Math.round(p.tokenomics_score)) + "</span>");
  }
  if (p.coingecko_price_usd != null) {
    chips.push('<span class="chip">' + esc(formatUsd(p.coingecko_price_usd)) + "</span>");
  }
  const stars = compact(p.github_stars);
  if (stars) {
    chips.push('<span class="chip">' + esc(stars) + " " + esc(t("dashboard.github_stars")) + "</span>");
  }
  if (p.is_active === false) {
    chips.push('<span class="chip is-risk">' + esc(t("dashboard.badge_inactive")) + "</span>");
  }

  return (
    '<button type="button" class="project" data-topic="' + esc(p.topic_id) + '">' +
    '<span class="project-top"><span class="project-title">' +
    esc(p.title || NONE()) + "</span>" +
    '<span class="score ' + scoreTone(p.final_score) + '">' +
    esc(p.final_score != null ? p.final_score : "-") +
    '<span class="score-cap">' + esc(t("projects.score_cap")) + "</span></span></span>" +
    '<span class="project-meta"><span>' + esc(p.author || NONE()) + "</span>" +
    '<span class="project-algo">' + esc(p.mining_algorithm || t("dashboard.algo_unknown")) +
    "</span><span>" + esc(dateOnly(p.post_date)) + "</span></span>" +
    (chips.length ? '<span class="chips">' + chips.join("") + "</span>" : "") +
    "</button>"
  );
}

function renderProjects() {
  const host = $("rows");
  const rows = sortProjects(filteredProjects());
  const count = $("result-count");
  if (count) count.textContent = tfmt("dashboard.result_count", { n: rows.length });

  if (!allProjects.length) {
    host.innerHTML = emptyState(t("dashboard.empty_projects"), t("dashboard.empty_projects_note"), "i-projects");
    return;
  }
  if (!rows.length) {
    host.innerHTML = emptyState(t("dashboard.no_results"), t("dashboard.no_results_note"), "i-search");
    return;
  }
  host.innerHTML = rows.map(projectCard).join("");
}

/* ---------- Enrichissement ---------- */

function hasGithub(p) {
  return !!(p.github_stars || p.github_forks || p.github_language || p.github_link);
}
function hasWhitepaper(p) {
  return !!(p.whitepaper_text || p.whitepaper_link);
}
function hasCoingecko(p) {
  return !!(p.coingecko_id || p.coingecko_price_usd != null);
}
function hasSocial(p) {
  const s = p.social_metrics || {};
  return !!(s.discord_members || s.telegram_members || s.twitter_followers ||
            s.reddit_subscribers || s.discord_link || s.telegram_link);
}
function hasMining(p) {
  return !!(p.mining_algorithm || p.hashrate_str || p.mining_link);
}

const SOURCES = [
  { key: "github", test: hasGithub, kpi: "enrich.kpi_github", col: "enrich.col_github" },
  { key: "whitepaper", test: hasWhitepaper, kpi: "enrich.kpi_whitepaper", col: "enrich.col_whitepaper" },
  { key: "coingecko", test: hasCoingecko, kpi: "enrich.kpi_coingecko", col: "enrich.col_coingecko" },
  { key: "social", test: hasSocial, kpi: "enrich.kpi_social", col: "enrich.col_social" },
  { key: "mining", test: hasMining, kpi: "enrich.kpi_mining", col: "enrich.col_mining" },
];

function renderCoverage() {
  const kpiHost = $("coverage-kpis");
  const body = $("coverage-body");
  const pool = filteredProjects();

  const counts = SOURCES.map((s) => pool.filter(s.test).length);
  kpiHost.innerHTML =
    kpi(t("enrich.kpi_total"), formatNumber(pool.length)) +
    SOURCES.map((s, i) =>
      kpi(t(s.kpi), formatNumber(counts[i]),
          counts[i] === 0 ? "" : counts[i] >= pool.length ? "is-green" : "is-accent")
    ).join("");

  if (!pool.length) {
    body.innerHTML =
      '<tr><td colspan="6">' + emptyState(t("enrich.empty"), t("enrich.empty_note"), "i-enrich") + "</td></tr>";
    return;
  }

  body.innerHTML = pool
    .map((p) => {
      const cells = SOURCES.map((s) =>
        "<td>" + markCell(s.test(p), t(s.col)) + "</td>"
      ).join("");
      return '<tr data-topic="' + esc(p.topic_id) + '">' +
        '<td class="ledger-name">' + esc(p.title || NONE()) + "</td>" + cells + "</tr>";
    })
    .join("");
}

/* ---------- Tokenomics et code ---------- */

function renderTokenomics() {
  const tokHost = $("tok-meters");
  const srcHost = $("src-meters");
  const pool = filteredProjects().filter((p) => p.tokenomics_score || (p.source_analysis || {}).source_maturity != null);

  if (!pool.length) {
    tokHost.innerHTML = emptyState(t("tok.empty"), t("tok.empty_note"), "i-token");
    srcHost.innerHTML = emptyState(t("src.empty"), t("src.empty_note"), "i-enrich");
    return;
  }

  const sorted = pool.slice().sort((a, b) =>
    (b.tokenomics_score || 0) - (a.tokenomics_score || 0)
  );

  tokHost.innerHTML = sorted
    .map((p) => meterRow(p.title || NONE(), p.tokenomics_score, Math.round(p.tokenomics_score || 0)))
    .join("");

  srcHost.innerHTML = sorted
    .map((p) => {
      const src = p.source_analysis || {};
      return meterRow(p.title || NONE(), src.source_maturity, src.source_maturity);
    })
    .join("");
}

/* ---------- Minage ---------- */

function renderMining() {
  const kpiHost = $("mining-kpis");
  const host = $("mining-rows");
  const pool = filteredProjects().filter(hasMining);

  const withHash = pool.filter((p) => p.hashrate_str).length;
  const pools = pool.reduce((sum, p) => sum + (p.mining_active_pools || 0), 0);
  const algos = new Set(pool.map((p) => p.mining_algorithm).filter(Boolean));
  const guides = pool.filter((p) => p.mining_guide && (p.mining_guide.pool || []).length).length;

  kpiHost.innerHTML =
    kpi(t("mine.kpi_projects"), formatNumber(pool.length)) +
    kpi(t("mine.kpi_hashrate"), formatNumber(withHash), withHash ? "is-accent" : "") +
    kpi(t("mine.kpi_pools"), formatNumber(pools)) +
    kpi(t("mine.kpi_algorithms"), formatNumber(algos.size)) +
    kpi(t("mine.kpi_guides"), formatNumber(guides));

  if (!pool.length) {
    host.innerHTML = emptyState(t("mine.empty"), t("mine.empty_note"), "i-mining");
    return;
  }

  host.innerHTML = pool
    .map((p) => {
      const facts = [
        [t("dashboard.detail_algo"), p.mining_algorithm],
        [t("dashboard.hashrate"), p.hashrate_str],
        [t("dashboard.mining_status"), p.mining_status],
        [t("mine.active_pools"), p.mining_active_pools || null],
      ];
      const cells = facts
        .map(([k, v]) =>
          '<div class="fact"><p class="fact-label">' + esc(k) + '</p><p class="fact-value">' +
          esc(v == null || v === "" ? NONE() : v) + "</p></div>")
        .join("");
      const link = safeUrl(p.mining_link);
      return (
        '<article class="panel" data-topic="' + esc(p.topic_id) + '" style="cursor:pointer">' +
        '<header class="panel-head"><h3 class="panel-title">' + esc(p.title || NONE()) + "</h3>" +
        '<span class="panel-note">' +
        esc(p.final_score != null ? t("projects.score_cap") + " " + p.final_score : "") +
        "</span></header>" +
        '<div class="facts">' + cells + "</div>" +
        miningGuideBlock(p.mining_guide) +
        (link
          ? '<div class="link-row" style="margin-top:13px"><a class="link-chip" href="' +
            escAttr(link) + '" target="_blank" rel="noopener noreferrer">MiningPoolStats' +
            '<span aria-hidden="true">&#8599;</span></a></div>'
          : "") +
        "</article>"
      );
    })
    .join("");
}

function miningGuideBlock(guide) {
  if (!guide) return "";
  const rows = [];
  if (guide.network) rows.push([t("mine.guide_network"), esc(guide.network)]);
  if (guide.algorithm) rows.push([t("dashboard.detail_algo"), esc(guide.algorithm)]);
  if ((guide.pool || []).length) rows.push([t("mine.guide_pool"), esc(guide.pool.join(", "))]);
  if (guide.port) rows.push([t("mine.guide_port"), esc(guide.port)]);
  if ((guide.stratum || []).length) rows.push([t("mine.guide_stratum"), esc(guide.stratum.join(", "))]);
  if (guide.worker) rows.push([t("mine.guide_worker"), esc(guide.worker)]);
  if (guide.password) rows.push([t("mine.guide_password"), esc(guide.password)]);
  if (guide.payout) rows.push([t("mine.guide_payout"), esc(guide.payout)]);
  const explorer = safeUrl(guide.explorer);
  if (explorer) {
    rows.push([
      t("mine.guide_explorer"),
      '<a class="fact-link" href="' + escAttr(explorer) + '" target="_blank" rel="noopener noreferrer">' +
        esc(guide.explorer) + '<span aria-hidden="true">&#8599;</span></a>',
    ]);
  }
  if (!rows.length) return "";
  return (
    '<details class="mine-guide"><summary>' + esc(t("mine.guide")) + "</summary>" +
    '<dl class="facts">' +
    rows
      .map(([k, v]) =>
        '<div class="fact"><dt class="fact-label">' + esc(k) + "</dt>" +
        '<dd class="fact-value">' + v + "</dd></div>")
      .join("") +
    "</dl></details>"
  );
}

/* ---------- Détail ---------- */

function factList(pairs) {
  const cells = pairs
    .map(([k, v]) =>
      '<div class="fact"><dt class="fact-label">' + esc(k) + '</dt><dd class="fact-value">' +
      esc(v == null || v === "" ? NONE() : v) + "</dd></div>")
    .join("");
  return '<dl class="facts">' + cells + "</dl>";
}

function tagList(items, variant) {
  if (!items || !items.length) {
    return '<p class="prose">' + esc(t("dashboard.list_empty")) + "</p>";
  }
  return '<ul class="tag-list ' + variant + '">' +
    items.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul>";
}

function linkChips(p) {
  const links = [];
  const add = (href, label) => {
    const url = safeUrl(href);
    if (url) {
      links.push(
        '<a class="link-chip" href="' + escAttr(url) + '" target="_blank" rel="noopener noreferrer">' +
        esc(label) + '<span aria-hidden="true">&#8599;</span></a>'
      );
    }
  };
  add(p.github_link, "GitHub");
  add(p.whitepaper_link, t("dashboard.whitepaper"));
  add(p.website_link, t("dashboard.website"));
  if (p.topic_id) add("https://bitcointalk.org/index.php?topic=" + p.topic_id, "BitcoinTalk");
  if (p.mining_link) add(p.mining_link, "MiningPoolStats");
  if (p.coingecko_id) add("https://www.coingecko.com/en/coins/" + p.coingecko_id, "CoinGecko");
  const social = p.social_metrics || {};
  add(social.discord_link, "Discord");
  add(social.telegram_link, "Telegram");
  add(social.twitter_link, "X");
  add(social.reddit_link, "Reddit");
  return links.length ? '<div class="link-row">' + links.join("") + "</div>" : "";
}

function openDetail(topicId) {
  const overlay = $("overlay");
  const body = $("sheet-body");
  lastFocus = document.activeElement;
  overlay.hidden = false;
  body.innerHTML = '<div class="empty"><h2 class="sheet-title" id="sheet-heading">' +
    esc(t("dashboard.loading")) + "</h2></div>";
  $("sheet-close").focus();

  fetchJSON("/api/projects/" + topicId)
    .then((p) => {
      const src = p.source_analysis || {};
      const social = p.social_metrics || {};
      const parts = [];

      parts.push(
        '<div class="sheet-head"><h2 class="sheet-title" id="sheet-heading">' + esc(p.title || NONE()) + "</h2>" +
        '<p class="sheet-sub"><span>' + esc(p.author || NONE()) + "</span>" +
        "<span>" + esc(dateOnly(p.post_date)) + "</span>" +
        '<span class="sheet-score ' + scoreTone(p.final_score) + '">' +
        esc(p.final_score != null ? p.final_score : "-") + "</span></p></div>"
      );

      parts.push(
        '<div class="section"><h3 class="section-title">' + esc(t("dashboard.detail_scores")) + "</h3>" +
        '<div class="facts">' +
        meterRow(t("dashboard.detail_bar_tech"), p.technical_score) +
        meterRow(t("dashboard.detail_bar_inno"), p.innovation_score) +
        meterRow(t("dashboard.detail_bar_disp"), p.disruptiveness_score) +
        meterRow(t("dashboard.detail_bar_cred"), p.credibility_score) +
        "</div></div>"
      );

      parts.push(
        '<div class="section"><h3 class="section-title">' + esc(t("dashboard.detail_info")) + "</h3>" +
        factList([
          [t("dashboard.detail_author"), p.author],
          [t("dashboard.detail_date"), dateOnly(p.post_date)],
          [t("dashboard.detail_algo"), p.mining_algorithm],
          [t("dashboard.detail_consensus"), p.consensus_mechanism],
          [t("dashboard.detail_premine"), p.premine_percentage != null ? p.premine_percentage + "%" : null],
          [t("dashboard.detail_fork"), p.is_fork ? p.fork_base : t("dashboard.detail_fork_no")],
          [t("dashboard.quality_level"), p.quality_level],
          [t("dashboard.innovation_level"), p.innovation_level],
          [t("dashboard.mining_type"), p.mining_type],
        ]) + "</div>"
      );

      parts.push(
        '<div class="section"><h3 class="section-title">' + esc(t("dashboard.sources")) + "</h3>" +
        factList([
          [t("dashboard.github_stars"), compact(p.github_stars)],
          [t("dashboard.github_forks"), compact(p.github_forks)],
          [t("dashboard.github_issues"), compact(p.github_open_issues)],
          [t("dashboard.github_lang"), p.github_language],
          [t("dashboard.discord"), compact(p.discord_members)],
          [t("dashboard.telegram"), compact(p.telegram_members)],
          [t("dashboard.price"), p.coingecko_price_usd != null ? formatUsd(p.coingecko_price_usd) : null],
          [t("dashboard.mcap"), p.coingecko_market_cap_usd != null ? formatUsd(p.coingecko_market_cap_usd) : null],
          [t("dashboard.mg_rank"), p.coingecko_rank],
          [t("dashboard.hashrate"), p.hashrate_str],
        ]) + "</div>"
      );

      if (p.token_distribution || p.team_allocation || p.vesting_period ||
          p.inflation_model || p.utility_description || p.tokenomics_score) {
        parts.push(
          '<div class="section"><h3 class="section-title">' + esc(t("dashboard.tokenomics")) + "</h3>" +
          factList([
            [t("dashboard.tok_score"), p.tokenomics_score ? Math.round(p.tokenomics_score) : null],
            [t("dashboard.tok_distribution"), p.token_distribution],
            [t("dashboard.tok_team"), p.team_allocation],
            [t("dashboard.tok_vesting"), p.vesting_period],
            [t("dashboard.tok_inflation"), p.inflation_model],
            [t("dashboard.tok_utility"), p.utility_description],
          ]) + "</div>"
        );
      }

      if (src.source_notes || src.source_maturity != null) {
        parts.push(
          '<div class="section"><h3 class="section-title">' + esc(t("dashboard.src_maturity")) + "</h3>" +
          factList([
            [t("dashboard.src_maturity"), src.source_maturity],
            [t("dashboard.src_scam"), src.scam_risk],
            [t("dashboard.src_quality"), src.code_quality],
          ]) +
          (src.source_notes
            ? '<div class="quote-block" style="margin-top:12px"><p class="prose">' +
              esc(src.source_notes) + "</p></div>"
            : "") +
          "</div>"
        );
      }

      if (social.discord_members || social.telegram_members || social.twitter_followers ||
          social.reddit_subscribers) {
        parts.push(
          '<div class="section"><h3 class="section-title">' + esc(t("dashboard.sources")) + "</h3>" +
          factList([
            [t("dashboard.discord"), compact(social.discord_members)],
            [t("dashboard.telegram"), compact(social.telegram_members)],
            [t("dashboard.twitter"), compact(social.twitter_followers)],
            [t("dashboard.reddit"), compact(social.reddit_subscribers)],
          ]) + "</div>"
        );
      }

      if (p.tokenomics_risks && p.tokenomics_risks.length) {
        parts.push(
          '<div class="section"><h3 class="section-title">' + esc(t("dashboard.tok_risks")) + "</h3>" +
          tagList(p.tokenomics_risks, "is-risk") + "</div>"
        );
      }
      if (p.strengths && p.strengths.length) {
        parts.push(
          '<div class="section"><h3 class="section-title">' + esc(t("dashboard.detail_strengths")) + "</h3>" +
          tagList(p.strengths, "is-good") + "</div>"
        );
      }
      if (p.unique_features && p.unique_features.length) {
        parts.push(
          '<div class="section"><h3 class="section-title">' + esc(t("dashboard.detail_features")) + "</h3>" +
          tagList(p.unique_features, "") + "</div>"
        );
      }
      if (p.red_flags && p.red_flags.length) {
        parts.push(
          '<div class="section"><h3 class="section-title">' + esc(t("dashboard.detail_flags")) + "</h3>" +
          tagList(p.red_flags, "is-risk") + "</div>"
        );
      }
      if (p.whitepaper_text) {
        parts.push(
          '<div class="section"><h3 class="section-title">' +
          esc(t("dashboard.whitepaper_extract")) + "</h3>" +
          '<pre class="prose">' + esc(p.whitepaper_text.slice(0, 1200)) + "</pre></div>"
        );
      }
      if (p.quality_note) {
        parts.push(
          '<div class="section"><h3 class="section-title">' + esc(t("dashboard.quality_note")) +
          "</h3>" + '<pre class="prose">' + esc(p.quality_note) + "</pre></div>"
        );
      }

      parts.push(
        '<div class="section"><h3 class="section-title">' + esc(t("dashboard.detail_links")) +
        "</h3>" + linkChips(p) + "</div>"
      );

      parts.push(
        '<div class="section"><h3 class="section-title">' + esc(t("dashboard.detail_history")) +
        '</h3><div class="facts" id="history"></div></div>'
      );

      body.innerHTML = parts.join("");
      $("sheet-actions").dataset.topic = p.topic_id;
      $("sheet-actions").innerHTML =
        (p.is_active === false
          ? '<button type="button" class="btn btn-quiet" data-act="enable">' +
            esc(t("dashboard.act_enable")) + "</button>"
          : '<button type="button" class="btn btn-quiet" data-act="disable">' +
            esc(t("dashboard.act_disable")) + "</button>") +
        '<div class="model-selector" id="model-selector">' +
        '<label for="reanalyze-model">' + esc(t("dashboard.model_select")) + '</label>' +
        '<select id="reanalyze-model">' + reanalyzeModelOptions() + '</select></div>' +
        '<button type="button" class="btn btn-primary" data-act="reanalyze">' +
        esc(t("dashboard.act_reanalyze")) + "</button>";
      populateReanalyzeModels();

      fetchJSON("/api/history/" + p.topic_id)
        .then((h) => {
          const host2 = $("history");
          if (!host2) return;
          const items = h.history || [];
          host2.innerHTML = items.length
            ? items.map((x) =>
                '<div class="fact"><p class="fact-label">' +
                esc(String(x.analysis_date || "").slice(0, 10)) +
                '</p><p class="fact-value ' + scoreTone(x.score) + '">' +
                esc(x.score) + "</p></div>").join("")
            : '<p class="prose">' + esc(t("dashboard.detail_no_history")) + "</p>";
        })
        .catch(() => {});
    })
    .catch((e) => {
      body.innerHTML = notice(tfmt("dashboard.error_detail", { error: e.message }));
    });
}

function closeDetail() {
  const overlay = $("overlay");
  if (overlay.hidden) return;
  overlay.hidden = true;
  $("sheet-body").innerHTML = "";
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

/* Liste du selecteur de reanalyse : le catalogue du fournisseur deja charge
   est rendu d'emblee ; la liste fixe ne sert que de repli hors ligne. */
function reanalyzeModelOptions() {
  const models = llmState.models || [];
  if (models.length) {
    return models
      .map((m) => '<option value="' + escAttr(m.name) + '">' + esc(m.name) + "</option>")
      .join("");
  }
  const fallback =
    '<option value="gpt-4o">' + esc(t("dashboard.select_gpt4o")) + "</option>" +
    '<option value="gpt-4">' + esc(t("dashboard.select_gpt4")) + "</option>" +
    '<option value="gpt-3.5-turbo">' + esc(t("dashboard.select_gpt35")) + "</option>" +
    '<option value="claude-3.5-sonnet">' + esc(t("dashboard.select_claude")) + "</option>" +
    '<option value="claude-3-opus">' + esc(t("dashboard.select_claudeopus")) + "</option>" +
    '<option value="llama3">' + esc(t("dashboard.select_llama3")) + "</option>" +
    '<option value="llama2">' + esc(t("dashboard.select_llama2")) + "</option>";
  return fallback;
}

/* La liste fixe servait de repli : elle ne propose que des modeles hors
   provider configure (Ollama ici). Le vrai catalogue vient du fournisseur
   courant ; en cas d'echec la liste fixe reste en place. */
function populateReanalyzeModels() {
  const sel = $("reanalyze-model");
  if (!sel) return;
  const apply = (models) => {
    const target = $("reanalyze-model");
    if (!models.length || target !== sel) return;
    const previous = sel.value;
    const preferred = ($("llm-model") && $("llm-model").value) || previous;
    sel.innerHTML = models
      .map((m) => '<option value="' + escAttr(m.name) + '">' + esc(m.name) + "</option>")
      .join("");
    const names = models.map((m) => m.name);
    sel.value = names.indexOf(preferred) >= 0 ? preferred : names[0];
  };
  /* Le catalogue vient d'etre charge pour le panneau LLM : inutile d'attendre. */
  if (llmState.models && llmState.models.length) {
    apply(llmState.models);
    return;
  }
  fetchJSON("/api/llm/models", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(llmFields()),
  })
    .then((listing) => apply((listing && listing.models) || []))
    .catch(() => {
      /* Fournisseur injoignable : la liste fixe reste proposee. */
    });
}

async function actCall(topicId, action) {
  try {
    const path = action === "reanalyze"
      ? "/api/reanalyze/" + topicId
      : "/api/projects/" + topicId + "/" + action;
    let body = { method: "POST" };
    if (action === "reanalyze") {
      const model = $("reanalyze-model") ? $("reanalyze-model").value : null;
      body.headers = { "Content-Type": "application/json" };
      body.body = JSON.stringify({ model, llm_config: { model } });
    }
    await fetchJSON(path, body);
    toast(action === "reanalyze" ? t("dashboard.act_queued") : t("dashboard.act_" + action + "d"));
    await refresh();
    openDetail(topicId);
  } catch (e) {
    if (e.message.indexOf("analyzing") >= 0) toast(t("dashboard.error_busy"), true);
    else if (/(ollama|llm)_unavailable/.test(e.message))
      toast(t("dashboard.error_down") + (e.reason ? " — " + e.reason : ""), true);
    else toast(e.message, true);
  }
}

/* ---------- Scan ---------- */

let scanWasRunning = false;

function renderScan(state) {
  const live = $("scan-live");
  const fill = $("scan-fill");
  const meta = $("scan-status");
  const btn = $("btn-scan");
  const pct = state.pages_total ? Math.round((state.pages_done / state.pages_total) * 100) : 0;
  const counts = {
    found: state.topics_found || 0,
    analyzed: state.analyzed_count || 0,
    skipped: state.skipped_count || 0,
  };

  if (state.status === "running") {
    live.hidden = false;
    fill.style.width = pct + "%";
    meta.textContent = tfmt("scan.running", {
      done: state.pages_done, total: state.pages_total,
      found: counts.found, analyzed: counts.analyzed, skipped: counts.skipped,
    });
    btn.disabled = true;
  } else if (state.status === "done") {
    live.hidden = false;
    fill.style.width = "100%";
    meta.textContent = tfmt("scan.done", counts);
    btn.disabled = false;
    if (scanWasRunning) toast(tfmt("scan.done", counts));
  } else if (state.status === "error") {
    live.hidden = false;
    fill.style.width = "0%";
    meta.textContent = state.error
      ? t("scan.failed") + " : " + state.error
      : t("scan.failed");
    btn.disabled = false;
    if (scanWasRunning) toast(t("scan.failed") + (state.error ? " : " + state.error : ""), true);
  } else {
    live.hidden = true;
    fill.style.width = "0%";
    meta.textContent = t("scan.idle");
    btn.disabled = false;
  }
  scanWasRunning = state.status === "running";
}

async function pollScan() {
  try {
    const state = await fetchJSON("/api/scan/status");
    renderScan(state);
    if (state.status === "running") {
      scanTimer = setTimeout(pollScan, 1000);
    } else {
      await refresh();
    }
  } catch (e) {
    scanTimer = setTimeout(pollScan, 4000);
  }
}

async function startScan() {
  const pages = Number($("scan-pages").value) || 3;
  const section = Number($("scan-section").value) || 159;
  try {
    await fetchJSON("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ section_id: section, pages: Math.min(pages, 20) }),
    });
    clearTimeout(scanTimer);
    pollScan();
  } catch (e) {
    toast(t("scan.failed") + " : " + e.message, true);
    renderScan({ status: "idle" });
  }
}

/* ---------- Configuration LLM ---------- */

const llmState = {
  models: [],
  keyDirty: false,
  loaded: false,
  modelsLoaded: false,
  modelsRequest: 0,
  /* Vrai des que l'utilisateur touche un champ : un rechargement en vol ne doit
     plus remplacer les valeurs affichees. */
  editing: false,
  /* Modele demande par la configuration : le select doit le retrouver meme
     apres un repeuplement de la liste. */
  preferredModel: "",
};

/* Les champs de saisie du panneau : toute modification suspends l'ecrasement
   par une reponse de chargement. */
const LLM_INPUT_IDS = ["llm-provider", "llm-model", "llm-ollama-host",
                       "llm-api-base", "llm-api-key", "llm-temperature",
                       "llm-timeout"];

function bindLLMInputs() {
  LLM_INPUT_IDS.forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("input", () => { llmState.editing = true; });
    el.addEventListener("change", () => { llmState.editing = true; });
  });
}

function llmFields() {
  return {
    provider: $("llm-provider").value,
    model: $("llm-model").value.trim(),
    ollama_host: $("llm-ollama-host").value.trim(),
    api_base: $("llm-api-base").value.trim(),
    api_key: llmState.keyDirty ? $("llm-api-key").value : "",
    temperature: Number($("llm-temperature").value),
    timeout: Number($("llm-timeout").value),
  };
}

/* Affiche les champs correspondant au fournisseur choisi. */
function syncLLMFields() {
  const openai = $("llm-provider").value === "openai";
  $("llm-ollama-field").hidden = openai;
  $("llm-base-field").hidden = !openai;
  $("llm-key-field").hidden = !openai;
  $("llm-badge").textContent = $("llm-model").value.trim() || t("llm.provider_ollama");
}

function llmMessage(text, tone) {
  const box = $("llm-msg");
  box.hidden = false;
  box.textContent = text;
  box.classList.remove("is-ok", "is-err", "is-warn");
  if (tone) box.classList.add("is-" + tone);
}

/* Les modeles installes sur la machine sont decouverts, pas saisis : le
   panneau doit proposer ce que le fournisseur sert reellement. */
function renderLLMModels(listing) {
  const models = (listing && listing.models) || [];
  llmState.models = models;
  $("llm-models").innerHTML = models
    .map((m) => '<option value="' + escAttr(m.name) + '"></option>')
    .join("");

  /* Le champ est un select : la liste des modeles installes est visible
     d'emblee, sans frappe ni popup de saisie semi-cachee. */
  const select = $("llm-model");
  const preferred = ((select && select.value) || llmState.preferredModel || "").trim();
  const names = models.map((m) => m.name);
  if (preferred && names.indexOf(preferred) < 0) names.unshift(preferred);
  if (select) {
    select.innerHTML = names
      .map((name) => '<option value="' + escAttr(name) + '">' + esc(name) + "</option>")
      .join("");
    if (preferred) select.value = preferred;
  }

  const note = $("llm-models-note");
  if (!models.length) {
    /* Un serveur injoignable et un serveur sans modeles ne se distinguent pas
       pour l'utilisateur : la raison renvoyee par l'API est la seule piste. */
    note.textContent = (listing && listing.error)
      ? tfmt("llm.models_error", {detail: listing.error})
      : t("llm.models_none");
    return;
  }
  const parts = models.map((m) => {
    const meta = [m.parameters, m.quantization].filter(Boolean).join(" ");
    return meta ? m.name + " (" + meta + ")" : m.name;
  });
  note.textContent = tfmt("llm.models_found", {
    count: models.length, latency: formatNumber(listing.latency_ms || 0) + " ms",
  });
  note.title = parts.join("\n");

  /* Le modele configure doit rester selectionne meme s'il n'est plus servi. */
  const current = $("llm-model").value.trim();
  if (current && !models.some((m) => m.name === current)) {
    note.textContent += " " + tfmt("llm.model_not_installed", {model: current});
  }
}

async function refreshLLMModels() {
  /* Chaque appel porte un numero : les reponses hors ordre doivent etre
     ignorees, sinon un serveur lent remplace la liste d'un serveur deja
     selectionne. Le bouton reste cliquable pendant une requete pour ne pas
     avaler le clic de l'utilisateur. */
  const token = ++llmState.modelsRequest;
  const note = $("llm-models-note");
  $("llm-refresh").classList.add("is-busy");
  note.textContent = t("llm.loading_models");
  let listing;
  try {
    /* Le corps porte l'etat courant des champs, pas la configuration
       enregistree : basculer de fournisseur doit lister le bon serveur avant
       meme d'enregistrer. */
    listing = await fetchJSON("/api/llm/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(llmFields()),
    });
  } catch (e) {
    if (token !== llmState.modelsRequest) return;
    $("llm-models").innerHTML = "";
    llmState.models = [];
    note.textContent = e.message;
    return;
  }
  if (token !== llmState.modelsRequest) return;
  $("llm-refresh").classList.remove("is-busy");
  renderLLMModels(listing);
}

function reportLLMLoadError(e) {
  llmMessage(e.message, "err");
}

/* Un clic sur le resume et l'ouverture declenchent chacun un chargement : ces
   reponses ne doivent pas s'ecraser entre elles. Le jeton retient la derniere
   demande, pas la derniere reponse. */
let llmConfigRequest = 0;

async function loadLLMConfig() {
  const token = ++llmConfigRequest;
  const config = await fetchJSON("/api/llm/config");
  if (token !== llmConfigRequest) return config;
  /* Un champ modifie entre-temps garde la main : ecraser la saisie en cours
      ferait perdre ce que l'utilisateur vient de taper. */
  if (!llmState.editing) {
    $("llm-provider").value = config.provider;
    $("llm-model").value = config.model || "";
    llmState.preferredModel = config.model || "";
    $("llm-ollama-host").value = config.ollama_host || "";
    $("llm-api-base").value = config.api_base || "";
    $("llm-temperature").value = config.temperature;
    $("llm-timeout").value = config.timeout;
    /* La cle n'est jamais renvoyee par le serveur : on ne fait qu'afficher
       la confirmation qu'une cle existe. */
    $("llm-api-key").value = "";
    llmState.keyDirty = false;
  }
  llmState.editing = false;
  $("llm-key-note").textContent = config.has_api_key
    ? tfmt("llm.key_saved", {hint: config.api_key_hint})
    : t("llm.key_missing");
  llmState.loaded = true;
  syncLLMFields();
  if (llmState.modelsLoaded) await refreshLLMModels();

  if ((config.env_overrides || []).length) {
    llmMessage(tfmt("llm.env_locked", {names: config.env_overrides.join(", ")}), "warn");
  }
  return config;
}

async function saveLLMConfig() {
  const fields = llmFields();
  try {
    const saved = await fetchJSON("/api/llm/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    llmState.keyDirty = false;
    $("llm-api-key").value = "";
    $("llm-key-note").textContent = saved.has_api_key
      ? tfmt("llm.key_saved", {hint: saved.api_key_hint})
      : t("llm.key_missing");
    syncLLMFields();
    llmMessage(t("llm.saved"), "ok");
  } catch (e) {
    llmMessage(e.message, "err");
  }
}

async function testLLMConfig() {
  const msg = $("llm-msg");
  msg.hidden = false;
  msg.classList.remove("is-ok", "is-err", "is-warn");
  msg.textContent = t("llm.testing");
  try {
    const result = await fetchJSON("/api/llm/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(llmFields()),
    });
    /* Un echec de joignabilite revient en 200 avec ok=false : sans ce test,
       l'interface afficherait « Connecté » sur un fournisseur injoignable. */
    if (result.ok === false) {
      llmMessage(result.error || tfmt("llm.err_unreachable", {detail: ""}), "err");
      return;
    }
    /* Le test partage le meme rendu de liste : une seule representation des
       modeles disponibles dans tout le panneau. */
    renderLLMModels({models: (result.models || []).map((name) => ({name: name})),
                     latency_ms: result.latency_ms});
    const latency = formatNumber(result.latency_ms || 0) + " ms";
    if (result.model_present) {
      llmMessage(tfmt("llm.test_ok", {latency: latency, count: result.models.length}), "ok");
    } else if (result.models && result.models.length) {
      llmMessage(tfmt("llm.test_model_missing", {model: llmFields().model,
                                          latency: latency}), "warn");
    } else {
      llmMessage(tfmt("llm.test_ok", {latency: latency, count: 0}), "ok");
    }
  } catch (e) {
    llmMessage(e.message, "err");
  }
}

/* ---------- Cycle de rendu ---------- */

function renderAll() {
  renderProjects();
  renderCoverage();
  renderTokenomics();
  renderMining();
  const badge = $("nav-projects-count");
  if (badge) badge.textContent = String(allProjects.length);
  paintTabChrome();
}

async function refresh() {
  try {
    allProjects = await fetchJSON("/api/projects?include_inactive=" + (showInactive ? 1 : 0));
  } catch (e) {
    allProjects = [];
    $("rows").innerHTML = notice(tfmt("dashboard.error_detail", { error: e.message }));
    return;
  }
  renderAll();
  await Promise.all([renderStats(), renderTimeseries(currentPeriod), renderAlgorithms()]);
}

async function populateLangSelect() {
  const select = $("lang");
  let langs = [];
  try {
    langs = await fetchJSON("/api/i18n/languages");
  } catch (e) {
    langs = [{ code: LANG, name: LANG === "en" ? "English" : "Français" },
             { code: LANG === "en" ? "fr" : "en", name: LANG === "en" ? "Français" : "English" }];
  }
  select.innerHTML = langs
    .map((l) => '<option value="' + esc(l.code) + '"' +
      (l.code === LANG ? " selected" : "") + ">" + esc(l.name) + "</option>")
    .join("");
}

async function switchLanguage(lang) {
  LANG = lang;
  try {
    localStorage.setItem("bt_lang", lang);
  } catch (e) {
    /* storage indisponible */
  }
  await loadCatalog(lang);
  applyI18n();
  if (llmState.loaded) syncLLMFields();
  await refresh();
}

/* ---------- Événements ---------- */

function onTopicClick(event) {
  const node = event.target.closest("[data-topic]");
  if (node && node.dataset.topic) openDetail(node.dataset.topic);
}

function wireEvents() {
  document.addEventListener("click", (e) => {
    const tab = e.target.closest("[data-tab]");
    if (tab) switchTab(tab.dataset.tab);
  });

  $("rail-toggle").addEventListener("click", openRail);
  $("rail-close").addEventListener("click", closeRail);
  $("scrim").addEventListener("click", closeRail);

  $("rows").addEventListener("click", onTopicClick);
  $("coverage-body").addEventListener("click", onTopicClick);
  $("mining-rows").addEventListener("click", onTopicClick);

  $("search").addEventListener("input", (e) => {
    query = e.target.value;
    renderProjects();
  });
  $("sort").addEventListener("change", renderProjects);
  $("include-inactive").addEventListener("change", async (e) => {
    showInactive = e.target.checked;
    await refresh();
  });

  $("btn-scan").addEventListener("click", startScan);

  $("overlay").addEventListener("click", (e) => {
    if (e.target === $("overlay")) closeDetail();
  });
  $("sheet-close").addEventListener("click", closeDetail);
  $("sheet-actions").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (btn) actCall($("sheet-actions").dataset.topic, btn.dataset.act);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("overlay").hidden) closeDetail();
    else closeRail();
  });

  document.querySelectorAll("[data-period]").forEach((btn) => {
    btn.addEventListener("click", () => renderTimeseries(btn.dataset.period));
  });

  bindLLMInputs();

  $("llm-provider").addEventListener("change", () => {
    syncLLMFields();
    /* Le serveur a change : la liste precedente n'a plus aucun rapport avec les
       modeles disponibles, il faut la refaire. */
    refreshLLMModels();
  });
  $("llm-model").addEventListener("input", syncLLMFields);
  $("llm-api-key").addEventListener("input", () => {
    llmState.keyDirty = true;
  });
  /* Les modeles dependent de l'adresse du serveur : une URL saisie puis corrigee
     doit pouvoir etre sondee sans passer par l'enregistrement. */
  $("llm-ollama-host").addEventListener("change", refreshLLMModels);
  $("llm-api-base").addEventListener("change", refreshLLMModels);
  $("llm-save").addEventListener("click", saveLLMConfig);
  $("llm-test").addEventListener("click", testLLMConfig);
  $("llm-refresh").addEventListener("click", refreshLLMModels);
  $("llm-box").addEventListener("toggle", () => {
    if ($("llm-box").open) loadLLMConfig().catch(reportLLMLoadError);
  });
  /* Le resume est la cible tactile reelle du panneau : il doit aussi
     recharger la configuration, pour rester reactif au clic comme au clavier. */
  $("llm-summary").addEventListener("click", () => {
    if ($("llm-box").open) loadLLMConfig().catch(reportLLMLoadError);
  });

  $("lang").addEventListener("change", (e) => switchLanguage(e.target.value));
}

(async function init() {
  LANG = detectLang();
  try {
    const stored = localStorage.getItem("bt_tab");
    if (stored && TAB_KEYS[stored]) activeTab = stored;
  } catch (e) {
    /* storage indisponible */
  }

  skeletonGrid("rows", 6);
  wireEvents();
  await populateLangSelect();
  await loadCatalog(LANG);
  applyI18n();
  paintTabChrome();
  /* Le formulaire est charge sans etre ouvert : l'utilisateur voit la
     configuration effective et les modeles installes des l'ouverture du
     panneau, sans avoir a lancer un test au prealable. */
  llmState.modelsLoaded = true;
  await loadLLMConfig().catch(() => {});
  await refresh();

  try {
    const initialScan = await fetchJSON("/api/scan/status");
    renderScan(initialScan);
    /* Un scan lance avant le rechargement de la page doit continuer a
       etre suivi : sans cela le statut gele jusqu'au prochain clic. */
    if (initialScan.status === "running") pollScan();
  } catch (e) {
    renderScan({ status: "idle" });
  }
})();
