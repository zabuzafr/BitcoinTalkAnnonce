#!/usr/bin/env python3
"""
Agent Ultimate d'Analyse Bitcointalk - Scan technique complet des nouvelles cryptos
"""

import argparse
import asyncio
import base64
import io
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup
from pypdf import PdfReader

import llm
import mining_guide
import urlclean
from i18n import T

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.environ.get('BT_LOG_FILE', 'crypto_analysis.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

MININGPOOLSTATS_URL = "https://miningpoolstats.stream"

# Une réponse 404/410 est définitive pour cette variante : inutile d'insister
# avec un `GET`, et inutile d'essayer les variantes plus courtes.
_DEFINITIVE_MISSING = frozenset({404, 410})

# Plafond d'octets lus pour un PDF externe : au-dela, on abandonne l'extraction.
# Un whitepaper texte tient largement dessous ; un fichier surcharge ne doit pas
# etre mis en memoire en entier.
_MAX_PDF_BYTES = 20 * 1024 * 1024


def _is_public_address(address: str) -> bool:
    """Vrai si l'adresse IP appartient à l'Internet public.

    Sont rejetées loopback, lien-local (169.254.0.0/16, donc les métadonnées
    cloud), privées, multicast et non routables.
    """
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )
BITCOIN_FAMILY_ALGOS = frozenset({
    'sha-256', 'sha256', 'sha-256d', 'sha256d', 'sha-256asic', 'sha-256a',
    'sha-3', 'kawpow', 'scrypt', 'x11', 'x16r', 'ethash', 'etchash',
    'kheavyhash', 'blake2s', 'blake2b', 'blake3', 'randomx',
})

BITCOIN_ONLY_ALGOS = frozenset({
    'sha-256', 'sha256', 'sha-256d', 'sha256d', 'sha-256asic', 'sha-256a',
    'sha-3',
})

_TITLE_STOPWORDS = frozenset({
    'ann', 'announce', 'announcement', 'new', 'the', 'a', 'an', 'of', 'for',
    'now', 'launch', 'launched', 'presale', 'ico', 'update', 'release',
    'coin', 'token', 'project', 'protocol', 'network', 'chain',
})


@dataclass
class CryptoProject:
    topic_id: int
    title: str
    author: str
    content: str
    post_date: str
    github_link: str = ""
    whitepaper_link: str = ""
    website_link: str = ""
    technical_score: int = 0
    innovation_score: int = 0
    disruptiveness_score: int = 0
    credibility_score: int = 0
    risk_score: int = 0
    premine_percentage: float = 0.0
    is_fork: bool = False
    fork_base: str = ""
    mining_algorithm: str = ""
    consensus_mechanism: str = ""
    unique_features: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    quality_level: str = ""
    innovation_level: str = ""
    quantum_risk_score: float = 0.0
    mining_type: str = ""
    quality_note: str = ""
    final_score: int = 0
    analysis_date: str = ""
    github_stars: int = 0
    github_forks: int = 0
    github_open_issues: int = 0
    github_language: str = ""
    github_updated_at: str = ""
    discord_members: int = 0
    telegram_members: int = 0
    social_json: str = ""
    mining_status: str = ""
    hashrate_str: str = ""
    mining_active_pools: int = 0
    mining_pool_list: str = ""
    mining_link: str = ""
    mining_guide_json: str = ""
    coingecko_id: str = ""
    coingecko_symbol: str = ""
    coingecko_name: str = ""
    coingecko_price_usd: Optional[float] = None
    coingecko_market_cap_usd: Optional[float] = None
    coingecko_volume_usd: Optional[float] = None
    coingecko_rank: Optional[int] = None
    whitepaper_text: str = ""
    source_analysis: str = ""
    token_distribution: str = ""
    team_allocation: str = ""
    vesting_period: str = ""
    inflation_model: str = ""
    burn_mechanisms: str = ""
    utility_description: str = ""
    tokenomics_risks: str = ""
    tokenomics_score: float = 0.0


PROJECT_COLUMNS: tuple = (
    'topic_id', 'title', 'author', 'post_date', 'content', 'technical_score',
    'innovation_score', 'disruptiveness_score', 'credibility_score', 'risk_score',
    'premine_percentage', 'is_fork', 'fork_base', 'mining_algorithm',
    'consensus_mechanism', 'unique_features', 'red_flags', 'strengths', 'final_score',
    'github_link', 'whitepaper_link', 'website_link', 'analysis_date',
    'last_updated', 'is_promising',
)

ENRICHMENT_COLUMNS: tuple = PROJECT_COLUMNS + (
    'quality_level', 'innovation_level', 'quantum_risk_score', 'mining_type', 'quality_note',
    'github_stars', 'github_forks', 'github_open_issues', 'github_language', 'github_updated_at',
    'discord_members', 'telegram_members', 'social_json', 'mining_status', 'hashrate_str',
    'mining_active_pools', 'mining_pool_list', 'mining_link', 'mining_guide_json',
    'coingecko_id', 'coingecko_symbol',
    'coingecko_name', 'coingecko_price_usd', 'coingecko_market_cap_usd',
    'coingecko_volume_usd', 'coingecko_rank', 'whitepaper_text', 'source_analysis',
    'token_distribution', 'team_allocation', 'vesting_period', 'inflation_model',
    'burn_mechanisms', 'utility_description', 'tokenomics_risks', 'tokenomics_score',
)

ENRICHMENT_DEFAULTS: Dict[str, Any] = {
    'unique_features': '[]', 'red_flags': '[]', 'strengths': '[]',
    'social_json': '{}', 'mining_pool_list': '[]', 'source_analysis': '{}',
    'mining_guide_json': '{}',
    'burn_mechanisms': '[]', 'tokenomics_risks': '[]',
    'quality_level': '', 'innovation_level': '', 'mining_type': '', 'quality_note': '',
    'github_language': '', 'github_updated_at': '', 'mining_status': '', 'hashrate_str': '',
    'mining_link': '',
    'coingecko_id': '', 'coingecko_symbol': '', 'coingecko_name': '', 'whitepaper_text': '',
    'token_distribution': '', 'team_allocation': '', 'vesting_period': '',
    'inflation_model': '', 'utility_description': '',
    'github_stars': 0, 'github_forks': 0, 'github_open_issues': 0, 'discord_members': 0,
    'telegram_members': 0, 'mining_active_pools': 0, 'tokenomics_score': 0.0,
    'quantum_risk_score': 0.0,
    'coingecko_price_usd': None, 'coingecko_market_cap_usd': None,
    'coingecko_volume_usd': None, 'coingecko_rank': None,
}


