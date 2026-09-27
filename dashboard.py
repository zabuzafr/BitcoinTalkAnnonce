"""Tableau de bord Web pour les annonces cryptographiques analysées."""

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import bitcointalk
from i18n import DEFAULT_LANG, T, available_languages

DB_PATH = os.environ.get("BT_DB_PATH", str(Path(__file__).parent / "crypto_analysis.db"))

app = FastAPI(title="BitcoinTalk Dashboard", version="1.0")

_scan_lock = asyncio.Lock()
_scan_init_lock = asyncio.Lock()
_scan_state: Dict[str, Any] = {
    "status": "idle",
    "scan_id": None,
    "pages_done": 0,
    "pages_total": 0,
    "started_at": None,
    "finished_at": None,
    "message": None,
}
_reanalyze_sem = asyncio.Semaphore(2)
_reanalyzing: set = set()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value or []


def _row_to_project(row: sqlite3.Row, include_lists: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "topic_id": row["topic_id"],
        "title": row["title"],
        "author": row["author"],
        "post_date": row["post_date"],
        "technical_score": row["technical_score"],
        "innovation_score": row["innovation_score"],
        "disruptiveness_score": row["disruptiveness_score"],
        "credibility_score": row["credibility_score"],
        "risk_score": row["risk_score"],
        "premine_percentage": row["premine_percentage"],
        "is_fork": bool(row["is_fork"]),
        "fork_base": row["fork_base"] or "",
        "mining_algorithm": row["mining_algorithm"] or "",
        "consensus_mechanism": row["consensus_mechanism"] or "",
        "final_score": row["final_score"],
        "github_link": row["github_link"] or "",
        "whitepaper_link": row["whitepaper_link"] or "",
        "website_link": row["website_link"] or "",
        "analysis_date": row["analysis_date"] or "",
        "last_updated": row["last_updated"] or "",
        "is_promising": bool(row["is_promising"]),
        "is_active": bool(row["is_active"]),
    }
    if include_lists:
        data["unique_features"] = _decode_json(row["unique_features"])
        data["red_flags"] = _decode_json(row["red_flags"])
        data["strengths"] = _decode_json(row["strengths"])
        data["content"] = row["content"] or ""
    return data


def _t(request: Request, key: str, **kwargs: Any) -> str:
    lang = T.resolve(getattr(request, "headers", {}).get("accept-language"))
    return T.t(key, lang=lang, **kwargs)


def _set_active(topic_id: int, active: bool) -> Optional[int]:
    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM projects WHERE topic_id = ?", (topic_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE projects SET is_active = ?, last_updated = ? WHERE topic_id = ?",
            (int(active), datetime.now().isoformat(), topic_id),
        )
        conn.commit()
        return row["id"]
    finally:
        conn.close()


async def _do_reanalyze(topic_id: int) -> None:
    try:
        async with _reanalyze_sem:
            await asyncio.to_thread(bitcointalk.reanalyze_project, topic_id)
    finally:
        _reanalyzing.discard(topic_id)


@app.get("/")
def dashboard() -> HTMLResponse:
    html = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BitcoinTalk — Tableau de bord</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #e6edf3; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
