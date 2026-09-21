#!/usr/bin/env python3
"""
Agent Ultimate d'Analyse Bitcointalk - Scan technique complet des nouvelles cryptos
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp
import ollama
import pandas as pd
from bs4 import BeautifulSoup

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
    final_score: int = 0
    analysis_date: str = ""


class UltimateBitcointalkAnalyzer:
    def __init__(
        self,
        db_path: str = os.environ.get('BT_DB_PATH', 'crypto_analysis.db'),
        model: str = os.environ.get('BT_MODEL', 'llama3.1'),
        base_url: str = os.environ.get('BT_BASE_URL', 'https://bitcointalk.org'),
        board_id: int = int(os.environ.get('BT_BOARD_ID', '159')),
        pages: int = int(os.environ.get('BT_PAGES', '2')),
        user_agent: str = os.environ.get(
            'BT_USER_AGENT',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        ),
        timeout: int = int(os.environ.get('BT_TIMEOUT', '30')),
    ):
        self.db_path = db_path
        self.model = model
        self.base_url = base_url.rstrip('/')
        self.board_id = board_id
        self.pages = pages
        self.user_agent = user_agent
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session: Optional[aiohttp.ClientSession] = None
        self.scraped_count = 0
        self.analyzed_count = 0
        self.init_database()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def init_database(self) -> None:
        """Initialise la base de données SQLite complète"""
        with self._connect() as conn:
            conn.executescript('''
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
            ''')
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
        """Extrait tous les liens importants du contenu"""
        links: Dict[str, List[str]] = {
            'github': [],
            'whitepaper': [],
            'website': [],
            'other': []
        }

        patterns = {
            'github': r'github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+',
            'whitepaper': r'(whitepaper|white paper|litepaper|technical paper)',
            'website': r'https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        }

        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
        found_urls = re.findall(url_pattern, text, re.IGNORECASE)

        for url in found_urls:
            if re.search(patterns['github'], url, re.IGNORECASE):
                links['github'].append(url)
            elif re.search(patterns['whitepaper'], url, re.IGNORECASE):
                links['whitepaper'].append(url)
            elif re.search(patterns['website'], url, re.IGNORECASE):
                links['website'].append(url)
            else:
                links['other'].append(url)

        return links

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

        loop = asyncio.get_running_loop()

        def _chat() -> str:
            response = ollama.chat(model=self.model, messages=[
                {'role': 'user', 'content': prompt}
            ])
            return response['message']['content']

        try:
            result_text = await loop.run_in_executor(None, _chat)
        except Exception as e:
            logger.error(T.t("console.logger_analysis_error", error=e))
            return {}

        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if not json_match:
            logger.warning(T.t("console.logger_json_not_found"))
            return {}

        try:
            analysis = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.error(T.t("console.logger_json_invalid", error=e))
            return {}

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

    async def process_announcement(self, topic_id: int, url: str) -> None:
        """Traite une annonce complète"""
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

            links = self.extract_links(content)

            technical_analysis = await self.analyze_technical_depth(content)
            if not technical_analysis:
                logger.warning(T.t("console.logger_empty_analysis", topic_id=topic_id))
                has_analysis = False
            else:
                has_analysis = True

            has_whitepaper = len(links['whitepaper']) > 0
            has_github = len(links['github']) > 0
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
                mining_algorithm=technical_analysis.get('mining_algorithm') or '',
                consensus_mechanism=technical_analysis.get('consensus_mechanism') or '',
                unique_features=list(technical_analysis.get('unique_technical_features') or []),
                red_flags=list(technical_analysis.get('technical_red_flags') or []),
                strengths=list(technical_analysis.get('technical_strengths') or []),
                final_score=final_score,
                analysis_date=datetime.now().isoformat()
            )

            self.save_project(project)
            self.analyzed_count += 1

            if final_score >= 75:
                logger.warning(T.t("console.logger_promising", title=title, score=final_score))

        except Exception as e:
            logger.error(T.t("console.logger_process_error", topic_id=topic_id, error=e))

    def save_project(self, project: CryptoProject) -> None:
        """Sauvegarde un projet en base de données"""
        try:
            with self._connect() as conn:
                conn.execute('''
                INSERT OR REPLACE INTO projects
                (topic_id, title, author, post_date, content, technical_score, innovation_score,
                 disruptiveness_score, credibility_score, risk_score, premine_percentage, is_fork,
                 fork_base, mining_algorithm, consensus_mechanism, unique_features, red_flags,
                 strengths, final_score, github_link, whitepaper_link, website_link, analysis_date,
                 last_updated, is_promising)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    project.topic_id, project.title, project.author, project.post_date,
                    project.content, project.technical_score, project.innovation_score,
                    project.disruptiveness_score, project.credibility_score, project.risk_score,
                    project.premine_percentage, project.is_fork, project.fork_base,
                    project.mining_algorithm, project.consensus_mechanism,
                    json.dumps(project.unique_features, ensure_ascii=False, allow_nan=False),
                    json.dumps(project.red_flags, ensure_ascii=False, allow_nan=False),
                    json.dumps(project.strengths, ensure_ascii=False, allow_nan=False),
                    project.final_score, project.github_link,
                    project.whitepaper_link, project.website_link, project.analysis_date,
                    datetime.now().isoformat(), project.final_score >= 75
                ))
                conn.execute(
                    "INSERT INTO analysis_history (topic_id, analysis_date, score, notes) VALUES (?, ?, ?, ?)",
                    (project.topic_id, datetime.now().isoformat(), project.final_score,
                     "Analyse automatique")
                )
            logger.info(T.t("console.logger_saved", topic_id=project.topic_id, score=project.final_score))
        except Exception as e:
            logger.error(T.t("console.logger_save_error", error=e))

    async def scan_bitcointalk_section(self, section_id: Optional[int] = None, pages: Optional[int] = None) -> None:
        """Scan une section de Bitcointalk"""
        section_id = section_id if section_id is not None else self.board_id
        pages = pages if pages is not None else self.pages

        await self.init_session()

        try:
            for page in range(pages):
                url = f"{self.base_url}/index.php?board={section_id}.{page * 40}"
                logger.info(T.t("console.logger_scan_page", page=page + 1, total=pages))

                html = await self.fetch_with_retry(url)
                if not html:
                    continue

                soup = BeautifulSoup(html, 'html.parser')

                topic_links: List[str] = []
                for link in soup.find_all('a', href=re.compile(r'topic=\d+\.msg\d+')):
                    href = link.get('href')
                    if href:
                        topic_links.append(href)
                self.scraped_count += len(topic_links)

                analyzed_ids = set()
                with self._connect() as conn:
                    cur = conn.execute("SELECT topic_id FROM projects")
                    analyzed_ids = {row[0] for row in cur.fetchall()}

                for topic_link in topic_links:
                    match = re.search(r'topic=(\d+)', topic_link)
                    if not match:
                        continue
                    topic_id = int(match.group(1))

                    if topic_id in analyzed_ids:
                        continue

                    full_url = urljoin(self.base_url, topic_link)
                    if urlsplit(full_url).netloc not in (urlsplit(self.base_url).netloc, "bitcointalk.org", "www.bitcointalk.org"):
                        continue
                    await self.process_announcement(topic_id, full_url)
                    if topic_id not in analyzed_ids:
                        analyzed_ids.add(topic_id)
                    await asyncio.sleep(1)

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


def parse_args() -> argparse.Namespace:
    model_default = os.environ.get('BT_MODEL', 'llama3.1')
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
    analyzer = UltimateBitcointalkAnalyzer(
        db_path=args.db,
        model=args.model,
        base_url=args.base_url,
        board_id=args.section,
        pages=args.pages,
        timeout=args.timeout,
    )

    print(T.t("console.banner"))
    print(T.t("console.scan_info", section=args.section, pages=args.pages, model=args.model))
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