ADDED_COLUMNS: Dict[str, str] = {
    "content": "TEXT",
    "is_active": "INTEGER NOT NULL DEFAULT 1",
    "last_updated": "TEXT",
    "is_promising": "BOOLEAN",
    "quality_level": "TEXT NOT NULL DEFAULT ''",
    "innovation_level": "TEXT NOT NULL DEFAULT ''",
    "quantum_risk_score": "REAL NOT NULL DEFAULT 0.0",
    "mining_type": "TEXT NOT NULL DEFAULT ''",
    "quality_note": "TEXT NOT NULL DEFAULT ''",
    "github_stars": "INTEGER NOT NULL DEFAULT 0",
    "github_forks": "INTEGER NOT NULL DEFAULT 0",
    "github_open_issues": "INTEGER NOT NULL DEFAULT 0",
    "github_language": "TEXT NOT NULL DEFAULT ''",
    "github_updated_at": "TEXT NOT NULL DEFAULT ''",
    "discord_members": "INTEGER NOT NULL DEFAULT 0",
    "telegram_members": "INTEGER NOT NULL DEFAULT 0",
    "hashrate_str": "TEXT NOT NULL DEFAULT ''",
    "mining_status": "TEXT NOT NULL DEFAULT ''",
    "mining_active_pools": "INTEGER NOT NULL DEFAULT 0",
    "mining_pool_list": "TEXT NOT NULL DEFAULT ''",
    "mining_link": "TEXT NOT NULL DEFAULT ''",
    "mining_guide_json": "TEXT NOT NULL DEFAULT ''",
    "token_distribution": "TEXT NOT NULL DEFAULT ''",
    "team_allocation": "TEXT NOT NULL DEFAULT ''",
    "vesting_period": "TEXT NOT NULL DEFAULT ''",
    "inflation_model": "TEXT NOT NULL DEFAULT ''",
    "burn_mechanisms": "TEXT NOT NULL DEFAULT ''",
    "utility_description": "TEXT NOT NULL DEFAULT ''",
    "tokenomics_risks": "TEXT NOT NULL DEFAULT ''",
    "tokenomics_score": "REAL NOT NULL DEFAULT 0.0",
    "coingecko_id": "TEXT NOT NULL DEFAULT ''",
    "coingecko_symbol": "TEXT NOT NULL DEFAULT ''",
    "coingecko_name": "TEXT NOT NULL DEFAULT ''",
    "coingecko_price_usd": "REAL",
    "coingecko_market_cap_usd": "REAL",
    "coingecko_volume_usd": "REAL",
    "coingecko_rank": "INTEGER",
    "whitepaper_text": "TEXT NOT NULL DEFAULT ''",
    "source_analysis": "TEXT NOT NULL DEFAULT ''",
    "social_json": "TEXT NOT NULL DEFAULT ''",
}


# Schéma initial : création des tables d'une base vierge. Partagé par
# `init_database` et `ensure_schema` pour que le dashboard puisse migrer une
# base existante sans construire un analyseur complet.
_SCHEMA_SQL = '''
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER UNIQUE,
        title TEXT,
        author TEXT,
        post_date TEXT,
        content TEXT,
        technical_score INTEGER,
        innovation_score INTEGER,
        disruptiveness_score INTEGER,
        credibility_score INTEGER,
        risk_score INTEGER,
        premine_percentage REAL,
        is_fork BOOLEAN,
        fork_base TEXT,
        mining_algorithm TEXT,
        consensus_mechanism TEXT,
        unique_features TEXT,
        red_flags TEXT,
        strengths TEXT,
        final_score INTEGER,
        github_link TEXT,
        whitepaper_link TEXT,
        website_link TEXT,
        analysis_date TEXT,
        last_updated TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        is_promising BOOLEAN
    );

    CREATE TABLE IF NOT EXISTS analysis_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER,
        analysis_date TEXT,
        score INTEGER,
        notes TEXT,
        FOREIGN KEY (topic_id) REFERENCES projects (topic_id)
    );

    CREATE INDEX IF NOT EXISTS idx_projects_analysis_date ON projects(analysis_date);
    CREATE INDEX IF NOT EXISTS idx_projects_mining_algorithm ON projects(mining_algorithm);
    CREATE INDEX IF NOT EXISTS idx_projects_is_active ON projects(is_active);
'''


def ensure_schema(db_path: str) -> None:
    """Crée la table projects si absente puis aligne ses colonnes sur ADDED_COLUMNS.

    Idempotent : lecture du schéma réel via PRAGMA table_info, ALTER TABLE
    uniquement pour les colonnes manquantes, données existantes préservées.
    Un PRAGMA sur une table absente répond vide au lieu de lever une erreur :
    sans création préalable, chaque ALTER échouerait silencieusement.
    """
    conn = sqlite3.connect(db_path)
    try:
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
        except sqlite3.OperationalError:
            cols = []
        if not cols:
            conn.executescript(_SCHEMA_SQL)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
        for col_name, col_def in ADDED_COLUMNS.items():
            if col_name not in cols:
                try:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {col_name} {col_def}")
                except sqlite3.OperationalError:
                    continue
        conn.commit()
    finally:
        conn.close()


def _ensure_active_column(db_path: str) -> None:
    ensure_schema(db_path)


