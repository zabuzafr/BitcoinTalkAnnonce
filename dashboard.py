"""Tableau de bord Web pour les annonces cryptographiques analysées."""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import bitcointalk
import llm
import urlclean
from i18n import DEFAULT_LANG, T, available_languages

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = os.environ.get("BT_DB_PATH", str(BASE_DIR / "crypto_analysis.db"))
# Migration au démarrage : les endpoints lisent des colonnes v2, et le
# dashboard ne construit jamais le schéma par lui-même (seul l'analyseur le
# fait). Sans cela, une base d'une version antérieure casse /api/projects.
bitcointalk.ensure_schema(DB_PATH)
DEFAULT_SECTION_ID = int(os.environ.get("BT_BOARD_ID", "159"))
MAX_SCAN_PAGES = 20

app = FastAPI(title="BitcoinTalk Dashboard", version="2.0")

_scan_lock = asyncio.Lock()
_scan_init_lock = asyncio.Lock()
_scan_state: Dict[str, Any] = {
    "status": "idle",
    "scan_id": None,
    "section_id": None,
    "pages_done": 0,
    "pages_total": 0,
    "topics_found": 0,
    "analyzed_count": 0,
    "skipped_count": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_reanalyze_sem = asyncio.Semaphore(2)
_reanalyzing: set = set()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _col(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value or []


_PROJECT_COLUMNS: tuple = (
    "topic_id", "title", "author", "post_date", "technical_score",
    "innovation_score", "disruptiveness_score", "credibility_score", "risk_score",
    "premine_percentage", "is_fork", "fork_base", "mining_algorithm",
    "consensus_mechanism", "unique_features", "red_flags", "strengths", "final_score",
    "github_link", "whitepaper_link", "website_link", "analysis_date", "last_updated",
    "is_promising", "is_active", "quality_level", "innovation_level",
    "quantum_risk_score", "mining_type", "quality_note", "github_stars",
    "discord_members", "telegram_members", "hashrate_str", "mining_status",
    "token_distribution", "team_allocation", "vesting_period", "inflation_model",
    "burn_mechanisms", "utility_description", "tokenomics_risks", "tokenomics_score",
    "coingecko_id", "coingecko_symbol", "coingecko_name", "coingecko_price_usd",
    "coingecko_market_cap_usd", "coingecko_volume_usd", "coingecko_rank",
    "github_forks", "github_open_issues", "github_language", "github_updated_at",
    "mining_active_pools", "mining_pool_list", "mining_link", "mining_guide_json",
    "whitepaper_text",
    "source_analysis", "social_json",
)

_SELECT_PROJECT_COLUMNS: str = ", ".join(_PROJECT_COLUMNS)


def _stored_link(raw: Any, kind: str) -> str:
    """Lien stocké, nettoyé à la lecture.

    Les annonces sont du texte brut collé : une ligne peut contenir
    `…/xCoin⛏️` ou deux URL bout à bout. Corriger l'affichage ne dispense pas
    de réparer les données, mais l'interface ne doit jamais proposer un lien
    mort. Un lien inexploitable est masqué plutôt que transmis tel quel.
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if kind == "github":
        return urlclean.canonical_github(value)
    cleaned = urlclean.clean_url(value)
    if kind == "website" and cleaned and not urlclean.is_project_site(cleaned):
        return ""
    return cleaned


def _stored_mining_guide(raw: Any) -> Dict[str, Any]:
    """Réglages de minage stockés, normalisés pour l'interface.

    Un hôte de piscine n'a pas de schéma : l'interface ne peut pas le rendre
    cliquable en HTTP, il reste donc du texte à copier dans le mineur.
    """
    guide = _decode_json(raw)
    if not isinstance(guide, dict):
        return {}
    normalized = dict(guide)
    normalized["pool"] = [str(host) for host in guide.get("pool") or [] if host]
    normalized["stratum"] = [str(item) for item in guide.get("stratum") or [] if item]
    return normalized if any(normalized.values()) else {}


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
        "github_link": _stored_link(row["github_link"], "github"),
        "whitepaper_link": _stored_link(row["whitepaper_link"], "whitepaper"),
        "website_link": _stored_link(row["website_link"], "website"),
        "analysis_date": row["analysis_date"] or "",
        "last_updated": row["last_updated"] or "",
        "is_promising": bool(row["is_promising"]),
        "is_active": bool(row["is_active"]),
        "quality_level": row["quality_level"] or "",
        "innovation_level": row["innovation_level"] or "",
        "quantum_risk_score": row["quantum_risk_score"] or 0.0,
        "mining_type": row["mining_type"] or "",
        "quality_note": row["quality_note"] or "",
        "github_stars": _col(row, "github_stars", 0) or 0,
        "github_forks": _col(row, "github_forks", 0) or 0,
        "github_open_issues": _col(row, "github_open_issues", 0) or 0,
        "github_language": _col(row, "github_language", "") or "",
        "github_updated_at": _col(row, "github_updated_at", "") or "",
        "discord_members": _col(row, "discord_members", 0) or 0,
        "telegram_members": _col(row, "telegram_members", 0) or 0,
        "mining_status": _col(row, "mining_status", "") or "",
        "hashrate_str": _col(row, "hashrate_str", "") or "",
        "mining_active_pools": _col(row, "mining_active_pools", 0) or 0,
        "mining_pool_list": _decode_json(_col(row, "mining_pool_list", "")),
        "mining_link": _col(row, "mining_link", "") or "",
        "mining_guide": _stored_mining_guide(_col(row, "mining_guide_json", "")),
        "coingecko_id": _col(row, "coingecko_id", "") or "",
        "coingecko_symbol": _col(row, "coingecko_symbol", "") or "",
        "coingecko_name": _col(row, "coingecko_name", "") or "",
        "coingecko_price_usd": _col(row, "coingecko_price_usd", None),
        "coingecko_market_cap_usd": _col(row, "coingecko_market_cap_usd", None),
        "coingecko_volume_usd": _col(row, "coingecko_volume_usd", None),
        "coingecko_rank": _col(row, "coingecko_rank", None),
        "token_distribution": _col(row, "token_distribution", "") or "",
        "team_allocation": _col(row, "team_allocation", "") or "",
        "vesting_period": _col(row, "vesting_period", "") or "",
        "inflation_model": _col(row, "inflation_model", "") or "",
        "burn_mechanisms": _decode_json(_col(row, "burn_mechanisms", "")),
        "utility_description": _col(row, "utility_description", "") or "",
        "tokenomics_risks": _decode_json(_col(row, "tokenomics_risks", "")),
        "tokenomics_score": _col(row, "tokenomics_score", None),
        "social_metrics": _decode_json(_col(row, "social_json", "")),
        "whitepaper_text": _col(row, "whitepaper_text", "") or "",
        "source_analysis": _decode_json(_col(row, "source_analysis", "")),
    }
    if include_lists:
        data["unique_features"] = _decode_json(row["unique_features"])
        data["red_flags"] = _decode_json(row["red_flags"])
        data["strengths"] = _decode_json(row["strengths"])
        data["content"] = _col(row, "content", "") or ""
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


async def _do_reanalyze(topic_id: int, model: Optional[str] = None, llm_config: Optional[Dict[str, Any]] = None) -> None:
    try:
        async with _reanalyze_sem:
            config = llm.load_config()
            if isinstance(llm_config, dict):
                for key, value in llm_config.items():
                    if value is not None and key in llm.LLMConfig.__dataclass_fields__:
                        setattr(config, key, value)
            if model:
                config.model = model
            await asyncio.to_thread(
                bitcointalk.reanalyze_project, topic_id, model=config.model, llm_config=config
            )
    except Exception:
        logger.exception("reanalyze task failed for topic %s", topic_id)
    finally:
        _reanalyzing.discard(topic_id)


@app.get("/")
def dashboard() -> FileResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__DEFAULT_LANG__", DEFAULT_LANG)
    html = html.replace("__DEFAULT_SECTION__", str(DEFAULT_SECTION_ID))
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
        f"SELECT {_SELECT_PROJECT_COLUMNS} "
        f"FROM projects{where} ORDER BY final_score DESC NULLS LAST, analysis_date DESC"
    )
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_project(r) for r in rows]


@app.get("/api/promising")
def api_promising(
    request: Request,
    include_inactive: int = 0,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    limit = min(limit, 100) or 30
    path = Path(DB_PATH)
    if not path.exists():
        return []

    active_clauses: List[str] = ["p.is_active = 1", "p.is_promising = 1"]
    inactive_clauses: List[str] = ["p.is_promising = 1"]

    clauses = inactive_clauses if include_inactive else active_clauses
    where = f" WHERE {' AND '.join(clauses)}"

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rank_sql = (
            f"SELECT * FROM ("
            f"  SELECT p.*, ROW_NUMBER() OVER (ORDER BY COALESCE(p.final_score, 0) DESC, p.analysis_date DESC) AS rank_num "
            f"  FROM projects p{where}"
            f") WHERE rank_num <= ? ORDER BY rank_num ASC"
        )
        cur = conn.execute(rank_sql, (limit,))
        rows = cur.fetchall()

    return [_row_to_project(r) for r in rows]


@app.get("/api/trending")
def api_trending(
    request: Request,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    limit = min(limit, 100) or 30
    path = Path(DB_PATH)
    if not path.exists():
        return []

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        sql = (
            f"SELECT p.*, "
            f"MAX(ah.analysis_date) AS latest_analysis, "
            f"COUNT(ah.topic_id) AS analysis_count "
            f"FROM projects p "
            f"INNER JOIN analysis_history ah ON ah.topic_id = p.topic_id "
            f"WHERE p.is_active = 1 "
            f"GROUP BY p.topic_id "
            f"ORDER BY latest_analysis DESC, COALESCE(p.final_score, 0) DESC "
            f"LIMIT ?"
        )
        cur = conn.execute(sql, (limit,))
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
            f"SELECT {_SELECT_PROJECT_COLUMNS}, content FROM projects WHERE topic_id = ?",
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


def _llm_available() -> tuple[bool, Optional[str]]:
    """Le fournisseur configure est-il joignable ?

    Le test porte sur le fournisseur réellement configuré : exiger Ollama
    empêcherait toute ré-analyse chez qui utilise une API compatible ChatGPT.
    Le retour est `(joignable, detail)` ; `detail` n'est jamais journalisée avec
    une clé, le client de `llm` ne la place pas dans ses messages d'erreur.
    """
    config = llm.load_config()
    try:
        llm.ChatClient(config).probe_sync()
    except llm.LLMError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)[:200]
    return True, None


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
    # Transport bloquant : hors de la boucle, sinon /api/scan/status et le
    # reste du dashboard gelent le temps de la sonde (jusqu'à 15 s).
    available, detail = await asyncio.to_thread(_llm_available)
    if not available:
        return JSONResponse(
            status_code=503,
            content={"detail": "llm_unavailable", "reason": detail or ""},
        )
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
async def api_scan_scan(request: Request, body: Optional[Dict[str, Any]] = None) -> JSONResponse:
    body = body or {}
    section_id = int(body.get("section_id", DEFAULT_SECTION_ID))
    pages = max(1, min(int(body.get("pages", 5)), MAX_SCAN_PAGES))
    async with _scan_init_lock:
        if _scan_state["status"] == "running":
            return JSONResponse(
                {"scan_id": _scan_state["scan_id"], "status": "running"}, status_code=202
            )
        scan_id = uuid.uuid4().hex
        _scan_state.update(
            status="running",
            scan_id=scan_id,
            section_id=section_id,
            pages_done=0,
            pages_total=pages,
            topics_found=0,
            analyzed_count=0,
            skipped_count=0,
            started_at=datetime.now().isoformat(),
            finished_at=None,
            error=None,
        )
    asyncio.create_task(_run_scan(scan_id, section_id, pages))
    return JSONResponse({"scan_id": scan_id, "status": "running"}, status_code=202)


@app.get("/api/scan/status")
def api_scan_status() -> JSONResponse:
    return JSONResponse(dict(_scan_state))


async def _run_scan(scan_id: str, section_id: int, pages: int) -> None:
    async with _scan_lock:
        try:
            analyzer = bitcointalk.UltimateBitcointalkAnalyzer(db_path=DB_PATH, timeout=30)

            def _on_progress(event: Dict[str, Any]) -> None:
                if _scan_state.get("scan_id") == scan_id:
                    _scan_state.update({k: v for k, v in event.items() if k in _scan_state})

            await analyzer.scan_bitcointalk_section(section_id, pages, progress_cb=_on_progress)
            if _scan_state.get("scan_id") == scan_id:
                _scan_state.update(
                    status="done", pages_done=pages, finished_at=datetime.now().isoformat()
                )
        except Exception as exc:
            logger.exception("scan failed")
            if _scan_state.get("scan_id") == scan_id:
                _scan_state.update(
                    status="error", error=str(exc), finished_at=datetime.now().isoformat()
                )


def llm_env_overrides() -> list[str]:
    """Variables d'environnement qui priment sur le fichier de configuration."""
    return sorted(name for name in llm.ENV_KEYS.values() if os.environ.get(name))


@app.get("/api/llm/config")
def api_llm_config_get() -> JSONResponse:
    """Configuration effective. La cle API n'est jamais renvoyee en clair."""
    config = llm.load_config()
    payload = config.masked()
    payload["providers"] = list(llm.PROVIDERS)
    payload["config_path"] = str(llm.config_path())
    payload["env_overrides"] = llm_env_overrides()
    return JSONResponse(payload)


@app.post("/api/llm/config")
def api_llm_config_set(body: Dict[str, Any]) -> JSONResponse:
    """Enregistre la configuration. Une cle API vide ou `__KEEP__` la conserve."""
    current = llm.load_config()
    provider = str(body.get("provider", current.provider)).strip().lower()
    model = str(body.get("model", current.model)).strip()

    candidate = llm.LLMConfig(
        provider=provider,
        model=model,
        ollama_host=str(body.get("ollama_host", current.ollama_host)).strip(),
        api_base=str(body.get("api_base", current.api_base)).strip(),
        temperature=body.get("temperature", current.temperature),
        timeout=body.get("timeout", current.timeout),
    )
    # Le champ est vide quand l'utilisateur n'en saisit pas : dans ce cas, comme
    # avec la sentinelle __KEEP__, la cle deja enregistree est conservee. Sans
    # cela, réenregistrer un formulaire depuis un navigateur sans gestionnaire
    # de mots de passe effacerait silencieusement la cle. Changer d'URL de base
    # detachet la cle : elle reste rattachee a l'hote pour lequel elle a ete
    # enregistree.
    candidate.api_key = _resolve_probe_key(
        body.get("api_key"), current.api_key, current.api_base, candidate.api_base
    )

    try:
        candidate.validate()
    except llm.LLMError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # Toute variable d'environnement prioritaire rendrait l'ecriture inoperante :
    # le fichier serait ecrase puis immediatement ignore au rechargement.
    locked = llm_env_overrides()
    if locked:
        return JSONResponse(
            {"error": T.t("llm.err_env_locked", names=", ".join(locked))},
            status_code=409,
        )

    try:
        llm.save_config(candidate)
    except OSError as exc:
        logger.exception("cannot persist llm config")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(candidate.masked())


def _base_id(url: str) -> tuple:
    """Identifiant normalisé d'une URL de base, pour comparer deux cibles."""
    parsed = urlparse(str(url).strip())
    return (parsed.scheme.lower(), (parsed.hostname or "").lower(),
            parsed.port or "", parsed.path.rstrip("/"))


def _resolve_probe_key(supplied: Any, current: str,
                       current_base: str, target_base: str) -> str:
    """Cle à joindre pour une URL de base donnée.

    Champ vide ou sentinelle : la cle enregistrée n'est transmise qu'à l'URL de
    base à laquelle elle est rattachée. Un appelant qui change `api_base`
    (aperçu avant enregistrement, requête tierce) doit fournir la cle
    correspondante — sans quoi un serveur choisi par l'appelant recevrait le
    secret enregistré, jeton d'autorisation compris.
    """
    if supplied is not None:
        key = str(supplied).strip()
        if key and key != llm.KEY_KEEP:
            return key
    if _base_id(current_base) == _base_id(target_base):
        return current
    return ""


def _llm_candidate(body: Dict[str, Any]) -> llm.LLMConfig:
    """Configuration correspondant aux champs soumis, completee par l'enregistrement.

    `validate()` n'est pas appele : lister des modeles ne demande ni un nom de
    modele ni une cle. Invalider ici bloquerait la selection avant que
    l'utilisateur ait choisi quoi que ce soit.
    """
    current = llm.load_config()
    provider = str(body.get("provider") or current.provider).strip().lower()
    if provider not in llm.PROVIDERS:
        raise llm.LLMError(T.t("llm.err_provider", value=provider,
                               allowed="/".join(llm.PROVIDERS)))
    ollama_host = str(body.get("ollama_host") or current.ollama_host).strip()
    api_base = str(body.get("api_base") or current.api_base).strip()
    # validate() n'est pas appele (voir docstring) : la cible est quand meme
    # verifiee, ne serait-ce que pour refuser une IP de metadonnees cloud.
    if provider == "ollama" and ollama_host:
        llm.check_target(ollama_host, field="ollama_host")
    elif provider == "openai" and api_base:
        llm.check_target(api_base, field="api_base")
    return llm.LLMConfig(
        provider=provider,
        model=str(body.get("model") or current.model),
        ollama_host=ollama_host,
        api_base=api_base,
        api_key=_resolve_probe_key(body.get("api_key"), current.api_key,
                                   current.api_base, api_base),
        temperature=body.get("temperature", current.temperature),
        timeout=body.get("timeout", current.timeout),
    )


@app.post("/api/llm/models")
async def api_llm_models(body: Optional[Dict[str, Any]] = None) -> JSONResponse:
    """Modeles disponibles sur le fournisseur demande.

    Le panneau doit proposer les modeles reellement installes sur la machine,
    pas une liste figee : c'est ce qui permet de choisir un modele avant de
    lancer un scan. Le corps est optionnel et sert a sonder des champs non
    encore enregistres (apercu avant enregistrement, bascule de fournisseur).

    Un fournisseur injoignable donne une liste vide et un message, jamais une
    erreur 500 : le panneau doit rester utilisable pendant qu'un serveur
    demarre.
    """
    try:
        config = _llm_candidate(body or {})
    except llm.LLMError as exc:
        return JSONResponse({"models": [], "error": str(exc)}, status_code=200)
    try:
        listing = await llm.list_models_detailed(config)
    except llm.LLMError as exc:
        return JSONResponse({"models": [], "error": str(exc)}, status_code=200)
    return JSONResponse(listing)


@app.get("/api/models")
async def api_models() -> JSONResponse:
    """Retourne la liste des modèles disponibles pour l'analyse."""
    config = llm.load_config()
    listing = await llm.list_models_detailed(config)
    return JSONResponse(listing)


@app.post("/api/llm/test")
async def api_llm_test(body: Optional[Dict[str, Any]] = None) -> JSONResponse:
    """Teste la joignabilite du fournisseur sans enregistrer quoi que ce soit."""
    body = body or {}
    config = llm.load_config()
    if body.get("provider") or body.get("model") or body.get("ollama_host") or body.get("api_base"):
        target_base = str(body.get("api_base", config.api_base)).strip()
        candidate = llm.LLMConfig(
            provider=str(body.get("provider", config.provider)),
            model=str(body.get("model", config.model)),
            ollama_host=str(body.get("ollama_host", config.ollama_host)),
            api_base=target_base,
            api_key=_resolve_probe_key(body.get("api_key"), config.api_key,
                                       config.api_base, target_base),
            temperature=body.get("temperature", config.temperature),
            timeout=body.get("timeout", config.timeout),
        )
        try:
            candidate.validate()
        except llm.LLMError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        config = candidate

    client = llm.ChatClient(config)
    try:
        result = await client.probe()
    except llm.LLMError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)
    result["provider"] = config.provider
    result["model"] = config.model
    return JSONResponse(result)


@app.post("/api/reanalyze/{topic_id}")
async def api_reanalyze(topic_id: int, body: Optional[Dict[str, Any]] = None) -> JSONResponse:
    """Déclenche une réanalyse pour un projet spécifique.
    
    Le corps optionnel peut contenir:
    - model: le modèle à utiliser pour l'analyse
    - llm_config: configuration LLM personnalisée
    """
    if topic_id in _reanalyzing:
        raise HTTPException(status_code=409, detail=T.t("errors.reanalysis_in_progress", topic_id=topic_id))

    # Transport bloquant : hors de la boucle, sinon /api/scan/status et le
    # reste du dashboard gelent le temps de la sonde (jusqu'à 15 s).
    available, detail = await asyncio.to_thread(_llm_available)
    if not available:
        return JSONResponse(
            status_code=503,
            content={"detail": "llm_unavailable", "reason": detail or ""},
        )

    _reanalyzing.add(topic_id)
    body = body or {}
    asyncio.create_task(
        _do_reanalyze(topic_id, model=body.get("model"), llm_config=body.get("llm_config"))
    )
    return JSONResponse({"ok": True, "topic_id": topic_id})


if __name__ == "__main__":
    import uvicorn

    # Sans authentification : écoute locale par défaut. Exposition LAN
    # explicite via BT_DASH_HOST=0.0.0.0 (réseau de confiance uniquement).
    uvicorn.run(
        app,
        host=os.environ.get("BT_DASH_HOST", "127.0.0.1"),
        port=int(os.environ.get("BT_DASH_PORT", "8080")),
    )
