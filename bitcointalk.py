#!/usr/bin/env python3
"""
Agent Ultimate d'Analyse Bitcointalk - Scan technique complet des nouvelles cryptos
"""

import requests
import ollama
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import logging
import sqlite3
import time
import urllib.parse
from pathlib import Path
import pandas as pd
from dataclasses import dataclass
import httpx

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('crypto_analysis.log'),
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
    unique_features: List[str] = None
    red_flags: List[str] = None
    strengths: List[str] = None
    final_score: int = 0
    analysis_date: str = ""

class UltimateBitcointalkAnalyzer:
    def __init__(self, db_path: str = "crypto_analysis.db"):
        self.base_url = "https://bitcointalk.org"
        self.session = None
        self.db_path = db_path
        self.scraped_count = 0
        self.analyzed_count = 0
        self.init_database()
        
    def init_database(self):
        """Initialise la base de données SQLite complète"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Table principale des projets
        cursor.execute('''
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
        )
        ''')
        
        # Table d'historique des analyses
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS analysis_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER,
            analysis_date TEXT,
            score INTEGER,
            notes TEXT,
            FOREIGN KEY (topic_id) REFERENCES projects (topic_id)
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Base de données initialisée")

    async def init_session(self):
        """Initialise la session HTTP asynchrone"""
        self.session = aiohttp.ClientSession(headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        })

    async def close_session(self):
        """Ferme la session HTTP"""
        if self.session:
            await self.session.close()

    async def fetch_with_retry(self, url: str, retries: int = 3) -> Optional[str]:
        """Récupère une page avec mécanisme de retry"""
        for attempt in range(retries):
            try:
                async with self.session.get(url, timeout=30) as response:
                    if response.status == 200:
                        return await response.text()
                    elif response.status == 429:
                        wait_time = 2 ** attempt
                        logger.warning(f"Rate limit hit, waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.warning(f"HTTP {response.status} for {url}")
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(1)
        return None

    def extract_links(self, text: str) -> Dict[str, str]:
        """Extrait tous les liens importants du contenu"""
        links = {
            'github': [],
            'whitepaper': [],
            'website': [],
            'other': []
        }
        
        # Patterns pour identifier les types de liens
        patterns = {
            'github': r'github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+',
            'whitepaper': r'(whitepaper|white paper|litepaper|technical paper)',
            'website': r'https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        }
        
        # Extraction des URLs
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
        """Analyse technique approfondie avec Ollama"""
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
        
        try:
            response = ollama.chat(model='llama3.1', messages=[{
                'role': 'user',
                'content': prompt
            }])
            
            result_text = response['message']['content']
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            
            if json_match:
                analysis = json.loads(json_match.group())
                
                # Nettoyage et validation des scores
                for score_key in ['innovation_score', 'disruptiveness_score', 'technical_score']:
                    if score_key in analysis:
                        analysis[score_key] = max(0, min(100, int(analysis[score_key])))
                
                return analysis
                
        except Exception as e:
            logger.error(f"Erreur analyse technique: {e}")
        
        return {}

    def calculate_final_score(self, analysis: Dict, has_whitepaper: bool, has_github: bool) -> int:
        """Calcule un score final pondéré"""
        weights = {
            'innovation': 0.35,
            'technical': 0.30,
            'disruptiveness': 0.25,
            'bonuses': 0.10
        }
        
        base_score = (
            analysis.get('innovation_score', 0) * weights['innovation'] +
            analysis.get('technical_score', 0) * weights['technical'] +
            analysis.get('disruptiveness_score', 0) * weights['disruptiveness']
        )
        
        # Bonus/Malus
        bonuses = 0
        
        if has_whitepaper:
            bonuses += 5
        if has_github:
            bonuses += 5
        if not analysis.get('is_fork', False):
            bonuses += 10
            
        # Malus pour premine élevé
        premine_str = analysis.get('premine_analysis', '0%')
        if '%' in premine_str:
            try:
                premine_pct = float(premine_str.replace('%', ''))
                if premine_pct > 20:
                    bonuses -= 25
                elif premine_pct > 10:
                    bonuses -= 15
                elif premine_pct > 5:
                    bonuses -= 5
            except:
                pass
        
        final_score = base_score + bonuses
        return max(0, min(100, int(final_score)))

    async def process_announcement(self, topic_id: int, url: str):
        """Traite une annonce complète"""
        try:
            logger.info(f"Traitement de l'annonce {topic_id}")
            
            # Récupération de la page
            html = await self.fetch_with_retry(url)
            if not html:
                return
                
            # Extraction des données
            soup = BeautifulSoup(html, 'html.parser')
            
            # Titre
            title_tag = soup.find('title')
            title = title_tag.get_text().split(' | ')[0] if title_tag else "Titre inconnu"
            
            # Auteur
            author_span = soup.find('span', id=re.compile(r'author_'))
            author = author_span.get_text() if author_span else "Auteur inconnu"
            
            # Contenu
            post_div = soup.find('div', class_='post')
            content = post_div.get_text().strip() if post_div else ""
            
            # Extraction des liens
            links = self.extract_links(content)
            
            # Analyse technique
            technical_analysis = await self.analyze_technical_depth(content)
            
            # Calcul du score final
            has_whitepaper = len(links['whitepaper']) > 0
            has_github = len(links['github']) > 0
            final_score = self.calculate_final_score(technical_analysis, has_whitepaper, has_github)
            
            # Création de l'objet projet
            project = CryptoProject(
                topic_id=topic_id,
                title=title,
                author=author,
                content=content[:1000] + "..." if len(content) > 1000 else content,
                post_date=datetime.now().isoformat(),
                github_link=links['github'][0] if links['github'] else "",
                whitepaper_link=links['whitepaper'][0] if links['whitepaper'] else "",
                website_link=links['website'][0] if links['website'] else "",
                technical_score=technical_analysis.get('technical_score', 0),
                innovation_score=technical_analysis.get('innovation_score', 0),
                disruptiveness_score=technical_analysis.get('disruptiveness_score', 0),
                premine_percentage=float(technical_analysis.get('premine_analysis', '0%').replace('%', '')) if '%' in technical_analysis.get('premine_analysis', '0%') else 0.0,
                is_fork=technical_analysis.get('is_fork', False),
                fork_base=technical_analysis.get('fork_base', ''),
                mining_algorithm=technical_analysis.get('mining_algorithm', ''),
                consensus_mechanism=technical_analysis.get('consensus_mechanism', ''),
                unique_features=technical_analysis.get('unique_technical_features', []),
                red_flags=technical_analysis.get('technical_red_flags', []),
                strengths=technical_analysis.get('technical_strengths', []),
                final_score=final_score,
                analysis_date=datetime.now().isoformat()
            )
            
            # Sauvegarde en base
            self.save_project(project)
            self.analyzed_count += 1
            
            # Log des résultats prometteurs
            if final_score >= 75:
                logger.warning(f"🎯 PROJET PROMETTEUR: {title} (Score: {final_score}/100)")
                
        except Exception as e:
            logger.error(f"Erreur traitement annonce {topic_id}: {e}")

    def save_project(self, project: CryptoProject):
        """Sauvegarde un projet en base de données"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
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
                json.dumps(project.unique_features), json.dumps(project.red_flags),
                json.dumps(project.strengths), project.final_score, project.github_link,
                project.whitepaper_link, project.website_link, project.analysis_date,
                datetime.now().isoformat(), project.final_score >= 75
            ))
            
            conn.commit()
            logger.info(f"Projet {project.topic_id} sauvegardé (Score: {project.final_score})")
            
        except Exception as e:
            logger.error(f"Erreur sauvegarde projet: {e}")
        finally:
            conn.close()

    async def scan_bitcointalk_section(self, section_id: int = 159, pages: int = 2):
        """Scan une section de Bitcointalk"""
        await self.init_session()
        
        try:
            for page in range(pages):
                url = f"{self.base_url}/index.php?board={section_id}.{page * 40}"
                logger.info(f"Scan de la page {page + 1}/{pages}")
                
                html = await self.fetch_with_retry(url)
                if not html:
                    continue
                    
                soup = BeautifulSoup(html, 'html.parser')
                
                # Recherche des liens des annonces
                topic_links = []
                for link in soup.find_all('a', href=re.compile(r'topic=\d+\.msg\d+')):
                    href = link.get('href')
                    if href and 'new' in link.get('class', []):
                        topic_links.append(href)
                
                # Traitement des annonces
                for topic_link in topic_links:
                    topic_id = int(re.search(r'topic=(\d+)', topic_link).group(1))
                    
                    # Vérifier si déjà analysé
                    if not self.is_project_analyzed(topic_id):
                        full_url = f"{self.base_url}/{topic_link}" if topic_link.startswith('index.php') else topic_link
                        await self.process_announcement(topic_id, full_url)
                        await asyncio.sleep(1)  # Respect rate limiting
                
        finally:
            await self.close_session()

    def is_project_analyzed(self, topic_id: int) -> bool:
        """Vérifie si un projet a déjà été analysé"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM projects WHERE topic_id = ?", (topic_id,))
        result = cursor.fetchone() is not None
        conn.close()
        return result

    def generate_report(self):
        """Génère un rapport des analyses"""
        conn = sqlite3.connect(self.db_path)
        
        # Chargement des données
        df = pd.read_sql_query("""
            SELECT topic_id, title, author, technical_score, innovation_score, 
                   disruptiveness_score, final_score, premine_percentage, is_fork,
                   mining_algorithm, consensus_mechanism, github_link,
                   analysis_date, is_promising
            FROM projects 
            ORDER BY final_score DESC
        """, conn)
        
        conn.close()
        
        # Génération du rapport
        report = {
            'total_projects': len(df),
            'promising_projects': len(df[df['is_promising'] == 1]),
            'average_score': df['final_score'].mean(),
            'top_projects': df.head(10).to_dict('records'),
            'analysis_date': datetime.now().isoformat()
        }
        
        # Sauvegarde du rapport
        with open('crypto_analysis_report.json', 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            
        return report

async def main():
    """Fonction principale"""
    analyzer = UltimateBitcointalkAnalyzer()
    
    print("🚀 Ultimate Bitcointalk Analyzer")
    print("📊 Scan technique complet des nouvelles cryptomonnaies")
    print("=" * 60)
    
    try:
        # Scan de la section Announcements (ALT)
        print("🔍 Scan des annonces récentes...")
        await analyzer.scan_bitcointalk_section(section_id=159, pages=2)
        
        # Génération du rapport
        print("\n📈 Génération du rapport...")
        report = analyzer.generate_report()
        
        # Affichage des résultats
        print(f"\n✅ Analyse terminée!")
        print(f"📊 Projets analysés: {report['total_projects']}")
        print(f"🎯 Projets prometteurs: {report['promising_projects']}")
        print(f"⭐ Score moyen: {report['average_score']:.1f}/100")
        
        if report['promising_projects'] > 0:
            print(f"\n🏆 TOP PROJETS:")
            for i, project in enumerate(report['top_projects'][:5], 1):
                if project['is_promising']:
                    print(f"{i}. {project['title']}")
                    print(f"   👤 Auteur: {project['author']}")
                    print(f"   ⭐ Score: {project['final_score']}/100")
                    print(f"   ⚙️  Algo: {project['mining_algorithm']}")
                    print(f"   🔗 GitHub: {project['github_link'][:50]}..." if project['github_link'] else "   🔗 GitHub: Non fourni")
                    print()
        
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse: {e}")
    finally:
        print("📁 Rapport sauvegardé: crypto_analysis_report.json")
        print("📊 Base de données: crypto_analysis.db")

if __name__ == "__main__":
    asyncio.run(main())
