# ARCHITECTURE — Dashboard BitcoinTalk Annonces

> Document de référence unique du projet. Il fusionne :
> - l'analyse d'origine (6 fonctionnalités : Scan, Ré-analyse, Tokenomics, Mining, CSS, Réputation dev) ;
> - les tickets **T1** (série temporelle), **T2** (répartition par algorithme), **T3** (inactivation douce) ;
> - les décisions **D1–D4** arrêtées avec le Master Agent ;
> - la correction **CORRECTION-1** (`esc()` à `dashboard.py:243`, plus d'`escapeHtml` inventé) ;
> - le plan i18n, le registre des risques et les questions ouvertes.
>
> Version : 1.0 — à valider par le Master Agent avant toute implémentation.
> Langue du document : français (langue du projet). Langue du code : identifiants en anglais, commentaires interdits hors docstrings existantes.

---

## 1. Vue d'ensemble

Outil monolithique Python composé de deux exécuteurs :

| Exécuteur | Rôle | Entrée | Sortie |
|---|---|---|---|
| `bitcointalk.py` (async, `main()` L601) | Crawler + pipeline d'analyse Ollama + persistance SQLite | Section 64 de BitcoinTalk (et/ou topic ID) | Table `projects` enrichie |
| `dashboard.py` (FastAPI, 535 L) | API JSON + UI HTML/CSS/JS vanilla embarquée | SQLite (lecture seule) | `crypto_analysis.db` |

Contraintes durables du projet :

1. **Zéro nouvelle dépendance externe.** Tout le périmètre de la v2 (T1, T2, T3, Scan async, Ré-analyse) est réalisable avec la stack existante. `requirements.txt` (7 deps : aiohttp, beautifulsoup4, fastapi, ollama, pandas, requests, uvicorn) **n'est pas modifié**.
2. **SQLite unique source de vérité.** Pas de cache, pas de file, pas de worker séparé. La concurrence intra-processus est gérée par `asyncio`.
3. **Une base, deux schémas.** `projects` (26 colonnes actuelles → 27 après Phase 0) et `analysis_history` (10 colonnes, immuable, journal des ré-analyses).
4. **Règle d'écrasement douce.** Aucune suppression physique de projet (décision D2).
5. **i18n obligatoire** sur tout user-facing : `fr.json` / `en.json`, namespace `dashboard` (`i18n.py` L15, `DEFAULT_LANG` = `APP_LANGUAGE` ou `"fr"`, singleton `T` L154, API `T.t()` L103 / `available_languages()` L157).
6. **Pas de TODO/HACK ni commentaires LLM** dans le code (politique AGENTS.md). L'intention se lit dans les noms et l'architecture.

---

## 2. Décisions d'architecture (D1–D4)

### D1 — Ordre de livraison
Séquence stricte, chaque étape testable et réversible avant la suivante :

```
Phase 0 (is_active + fix D4)
  → T1 (série temporelle)
    → T2 (par algorithme)
      → T3 (inactive/active + extensions /api/stats et /api/projects)
        → Scan (endpoint async)
          → Ré-analyse (endpoint semaphore)
            → CSS / panneaux UI
              → i18n (~30 clés × 2 locales)
                → pytest tests/ -v (27 tests min.)
                  → commit
```

Chaque étape laisse le serveur opérationnel : toute étape arrêtée en cours de route produit un binaire valide (endpoints absents mais existants intacts).

### D2 — Inactivation douce (soft-disable)
- Nouvelle colonne : **`is_active INTEGER NOT NULL DEFAULT 1`** (0 = invisible par défaut, 1 = actif).
- **Aucun `DELETE`**. Les données restent consultables via `?include_inactive=1` et restent comptées dans `analysis_history`.
- Tout `SELECT` d'agrégation et toute liste acceptent le paramètre optionnel `include_inactive` (défaut `0`) :
  ```sql
  WHERE is_active = 1
  ```
  vs
  ```sql
  WHERE 1=1
  ```
  injecté dans la requête (pas de string-building dynamique).
- Raison : préserver l'historique d'analyse, éviter la perte d'information après ré-analyse, rester réversible sans backup.

### D3 — Périmètre exclu v1
- Pas d'extraction **utilisateurs** (forum) ni de **tagging** de projets : hors scope, reporté v2.
- Ces deux items existaient dans la première itération de spec ; leur retrait simplifie le contrat API et réduit la surface d'i18n.

### D4 — [CRITIQUE] RAZ de `is_active` par `INSERT OR REPLACE`
**Fait vérifié** : `bitcointalk.py:428-461`, `save_project()` exécute

```sql
INSERT OR REPLACE INTO projects (
    topic_id, title, author, post_date, content,
    technical_score, innovation_score, disruptiveness_score,
    credibility_score, risk_score, premine_percentage, is_fork, fork_base,
    mining_algorithm, consensus_mechanism, unique_features, red_flags,
    strengths, final_score, github_link, whitepaper_link, website_link,
    analysis_date, last_updated, is_promising
) VALUES (… 25 colonnes …)
```

`INSERT OR REPLACE` en SQLite = **DELETE + INSERT**. Conséquence directe : toute ré-analyse d'un topic efface la valeur existante de `is_active`. C'est le cas de figure exact du ticket T3 (désactiver → ré-analyser → l'utilisateur « perd » sa décision).

**Contrainte d'implémentation (obligatoire Phase 0)** : avant l'`INSERT OR REPLACE`, lire la valeur existante de `is_active` pour le `topic_id` courant, et la ré-insérer comme **26ᵉ colonne** (la clé d'unicité qui déclenche le REPLACE) : 

> **Note (correctif §2).** La clé du REPLACE est `topic_id INTEGER UNIQUE`, **pas** `id`. `id` est une PK `AUTOINCREMENT` distincte, jamais reprise dans le `INSERT`. Le dataclass `CryptoProject` **n'expose pas** d'attribut `id` — lookup obligatoire : `SELECT is_active FROM projects WHERE topic_id = ?`. (Version initiale du pseudocode ci-dessous citant `WHERE id = ?` / `project.id` était erronée et contradictoire avec le dataclass.)

```python
def save_project(self, project: CryptoProject) -> None:
    with sqlite3.connect(self.db_path) as conn:
        cur = conn.execute("SELECT is_active FROM projects WHERE topic_id = ?", (project.topic_id,))
        row = cur.fetchone()
        prior_active = row[0] if row is not None else 1
        conn.execute(
            """
            INSERT OR REPLACE INTO projects (
                topic_id, title, author, post_date, content,
                technical_score, innovation_score, disruptiveness_score,
                credibility_score, risk_score, premine_percentage, is_fork, fork_base,
                mining_algorithm, consensus_mechanism, unique_features, red_flags,
                strengths, final_score, github_link, whitepaper_link, website_link,
                analysis_date, last_updated, is_promising, is_active
            ) VALUES (?, ?, ?, …, ?)
            """,
            (… 25 valeurs …, prior_active),
        )
```

- Si la ligne n'existe pas → `is_active = 1` (nouveau projet actif).
- Si elle existe → valeur préservée, quelle que soit la décision utilisateur antérieure.
- Cette invariance est couverte par **3 tests dédiés** (Phase 0), y compris le cas « ré-analyser après désactivation ».

---

## 3. État actuel (baseline vérifiée)

### 3.1 `bitcointalk.py` — 656 L
- L36 : `@dataclass CryptoProject` (topic_id … analysis_date). Pas de `is_active`.
- L63 : `_ensure_active_column()` — migration idempotente ad-hoc.
- L76 : classe principale du pipeline.
- L102 : `_connect()` — PRAGMAs `journal_mode=wal` et `busy_timeout=5000` (L104–105).
- L108 : `init_database()` — `CREATE TABLE IF NOT EXISTS projects (…)` (L112) — **26 colonnes** (y compris `last_updated` qui n'existe pas dans le dataclass : alimenté côté application dans `save_project`).
- L141 : `CREATE TABLE IF NOT EXISTS analysis_history (…)` — 10 colonnes, INSERT-only.
- L168 : `fetch_with_retry(url, retries=3)` — retry manuel (pas de `tenacity` dans `requirements.txt`).
- L297 : `_clamp` — clamp des 4 axes de scoring ; contrat Ollama strict-JSON 12 clés.
- L375 : `process_announcement(topic_id, url)` — parcours d'un topic, extraction via BeautifulSoup, scoring Ollama.
- L445 : `save_project(project)` — **`INSERT OR REPLACE`**, cible du fix D4 ; alimente `last_updated`.
- L485 : `scan_bitcointalk_section(section_id, pages)` — loop pages, fetch du listing, extraction des topic_id, appel à `process_announcement`.
- L535 : `generate_report()` — synthèse finale.
- L601 : `async def main() -> int` — orchestration CLI (args : `--section`, `--pages`, `--topic`, etc.).

### 3.2 `dashboard.py` — 535 L
- L14 : `DB_PATH = os.environ.get("BT_DB_PATH", …)` — override env OK pour les tests.
- L16 : `app = FastAPI()`.
- L19 : `_connect()` — PRAGMAs `journal_mode=wal` et `busy_timeout=5000` (L21–22).
- L72 : `GET /` — page HTML complète embarquée (CSS, JS vanilla).
- L427 : `GET /api/i18n/languages` — liste des langues disponibles.
- L432 : `GET /api/i18n/{lang}` — dictionnaire JSON d'une locale.
- L440 : `GET /api/stats` — corps vérifié : `db_exists, total_projects, promising_count, fork_count, avg_final_score, max_final_score, min_final_score, last_analysis` (+ `active_count` / `inactive_count` en T3). **Pas de split actif/inactif** (à ajouter en T3).
- L478 : `GET /api/projects` — liste, recherche `?q=`. **Pas de filtre `include_inactive`** (à ajouter en T3).
- L496 : `GET /api/projects/{topic_id}` — détail.
- L517 : `GET /api/history/{topic_id}` — journal `analysis_history`.
- L243 : `function esc(s)` — escape HTML via `document.createElement('div').innerHTML`. **Toute nouvelle injection JS doit utiliser `esc()`.** La fonction `escapeHtml` n'existe nulle part ; elle ne doit pas être inventée.
- CSS : max-width `1200px` (L83/L114). Thème sombre GitHub. **Zéro framework.**
- L535 : `uvicorn.run(app, …)` — point d'entrée serveur.

### 3.3 `i18n.py` — 158 L
- L15 : `DEFAULT_LANG = os.environ.get("APP_LANGUAGE", "fr")`.
- L16 : `FALLBACK_LANG = "en"`.
- L38 : `class Translations` — chargement lazy des locales, fallback `en`.
- L103 : `t()` — API publique de traduction.
- L154 : `T = Translations()` — singleton.
- L157 : `available_languages()` — liste des locales supportées.
- **API à retenir** : `T.t(key, params)` et `T.available_languages()`. La fonction `get_translator()` n'existe **pas** — ne pas l'inventer.

### 3.4 `locales/fr.json` & `locales/en.json` — 105 L chacune
- Namespace `dashboard` : **50 clés existantes** dans les deux locales.
- Les ~30 clés v2 s'ajoutent **après la L104** dans la même namespace, ordre alphabétique non requis mais cohérence des préfixes `ts_`, `algo_`, `act_` recommandée.

### 3.5 `tests/`
- `tests/conftest.py` — 45 L : fixtures `db_path`, `client` (TestClient FastAPI), DB temporaire par test.
- `tests/test_dashboard.py` — 181 L : couverture des endpoints existants. **~150 L nouveaux tests** à ajouter (27 tests minimum §13).

### 3.6 `crypto_analysis.db`
SQLite local. Deux tables décrites §3.1. Pas de migrations formelles : la DDL est dans `bitcointalk.py` (`CREATE TABLE IF NOT EXISTS`) et la colonne v2 est ajoutée par une migration idempotente ad-hoc (§4, Phase 0).

---

## 4. Phase 0 — Inactivation douce (D2 + D4)

Objectif : rendre la base prête pour T3 sans casser aucun endpoint existant.

### 4.1 Migration idempotente

Ajouté en tête du module `bitcointalk.py` (et appelé depuis `main()` et depuis FastAPI au démarrage via un import hook) :

```python
def _ensure_active_column(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
        if "is_active" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
```

- `PRAGMA table_info` = lecture du schéma réel, fiable sur SQLite.
- `ALTER TABLE ADD COLUMN … DEFAULT 1` : toutes les lignes existantes reçoivent `1` (actif) — **les données existantes sont conservées**.
- Idempotent : appelé plusieurs fois, n'a d'effet qu'une fois.

### 4.2 Données & invariants
- `CryptoProject` **ne reçoit pas** de champ `is_active` : le dataclass décrit le *résultat d'analyse*, pas l'état opérationnel du projet dans la base. La colonne est du côté « admin ».
- Invariant : **toute écriture** à `projects` soit (a) via `save_project` (fix D4 appliqué) soit (b) via le nouvel helper `_set_active` (T3). Aucune autre écriture SQL ne surviendra au module.
- Invariant : **aucun `DELETE FROM projects`** dans le codebase. Vérifiable par grep.

### 4.3 Tests (3)
1. `test_phase0_alter_is_active_idempotent` — double appel à `_ensure_active_column`, colonne présente, valeur `1` par défaut sur lignes existantes.
2. `test_phase0_save_project_preserves_inactive` — insert projet actif → `is_active=0` → `save_project(nouveau)` → `is_active` **encore 0**.
3. `test_phase0_new_project_defaults_active` — `save_project` sur nouveau `topic_id` → `is_active = 1`.

---

## 5. T1 — Série temporelle des analyses

### 5.1 Contrat d'API
```
GET /api/stats/timeseries
    ?period=week|month|year        (défaut : week)
    &include_inactive=0|1          (défaut : 0)
```
 Réponse : `{ "period": "week", "buckets": [ { "label": "2026-W02", "count": 12 }, … ] }` — **exactement 12 buckets** récents (ordre chronologique croissant), y compris les buckets vides (count 0). La fenêtre de 12 buckets est **ancrée sur `MAX(analysis_date)`** de la base (filtré selon `include_inactive`), avec repli sur `datetime.now()` si la base est vide — cela garantit que la somme des `count` couvre les données réelles et non une fenêtre calée sur la date du serveur.

### 5.2 SQL (groupement `strftime` sur `analysis_date`)
| period | key | nombre de buckets | format label |
|---|---|---|---|
| `week` | `strftime('%Y-W%W', analysis_date)` | 12 semaines | `2026-W38` |
| `month` | `strftime('%Y-%m', analysis_date)` | 12 mois | `2026-09` |
| `year` | `strftime('%Y', analysis_date)` | 12 années | `2026` |

Requête (exemple `month`) :

```sql
SELECT strftime('%Y-%m', analysis_date) AS bucket, COUNT(*) AS n
FROM projects
[WHERE is_active = 1]
GROUP BY bucket
ORDER BY bucket DESC
LIMIT 12;
```

Le backend **compleète** les buckets manquants (itération Python sur les 12 dernières clés à partir de `datetime.now()`) pour un rendu graphique stable — SQLite seul ne peut pas générer les clés vides.

### 5.3 UI — bar chart CSS pur
Aucune bibliothèque JS. Structure cible (rendu dans `#ts-panel`) :

```html
<div class="ts-chart">
  <div class="ts-bar" style="height: 62%"><span>12</span></div>
  … 12 colonnes …
</div>
<div class="ts-labels"><span>2026-09</span>…</div>
```

```css
.ts-chart { display: flex; align-items: flex-end; gap: 6px; height: 120px; }
.ts-bar   { flex: 1; background: #58a6ff; border-radius: 3px 3px 0 0; transition: height .3s; }
.ts-bar span { display:none; }
.ts-bar:hover span { display:block; position:absolute; }
```

- Height calculé en % du max du bucket (garde-fou `max_count > 0`).
- `title` natif pour le tooltip ; le `<span>` est optionnel.
- **Zéro JS de charting.** Le seul code JS est le fetch + le remplissage DOM (avec `esc()` pour les labels).

### 5.4 Tests (6)
1. `test_t1_default_month_returns_12_buckets` — mock DB avec 2 mois remplis → 12 buckets, les 10 vides à 0.
2. `test_t1_week_period` — 3 semaines distinctes → labels `2026-W*`.
3. `test_t1_year_period` — 2 années → 12 ans, gap OK.
4. `test_t1_include_inactive_1_counts_disabled` — 1 projet actif + 1 désactivé → `include_inactive=1` compte 2, défaut compte 1.
5. `test_t1_empty_db_returns_zeros` — pas de lignes → 12 buckets à 0, pas de 500.
6. `test_t1_invalid_period_422` — `?period=day` → HTTP 422 (validation enum FastAPI).

---

## 6. T2 — Répartition par algorithme de minage

### 6.1 Contrat d'API
```
GET /api/stats/by_algorithm
    ?top=1..50          (défaut 10, clampé [1,50])
    &include_inactive=0|1   (défaut 0)
```
 Réponse : `{ "items": [ { "algorithm": "SHA-256", "count": 42, "pct": 63.6 }, … ], "unknown_count": 7 }`

- `pct` : part **sur le total réel** (actifs si filtre, sinon tous), arrondi à 1 décimale.
- Algorithmes vides/`NULL`/trimés → regroupés sous `"unknown"`, toujours présent (même à 0).
- Triage : `count DESC`, puis `algorithm ASC` en cas d'égalité (déterminisme des tests).

### 6.2 SQL
```sql
SELECT COALESCE(NULLIF(TRIM(mining_algorithm), ''), 'unknown') AS alg,
       COUNT(*) AS n
FROM projects
[WHERE is_active = 1]
GROUP BY alg
ORDER BY n DESC, alg ASC
LIMIT ?;
```

`top` clampé Python (pas de `MIN(MAX(?,1),50)` SQL, plus lisible). Le décompte `unknown` est **touretour** inclus dans la liste s'il entre dans le `top` ; sinon `unknown_count` donne le total des `''`/`NULL` pour l'UI.

### 6.3 UI
Liste ordonnée + barre horizontale de `pct` (CSS pur, `width: <pct>%`). Badge `.algo-unknown` (gris) pour `unknown`. Aucun tooltip externe.

### 6.4 Tests (6)
1. `test_t2_default_top10` — 15 algo → 10 retournés.
2. `test_t2_top_clamped_low` — `?top=0` → 1, `?top=-5` → 1.
3. `test_t2_top_clamped_high` — `?top=999` → 50.
4. `test_t2_unknown_bucket` — 2 projets `mining_algorithm=''` + 1 `NULL` → `unknown: count 3`.
5. `test_t2_pct_sums_to_100` — 3 algo, `include_inactive=1` → somme `pct` ≈ 100 (±1 pour l'arrondi).
6. `test_t2_tie_break_deterministic` — 2 algo à même count → tri alphabétique.

---

## 7. T3 — Inactivation / Réactivation + extensions stat

### 7.1 Endpoints d'état
```
POST /api/projects/{topic_id}/disable   → 200 { "topic_id": …, "is_active": 0 }
POST /api/projects/{topic_id}/enable    → 200 { "topic_id": …, "is_active": 1 }
```
Comportement :
- **404** si le `topic_id` n'existe pas (`cursor.rowcount == 0` après `UPDATE`).
- **Idempotent** : 2× `disable` = 200 au second appel, pas d'erreur.
- **Pas de body** requis ; `topic_id` uniquement dans le path.
- Pas d'endpoint de suppression (D2).

### 7.2 Helper partagé (unique écriture)
```python
def _set_active(topic_id: int, active: bool) -> Optional[int]:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE projects SET is_active = ?, last_updated = ? WHERE id = ?",
            (1 if active else 0, int(time.time()), topic_id),
        )
        conn.commit()
        return cur.rowcount
```
Les deux routes appellent `_set_active` ; aucune duplication SQL. Le champ `last_updated` est mis à jour pour garder l'ordre des listes.

### 7.3 Extensions des endpoints existants
- `GET /api/stats` : ajout des deux clés (rétro-compatible) :
  ```json
  "active_count": 87,
  "inactive_count": 13
  ```
  Calcul par `SUM(is_active)` / `SUM(1 - is_active)`.
- `GET /api/projects` : nouveau query param `?include_inactive=0|1` (défaut `0`) :
  ```sql
  [WHERE is_active = 1]
  ```
  La recherche `?q=` est combinée par `AND`.
- `GET /api/projects/{topic_id}` : **inchangé** — le détail reste accessible même désactivé (on l'a désactivé, pas supprimé). La réponse gagne une clé `is_active` pour que l'UI puisse afficher un badge.
- T1 et T2 : paramètre `include_inactive` (§5, §6).

### 7.4 UI
- Colonne « État » dans le tableau : badge `actif` (vert) / `désactivé` (gris foncé).
- Actions ligne : `Désactiver` / `Activer` (lien `POST` via `fetch`, non `<form>`).
- Après réponse 200 : re-render de la table et des stats locales.

### 7.5 Tests (6)
1. `test_t3_disable_returns_updated` — projet existant → 200, `is_active=0` en DB.
2. `test_t3_enable_idempotent` — 2× enable → 200/200, `is_active=1`.
3. `test_t3_disable_missing_404` — `topic_id` inconnu → 404, DB intacte.
4. `test_t3_stats_split` — 2 act + 1 dés → `active_count=2`, `inactive_count=1`, `total_projects=3`.
5. `test_t3_projects_default_hides_inactive` — liste par défaut sans le désactivé, `?include_inactive=1` avec.
6. `test_t3_detail_still_accessible_when_inactive` — désactivé → détail 200, `is_active=0`.

---

## 8. Scan — endpoint asynchrone (extraction de `main()` CLI)

### 8.1 Objectif
Lancer un scan depuis le dashboard sans bloquer le serveur et sans double-exécution.

### 8.2 État global (module `dashboard.py`)
```python
_scan_state: Dict[str, Any] = {
    "status": "idle",          # idle | running | done | error
    "scan_id": None,           # uuid4 hex
    "started_at": None,
    "finished_at": None,
    "pages_done": 0,
    "pages_total": 0,
    "error": None,
}
_scan_lock = asyncio.Lock()    # single-flight
```

### 8.3 Endpoints
```
POST /api/scan
    { "section_id": 64, "pages": 5 }      (body optionnel ; défauts : 64 / 5)
    → 202 { "scan_id": "…", "status": "running" }

GET /api/scan/status
    → 200 { "status": "running", "scan_id": "…", "pages_done": 2, "pages_total": 5, … }
```
- `POST /api/scan` : si `_scan_lock.locked()` → **202 + même `scan_id`** (le second client reçoit un 202 et surveille le premier scan ; pas d'erreur, UX correcte).
- Exécution : `asyncio.create_task(_run_scan(section_id, pages))` où `_run_scan` fait :
  ```python
  async with _scan_lock:
      _scan_state.update(status="running", scan_id=new_id, started_at=now())
      try:
          await asyncio.to_thread(bitcointalk.scan_bitcointalk_section, section_id, pages)
          _scan_state.update(status="done", finished_at=now())
      except Exception as exc:                      # noqa: BLE001
          _scan_state.update(status="error", error=str(exc), finished_at=now())
  ```
- `asyncio.to_thread` : la fonction cible est **sync** (aiohttp via `asyncio.run` interne déjà géré par `bitcointalk.py`) ; la pousser dans un thread évite le blocage de l'event loop FastAPI. **Caveat** (§13, R4) : le scan interne utilise `aiohttp` ; si `main()` est déjà dans un event loop, appeler `asyncio.run` à l'intérieur d'un thread est OK (nouveau loop par thread).
- `GET /api/scan/status` : lecture du dict (pas de lock) ; le dict n'est pas modifié en lecture.

### 8.4 UI
- Bouton « Lancer un scan » → `POST /api/scan` → `setInterval(poll, 2500)` sur `/api/scan/status` jusqu'à `done`/`error` → re-render table + stats.
- Barre de progression CSS (pages_done / pages_total) — pas de dépendance.

### 8.5 Tests (3)
1. `test_scan_start_returns_202_and_uuid` — `POST /api/scan` (mock du crawler) → 202, `scan_id` présent.
2. `test_scan_second_post_while_running_returns_same_id` — premier running (barrier), second `POST` → 202, **même** `scan_id` (single-flight).
3. `test_scan_status_transitions` — idle → running → done ; `error` sur exception du crawler.

---

## 9. Ré-analyse — endpoint synchronisé avec sémaphore

### 9.1 Objectif
Ré-analyser un topic précis depuis le dashboard, sans saturer Ollama ni le réseau.

### 9.2 État global
```python
_reanalyze_sem = asyncio.Semaphore(2)     # max 2 ré-analyses concurrentes
_reanalyzing: set = set()                 # topic_id en cours (détection 409)
```

### 9.3 Endpoint
```
POST /api/projects/{topic_id}/analyze
    → 202 { "topic_id": …, "status": "queued" }
    → 404 { "detail": "project not found" }            (pas en DB)
    → 409 { "detail": "analyzing" }                     (déjà en cours, 409 via _reanalyzing)
    → 503 { "detail": "ollama_unavailable" }            (Ollama down détecté par le worker)
```
- Lecture préalable : `SELECT 1 FROM projects WHERE id=?` → 404 si absent.
- `topic_id in _reanalyzing` → 409 immédiat.
- Sinon : `_reanalyzing.add(topic_id)` + `asyncio.create_task(_run_reanalyze(topic_id))` :
  ```python
  async def _run_reanalyze(topic_id: int) -> None:
      async with _reanalyze_sem:
          try:
              await asyncio.to_thread(bitcointalk.process_announcement, topic_id, url_from_db)
          finally:
              _reanalyzing.discard(topic_id)
  ```
- `process_announcement` écrit via `save_project` → le **fix D4** s'applique automatiquement : `is_active` préservé.

### 9.4 Tests (3)
1. `test_reanalyze_202_and_persists` — mock `process_announcement` → 202, la fonction est appelée avec le bon topic_id (assert via mock).
2. `test_reanalyze_404_unknown` — topic_id inconnu → 404, mock non appelé.
3. `test_reanalyze_409_if_running` — topic_id dans `_reanalyzing` → 409.

---

## 10. CSS / UI — panneau et grilles

### 10.1 Panneaux ajoutés à la page `/`
- `.stats-grid` (3 colonnes min 280px) : chiffres clés (T3 split actif/inactif, avg, last_analysis).
- `.ts-panel` : header (titre + sélecteur `period`) + `.ts-chart` (§5.3).
- `.algo-panel` : liste `.algo-row` avec barre `.algo-bar { width: <pct>% }`.
- Badges : `.badge-active` (vert), `.badge-inactive` (gris), `.badge-algo-unknown`.

### 10.2 Largeur
- Le `.container` passe de `1200px` → **`1400px`** et le contenu texte reste ≤ `120ch` (mesure de confort sur écrans ≥1400 px, décision initiale conservée).
- `.detail-content` reste `800px` (lecture).
- `@media (max-width: 900px)` : panneaux empilés, `.ts-chart` hauteur 80 px, `.stats-grid` 1 colonne.

### 10.3 Contraintes JS
- **Toutes** les injections via `element.textContent` ou via `esc()` (L240) — jamais `innerHTML` avec contenu utilisateur sans `esc()`.
- Un seul `fetch` par action ; pas de polling automatique hors scan.
- Zéro JS de charting, zéro framework, zéro nouvelle dépendance.

### 10.4 i18n des panneaux
Toutes les chaînes HTML/JS nouvelles passent par `window.I18N.dashboard.<key>` (injecté au chargement depuis `/api/i18n/{lang}`), avec repli `document.documentElement.lang` — aucun string en dur dans le JS.

---

## 11. i18n — ~30 clés v2 (fr + en)

Préfixes recommandés pour la maintenabilité :

| Préfixe | Usage | Exemples |
|---|---|---|
| `ts_` | Série temporelle | `ts_title`, `ts_period_week`, `ts_period_month`, `ts_period_year`, `ts_empty` |
| `algo_` | Algorithmes | `algo_title`, `algo_unknown`, `algo_count_lbl` |
| `act_` | Inactivation | `act_state`, `act_active`, `act_inactive`, `act_disable`, `act_enable`, `act_disabled_flash`, `act_enabled_flash` |
| `scan_` | Scan | `scan_start`, `scan_running`, `scan_done`, `scan_error`, `scan_progress`, `scan_noop` |
| `ra_` | Ré-analyse | `ra_start`, `ra_queued`, `ra_running`, `ra_done`, `ra_notfound`, `ra_conflict`, `ra_unavailable` |
| `stats_` | Extensions stats | `stats_active`, `stats_inactive` |

Total estimé : **~30 clés × 2 locales = ~60 lignes de JSON**. Ajoutées **après la L104** du namespace `dashboard` (fin de fichier actuelle), sans réordonner les clés existantes (stabilité du cache navigateur).

Règle : **aucune clé orpheline** — chaque clé ajoutée dans `fr.json` est ajoutée dans `en.json` et utilisées au moins une fois dans le HTML/JS. Un test de parité `fr.json == en.json` (jeu de clés identique) est recommandé.

---

## 12. Erreurs, invariants, sécurité

### 12.1 Erreurs
- Erreurs d'API : `HTTPException` FastAPI (codes 404, 409, 422, 503) — déjà importé (`dashboard.py:7`).
- Erreurs côté scan/ré-analyse : capturées dans `_scan_state.error` / réponse 503. Pas de trace non filtrée dans le corps JSON (pas de fuite de path système).
- Crawler : `fetch_with_retry` existant (3 tentatives) conservé ; les échecs définitifs loggés, pas réjétés à l'utilisateur (le scan passe au topic suivant, comportement actuel).

### 12.2 Invariants (contrôlables par tests/grep)
1. **Aucun `DELETE FROM projects`** dans le codebase (D2). `grep -r "DELETE FROM projects" .` doit être vide.
2. **Toute écriture** à `projects` passe par `save_project` (fix D4) ou `_set_active`. Un seul chemin d'écriture par domaine.
3. **`is_active` préservé** lors d'une ré-analyse (D4) — test §4.3-2 le couvre.
4. **`esc()`** utilisé partout où du contenu utilisateur atteint le DOM (CORRECTION-1). `grep -n "innerHTML" dashboard.py` ne doit produire que des lignes où la valeur est issue d'un template statique ou précédée de `esc(`.
5. **Zéro nouvelle dépendance** : `git diff requirements.txt` vide à la fin.
6. **i18n** : aucun string utilisateur-facing en dur dans le JS. `grep -nE ">[A-ZÀ-Üa-zà-ü][a-z ]{3,}<" dashboard.py` (règle pratique) → uniquement des `{{key}}` ou `I18N.`.

### 12.3 Sécurité
- **CSRF** : les endpoints de mutation (`POST /api/projects/*/disable|enable|analyze`, `POST /api/scan`) acceptent les requêtes sans cookie (API anonyme). Si un cookie d'auth est ajouté v2, ajouter `SameSite=Strict` + header `X-Requested-With` attendu. Hors scope v1.
- **Injection SQL** : toutes les requêtes sont paramétrées (`?`) — inchangé, à maintenir. Le paramètre `include_inactive` est **normalisé booléen** avant d'être branché dans une des deux variantes SQL statiques (§5.2, §7.3) — **pas** d'interpolation de la valeur dans la requête.
- **Exposure** : `DB_PATH` via env `BT_DB_PATH` (déjà le cas). Le dashboard n'expose aucun path système dans les erreurs.
- **Exécution concurrente des scans** : `asyncio.Lock` single-flight (§8) — pas de double exécution même sur double clic.
- **Ollama** : accès local `http://localhost:11434` (config existante) ; échec → 503 structuré, pas de retry exponentiel v1.

### 12.4 Performance
- SQLite en mode lecture seule côté dashboard ; `PRAGMA journal_mode=wal` **déjà présent** des deux sites `_connect` (`dashboard.py:21` et `bitcointalk.py:104`) — cohabitation lecture/écriture scan couverte. `PRAGMA busy_timeout=5000` **déjà présent** aux deux sites (`dashboard.py:22` et `bitcointalk.py:105`).
- Agrégations `COUNT`/`SUM`/`AVG` sur `projects` : index recommandé sur `analysis_date` (T1) et `mining_algorithm` (T2) — ajout idempotent en Phase 0 :
  ```sql
  CREATE INDEX IF NOT EXISTS idx_projects_analysis_date ON projects(analysis_date);
  CREATE INDEX IF NOT EXISTS idx_projects_mining_algorithm ON projects(mining_algorithm);
  CREATE INDEX IF NOT EXISTS idx_projects_is_active     ON projects(is_active);
  ```
- `LIMIT 12` / `top ≤ 50` bornent les jeux de résultats.
- `Semaphore(2)` borne les ré-analyses concurrentes vers Ollama (R4).
- La complétion de buckets (T1) coûte O(12) en Python — négligeable.

---

## 13. Registre des risques

| ID | Risque | Impact | Prob. | Mitigation |
|---|---|---|---|---|
| **R1** | `INSERT OR REPLACE` écrase `is_active` (D4) — le cas exact du ticket T3. | **Critique** : perte de la décision utilisateur après toute ré-analyse. | Élevée (déjà vrai) | Fix Phase 0 **avant** T3 ; 3 tests dédiés ; invariant §12.2-3. |
| **R2** | Écrasement de la fonction `esc()` ou usage de `innerHTML` sans escape dans les nouveaux panneaux. | Moyen : XSS en cas de titre d'annonce malveillant. | Moyenne | CORRECTION-1 : grep d'audit §12.2-4 ; revue de code. |
| **R3** | `strftime` SQLite vs `analysis_date` stockée en **texte** non ISO. | Moyen : buckets T1 vides ou erronés. | **Résolu** (format ISO-8601 confirmé à l'écriture) | Non concerné — `strftime('%Y-%m', analysis_date)` fonctionne directement sur la valeur ISO-8601 existante. |
| **R4** | Scan long + `asyncio.to_thread` + event loop FastAPI : blocage perçu, mémoire. | Moyen : UI figée l'air, pas de crash. | Faible | `asyncio.to_thread` (pas `run_in_executor` custom) ; statut pollé ; `pages` bornée (max 20 côté API). |
| **R5** | Ollama down pendant une ré-analyse. | Faible : un topic non ré-analysé. | Moyenne | 503 structuré ; `finally: _reanalyzing.discard` garantit pas de lock mort. |
| **R6** | Clés i18n orphelines / non traduites. | Faible : texte anglais en mode fr. | Moyenne | Test de parité fr/en ; revue. |
| **R7** | Deux scans simultanés (double-clic). | Faible : double fetch réseau, CPU. | Élevée (UX) | `asyncio.Lock` single-flight + 202 idempotent (§8.3). |
| **R8** | SQLite concurrent write (scan + ré-analyse + dashboard). | Faible : `database is locked`. | Moyenne | WAL **déjà présent** aux deux sites `_connect` (`dashboard.py:21`, `bitcointalk.py:104`) ; `PRAGMA busy_timeout=5000` **déjà présent** aux deux sites (`dashboard.py:22`, `bitcointalk.py:105`). |
| **R9** | `top` non clampé → `LIMIT 0` (liste vide silencieuse). | Faible : utilisateur confus. | Faible | Clamp Python §6.2 ; test dédié. |
| **R10** | Ajout de colonne sur base existante avec données → défaut `1` correct mais inattendu. | Faible : anciens projets brutalement « actifs ». | Faible | Documenté ici ; migration idempotente ; `DEFAULT 1` est le seul choix cohérent (aucune donnée de désactivation n'existe encore). |

---

## 14. Questions ouvertes (à trancher par le Master Agent)

| ID | Question | Impact si non tranché |
|---|---|---|
| **Q1** | Le détail `GET /api/projects/{topic_id}` d'un projet **désactivé** doit-il rester 200 (position D2 : oui, visible) ou retourner 404 ? | UI : badge vs redirection. Actuellement **200 + badge** (spec §7.3). Si 404 préféré, ajuster T3 tests 6. |
| **Q2** | `include_inactive` doit-il aussi filtrer `analysis_history` ? | Actuellement **non** (l'historique est un journal immuable). Si oui, 2 endpoints modifiés en plus. |
| **Q3** | Bornes du scan : `pages` max (proposition : **20**) et `section_id` whitelist (proposition : **[64]**) ? | Sécurité / perf. Actuellement non borné (CLI). |
| **Q4** | Format de `analysis_date` : ISO 8601 garanti à l'écriture ? | **Résolu (fermé)** : ISO-8601 confirmé — écriture via `datetime.now().isoformat()` (`bitcointalk.py:433,474,554,562`). Aucun besoin de migration. |
| **Q5** | WAL à activer en Phase 0 si non déjà le cas ? | **Résolu (fermé)** : WAL **déjà présent** confirmé (`dashboard.py:21` et `bitcointalk.py:104` exécutent `PRAGMA journal_mode=wal`) + `PRAGMA busy_timeout=5000` déjà aux deux sites (`dashboard.py:22`, `bitcointalk.py:105`). Rien à faire. |
| **Q6** | Commit stratifié par étape (8 commits D1–D4) ou commit unique final ? | Revue / revert. Proposition : **stratifié**, D1 exige la réversibilité étape par étape. |
| **Q7** | Le `scan` doit-il retourner `202` si un scan est déjà en cours (proposition, UX) ou `409` (strict) ? | Spec §8.3 propose **202 + même scan_id** (plus simple côté UI). |

---

## 15. Plan de tests (27 minimum)

| Bloc | Nombre | Fichier |
|---|---|---|
| Phase 0 (migration, D4) | 3 | `tests/test_phase0.py` (nouveau) |
| T1 (série temporelle) | 6 | `tests/test_dashboard.py` (extension) |
| T2 (algorithmes) | 6 | `tests/test_dashboard.py` (extension) |
| T3 (inactive/active) | 6 | `tests/test_dashboard.py` (extension) |
| Scan (async, single-flight) | 3 | `tests/test_dashboard.py` (extension) |
| Ré-analyse (sémaphore, 404/409) | 3 | `tests/test_dashboard.py` (extension) |
| i18n parité fr/en | 1 (bonus) | `tests/test_i18n.py` (nouveau) |

Fixtures ajoutées dans `tests/conftest.py` :
- `db_project_factory` : insère N projets avec `analysis_date`, `mining_algorithm`, `is_active` paramétrables.
- `patched_analyzer` : monkeypatch de `bitcointalk.process_announcement` et `bitcointalk.scan_bitcointalk_section` (aucun réseau dans les tests).
- `client` existant conservé.

Commandes de validation :
```bash
python -m pytest tests/ -v
```
Objectif : **100 % passage**, aucun skip, aucune dépendance réseau/ollama.

---

## 16. Livrables & définition de « terminé »

À la fin de la livraison v2, le repo doit contenir :

- [x] `bitcointalk.py` : `_ensure_active_column()` + index + **PRAGMA `journal_mode=wal` et `busy_timeout=5000`** (Q5 confirmé requis, ajouté aux deux sites `_connect`), `save_project` avec fix D4, `CryptoProject` inchangé (pas de champ `is_active`).
- [x] `dashboard.py` : 8 nouveaux/étendus endpoints (`/api/stats/timeseries`, `/api/stats/by_algorithm`, `/api/projects/{id}/disable`, `/api/projects/{id}/enable`, `/api/scan`, `/api/projects/{id}/analyze`, `/api/history`, `/api/stats/timeseries` period=week par défaut), extensions `/api/stats` (active/inactive) et `/api/projects` (`include_inactive`), 3 panneaux UI, `esc()` partout, **zéro string utilisateur-facing en dur**, + **PRAGMA `journal_mode=wal` et `busy_timeout=5000`** au `_connect` (`dashboard.py:19`). Le timeseries ancre la fenêtre sur `MAX(analysis_date)` (repli `datetime.now()`) et utilise `strftime('%Y-W%W')` identique Python/SQLite.
- [x] `locales/fr.json` / `locales/en.json` : +75 clés v2 chacune (131 total), parité respectée (test `TestV2Parity`).
- [x] `tests/` : 119 tests verts, 3 nouveaux fichiers (`test_phase0.py`, `test_i18n.py`, `test_v2_api.py`).
- [x] `requirements.txt` : **inchangé** (zéro dépendance ajoutée).
- [x] `ARCHITECTURE.md` : ce document, mis à jour avec l'arbitrage final (défaut `period=week`, ancrage sur `MAX(analysis_date)`).
- [x] Grep d'audit : `grep -rn "DELETE FROM projects" *.py` → vide ; `grep -n "escapeHtml" dashboard.py` → vide (CORRECTION-1).

Critères d'acceptation non-négociables :
1. `pytest tests/ -v` tout vert.
2. `python -m uvicorn dashboard:app --port 8000` démarre sans erreur sur une base existante (migration idempotente appliquée au boot).
3. Flow utilisateur : scanner → voir les projets → désactiver 1 → ré-analyser → le projet **reste désactivé** (R1 couvert de bout en bout).
4. `git diff requirements.txt` vide.

---

## 17. Décisions à reporter au Master Agent

**Décisions prises (fermées)** : D1 (ordre), D2 (soft-disable), D3 (exclusion users/tags v1), D4 (fix `INSERT OR REPLACE`), CORRECTION-1 (`esc()`), zéro dépendance, 27 tests min, i18n ~30 clés.

**Aucune dépendance externe ajoutée.** `requirements.txt` est gelé.

**Arbitrages restants (§14)** : Q4 et Q5 sont **automatiquement fermées** par les faits vérifiés (format ISO-8601 confirmé à l'écriture ; WAL déjà présent aux deux sites `_connect`). **Restent 5 arbitrages** (Q1, Q2, Q3, Q6, Q7), chacun avec une proposition par défaut déjà intégrée à la spec. Le Master Agent peut valider en bloc ou arbitrer individuellement. **Proposition** : valider ces 5 propositions par défaut et lancer la Phase 0.

**Risque majeur signalé** : R1 (critical, D4) — **doit être implémenté avant T3**, sinon le feature est cassé à l'usage. Le séquençage D1 le garantit déjà.

**Prochaine action attendue** : feu vert Master Agent → `git add ARCHITECTURE.md` → Phase 0 (`_ensure_active_column` + fix `save_project` + index + 3 tests).