h1 { font-size: 1.8rem; margin-bottom: 20px; font-weight: 600; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
h1 span { color: #f7931a; }
.lang-select { padding: 6px 10px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 0.85rem; font-weight: 400; }
.lang-select:focus { outline: none; border-color: #58a6ff; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 30px; }
.stat-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.stat-card .label { font-size: 0.8rem; color: #8b949e; margin-bottom: 4px; }
.stat-card .value { font-size: 1.6rem; font-weight: 700; }
.stat-card .value.good { color: #3fb950; }
.stat-card .value.bad { color: #f85149; }
table { width: 100%; border-collapse: collapse; background: #161b22; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #21262d; }
th { background: #1c2128; color: #8b949e; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; cursor: pointer; user-select: none; }
th:hover { color: #e6edf3; }
tr:last-child td { border-bottom: none; }
tr { cursor: pointer; }
tr:hover { background: #1c2128; }
.score { font-weight: 700; font-size: 1rem; }
.score.excellent { color: #3fb950; }
.score.good { color: #d29922; }
.score.poor { color: #f85149; }
.score.unanalyzed { color: #8b949e; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
.badge.promising { background: rgba(63,185,80,0.15); color: #3fb950; }
.badge.fork { background: rgba(139,148,158,0.15); color: #8b949e; }
.badge.normal { background: rgba(247,147,26,0.15); color: #f7931a; }
.title-link { color: #58a6ff; text-decoration: none; }
.title-link:hover { text-decoration: underline; }
.detail-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); z-index: 100; justify-content: center; align-items: flex-start; padding: 40px 20px; overflow-y: auto; }
.detail-overlay.open { display: flex; }
.detail-content { background: #161b22; border: 1px solid #30363d; border-radius: 8px; max-width: 800px; width: 100%; padding: 24px; }
.detail-content h2 { font-size: 1.3rem; margin-bottom: 16px; padding-right: 40px; }
.detail-close { position: absolute; top: 12px; right: 16px; background: none; border: none; color: #8b949e; font-size: 1.5rem; cursor: pointer; }
.detail-close:hover { color: #e6edf3; }
.detail-section { margin-bottom: 16px; }
.detail-section h3 { font-size: 0.9rem; color: #8b949e; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
.detail-section ul { list-style: none; }
.detail-section li { padding: 4px 0; padding-left: 16px; position: relative; }
.detail-section li::before { content: '•'; position: absolute; left: 0; color: #8b949e; }
.detail-section li.flag::before { color: #f85149; }
.detail-section li.good::before { color: #3fb950; }
.detail-section li.feature::before { color: #58a6ff; }
.meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.meta-item { background: #0d1117; border-radius: 6px; padding: 10px; }
.meta-item .k { font-size: 0.75rem; color: #8b949e; }
.meta-item .v { font-size: 0.9rem; font-weight: 600; margin-top: 2px; word-break: break-word; }
.score-bar { display: flex; gap: 6px; margin-top: 12px; }
.score-bar .bar { height: 6px; border-radius: 3px; background: #30363d; flex: 1; position: relative; }
.score-bar .bar .fill { height: 100%; border-radius: 3px; }
.search { width: 100%; padding: 10px 14px; margin-bottom: 20px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 0.95rem; }
.search:focus { outline: none; border-color: #58a6ff; }
.empty { text-align: center; padding: 40px; color: #8b949e; }
.empty a { color: #58a6ff; }
.loading { text-align: center; padding: 40px; color: #8b949e; }
a.link { color: #58a6ff; text-decoration: none; font-size: 0.85rem; }
a.link:hover { text-decoration: underline; }
.detail-links { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; }

.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 16px; }
.panel { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 14px; }
.panel h3 { margin: 0 0 10px; font-size: 14px; color: #e6edf3; }
.ts-chart { display: flex; align-items: flex-end; gap: 4px; height: 120px; padding-top: 8px; }
.ts-bar { flex: 1; background: #58a6ff; border-radius: 3px 3px 0 0; min-height: 2px; position: relative; }
.ts-bar span { position: absolute; top: -20px; left: 50%; transform: translateX(-50%); font-size: 11px; color: #e6edf3; opacity: 0; }
.ts-bar:hover span { opacity: 1; }
.ts-labels { display: flex; justify-content: space-between; margin-top: 6px; font-size: 11px; color: #8b949e; }
.algo-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13px; }
.algo-row .name { width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.algo-bar { flex: 1; height: 10px; background: #21262d; border-radius: 5px; overflow: hidden; }
.algo-bar .fill { height: 100%; background: #3fb950; }
.algo-row .pct { width: 48px; text-align: right; color: #8b949e; }

.badge-active { background: #1a3d2a; color: #3fb950; border: 1px solid #2ea043; padding: 1px 8px; border-radius: 10px; font-size: 11px; white-space: nowrap; }
.badge-inactive { background: #21262d; color: #8b949e; border: 1px solid #484f58; padding: 1px 8px; border-radius: 10px; font-size: 11px; white-space: nowrap; }
.badge-algo-unknown { background: #21262d; color: #8b949e; border: 1px dashed #484f58; padding: 1px 8px; border-radius: 10px; font-size: 11px; white-space: nowrap; }

.btn { background: #21262d; color: #e6edf3; border: 1px solid #30363d; border-radius: 6px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
.btn:hover { border-color: #58a6ff; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-warn { background: #3a2a00; border-color: #9e6a03; color: #d29922; }
.btn-warn:hover { border-color: #d29922; }
.btn-danger { background: #3a1015; border-color: #da3633; color: #f85149; }
.btn-danger:hover { border-color: #f85149; }
.action-row { display: flex; gap: 8px; margin: 10px 0; flex-wrap: wrap; }

.scan-progress { height: 10px; background: #21262d; border-radius: 5px; overflow: hidden; margin: 8px 0; }
.scan-progress .fill { height: 100%; background: #58a6ff; width: 0%; transition: width 0.3s; }
.scan-meta { font-size: 12px; color: #8b949e; }

@media (max-width: 900px) {
  .stats-grid { grid-template-columns: 1fr; }
  .ts-chart { height: 80px; }
  .action-row { flex-direction: column; }
}
</style>
</head>
<body>
<div class="container">
  <h1><span class="h1-left">BitcoinTalk — <span data-i18n="dashboard.h1_sub"></span></span>
  <label data-i18n="dashboard.lang_label">Langue</label> <select id="lang-select" class="lang-select"></select></h1>
  <div id="stats">
    <div class="loading" data-i18n="dashboard.loading">Chargement…</div>
  </div>
  <div class="stats-grid">
    <div class="panel ts-panel">
      <h3 data-i18n="dashboard.ts_title">Activité</h3>
      <div class="ts-period" style="margin-bottom:8px;display:flex;gap:6px">
        <button class="btn" data-ts="week" onclick="loadTimeseries('week')" data-i18n="dashboard.ts_week">7j</button>
        <button class="btn" data-ts="month" onclick="loadTimeseries('month')" data-i18n="dashboard.ts_month">30j</button>
        <button class="btn" data-ts="year" onclick="loadTimeseries('year')" data-i18n="dashboard.ts_year">12m</button>
      </div>
      <div id="ts-chart" class="ts-chart"></div>
      <div id="ts-labels" class="ts-labels"></div>
    </div>
    <div class="panel algo-panel">
      <h3 data-i18n="dashboard.algo_title">Algorithmes</h3>
      <div id="algo-list"></div>
    </div>
    <div class="panel scan-panel">
      <h3 data-i18n="dashboard.scan_title">Scan</h3>
      <div style="display:flex;gap:8px;margin-bottom:8px">
        <input type="number" id="scan-pages" class="search" min="1" max="100" value="5" style="margin-bottom:0;width:70px">
        <button class="btn btn-warn" id="btn-scan" onclick="startScan()" data-i18n="dashboard.scan_start">Lancer</button>
      </div>
      <div id="scan-section">
        <div class="scan-progress"><div class="fill" id="scan-fill"></div></div>
        <div class="scan-meta" id="scan-meta" data-i18n="dashboard.scan_idle">Inactif</div>
      </div>
    </div>
  </div>
  <input type="text" id="search" class="search" data-i18n-ph="dashboard.search_placeholder" placeholder="Rechercher par titre, auteur, algorithme…">
  <div id="table-wrap">
    <div class="loading" data-i18n="dashboard.loading">Chargement…</div>
  </div>
</div>

<div class="detail-overlay" id="detail-overlay" onclick="if(event.target===this)closeDetail()">
  <div class="detail-content" id="detail-content" style="position:relative">
    <button class="detail-close" onclick="closeDetail()">&times;</button>
    <div id="detail-body"><div class="loading" data-i18n="dashboard.loading">Chargement…</div></div>
  </div>
</div>

<script>
let allProjects = [];
let CATALOG = {};
let LANG = '__DEFAULT_LANG__';

function detectLang() {
  try {
    const stored = localStorage.getItem('bt_lang');
    if (stored && stored.split('-')[0]) return stored.split('-')[0].toLowerCase();
  } catch (e) {}
  return '__DEFAULT_LANG__';
}
LANG = detectLang();

function t(key) {
  const parts = key.split('.');
  let v = CATALOG;
  for (const p of parts) { v = v && v[p]; }
  return (v != null) ? String(v) : key;
}

function tfmt(key, params) {
  let s = t(key);
  if (params) {
    for (const k of Object.keys(params)) s = s.split('{' + k + '}').join(params[k]);
  }
  return s;
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.innerHTML = tfmt(el.getAttribute('data-i18n'));
  });
  document.querySelectorAll('[data-i18n-ph]').forEach(el => {
    el.setAttribute('placeholder', t(el.getAttribute('data-i18n-ph')));
  });
  const h1sub = document.querySelector('.h1-left');
  if (h1sub) h1sub.innerHTML = 'BitcoinTalk — <span>' + t('dashboard.h1_sub') + '</span>';
  const select = document.getElementById('lang-select');
  if (select) select.value = LANG;
  document.title = t('dashboard.title');
  document.documentElement.lang = LANG;
}

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

async function loadCatalog(lang) {
  CATALOG = await fetchJSON('/api/i18n/' + lang);
}

async function loadStats() {
  try {
    const s = await fetchJSON('/api/stats');
    const cards = [
      { label: t('dashboard.stat_total'), value: s.total_projects, cls: '' },
      { label: t('dashboard.stat_promising'), value: s.promising_count, cls: 'good' },
      { label: t('dashboard.stat_avg'), value: (s.avg_final_score != null) ? s.avg_final_score : t('dashboard.list_empty'), cls: '' },
      { label: t('dashboard.stat_forks'), value: s.fork_count, cls: 'bad' },
      { label: t('dashboard.stat_max'), value: (s.max_final_score != null) ? s.max_final_score : t('dashboard.list_empty'), cls: 'good' },
      { label: t('dashboard.stat_last'), value: (s.last_analysis || t('dashboard.list_empty')), cls: '', small: true }
    ];
    document.getElementById('stats').innerHTML = cards.map(c =>
      '<div class="stat-card"><div class="label">' + esc(c.label) + '</div><div class="value ' + c.cls + '"' + (c.small ? ' style="font-size:0.9rem"' : '') + '>' + esc(c.value) + '</div></div>'
    ).join('');
  } catch (e) {
    document.getElementById('stats').innerHTML = '<div class="empty">' + tfmt('dashboard.error_stats', { error: esc(e.message) }) + '</div>';
  }
}

function scoreClass(s) {
  if (s == null) return 'unanalyzed';
  if (s >= 75) return 'excellent';
  if (s >= 50) return 'good';
  return 'poor';
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function renderTable(projects) {
  const wrap = document.getElementById('table-wrap');
  if (!projects.length) {
    wrap.innerHTML = '<div class="empty">' + t('dashboard.empty_projects') + '</div>';
    return;
  }
  let rows = projects.map(p => {
    const link = p.topic_id ? 'https://bitcointalk.org/index.php?topic=' + p.topic_id : '';
    const meta = '<div class="meta-item"><div class="k">' +
      t('dashboard.score_tech') + ' ' + esc(p.technical_score != null ? p.technical_score : '—') + ' / ' +
      t('dashboard.score_inno') + ' ' + esc(p.innovation_score != null ? p.innovation_score : '—') + ' / ' +
      t('dashboard.score_disp') + ' ' + esc(p.disruptiveness_score != null ? p.disruptiveness_score : '—') + ' / ' +
      t('dashboard.score_cred') + ' ' + esc(p.credibility_score != null ? p.credibility_score : '—') +
      '</div></div>';
    const type = p.is_fork
      ? '<span class="badge fork">' + tfmt('dashboard.badge_fork', { base: esc(p.fork_base || '?') }) + '</span>'
      : '<span class="badge normal">' + t('dashboard.badge_native') + '</span>';
    const status = p.is_promising ? '<span class="badge promising">' + t('dashboard.badge_promising') + '</span>' : '';
    return '<tr onclick="openDetail(' + p.topic_id + ')">' +
      '<td><a class="title-link" href="' + link + '" target="_blank" onclick="event.stopPropagation()">' + esc(p.title || '—') + '</a></td>' +
      '<td>' + esc(p.author || '—') + '</td>' +
      '<td>' + meta + '</td>' +
      '<td><span class="score ' + scoreClass(p.final_score) + '">' + esc(p.final_score != null ? p.final_score : '—') + '</span></td>' +
      '<td>' + type + '</td>' +
      '<td>' + status + '</td>' +
      '<td>' + renderActivityBadge(p) + '</td>' +
      '<td>' + actButtons(p) + '</td>' +
      '</tr>';
  }).join('');
  wrap.innerHTML = '<table><thead><tr>' +
    '<th data-key="title">' + t('dashboard.col_title') + '</th>' +
    '<th data-key="author">' + t('dashboard.col_author') + '</th>' +
    '<th>' + t('dashboard.col_scores') + '</th>' +
    '<th data-key="final_score" class="score-col">' + t('dashboard.col_score') + '</th>' +
    '<th>' + t('dashboard.col_type') + '</th>' +
    '<th>' + t('dashboard.col_status') + '</th>' +
    '<th>' + t('dashboard.col_activity') + '</th>' +
    '<th>' + t('dashboard.col_actions') + '</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table>';
  document.querySelectorAll('th[data-key]').forEach(th => {
    th.addEventListener('click', () => sortTable(th.dataset.key, th));
  });
}

let sortState = { key: null, dir: -1 };
function sortTable(key, th) {
  if (sortState.key === key) sortState.dir *= -1;
  else { sortState.key = key; sortState.dir = 1; }
  allProjects.sort((a, b) => {
    let va = a[key], vb = b[key];
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va == null) va = '';
    if (vb == null) vb = '';
    if (va < vb) return -1 * sortState.dir;
    if (va > vb) return 1 * sortState.dir;
    return 0;
  });
  renderTable(allProjects);
}

async function openDetail(topicId) {
  const body = document.getElementById('detail-body');
  body.innerHTML = '<div class="loading">' + t('dashboard.loading') + '</div>';
  document.getElementById('detail-overlay').classList.add('open');
  try {
    const p = await fetchJSON('/api/projects/' + topicId);
    const dash = t('dashboard.list_empty');
    const list = (arr, cls) => (arr && arr.length)
      ? '<ul>' + arr.map(x => '<li class="' + cls + '">' + esc(x) + '</li>').join('') + '</ul>'
      : '<li style="color:#8b949e">' + dash + '</li>';
    const bar = (label, val) => {
      const v = Math.max(0, Math.min(100, Number(val) || 0));
      const color = v >= 75 ? '#3fb950' : v >= 50 ? '#d29922' : '#f85149';
      return '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><div style="width:110px;font-size:0.8rem;color:#8b949e">' + label + '</div><div class="bar" style="flex:1"><div class="fill" style="width:' + v + '%;background:' + color + '"></div></div><div style="font-size:0.8rem;font-weight:700;width:30px;text-align:right">' + v + '</div></div>';
    };
    const links = [];
    if (p.github_link) links.push('<a class="link" href="' + esc(p.github_link) + '" target="_blank">GitHub</a>');
    if (p.whitepaper_link) links.push('<a class="link" href="' + esc(p.whitepaper_link) + '" target="_blank">Whitepaper</a>');
    if (p.website_link) links.push('<a class="link" href="' + esc(p.website_link) + '" target="_blank">' + t('dashboard.detail_links').split(' ')[0] + '</a>');
    if (p.topic_id) links.push('<a class="link" href="https://bitcointalk.org/index.php?topic=' + p.topic_id + '" target="_blank">Bitcointalk</a>');
    const forkVal = p.is_fork ? tfmt('dashboard.detail_fork_yes', { base: esc(p.fork_base || '?') }) : t('dashboard.detail_fork_no');
    const premineVal = (p.premine_percentage != null) ? esc(p.premine_percentage) + '%' : dash;

    body.innerHTML = '' +
      '<h2>' + esc(p.title || dash) + '</h2>' +
      '<div class="detail-section">' +
      '  <div style="display:flex;justify-content:space-between;align-items:baseline">' +
      '    <span class="k" style="color:#8b949e;font-size:0.9rem">' + t('dashboard.detail_score_final') + '</span>' +
      '    <span class="score ' + scoreClass(p.final_score) + '" style="font-size:2rem">' + esc(p.final_score != null ? p.final_score : dash) + '</span>' +
      '  </div>' +
      '  <div style="margin-top:12px">' + bar(t('dashboard.detail_bar_tech'), p.technical_score) + bar(t('dashboard.detail_bar_inno'), p.innovation_score) + bar(t('dashboard.detail_bar_disp'), p.disruptiveness_score) + bar(t('dashboard.detail_bar_cred'), p.credibility_score) + '</div>' +
      '</div>' +
      '<div class="detail-section"><h3>' + t('dashboard.detail_info') + '</h3><div class="meta-grid">' +
      '  <div class="meta-item"><div class="k">' + t('dashboard.detail_author') + '</div><div class="v">' + esc(p.author || dash) + '</div></div>' +
      '  <div class="meta-item"><div class="k">' + t('dashboard.detail_date') + '</div><div class="v">' + esc(p.post_date || dash) + '</div></div>' +
      '  <div class="meta-item"><div class="k">' + t('dashboard.detail_algo') + '</div><div class="v">' + esc(p.mining_algorithm || dash) + '</div></div>' +
      '  <div class="meta-item"><div class="k">' + t('dashboard.detail_consensus') + '</div><div class="v">' + esc(p.consensus_mechanism || dash) + '</div></div>' +
      '  <div class="meta-item"><div class="k">' + t('dashboard.detail_premine') + '</div><div class="v">' + premineVal + '</div></div>' +
      '  <div class="meta-item"><div class="k">' + t('dashboard.detail_fork') + '</div><div class="v">' + forkVal + '</div></div>' +
      '</div></div>' +
      (links.length ? '<div class="detail-section"><h3>' + t('dashboard.detail_links') + '</h3><div class="detail-links">' + links.join('') + '</div></div>' : '') +
      '<div class="detail-section"><h3>' + t('dashboard.detail_strengths') + '</h3>' + list(p.strengths, 'good') + '</div>' +
      '<div class="detail-section"><h3>' + t('dashboard.detail_features') + '</h3>' + list(p.unique_features, 'feature') + '</div>' +
      '<div class="detail-section"><h3>' + t('dashboard.detail_flags') + '</h3>' + list(p.red_flags, 'flag') + '</div>' +
      '<div class="detail-section"><h3>' + t('dashboard.detail_content') + '</h3><p style="white-space:pre-wrap;font-size:0.85rem;color:#8b949e">' + esc(p.content || '') + '</p></div>' +
       '<div class="detail-section">' +
       '  <div style="font-size:0.8rem;color:#8b949e">' + tfmt('dashboard.detail_analyzed', { date: esc(p.analysis_date || dash) }) + '</div>' +
       '  <div id="hist-' + p.topic_id + '" style="margin-top:8px"><div class="loading" style="padding:8px">' + t('dashboard.detail_history') + '</div></div>' +
       '</div>' +
       '<div class="detail-section">' + actButtons(p) + '</div>';
    fetchJSON('/api/history/' + p.topic_id).then(h => {
      const el = document.getElementById('hist-' + p.topic_id);
      if (el) el.innerHTML = h.length
        ? '<ul>' + h.map(x => '<li>' + esc(x.analysis_date) + ' — <span class="score ' + scoreClass(x.score) + '">' + esc(x.score) + '</span>' + (x.notes ? ' — ' + esc(x.notes) : '') + '</li>').join('') + '</ul>'
        : '<li style="color:#8b949e">' + t('dashboard.detail_no_history') + '</li>';
    }).catch(() => {});
  } catch (e) {
    body.innerHTML = '<div class="empty">' + tfmt('dashboard.error_detail', { error: esc(e.message) }) + '</div>';
  }
}

function closeDetail() {
  document.getElementById('detail-overlay').classList.remove('open');
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDetail(); });

document.getElementById('search').addEventListener('input', function() {
  const q = this.value.toLowerCase();
  if (!q) { renderTable(allProjects); return; }
  renderTable(allProjects.filter(p =>
    (p.title || '').toLowerCase().includes(q) ||
    (p.author || '').toLowerCase().includes(q) ||
    (p.mining_algorithm || '').toLowerCase().includes(q) ||
    (p.consensus_mechanism || '').toLowerCase().includes(q)
  ));
});

async function populateLangSelect() {
  const select = document.getElementById('lang-select');
  let langs = [];
  try {
    langs = await fetchJSON('/api/i18n/languages');
  } catch (e) {
    langs = [{ code: '__DEFAULT_LANG__', name: 'Français' }, { code: 'en', name: 'English' }];
  }
  select.innerHTML = langs.map(l => '<option value="' + l.code + '"' + (l.code === LANG ? ' selected' : '') + '>' + esc(l.name) + '</option>').join('');
}

async function switchLanguage(lang) {
  LANG = lang;
  try {
    localStorage.setItem('bt_lang', lang);
  } catch (e) {}
  await loadCatalog(lang);
  applyI18n();
  await loadStats();
  renderTable(allProjects);
}

function renderActivityBadge(p) {
  if (p.is_active === false) return '<span class="badge-inactive">' + t('dashboard.badge_inactive') + '</span>';
  return '<span class="badge-active">' + t('dashboard.badge_active') + '</span>';
}

function actButtons(p) {
  const disabled = p.is_active === false;
  const busy = p._busy === true;
  const actDis = disabled ? '' : '<button class="btn" data-topic="' + p.topic_id + '" data-act="disable" ' + (busy ? 'disabled ' : '') + 'onclick="event.stopPropagation();actCall(' + p.topic_id + ',&#39;disable&#39;)">' + t('dashboard.act_disable') + '</button>';
  const actEn = disabled ? '<button class="btn" data-topic="' + p.topic_id + '" data-act="enable" ' + (busy ? 'disabled ' : '') + 'onclick="event.stopPropagation();actCall(' + p.topic_id + ',&#39;enable&#39;)">' + t('dashboard.act_enable') + '</button>' : '';
  const actAn = busy ? '' : '<button class="btn" data-topic="' + p.topic_id + '" data-act="reanalyze" onclick="event.stopPropagation();actCall(' + p.topic_id + ',&#39;reanalyze&#39;)">' + t('dashboard.act_reanalyze') + '</button>';
  return '<div class="action-row">' + actDis + actEn + actAn + '</div>';
}

function patchLocal(topicId, patch) {
  const idx = allProjects.findIndex(p => p.topic_id === topicId);
  if (idx >= 0) Object.assign(allProjects[idx], patch);
}

async function actCall(topicId, action) {
  patchLocal(topicId, { _busy: true });
  renderTable(allProjects);
  try {
    const path = action === 'reanalyze'
      ? '/api/projects/' + topicId + '/analyze'
      : '/api/projects/' + topicId + '/' + action;
    const r = await fetchJSON(path, { method: 'POST' });
    if (action === 'reanalyze') {
      patchLocal(topicId, { _busy: false });
      toast(t('dashboard.act_queued'));
    } else {
      const isActive = action === 'enable';
      patchLocal(topicId, { is_active: isActive, _busy: false });
      toast(t(isActive ? 'dashboard.act_enabled' : 'dashboard.act_disabled'));
    }
    renderTable(allProjects);
    loadStats();
    const ov = document.getElementById('detail-overlay');
    if (ov && ov.classList.contains('open')) openDetail(topicId);
  } catch (e) {
    patchLocal(topicId, { _busy: false });
    renderTable(allProjects);
    if (String(e.message).includes('409')) toast(t('dashboard.error_busy'), true);
    else if (String(e.message).includes('503')) toast(t('dashboard.error_down'), true);
    else toast(e.message, true);
  }
}

function toast(msg, isError) {
  let el = document.getElementById('toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.style.display = 'block';
  el.style.color = isError ? '#f85149' : '#3fb950';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.display = 'none'; }, 3000);
}

function renderScan(state) {
  const fill = document.getElementById('scan-fill');
  const meta = document.getElementById('scan-meta');
  if (!fill) return;
  if (state.status === 'running') {
    const pct = state.pages_done != null && state.total_pages != null
      ? Math.round((state.pages_done / state.total_pages) * 100) : 0;
    fill.style.width = pct + '%';
    meta.textContent = t('dashboard.scan_running') + ' ' + t('dashboard.scan_pages').replace('{done}', state.pages_done ?? '?').replace('{total}', state.total_pages ?? '?');
  } else if (state.status === 'done') {
    fill.style.width = '100%';
    meta.textContent = t('dashboard.scan_done') + ' — ' + t('dashboard.scan_found').replace('{n}', state.new_projects ?? 0);
  } else if (state.status === 'failed') {
    fill.style.width = '0%';
    meta.textContent = t('dashboard.scan_failed') + (state.error ? ' — ' + state.error : '');
  } else {
    fill.style.width = '0%';
    meta.textContent = t('dashboard.scan_idle');
  }
}

async function pollScan(scanId) {
  const tick = async () => {
    let state;
    try { state = await fetchJSON('/api/scan/status'); } catch (e) { return; }
    renderScan(state);
    if (state.status === 'running') { pollScan._timer = setTimeout(tick, 2000); }
    else if (state.status === 'done') { await loadTimeseries(currentTsPeriod); loadAlgo(currentAlgoTop); loadStats(); }
  };
  await tick();
}

async function startScan() {
  const btn = document.getElementById('btn-scan');
  if (btn) { btn.disabled = true; btn.style.opacity = '0.6'; }
  try {
    const r = await fetchJSON('/api/scan', { method: 'POST' });
    pollScan(r.scan_id);
  } catch (e) {
    if (String(e.message).includes('202') || String(e.message).includes('409')) {
      const state = await fetchJSON('/api/scan/status').catch(() => null);
      if (state) renderScan(state);
    } else {
      toast(t('dashboard.scan_failed') + ' — ' + e.message, true);
    }
  } finally {
    if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
  }
}

let currentTsPeriod = 'week';
let currentAlgoTop = 10;

async function loadTimeseries(period) {
  currentTsPeriod = period;
  const el = document.getElementById('ts-chart');
  const labels = document.getElementById('ts-labels');
  if (!el) return;
  try {
    const data = await fetchJSON('/api/stats/timeseries?period=' + period + '&include_inactive=0');
    const buckets = data.buckets || [];
    const vals = buckets.map(b => b.count || 0);
    const max = Math.max(...vals, 1);
    el.innerHTML = buckets.map((b, i) =>
      '<div class="ts-bar-wrap"><div class="ts-bar" style="height:' + Math.round((vals[i] / max) * 100) + '%" title="' + esc(b.label) + ': ' + vals[i] + '"></div></div>'
    ).join('');
    if (labels) labels.innerHTML = buckets.map(b => '<span class="ts-label">' + esc(b.label) + '</span>').join('');
  } catch (e) {
    el.innerHTML = '<div class="empty" style="padding:12px">' + esc(e.message) + '</div>';
  }
}

async function loadAlgo(top) {
  currentAlgoTop = top;
  const el = document.getElementById('algo-list');
  if (!el) return;
  try {
    const data = await fetchJSON('/api/stats/by_algorithm?top=' + top + '&include_inactive=0');
    const total = data.total || 0;
    el.innerHTML = '<div style="font-size:0.8rem;color:#8b949e;margin-bottom:8px">' + t('dashboard.algo_total').replace('{n}', total) + '</div>' +
      (data.algorithms || []).map(a => {
        const label = a.algorithm === 'unknown' || !a.algorithm
          ? t('dashboard.algo_unknown') : esc(a.algorithm);
        const pct = a.pct != null ? a.pct : (total ? Math.round((a.count / total) * 100) : 0);
        return '<div class="algo-row"><span class="algo-label">' + label + '</span>' +
          '<div class="algo-bar"><div class="fill" style="width:' + pct + '%"></div></div>' +
          '<span class="algo-count">' + a.count + '</span></div>';
      }).join('');
  } catch (e) {
    el.innerHTML = '<div class="empty" style="padding:12px">' + esc(e.message) + '</div>';
  }
}

(async function init() {
  await populateLangSelect();
  await loadCatalog(LANG);
  applyI18n();
  await loadStats();
  try {
    allProjects = await fetchJSON('/api/projects');
    allProjects.sort((a, b) => (b.final_score ?? 0) - (a.final_score ?? 0));
  } catch (e) {
    allProjects = [];
  }
  renderTable(allProjects);
  await loadTimeseries('month');
  await loadAlgo(10);
  const select = document.getElementById('lang-select');
  if (select) select.addEventListener('change', () => switchLanguage(select.value));
})();
</script>
</body>
</html>
""".replace("__DEFAULT_LANG__", DEFAULT_LANG)
    return HTMLResponse(html)


@app.get("/api/i18n/languages")
def api_i18n_languages() -> JSONResponse:
    return JSONResponse(available_languages())


@app.get("/api/i18n/{lang}")
def api_i18n_catalog(lang: str) -> JSONResponse:
    catalog = T.resolved_catalog(lang)
    if not catalog:
        raise HTTPException(status_code=404, detail=T.t("api.lang_not_found", lang=lang))
    return JSONResponse(catalog)


@app.get("/api/stats")
def api_stats() -> JSONResponse:
    path = Path(DB_PATH)
    if not path.exists():
        return JSONResponse({
            "db_exists": False,
            "total_projects": 0,
            "active_count": 0,
            "inactive_count": 0,
            "promising_count": 0,
            "fork_count": 0,
            "avg_final_score": None,
            "max_final_score": None,
            "min_final_score": None,
            "last_analysis": None,
        })
    with _connect() as conn:
        cur = conn.execute("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(is_promising), 0) AS promising,
                   COALESCE(SUM(CASE WHEN is_fork THEN 1 ELSE 0 END), 0) AS forks,
                   COALESCE(SUM(CASE WHEN is_active THEN 1 ELSE 0 END), 0) AS active,
                   AVG(final_score) AS avg_score,
                   MAX(final_score) AS max_score,
                   MIN(final_score) AS min_score,
                   MAX(analysis_date) AS last_analysis
            FROM projects
        """)
        row = cur.fetchone()
    total = row[0] or 0
    active = row[3] or 0
    return JSONResponse({
        "db_exists": True,
        "total_projects": total,
        "active_count": active,
        "inactive_count": total - active,
        "promising_count": row[1] or 0,
        "fork_count": row[2] or 0,
        "avg_final_score": round(row[4], 1) if row[4] is not None else None,
        "max_final_score": row[5],
        "min_final_score": row[6],
        "last_analysis": row[7],
    })


@app.get("/api/projects")
def api_projects(
    include_inactive: int = 0,
    q: Optional[str] = None,
) -> List[Dict[str, Any]]:
    path = Path(DB_PATH)
    if not path.exists():
        return []
    clauses: List[str] = []
    params: List[Any] = []
    if not include_inactive:
        clauses.append("is_active = 1")
    if q:
        clauses.append("(title LIKE ? OR author LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT topic_id, title, author, post_date, technical_score, innovation_score, "
        "disruptiveness_score, credibility_score, risk_score, premine_percentage, is_fork, "
        "fork_base, mining_algorithm, consensus_mechanism, final_score, github_link, "
        "whitepaper_link, website_link, analysis_date, last_updated, is_promising, is_active "
        f"FROM projects{where} ORDER BY final_score DESC NULLS LAST, analysis_date DESC"
    )
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_project(r) for r in rows]


@app.get("/api/projects/{topic_id}")
def api_project_detail(request: Request, topic_id: int) -> Dict[str, Any]:
    path = Path(DB_PATH)
    if not path.exists():
        raise HTTPException(status_code=404, detail=_t(request, "api.db_not_found"))
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT topic_id, title, author, post_date, content, technical_score, innovation_score, "
            "disruptiveness_score, credibility_score, risk_score, premine_percentage, is_fork, "
            "fork_base, mining_algorithm, consensus_mechanism, unique_features, red_flags, "
            "strengths, final_score, github_link, whitepaper_link, website_link, analysis_date, "
            "last_updated, is_promising, is_active FROM projects WHERE topic_id = ?",
            (topic_id,)
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=_t(request, "api.project_not_found"))
    return _row_to_project(row, include_lists=True)


@app.get("/api/history/{topic_id}")
def api_history(request: Request, topic_id: int) -> Dict[str, Any]:
    if not Path(DB_PATH).exists():
        raise HTTPException(status_code=404, detail=_t(request, "api.project_not_found"))
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT topic_id FROM projects WHERE topic_id = ?", (topic_id,)
        )
        exists = cur.fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail=_t(request, "api.project_not_found"))
        cur = conn.execute(
            "SELECT topic_id, analysis_date, score, notes FROM analysis_history "
            "WHERE topic_id = ? ORDER BY analysis_date DESC",
            (topic_id,)
        )
        rows = cur.fetchall()
    return {"topic_id": topic_id, "history": [dict(r) for r in rows]}


@app.post("/api/projects/{topic_id}/disable")
def api_project_disable(request: Request, topic_id: int) -> Dict[str, Any]:
    path = Path(DB_PATH)
    if not path.exists():
        raise HTTPException(status_code=404, detail=_t(request, "api.db_not_found"))
    result = _set_active(topic_id, False)
    if result is None:
        raise HTTPException(status_code=404, detail=_t(request, "api.project_not_found"))
    return {"id": result, "status": "disabled"}


@app.post("/api/projects/{topic_id}/enable")
def api_project_enable(request: Request, topic_id: int) -> Dict[str, Any]:
    path = Path(DB_PATH)
    if not path.exists():
        raise HTTPException(status_code=404, detail=_t(request, "api.db_not_found"))
    result = _set_active(topic_id, True)
    if result is None:
        raise HTTPException(status_code=404, detail=_t(request, "api.project_not_found"))
    return {"id": result, "status": "enabled"}


def _ollama_available() -> bool:
    try:
        import ollama

        ollama.list()
        return True
    except Exception:
        return False


@app.post("/api/projects/{topic_id}/analyze")
async def api_project_analyze(request: Request, topic_id: int) -> Dict[str, Any]:
    path = Path(DB_PATH)
    if not path.exists():
        raise HTTPException(status_code=404, detail=_t(request, "api.db_not_found"))
    with _connect() as conn:
        row = conn.execute("SELECT topic_id FROM projects WHERE topic_id = ?", (topic_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=_t(request, "api.project_not_found"))
    if topic_id in _reanalyzing:
        raise HTTPException(status_code=409, detail="analyzing")
    if not _ollama_available():
        raise HTTPException(status_code=503, detail="ollama_unavailable")
    asyncio.create_task(_do_reanalyze(topic_id))
    return {"topic_id": topic_id, "status": "queued"}


@app.get("/api/stats/timeseries")
def api_stats_timeseries(
    period: str = "week",
    include_inactive: int = 0,
) -> Dict[str, Any]:
    if period not in ("week", "month", "year"):
        raise HTTPException(status_code=422, detail="period must be week, month or year")
    if include_inactive not in (0, 1):
        raise HTTPException(status_code=422, detail="include_inactive must be 0 or 1")
    row_fmt = {"week": "%Y-W%W", "month": "%Y-%m", "year": "%Y"}[period]
    sql = "SELECT strftime(?, analysis_date) AS bucket, COUNT(*) AS n FROM projects"
    params: List[Any] = [row_fmt]
    if not include_inactive:
        sql += " WHERE is_active = 1"
    sql += " GROUP BY bucket ORDER BY bucket DESC"
    if not Path(DB_PATH).exists():
        data: Dict[str, int] = {}
    else:
        with _connect() as conn:
            cur = conn.execute(sql, params)
            data = {r["bucket"]: r["n"] for r in cur.fetchall()}
    anchor = datetime.now()
    if Path(DB_PATH).exists():
        sql_anchor = "SELECT MAX(analysis_date) AS max_date FROM projects"
        if not include_inactive:
            sql_anchor += " WHERE is_active = 1"
        with _connect() as conn:
            anchor_row = conn.execute(sql_anchor).fetchone()
        if anchor_row and anchor_row["max_date"]:
            try:
                anchor = datetime.fromisoformat(anchor_row["max_date"])
            except ValueError:
                anchor = datetime.now()
    if period == "year":
        keys = [str(anchor.year - i) for i in range(11, -1, -1)]
    elif period == "month":
        keys: List[str] = []
        y, m = anchor.year, anchor.month
        for i in range(11, -1, -1):
            y, m = divmod(y * 12 + m - 1 - i, 12)
            m += 1
            keys.append(f"{y}-{m:02d}")
    else:
        keys = [(anchor - timedelta(weeks=i)).strftime("%Y-W%W") for i in range(11, -1, -1)]
    buckets = [{"label": k, "count": data.get(k, 0)} for k in reversed(keys)]
    return {"period": period, "buckets": buckets}


@app.get("/api/stats/by_algorithm")
def api_stats_by_algorithm(top: int = 10, include_inactive: int = 0) -> Dict[str, Any]:
    if include_inactive not in (0, 1):
        raise HTTPException(status_code=422, detail="include_inactive must be 0 or 1")
    top = max(1, min(50, int(top)))
    sql = "SELECT mining_algorithm, COUNT(*) AS n FROM projects"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    sql += " GROUP BY mining_algorithm"
    if Path(DB_PATH).exists():
        with _connect() as conn:
            rows = conn.execute(sql).fetchall()
    else:
        rows = []
    counts: Dict[str, int] = {}
    for r in rows:
        key = (r[0] or "").strip() or "unknown"
        counts[key] = counts.get(key, 0) + r[1]
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    rows_out = [
        {
            "algorithm": name,
            "count": n,
            "pct": round(n * 100.0 / total, 1) if total else 0.0,
        }
        for name, n in ordered
    ]
    return {"total": total, "algorithms": rows_out}


@app.post("/api/scan")
async def api_scan_scan(request: Request, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body = body or {}
    section_id = int(body.get("section_id", 64))
    pages = int(body.get("pages", 5))
    async with _scan_init_lock:
        if _scan_state["status"] == "running":
            return {"scan_id": _scan_state["scan_id"], "status": "running"}
        scan_id = uuid.uuid4().hex
        _scan_state.update(
            status="running",
            scan_id=scan_id,
            pages_done=0,
            pages_total=pages,
            started_at=datetime.now().isoformat(),
            finished_at=None,
            message=None,
        )
    asyncio.create_task(_run_scan(scan_id, section_id, pages))
    return {"scan_id": scan_id, "status": "running"}


@app.get("/api/scan/status")
def api_scan_status() -> Dict[str, Any]:
    return dict(_scan_state)


async def _run_scan(scan_id: str, section_id: int, pages: int) -> None:
    async with _scan_lock:
        try:
            analyzer = bitcointalk.UltimateBitcointalkAnalyzer(db_path=DB_PATH, timeout=30)
            async with _scan_init_lock:
                if _scan_state["scan_id"] == scan_id:
                    _scan_state["message"] = "scanning"
            await analyzer.scan_bitcointalk_section(section_id, pages)
            async with _scan_init_lock:
                if _scan_state["scan_id"] == scan_id:
                    _scan_state.update(status="done", pages_done=pages, finished_at=datetime.now().isoformat())
        except Exception as exc:
            async with _scan_init_lock:
                if _scan_state["scan_id"] == scan_id:
                    _scan_state.update(status="error", message=str(exc), finished_at=datetime.now().isoformat())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("BT_DASH_HOST", "0.0.0.0"), port=8080)