class UltimateBitcointalkAnalyzer:
    def __init__(
        self,
        db_path: str = os.environ.get('BT_DB_PATH', 'crypto_analysis.db'),
        base_url: str = os.environ.get('BT_BASE_URL', 'https://bitcointalk.org'),
        board_id: int = int(os.environ.get('BT_BOARD_ID', '159')),
        pages: int = int(os.environ.get('BT_PAGES', '2')),
        user_agent: str = os.environ.get(
            'BT_USER_AGENT',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        ),
        timeout: int = int(os.environ.get('BT_TIMEOUT', '30')),
        link_timeout: int = int(os.environ.get('BT_LINK_TIMEOUT', '10')),
        llm_config: Optional[llm.LLMConfig] = None,
    ):
        self.db_path = db_path
        self.base_url = base_url.rstrip('/')
        self.board_id = board_id
        self.pages = pages
        self.user_agent = user_agent
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        # Un lien mort ne doit pas coûter le timeout d'une page entière.
        self.link_timeout = aiohttp.ClientTimeout(total=link_timeout)
        self.session: Optional[aiohttp.ClientSession] = None
        self.scraped_count = 0
        self.analyzed_count = 0
        self.skipped_count = 0
        self._last_mining_algorithm = ""
        # Fournisseur LLM effectif, source unique de verite pour le modele.
        # Par defaut on lit la configuration partagee (fichier + environnement).
        self.llm_config = llm_config or llm.load_config()
        self.init_database()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=wal")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def init_database(self) -> None:
        """Initialise la base de données SQLite complète"""
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
        _ensure_active_column(self.db_path)
        with self._connect() as conn:
            conn.execute("PRAGMA user_version=1")
            conn.commit()
        logger.info(T.t("console.logger_db_init"))

    async def init_session(self) -> None:
        """Initialise la session HTTP asynchrone"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={
                    'User-Agent': self.user_agent,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
                }
            )

    async def close_session(self) -> None:
        """Ferme la session HTTP"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_with_retry(self, url: str, retries: int = 3) -> Optional[str]:
        """Récupère une page avec mécanisme de retry"""
        if self.session is None or self.session.closed:
            await self.init_session()
        assert self.session is not None
        for attempt in range(retries):
            try:
                async with self.session.get(url, timeout=self.timeout) as response:
                    if response.status == 200:
                        return await response.text()
                    if response.status == 429:
                        wait_time = 2 ** attempt
                        logger.warning(T.t("console.logger_rate_limit", seconds=wait_time))
                        await asyncio.sleep(wait_time)
                        continue
                    logger.warning(T.t("console.logger_http_error", status=response.status, url=url))
            except Exception as e:
                logger.error(T.t("console.logger_attempt_failed", attempt=attempt + 1, error=e))
            await asyncio.sleep(1)
        return None

    def extract_links(self, text: str) -> Dict[str, List[str]]:
        """Extrait les liens utiles du contenu.

        Les annonces BitcoinTalk collent l'URL au mot suivant, la suivent d'un
        emoji ou enchaînent deux liens sans espace. Chaque candidat passe donc
        par `urlclean`, qui ne garde que ce qui est réellement joignable, et
        chaque URL n'est retenue qu'une fois.
        """
        links: Dict[str, List[str]] = {
            'github': [],
            'whitepaper': [],
            'website': [],
            'other': []
        }

        for candidate in urlclean.split_candidates(text):
            repo = urlclean.canonical_github(candidate)
            if repo:
                if repo not in links['github']:
                    links['github'].append(repo)
                continue

            url = urlclean.clean_url(candidate)
            if not url:
                continue
            if urlclean.is_whitepaper_url(url):
                if url not in links['whitepaper']:
                    links['whitepaper'].append(url)
            elif urlclean.is_project_site(url):
                if url not in links['website']:
                    links['website'].append(url)
            elif url not in links['other']:
                links['other'].append(url)

        return links

    async def verify_link(self, url: str) -> bool:
        """Indique si une URL répond sans erreur 4xx/5xx.

        `HEAD` d'abord : c'est le test le plus léger. Certains serveurs le
        refusent (405) ou répondent mal configurés, donc un `GET` est
        retombé sur les cas non concluants. Un lien joignable par un navigateur
        doit l'être aussi ici.

        L'hôte est résolu avant la requête : une annonce peut nommer
        `http://127.0.0.1:8080/` ou un nom interne, et cette URL ne doit pas
        faire sonner le port d/admin de la machine qui analyse.
        """
        candidate = urlclean.clean_url(url)
        if not candidate or not await self._is_public_url(candidate):
            return False
        if self.session is None or self.session.closed:
            await self.init_session()
        assert self.session is not None
        for method in ('HEAD', 'GET'):
            try:
                async with self.session.request(
                    method, candidate, timeout=self.link_timeout,
                    allow_redirects=True,
                ) as response:
                    if 200 <= response.status < 400:
                        return True
                    if response.status in _DEFINITIVE_MISSING:
                        return False
            except Exception as exc:
                logger.debug(T.t("console.logger_link_probe_failed", url=candidate, error=exc))
        return False

    async def _is_public_url(self, url: str) -> bool:
        """Vrai si l'URL ne peut viser que l'Internet public.

        Le nom d'hôte est résolu et chaque adresse obtenue est testée : un nom
        qui pointe vers une adresse privée est refusé, qu'il soit écrit en
        clair ou résolu par le serveur de noms.
        """
        host = urlclean.host_of(url)
        if not host:
            return False
        try:
            infos = await self._resolve(host)
        except (OSError, UnicodeError) as exc:
            logger.debug(T.t("console.logger_link_probe_failed", url=url, error=exc))
            return False
        if not infos:
            return False
        return all(_is_public_address(info[4][0]) for info in infos)

    async def _resolve(self, host: str) -> list:
        """Résout un nom d'hôte, point d'entrée unique pour le test."""
        loop = asyncio.get_running_loop()
        return await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)

    async def verify_repo(self, url: str) -> bool:
        """Indique si l'URL désigne un dépôt GitHub existant.

        Passer par l'API plutôt que par `verify_link` : la page d'un profil
        répond 200, donc un `HEAD` accepterait `/SystemThreat` pour un dépôt
        `/SystemThreat/xCoin` inexistant. L'API répond 404 sur un dépôt absent.
        """
        owner, repo = urlclean.github_slug(url)
        if not owner or not repo:
            return False
        if self.session is None or self.session.closed:
            await self.init_session()
        assert self.session is not None
        try:
            async with self.session.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers=self._github_headers(), timeout=self.timeout,
            ) as response:
                return response.status == 200
        except Exception as exc:
            logger.debug(T.t("console.logger_link_probe_failed", url=url, error=exc))
            return False

    def _github_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("BT_GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def resolve_link(self, url: str) -> str:
        """Confirme une URL ambiguë par une requête, avec et sans le mot collé.

        `…/whitepaperWho` peut être `/whitepaper` suivi de `Who`, ou une page
        réellement nommée `whitepaperWho`. Les deux lectures sont essayées dans
        l'ordre : la plus longue d'abord, pour ne pas mutiler une URL correcte.
        Si aucune ne répond, l'URL d'origine est conservée telle quelle — une
        coupure réseau ne doit pas effacer le lien de l'annonce.
        """
        variants = urlclean.link_variants(url)
        if len(variants) < 2:
            return variants[0] if variants else urlclean.clean_url(url)
        is_repo = urlclean.host_of(variants[0]) in ("github.com", "www.github.com")
        check = self.verify_repo if is_repo else self.verify_link
        for variant in variants:
            if await check(variant):
                if variant != variants[0]:
                    logger.info(T.t("console.logger_link_resolved", found=variant, source=variants[0]))
                return variant
        return variants[0]

    async def resolve_links(self, links: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Confirme en parallèle les liens des annonces, tous buckets confondus."""
        pending = [
            (bucket, index, url)
            for bucket, urls in links.items()
            for index, url in enumerate(urls)
            if len(urlclean.link_variants(url)) > 1
        ]
        if not pending:
            return links
        if self.session is None or self.session.closed:
            await self.init_session()
        results = await asyncio.gather(
            *(self.resolve_link(url) for _, _, url in pending), return_exceptions=True
        )
        for (bucket, index, _), result in zip(pending, results):
            if not isinstance(result, BaseException) and result:
                links[bucket][index] = result
        return links

    def _normalize_whitepaper_url(self, url: str) -> str:
        """Turns a whitepaper mention into a fetchable document URL.

        An absolute mention is cleaned like any project link: the stored value
        may carry the word that followed it in the announcement.
        """
        candidate = (url or "").strip()
        if not candidate:
            return ""
        if candidate.lower().startswith(("http://", "https://", "www.")):
            return urlclean.clean_url(candidate)
        if re.fullmatch(r'[\w./-]+\.(?:pdf|html?|txt|md)', candidate, re.IGNORECASE):
            return urljoin(self.base_url, candidate)
        if re.search(r'white[\s_-]?paper|lite[\s_-]?paper|tech(?:nical)?[\s_-]?paper', candidate, re.IGNORECASE):
            return urljoin(self.base_url, candidate)
        return ""

    async def fetch_whitepaper_text(self, url: str, max_chars: int = 8000) -> str:
        """Extracts readable text from an HTML or PDF whitepaper."""
        target = self._normalize_whitepaper_url(url)
        if not target:
            return ""
        # L'URL vient d'une annonce : elle peut viser une adresse interne
        # (metadonnees cloud, port d'admin). Meme garde que les liens.
        if not await self._is_public_url(target):
            logger.debug(T.t("console.logger_link_probe_failed", url=target,
                             error="non-public"))
            return ""
        try:
            if target.lower().split("?")[0].endswith(".pdf"):
                return await self._fetch_pdf_text(target, max_chars)
            html = await self.fetch_with_retry(target)
            if not html:
                return ""
            soup = BeautifulSoup(html, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'noscript']):
                tag.decompose()
            text = re.sub(r'\n{3,}', '\n\n', soup.get_text('\n', strip=True))
            return text[:max_chars]
        except Exception as exc:
            logger.error(T.t("console.logger_whitepaper_failed", error=exc, url=target))
            return ""

    async def _fetch_pdf_text(self, url: str, max_chars: int) -> str:
        assert self.session is not None
        # `max_chars` borne le texte extrait, pas les octets recus : une reponse
        # hostile de plusieurs Go serait mise en memoire entierement. Lecture
        # par paquets avec plafond absolu avant meme de parler a PdfReader.
        chunks: List[bytes] = []
        total = 0
        async with self.session.get(url, timeout=self.timeout) as response:
            if response.status != 200:
                return ""
            async for chunk in response.content.iter_chunked(65536):
                total += len(chunk)
                if total > _MAX_PDF_BYTES:
                    logger.error(T.t("console.logger_pdf_too_large", limit=_MAX_PDF_BYTES))
                    return ""
                chunks.append(chunk)
        payload = b"".join(chunks)
        try:
            reader = PdfReader(io.BytesIO(payload))
        except Exception as exc:
            logger.error(T.t("console.logger_pdf_invalid", error=exc))
            return ""
        pages: List[str] = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
            if sum(len(p) for p in pages) >= max_chars:
                break
        return re.sub(r'\n{3,}', '\n\n', "\n".join(pages))[:max_chars]

    @staticmethod
    def guess_coin_name(title: str) -> str:
        """Extrait le nom probable du coin depuis un titre d'annonce BitcoinTalk.

        Le titre est découpé en segments et chaque segment est nettoyé des
        marqueurs d'annonce ([ANN], préambules génériques) jusqu'à ce
        qu'un nom plausible reste. Le premier segment exploitable l'emporte.
        """
        text = re.sub(r"\[[^\]]{0,12}\]", " ", title or "").replace("-|", " ")
        text = re.sub(r"\([^)]*\)", " ", text)
        for segment in re.split(r"\s+[–—|-]\s+|\s*[|:?!,]\s+", text):
            words = [w for w in re.findall(r"[A-Za-z0-9$#\.]+", segment) if w]
            while words and words[0].lower() in _TITLE_STOPWORDS:
                words.pop(0)
            if not words:
                continue
            if len(words) > 4:
                words = words[:2]
            name = " ".join(words).strip().strip('.')
            if 2 <= len(name) <= 40:
                return name
        return ""

    async def fetch_coingecko_data(self, name: str, symbol: str) -> Dict[str, Any]:
        """Looks up CoinGecko market data. Empty dict when absent or rate-limited."""
        query = (symbol or self.guess_coin_name(name) or "").strip()
        if len(query) < 2:
            return {}
        headers = {"Accept": "application/json"}
        api_key = os.environ.get("BT_COINGECKO_API_KEY", "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        base = "https://api.coingecko.com/api/v3"
        try:
            html = await self.fetch_with_retry(f"{base}/search?query={query}", retries=2)
            if not html:
                return {}
            coins = json.loads(html).get("coins") or []
            if not coins:
                return {}
            best = coins[0]
            coin_id = best.get("id")
            if not coin_id:
                return {}
            html = await self.fetch_with_retry(f"{base}/coins/{coin_id}", retries=2)
            if not html:
                return {}
            data = json.loads(html)
            market = data.get("market_data") or {}
            return {
                "id": data.get("id") or "",
                "symbol": (data.get("symbol") or "").upper(),
                "name": data.get("name") or "",
                "price_usd": market.get("current_price", {}).get("usd"),
                "market_cap_usd": market.get("market_cap", {}).get("usd"),
                "volume_usd": market.get("total_volume", {}).get("usd"),
                "rank": data.get("market_cap_rank"),
            }
        except Exception as exc:
            logger.error(T.t("console.logger_coingecko_failed", error=exc, query=query))
            return {}

    async def fetch_github_source(self, github_url: str, max_chars: int = 6000) -> Dict[str, Any]:
        """Reads repository metadata and README for source-code analysis."""
        owner, repo = urlclean.github_slug(github_url)
        if not owner:
            return {}
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("BT_GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async def _get_json(path: str) -> Optional[dict]:
            url = f"https://api.github.com{path}"
            try:
                async with self.session.get(url, headers=headers, timeout=self.timeout) as resp:
                    if resp.status != 200:
                        return None
                    return json.loads(await resp.text())
            except Exception:
                return None

        meta = await _get_json(f"/repos/{owner}/{repo}")
        readme = await _get_json(f"/repos/{owner}/{repo}/readme")
        readme_text = ""
        if readme and readme.get("content"):
            try:
                readme_text = base64.b64decode(readme["content"]).decode("utf-8", "ignore")
            except Exception:
                readme_text = ""
        return {
            "stars": (meta or {}).get("stargazers_count", 0),
            "forks": (meta or {}).get("forks_count", 0),
            "open_issues": (meta or {}).get("open_issues_count", 0),
            "language": ((meta or {}).get("language") or "").strip(),
            "updated_at": (meta or {}).get("updated_at", ""),
            "readme": readme_text[:max_chars],
        }

    async def analyze_technical_depth(self, content: str) -> Dict:
        """Analyse technique approfondie avec Ollama (hors du thread de l'event loop)"""
        prompt = f"""
        Analyse technique COMPLÈTE de cette annonce de cryptomonnaie:

        CRITÈRES D'ANALYSE:
        1. INNOVATION RÉELLE (0-100): Nouveauté technique réelle vs marketing
        2. DISRUPTIVITÉ POTENTIELLE (0-100): Potentiel à changer le marché
        3. QUALITÉ TECHNIQUE (0-100): Robustesse architecturale
        4. ANALYSE PREMINE: Pourcentage et justification
        5. TYPE PROJET: Fork, clone ou véritable innovation
        6. MÉCANISMES UNIQUES: Features techniques originales
        7. RÉALISME: Faisabilité technique des propositions

        CONTENU:
        {content[:3000]}

        RÉPONSE EN JSON STRICT:
        {{
            "innovation_score": 0-100,
            "disruptiveness_score": 0-100,
            "technical_score": 0-100,
            "premine_analysis": "0% ou estimation",
            "is_fork": true/false,
            "fork_base": "nom projet ou null",
            "mining_algorithm": "algo spécifique",
            "consensus_mechanism": "PoW/PoS/DPoS/etc",
            "unique_technical_features": ["liste"],
            "technical_red_flags": ["liste"],
            "technical_strengths": ["liste"],
            "realism_assessment": "très réaliste/réaliste/optimiste/irréaliste"
        }}
        """

        analysis = await self.chat_json(prompt)
        for score_key in ('innovation_score', 'disruptiveness_score', 'technical_score'):
            if score_key in analysis:
                try:
                    analysis[score_key] = max(0, min(100, int(analysis[score_key])))
                except (TypeError, ValueError):
                    analysis[score_key] = 0

        return analysis

    @staticmethod
    def _parse_premine(raw) -> float:
        """Extrait un pourcentage de premine, 0.0 si introuvable ou invalide"""
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*%', str(raw))
        if not m:
            return 0.0
        try:
            return float(m.group(1).replace(',', '.'))
        except ValueError:
            return 0.0

    async def fetch_announcement(self, topic_id: int) -> str:
        """Texte du premier message d'un topic.

        Le contenu stocké en base est tronqué : un guide placé en bas de
        l'annonce y est absent. Relire le topic donne le texte entier, sans
        refaire l'analyse.
        """
        html = await self.fetch_with_retry(
            f"{self.base_url}/index.php?topic={topic_id}.0"
        )
        if not html:
            return ""
        post = BeautifulSoup(html, 'html.parser').find('div', class_='post')
        return post.get_text().strip() if post else ""

    async def fetch_mining_guide(self, website_url: str, content: str) -> Dict[str, Any]:
        """Mining connection details from the announcement, then from the site.

        The announcement is read first: when it already carries a pool or a
        port, no request is made. Otherwise the project website is crawled for
        a mining page, because most announcements link the guide instead of
        pasting it.
        """
        guide = mining_guide.parse_mining_guide(content)
        if mining_guide.guide_is_usable(guide):
            return guide

        site = urlclean.clean_url(website_url)
        if not site or not await self._is_public_url(site):
            return guide
        html = await self.fetch_with_retry(site)
        if not html:
            return guide
        guide = mining_guide.merge_mining_guides(
            guide, mining_guide.parse_mining_guide(self._page_text(html))
        )
        if mining_guide.guide_is_usable(guide):
            return guide

        pages = [
            (url, await self.fetch_with_retry(url))
            for url in mining_guide.find_guide_pages(html, site)
        ]
        for url, page_html in pages:
            if not page_html:
                continue
            guide = mining_guide.merge_mining_guides(
                guide, mining_guide.parse_mining_guide(self._page_text(page_html))
            )
            if mining_guide.guide_is_usable(guide):
                break
        return guide

    @staticmethod
    def _page_text(html: str) -> str:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'noscript']):
            tag.decompose()
        return soup.get_text('\n', strip=True)

    async def fetch_miningpool_stats(self, algorithm: str) -> Dict[str, Any]:
        """Mining statistics plus a canonical MiningPoolStats link.

        MiningPoolStats exposes no public API and blocks automated fetches of
        its dataset, so the numeric fields come from mempool.space for the
        Bitcoin family and stay empty for other algorithms. The link is always
        produced so the dashboard can deep-link to the right page.
        """
        algo = (algorithm or "").strip()
        result: Dict[str, Any] = {
            'status': '',
            'hashrate': '',
            'active_pools': 0,
            'pool_list': [],
            'link': self.miningpoolstats_url(algo),
        }
        if not algo or algo.lower() in ('unknown', 'non spécifié', 'not specified'):
            return result

        if algo.lower() in BITCOIN_ONLY_ALGOS:
            result.update(await self._fetch_mempool_mining())

        html = await self.fetch_with_retry(f"{MININGPOOLSTATS_URL}/?a={algo}", retries=2)
        if html:
            match = re.search(r'<span[^>]*class="[^"]*market-cap[^"]*"[^>]*>(.*?)</span>', html, re.S | re.I)
            if match:
                result['status'] = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        return result

    def miningpoolstats_url(self, algorithm: str) -> str:
        algo = re.sub(r'[^a-z0-9-]+', '', (algorithm or '').strip().lower())
        return f"{MININGPOOLSTATS_URL}/?a={algo}" if algo else ""

    async def _fetch_mempool_mining(self) -> Dict[str, Any]:
        try:
            hashrate = await self.fetch_with_retry(
                'https://mempool.space/api/v1/mining/hashrate/1m', retries=2
            )
            blocks = await self.fetch_with_retry(
                'https://mempool.space/api/v1/mining/blocks/fees/24h', retries=2
            )
        except Exception:
            return {}
        if not hashrate:
            return {}
        try:
            points = json.loads(hashrate).get('hashrates') or []
            if not points:
                return {}
            latest = points[-1].get('avgHashrate', 0)
            blocks = json.loads(blocks) if blocks else []
            pools = len(blocks) if isinstance(blocks, list) else 0
        except (json.JSONDecodeError, AttributeError, IndexError, TypeError, ValueError):
            return {}
        return {
            'status': 'active',
            'hashrate': f"{latest / 1e18:.1f} EH/s",
            'active_pools': pools,
            'pool_list': [],
        }

    async def fetch_social_metrics(self, website_link: str, content: str = "") -> Dict[str, Any]:
        """Community metrics from public endpoints and public profile pages.

        Telegram exposes no count without a bot token, so the public preview
        page is parsed instead. Every source degrades to zero on failure.
        """
        haystack = f"{website_link or ''}\n{content or ''}"
        result = {
            'twitter_followers': 0,
            'telegram_members': 0,
            'discord_members': 0,
            'reddit_subscribers': 0,
            'telegram_link': '',
            'discord_link': '',
            'reddit_link': '',
            'twitter_link': '',
        }
        result['telegram_link'] = self._first_link(haystack, r't\.me/([^/?\s"<>]+)')
        result['discord_link'] = self._first_link(
            haystack, r'discord(?:app)?\.com/invite/([^/?\s"<>]+)'
        )
        result['reddit_link'] = self._first_link(
            haystack, r'reddit\.com/r/([^/?\s"<>]+)'
        )
        result['twitter_link'] = self._first_link(
            haystack, r'(?:twitter|x)\.com/([^/?\s"<>]+)'
        )

        tasks = {
            'telegram_members': self._telegram_members(result['telegram_link']),
            'discord_members': self._discord_members(result['discord_link']),
            'reddit_subscribers': self._reddit_members(result['reddit_link']),
        }
        settled = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, value in zip(tasks.keys(), settled):
            result[key] = 0 if isinstance(value, BaseException) else value
        return result

    @staticmethod
    def _first_link(haystack: str, pattern: str) -> str:
        match = re.search(pattern, haystack or '', re.IGNORECASE)
        return match.group(1) if match else ''

    async def _telegram_members(self, slug: str) -> int:
        if not slug:
            return 0
        html = await self.fetch_with_retry(f"https://t.me/{slug}", retries=2)
        if not html:
            return 0
        match = re.search(r'([\d][\d\s,.]*)\s*(?:subscribers|membres|subscribers)', html, re.IGNORECASE)
        return self._parse_count(match.group(1)) if match else 0

    async def _discord_members(self, slug: str) -> int:
        if not slug:
            return 0
        assert self.session is not None
        url = f"https://discord.com/api/v9/invites/{slug}?with_counts=true"
        try:
            async with self.session.get(url, timeout=self.timeout) as response:
                if response.status != 200:
                    return 0
                data = json.loads(await response.text())
        except (aiohttp.ClientError, json.JSONDecodeError, asyncio.TimeoutError):
            return 0
        return int(data.get('approximate_member_count') or 0)

    async def _reddit_members(self, slug: str) -> int:
        if not slug:
            return 0
        html = await self.fetch_with_retry(f"https://www.reddit.com/r/{slug}/about.json", retries=2)
        if not html:
            return 0
        try:
            return int(json.loads(html)['data']['subscribers'])
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_count(raw: str) -> int:
        cleaned = re.sub(r'[\s,.]', '', raw)
        multiplier = 1
        if cleaned and cleaned[-1] in 'kKmM':
            multiplier = 1000 if cleaned[-1] in 'kK' else 1_000_000
            cleaned = cleaned[:-1]
        try:
            return int(float(cleaned) * multiplier)
        except ValueError:
            return 0

    async def chat_json(self, prompt: str, system: Optional[str] = None) -> Dict[str, Any]:
        """Appelle le fournisseur LLM configure hors de la boucle d'evenements.

        Le transport est choisi par llm.LLMConfig : Ollama local ou API
        compatible ChatGPT. Les erreurs sont journalisees et neutralisees en
        dict vide, comme avant, pour qu'un LLM indisponible ne casse pas un scan.
        """
        client = llm.ChatClient(self.llm_config)
        try:
            parsed = await asyncio.to_thread(client.complete_json, prompt, system)
        except llm.LLMError as exc:
            logger.error(T.t("console.logger_analysis_error", error=exc))
            return {}
        except Exception as exc:  # garde-fou : le scan ne doit jamais died sur le LLM
            logger.error(T.t("console.logger_analysis_error", error=exc))
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def analyze_source_code(self, github_url: str, readme: str,
                                  language: str = "") -> Dict[str, Any]:
        """Assesses the maturity and legitimacy of the source code."""
        if not readme and not github_url:
            return {}
        prompt = f"""
        Analyse la maturité technique d'un dépôt source de cryptomonnaie.

        REPO: {github_url}
        LANGAGE PRINCIPAL: {language or 'inconnu'}
        EXTRAITS README:
        {(readme or 'aucun')[:4000]}

        ÉVALUE:
        1. MATURITÉ DU CODE (0-100): structure, tests, documentation
        2. RISQUE DE SCAM (0-100): indicators of a fraudulent or unfinished project
        3. QUALITÉ TECHNIQUE (0-100): architecture et choix techniques
        4. NOTES: limites, points de vigilance

        RÉPONSE EN JSON STRICT:
        {{
            "source_maturity": 0-100,
            "scam_risk": 0-100,
            "code_quality": 0-100,
            "source_notes": "commentaire bref",
            "source_red_flags": ["liste"]
        }}
        """
        analysis = await self.chat_json(prompt)
        if not analysis:
            return {}
        for key in ("source_maturity", "scam_risk", "code_quality"):
            if key in analysis:
                try:
                    analysis[key] = max(0, min(100, int(analysis[key])))
                except (TypeError, ValueError):
                    analysis[key] = 0
        if not isinstance(analysis.get("source_red_flags"), list):
            analysis["source_red_flags"] = []
        return analysis

    async def analyze_whitepaper(self, text: str, title: str) -> Dict[str, Any]:
        """Grades a whitepaper against a fixed rubric."""
        if not text or len(text) < 200:
            return {}
        prompt = f"""
        Évalue la qualité technique du whitepaper d'un projet crypto.

        PROJET: {title}
        EXTRAITS:
        {text[:6000]}

        ÉVALUE:
        1. NIVEAU DE QUALITÉ: excellent, bon, moyen, faible, inexistant
        2. INNOVATION: niveau (excellent, bon, moyen, faible)
        3. NOTE QUALITÉ: commentaire bref et factuel sur le fond technique

        RÉPONSE EN JSON STRICT:
        {{
            "quality_level": "excellent|bon|moyen|faible|inexistant",
            "innovation_level": "excellent|bon|moyen|faible",
            "quality_note": "commentaire bref"
        }}
        """
        return await self.chat_json(prompt)

    async def analyze_tokenomics(self, content: str) -> Dict[str, Any]:
        """Analyse le tokenomics via LLM"""
        prompt = f"""
        Analyse le tokenomics de ce projet crypto basé sur l'annonce suivante:

        TOKENOMICS CHECKLIST:
        1. Distribution des tokens (équipe, public, investisseurs, fond)
        2. Vesting/cliff pour l'équipe et early investors
        3. Inflation/deflation (émission, burning, mécanismes de déflation)
        4. Valeur accrue (yield, staking, gouvernance)
        5. Risques tokenomiques

        CONTENU:
        {content[:3000]}

        REPONSE EN JSON STRICT:
        {{
            "token_distribution": "description de la distribution",
            "team_allocation": "pourcentage ou description",
            "vesting_period": "description du vesting",
            "inflation_model": "inflationniste/deflationnaire/neutre",
            "burn_mechanisms": ["liste des mécanismes"],
            "utility_description": "utilisation du token",
            "tokenomics_risks": ["liste de risques"],
            "tokenomics_score": score 0-100
        }}
        """

        analysis = await self.chat_json(prompt)
        if not analysis:
            return {}
        try:
            score = int(analysis.get('tokenomics_score', 0))
        except (TypeError, ValueError):
            score = 0
        analysis['tokenomics_score'] = max(0, min(100, score))
        for list_key in ('burn_mechanisms', 'tokenomics_risks'):
            if not isinstance(analysis.get(list_key), list):
                analysis[list_key] = []
        return analysis

    @staticmethod
    def _clamp(value: int) -> int:
        return max(0, min(100, value))

    def calculate_credibility_score(self, analysis: Dict) -> int:
        """Score de crédibilité : signal positif du projet + présence de liens"""
        if not analysis:
            return 0
        score = 0
        realistic = str(analysis.get('realism_assessment', '')).lower()
        if 'réaliste' in realistic and 'ir' not in realistic:
            score += 20
        if analysis.get('mining_algorithm'):
            score += 10
        if analysis.get('consensus_mechanism'):
            score += 10
        if analysis.get('unique_technical_features'):
            score += 10
        if analysis.get('technical_strengths'):
            score += 10
        return self._clamp(score)

    def calculate_risk_score(self, analysis: Dict) -> int:
        """Score de risque : premine + red flags + fork + irréalisme"""
        if not analysis:
            return 0
        score = 0
        premine = self._parse_premine(analysis.get('premine_analysis', ''))
        if premine > 20:
            score += 30
        elif premine > 10:
            score += 20
        elif premine > 5:
            score += 10
        score += min(30, 10 * len(analysis.get('technical_red_flags') or []))
        if analysis.get('is_fork'):
            score += 10
        if 'irréaliste' in str(analysis.get('realism_assessment', '')).lower():
            score += 20
        return self._clamp(score)

    def calculate_final_score(
        self,
        analysis: Dict,
        has_whitepaper: bool,
        has_github: bool
    ) -> int:
        """Calcule un score final pondéré"""
        analysis = analysis or {}
        weights = {
            'innovation': 0.35,
            'technical': 0.30,
            'disruptiveness': 0.25
        }

        base_score = (
            analysis.get('innovation_score', 0) * weights['innovation']
            + analysis.get('technical_score', 0) * weights['technical']
            + analysis.get('disruptiveness_score', 0) * weights['disruptiveness']
        )

        bonuses = 0
        if has_whitepaper:
            bonuses += 5
        if has_github:
            bonuses += 5
        if analysis and not analysis.get('is_fork', False):
            bonuses += 10

        premine = self._parse_premine(analysis.get('premine_analysis', ''))
        if premine > 20:
            bonuses -= 25
        elif premine > 10:
            bonuses -= 15
        elif premine > 5:
            bonuses -= 5

        return self._clamp(base_score + bonuses)

    async def collect_enrichment(
        self, links: Dict[str, List[str]], title: str, content: str = ""
    ) -> Dict[str, Any]:
        """Fans out to every external data source in parallel.

        Each collector degrades to empty values on failure so a single
        unavailable API never blocks the analysis or the database write.
        """
        github_url = links['github'][0] if links['github'] else ""
        whitepaper_url = links['whitepaper'][0] if links['whitepaper'] else ""
        website_url = links['website'][0] if links['website'] else ""

        tasks = [
            self.fetch_github_source(github_url),
            self.fetch_social_metrics(website_url, content),
            self.fetch_coingecko_data(title, ""),
            self.fetch_miningpool_stats(self._last_mining_algorithm),
            self.fetch_whitepaper_text(whitepaper_url),
            self.fetch_mining_guide(website_url, content),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        names = ('github', 'social', 'coingecko', 'mining', 'whitepaper', 'guide')
        collected: Dict[str, Any] = {}
        for name, value in zip(names, results):
            collected[name] = {} if isinstance(value, BaseException) else value
            if isinstance(value, BaseException):
                logger.error(T.t("console.logger_enrichment_failed", source=name, error=value))
        collected['whitepaper_text'] = collected.get('whitepaper') or ''
        return collected

    async def process_announcement(self, topic_id: int, url: str) -> None:
        """Traite une annonce complète"""
        owns_session = self.session is None or self.session.closed
        if owns_session:
            await self.init_session()
        try:
            logger.info(T.t("console.logger_processing", topic_id=topic_id))

            html = await self.fetch_with_retry(url)
            if not html:
                return

            soup = BeautifulSoup(html, 'html.parser')

            title_tag = soup.find('title')
            title = title_tag.get_text().split(' | ')[0] if title_tag else "Titre inconnu"

            author_span = soup.find('span', id=re.compile(r'author_'))
            author = author_span.get_text() if author_span else "Auteur inconnu"

            post_div = soup.find('div', class_='post')
            content = post_div.get_text().strip() if post_div else ""

            links = await self.resolve_links(self.extract_links(content))

            technical_analysis = await self.analyze_technical_depth(content)
            if not technical_analysis:
                logger.warning(T.t("console.logger_empty_analysis", topic_id=topic_id))
                has_analysis = False
            else:
                has_analysis = True

            mining_algorithm = technical_analysis.get('mining_algorithm') or '' if has_analysis else ''
            self._last_mining_algorithm = mining_algorithm
            enrichment = await self.collect_enrichment(links, title, content)

            github = enrichment.get('github') or {}
            social = enrichment.get('social') or {}
            coingecko = enrichment.get('coingecko') or {}
            mining = enrichment.get('mining') or {}
            whitepaper_text = enrichment.get('whitepaper_text') or ''

            whitepaper_analysis = await self.analyze_whitepaper(whitepaper_text, title)
            source_analysis = await self.analyze_source_code(
                links['github'][0] if links['github'] else "",
                github.get('readme', ''), github.get('language', '')
            )
            tokenomics = await self.analyze_tokenomics(content) if has_analysis else {}

            has_whitepaper = bool(whitepaper_text) or len(links['whitepaper']) > 0
            has_github = bool(github) or len(links['github']) > 0
            final_score = self.calculate_final_score(
                technical_analysis, has_whitepaper, has_github
            )

            project = CryptoProject(
                topic_id=topic_id,
                title=title,
                author=author,
                content=content[:1000] + "..." if len(content) > 1000 else content,
                post_date=datetime.now().isoformat(),
                github_link=links['github'][0] if links['github'] else "",
                whitepaper_link=links['whitepaper'][0] if links['whitepaper'] else "",
                website_link=links['website'][0] if links['website'] else "",
                technical_score=technical_analysis.get('technical_score', 0) if has_analysis else 0,
                innovation_score=technical_analysis.get('innovation_score', 0) if has_analysis else 0,
                disruptiveness_score=technical_analysis.get('disruptiveness_score', 0) if has_analysis else 0,
                credibility_score=self.calculate_credibility_score(technical_analysis),
                risk_score=self.calculate_risk_score(technical_analysis),
                premine_percentage=self._parse_premine(technical_analysis.get('premine_analysis', '')),
                is_fork=bool(technical_analysis.get('is_fork', False)) if has_analysis else False,
                fork_base=technical_analysis.get('fork_base') or '',
                mining_algorithm=mining_algorithm,
                consensus_mechanism=technical_analysis.get('consensus_mechanism') or '' if has_analysis else '',
                unique_features=list(technical_analysis.get('unique_technical_features') or []),
                red_flags=list(technical_analysis.get('technical_red_flags') or []),
                strengths=list(technical_analysis.get('technical_strengths') or []),
                final_score=final_score,
                analysis_date=datetime.now().isoformat(),
                github_stars=github.get('stars', 0),
                github_forks=github.get('forks', 0),
                github_open_issues=github.get('open_issues', 0),
                github_language=github.get('language', ''),
                github_updated_at=github.get('updated_at', ''),
                discord_members=social.get('discord_members', 0),
                telegram_members=social.get('telegram_members', 0),
                social_json=json.dumps(social, ensure_ascii=False, allow_nan=False),
                mining_status=mining.get('status', ''),
                hashrate_str=mining.get('hashrate', ''),
                mining_active_pools=mining.get('active_pools', 0),
                mining_pool_list=json.dumps(mining.get('pool_list') or [], ensure_ascii=False),
                mining_link=mining.get('link', ''),
                mining_guide_json=json.dumps(
                    enrichment.get('guide') or {}, ensure_ascii=False, allow_nan=False
                ),
                coingecko_id=coingecko.get('id', ''),
                coingecko_symbol=coingecko.get('symbol', ''),
                coingecko_name=coingecko.get('name', ''),
                coingecko_price_usd=coingecko.get('price_usd'),
                coingecko_market_cap_usd=coingecko.get('market_cap_usd'),
                coingecko_volume_usd=coingecko.get('volume_usd'),
                coingecko_rank=coingecko.get('rank'),
                whitepaper_text=whitepaper_text,
                source_analysis=json.dumps(source_analysis, ensure_ascii=False, allow_nan=False),
                token_distribution=tokenomics.get('token_distribution', ''),
                team_allocation=tokenomics.get('team_allocation', ''),
                vesting_period=tokenomics.get('vesting_period', ''),
                inflation_model=tokenomics.get('inflation_model', ''),
                burn_mechanisms=json.dumps(tokenomics.get('burn_mechanisms') or [], ensure_ascii=False),
                utility_description=tokenomics.get('utility_description', ''),
                tokenomics_risks=json.dumps(tokenomics.get('tokenomics_risks') or [], ensure_ascii=False),
                tokenomics_score=tokenomics.get('tokenomics_score', 0.0),
            )

            if whitepaper_analysis:
                project.quality_level = whitepaper_analysis.get('quality_level', '')
                project.quality_note = whitepaper_analysis.get('quality_note', '')

            self.save_project(project)
            self.analyzed_count += 1

            if final_score >= 75:
                logger.warning(T.t("console.logger_promising", title=title, score=final_score))

        except Exception as e:
            logger.error(T.t("console.logger_process_error", topic_id=topic_id, error=e))
        finally:
            if owns_session:
                await self.close_session()

    def save_project(self, project: CryptoProject) -> None:
        """Sauvegarde un projet en base de données"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT is_active FROM projects WHERE topic_id = ?",
                    (project.topic_id,)
                ).fetchone()
                is_active = int(row[0]) if row and row[0] is not None else 1

                columns = list(ENRICHMENT_COLUMNS)
                placeholders = ', '.join('?' * len(columns))
                values: List[Any] = []
                for name in columns:
                    if name == 'last_updated':
                        values.append(datetime.now().isoformat())
                    elif name == 'is_promising':
                        values.append(project.final_score >= 75)
                    else:
                        raw = getattr(project, name, ENRICHMENT_DEFAULTS.get(name))
                        if raw is None:
                            raw = ENRICHMENT_DEFAULTS.get(name)
                        if isinstance(raw, (list, dict)):
                            raw = json.dumps(raw, ensure_ascii=False, allow_nan=False)
                        values.append(raw)
                conn.execute(
                    f"INSERT OR REPLACE INTO projects ({', '.join(columns)}, is_active) "
                    f"VALUES ({placeholders}, ?)",
                    values + [is_active],
                )
                conn.execute(
                    "INSERT INTO analysis_history (topic_id, analysis_date, score, notes) VALUES (?, ?, ?, ?)",
                    (project.topic_id, datetime.now().isoformat(), project.final_score,
                     "Analyse automatique")
                )
            logger.info(T.t("console.logger_saved", topic_id=project.topic_id, score=project.final_score))
        except Exception as e:
            logger.error(T.t("console.logger_save_error", error=e))

    async def scan_bitcointalk_section(
        self,
        section_id: Optional[int] = None,
        pages: Optional[int] = None,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        reanalyze_existing: bool = False,
    ) -> None:
        """Scan une section de Bitcointalk"""
        section_id = section_id if section_id is not None else self.board_id
        pages = pages if pages is not None else self.pages

        def _report(**payload: Any) -> None:
            if progress_cb is not None:
                progress_cb(payload)

        await self.init_session()

        try:
            with self._connect() as conn:
                known = {row[0] for row in conn.execute("SELECT topic_id FROM projects")}

            for page in range(pages):
                url = f"{self.base_url}/index.php?board={section_id}.{page * 40}"
                logger.info(T.t("console.logger_scan_page", page=page + 1, total=pages))

                html = await self.fetch_with_retry(url)
                if not html:
                    _report(pages_done=page + 1)
                    continue

                soup = BeautifulSoup(html, 'html.parser')

                topic_links: List[str] = []
                for link in soup.find_all('a', href=re.compile(r'topic=\d+\.msg\d+')):
                    href = link.get('href')
                    if href:
                        topic_links.append(href)
                self.scraped_count += len(topic_links)
                # Compteur propre au scan : len(known) inclut tous les projets
                # de la base, pas seulement les sujets deja vus pendant CE scan.
                _report(topics_found=self.scraped_count,
                        skipped_count=self.skipped_count)

                for topic_link in topic_links:
                    match = re.search(r'topic=(\d+)', topic_link)
                    if not match:
                        continue
                    topic_id = int(match.group(1))

                    if topic_id in known and not reanalyze_existing:
                        self.skipped_count += 1
                        _report(skipped_count=self.skipped_count)
                        continue

                    full_url = urljoin(self.base_url, topic_link)
                    if urlsplit(full_url).netloc not in (urlsplit(self.base_url).netloc, "bitcointalk.org", "www.bitcointalk.org"):
                        continue
                    await self.process_announcement(topic_id, full_url)
                    known.add(topic_id)
                    _report(analyzed_count=self.analyzed_count)
                    await asyncio.sleep(1)

                _report(pages_done=page + 1)

        finally:
            await self.close_session()

    def generate_report(self, report_path: str = 'crypto_analysis_report.json') -> Dict:
        """Génère un rapport des analyses"""
        with self._connect() as conn:
            df = pd.read_sql_query("""
                SELECT topic_id, title, author, technical_score, innovation_score,
                       disruptiveness_score, credibility_score, risk_score,
                       final_score, premine_percentage, is_fork,
                       mining_algorithm, consensus_mechanism, github_link,
                       analysis_date, is_promising
                FROM projects
                ORDER BY final_score DESC
            """, conn)

        if df.empty:
            report = {
                'total_projects': 0,
                'promising_projects': 0,
                'average_score': 0.0,
                'top_projects': [],
                'analysis_date': datetime.now().isoformat()
            }
        else:
            report = {
                'total_projects': int(len(df)),
                'promising_projects': int((df['is_promising'] == 1).sum()),
                'average_score': round(float(df['final_score'].mean()), 2),
                'top_projects': df.head(10).to_dict('records'),
                'analysis_date': datetime.now().isoformat()
            }

        for project in report['top_projects']:
            if 'final_score' in project:
                project['final_score'] = int(project['final_score'])

        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, allow_nan=False)

        return report


def reanalyze_project(
    topic_id: int, model: Optional[str] = None,
    llm_config: Optional["llm.LLMConfig"] = None,
) -> None:
    """Re-analyse un projet existant (synchronise, à appeler via asyncio.to_thread)"""
    config = llm_config if llm_config else llm.load_config()
    if model:
        config.model = model
    analyzer = UltimateBitcointalkAnalyzer(llm_config=config)
    url = f"{analyzer.base_url}/index.php?topic={topic_id}.0"
    logger.info(T.t("console.reanalyze_start", topic_id=topic_id, url=url))
    try:
        asyncio.run(analyzer.process_announcement(topic_id, url))
    except Exception:
        raise
    logger.info(T.t("console.reanalyze_done", topic_id=topic_id))


def parse_args() -> argparse.Namespace:
    model_default = llm.load_config().model
    base_url_default = os.environ.get('BT_BASE_URL', 'https://bitcointalk.org')
    section_default = int(os.environ.get('BT_BOARD_ID', '159'))
    pages_default = int(os.environ.get('BT_PAGES', '2'))
    db_default = os.environ.get('BT_DB_PATH', 'crypto_analysis.db')
    timeout_default = int(os.environ.get('BT_TIMEOUT', '30'))

    parser = argparse.ArgumentParser(
        description=T.t("cli.description")
    )
    parser.add_argument('--model', default=model_default,
                        help=T.t("cli.help_model", value=model_default))
    parser.add_argument('--base-url', default=base_url_default,
                        help=T.t("cli.help_base_url", value=base_url_default))
    parser.add_argument('--section', type=int, default=section_default,
                        help=T.t("cli.help_section", value=section_default))
    parser.add_argument('--pages', type=int, default=pages_default,
                        help=T.t("cli.help_pages", value=pages_default))
    parser.add_argument('--db', default=db_default,
                        help=T.t("cli.help_db", value=db_default))
    parser.add_argument('--timeout', type=int, default=timeout_default,
                        help=T.t("cli.help_timeout", value=timeout_default))
    return parser.parse_args()


async def main() -> int:
    """Fonction principale"""
    args = parse_args()
    # --model est une surcharge ponctuelle : elle doit gagner sur la
    # configuration partagee sans l'ecrire sur disque.
    config = llm.load_config()
    config.model = args.model
    analyzer = UltimateBitcointalkAnalyzer(
        db_path=args.db,
        base_url=args.base_url,
        board_id=args.section,
        pages=args.pages,
        timeout=args.timeout,
        llm_config=config,
    )

    print(T.t("console.banner"))
    print(T.t("console.scan_info", section=args.section, pages=args.pages,
              model=config.model))
    print("=" * 60)

    scan_error: Optional[BaseException] = None
    try:
        print(T.t("console.scan_running"))
        await analyzer.scan_bitcointalk_section()
    except BaseException as e:
        scan_error = e
        logger.error(T.t("console.logger_scan_error", error=e))

    try:
        print("\n" + T.t("console.report_generating"))
        report = analyzer.generate_report()

        print("\n" + T.t("console.analysis_done"))
        print(T.t("console.projects_analyzed", count=report['total_projects']))
        print(T.t("console.projects_promising", count=report['promising_projects']))
        print(T.t("console.avg_score", score=f"{report['average_score']:.1f}"))

        if report['promising_projects'] > 0:
            print("\n" + T.t("console.top_projects"))
            for i, project in enumerate(report['top_projects'][:5], 1):
                if project['is_promising']:
                    print(f"{i}. {project['title']}")
                    print(T.t("console.project_author", author=project['author']))
                    print(T.t("console.project_score", score=project['final_score']))
                    print(T.t("console.project_algo", algo=project['mining_algorithm']))
                    print(T.t("console.project_github", link=project['github_link'][:50]) if project['github_link'] else T.t("console.project_github_none"))
                    print()
    except Exception as e:
        logger.error(T.t("console.logger_report_error", error=e))
        if scan_error is None:
            return 1
    finally:
        print(T.t("console.report_saved", path="crypto_analysis_report.json"))
        print(T.t("console.db_path", path=analyzer.db_path))

    return 1 if scan_error is not None else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
