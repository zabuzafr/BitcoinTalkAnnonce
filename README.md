# Bitcointalk POW Scanner 🔍⛏️

**Scan BitcoinTalk announcement discussions to find new Proof-of-Work serious projects**

## 🎯 Project Overview

An AI-powered scanner that analyzes BitcoinTalk "Announcements (ALT)" posts to identify
legitimate, technically-sound Proof-of-Work (POW) projects, then serves the results
through a local web dashboard and REST API.

Components:

| File | Role |
|---|---|
| `bitcointalk.py` | Crawler + LLM analysis + scoring + SQLite persistence (CLI) |
| `dashboard.py` | FastAPI web dashboard + JSON API (port 8080, host `127.0.0.1`) |
| `i18n.py` | i18n engine (lazy keys, `**kwargs` interpolation, fallback chain) |
| `locales/fr.json`, `locales/en.json` | Translation catalogs (92 keys each) |
| `tests/` | Test suite (77 tests) |

## ✨ Key Features

- 🔍 **Automated BitcoinTalk crawling** of the *Announcements (ALT)* section
- 🧠 **Local LLM due diligence** via Ollama (non-blocking: if Ollama is down, parsing & scoring still run)
- 📊 **Weighted scoring** with explicit red flags (premine tiers, forks, unrealistic claims)
- 📈 **Web dashboard** — responsive HTML + JSON API, no external JS dependencies
- 🌍 **Multilingual (fr + en)** — client-side language switcher, persisted in `localStorage`, served from `/api/i18n/{lang}`
- 🧪 **Tested** — 77 tests covering scoring, i18n, API endpoints, security behavior
- 🔒 **Security hardened** — localhost binding by default, SSRF-safe URL validation, HTML escaping of LLM output

## 🚀 How It Works

1. **Scan**: `bitcointalk.py` crawls announcement pages with `aiohttp` + `BeautifulSoup`
2. **Extract**: parses title, body, premine, fork claims, roadmap, red flags
3. **Analyze**: Ollama LLM performs technical due diligence (skipped gracefully if unavailable)
4. **Score**: weighted scoring → `premine > 20 % → -30`, `> 10 % → -20`, `> 5 % → -10`,
   red flags `min(30, 10 × count)`, fork `+10` penalty, unrealistic claims `+20` penalty
5. **Store**: projects + history in SQLite (`crypto_analysis.db`)
6. **Serve**: dashboard + API on `http://127.0.0.1:8080`

## 🛠️ Technical Stack

- **Python 3.14** (3.9+ supported)
- **Ollama** (`localhost:11434`) — local LLM, default model `llama3.1`
- **aiohttp / beautifulsoup4** — async crawling and HTML parsing
- **FastAPI + Uvicorn** — dashboard and REST API
- **SQLite** — persistence (stdlib, no migrations needed)
- **pandas** — data analysis and reports
- **pytest + httpx** — test suite (dev)

## 📦 Installation

```bash
git clone https://github.com/zabuzafr/BitcoinTalkAnnonce.git
cd BitcoinTalkAnnonce

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install Ollama (https://ollama.ai), if not already present
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull llama3.1
```

## ⚙️ Configuration & CLI

All defaults can be overridden by environment variables:

| Flag | Env | Default | Description |
|---|---|---|---|
| `--model` | `BT_MODEL` | `llama3.1` | Ollama model to use |
| `--base-url` | `BT_BASE_URL` | `https://bitcointalk.org` | Forum base URL |
| `--section` | `BT_BOARD_ID` | `159` | Board id (*Announcements (ALT)*) |
| `--pages` | `BT_PAGES` | `2` | Number of pages to scan |
| `--db` | `BT_DB_PATH` | `crypto_analysis.db` | SQLite database path |
| `--timeout` | `BT_TIMEOUT` | `30` | HTTP timeout (seconds) |
| — | `BT_LOG_FILE` | *(log file)* | Log file path |
| — | `BT_DASH_HOST` | `127.0.0.1` | Dashboard bind address |

## 🚦 Running

```bash
# 1. Scan & analyze (produces crypto_analysis.db + crypto_analysis_report.json)
python bitcointalk.py

# 2. Start the dashboard
python dashboard.py
# → http://127.0.0.1:8080
```

### REST API

| Endpoint | Description |
|---|---|
| `GET /` | HTML dashboard |
| `GET /api/i18n/languages` | Available languages |
| `GET /api/i18n/{lang}` | Translation catalog |
| `GET /api/stats` | Aggregate statistics |
| `GET /api/projects` | List of analyzed projects |
| `GET /api/projects/{topic_id}` | Project details (localized) |
| `GET /api/history/{topic_id}` | Analysis history for a project |

## 🌍 Multilingual

- Catalogs in `locales/*.json` (flat dotted keys merged over a base catalog)
- Server-side: `from i18n import T` — `T.t("key", **kwargs)`
- Client-side: language switcher in the dashboard, choice saved in `localStorage`,
  catalogs fetched from `/api/i18n/{lang}`
- Fallback chain: `locale → default (en)` for unknown keys/languages
- Adding a language = drop a new `locales/{lang}.json` + one entry in `available_languages`

## 🧪 Tests

```bash
pip install -r requirements-dev.txt   # pytest, httpx
.venv/bin/pytest tests/
```

Result: **77 passed** — scoring tiers, i18n catalog/fallback/interpolation,
API endpoints (FastAPI TestClient), and security behavior (localhost binding, output escaping).

## 🔒 Security Notes

- Dashboard binds to `127.0.0.1` by default (override with `BT_DASH_HOST` only if intentional)
- LLM-generated text is HTML-escaped before being injected into dashboard markup
- Relative/malformed project URLs are normalized against the base URL and validated before storage/fetch

## 📋 Detection Criteria

✅ **Positive** — fair launch / low premine, original codebase, whitepaper, active GitHub,
novel algorithm, realistic roadmap

🚩 **Red flags** — excessive premine (>20 %), vague marketing only, closed source,
fork/copy-paste, unrealistic ROI claims, anonymous team

## 🎯 Use Cases

- Early detection of promising POW projects
- Automated technical due diligence for new coins
- Research tracking of emerging POW cryptocurrencies
- Community/developer activity monitoring
