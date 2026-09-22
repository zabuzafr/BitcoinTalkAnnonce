import sqlite3

import bitcointalk
from conftest import make_analyzer, insert_project


class TestParsePremine:
    def test_integer_percentage(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine("10%") == 10.0

    def test_decimal_dot(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine("2.5%") == 2.5

    def test_decimal_comma(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine("2,5%") == 2.5

    def test_with_context(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine("environ 15% des token") == 15.0

    def test_no_match(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine("aucune donnée") == 0.0

    def test_none_input(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine(None) == 0.0

    def test_numeric_input(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine(7) == 0.0
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine("7") == 0.0
        assert bitcointalk.UltimateBitcointalkAnalyzer._parse_premine("7%") == 7.0


class TestClamp:
    def test_below_zero(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._clamp(-10) == 0

    def test_above_hundred(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._clamp(150) == 100

    def test_in_range(self):
        assert bitcointalk.UltimateBitcointalkAnalyzer._clamp(42) == 42


class TestCredibilityScore:
    def test_empty_analysis(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_credibility_score({}) == 0
        assert analyzer.calculate_credibility_score(None) == 0

    def test_full_positive_signals(self):
        analyzer = make_analyzer(":memory:")
        analysis = {
            "realism_assessment": "assez réaliste",
            "mining_algorithm": "SHA-256",
            "consensus_mechanism": "PoW",
            "unique_technical_features": ["zmq"],
            "technical_strengths": ["audit"],
        }
        assert analyzer.calculate_credibility_score(analysis) == 60

    def test_realistic_without_ir_prefix(self):
        analyzer = make_analyzer(":memory:")
        analysis = {"realism_assessment": "réaliste"}
        assert analyzer.calculate_credibility_score(analysis) == 20

    def test_irréaliste_not_counted(self):
        analyzer = make_analyzer(":memory:")
        analysis = {"realism_assessment": "irréaliste"}
        assert analyzer.calculate_credibility_score(analysis) == 0

    def test_partial_signals(self):
        analyzer = make_analyzer(":memory:")
        analysis = {"mining_algorithm": "SHA-256", "consensus_mechanism": "PoS"}
        assert analyzer.calculate_credibility_score(analysis) == 20


class TestRiskScore:
    def test_empty_analysis(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_risk_score({}) == 0
        assert analyzer.calculate_risk_score(None) == 0

    def test_high_premine(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_risk_score({"premine_analysis": "25%"}) == 30

    def test_mid_premine(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_risk_score({"premine_analysis": "12%"}) == 20

    def test_low_premine(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_risk_score({"premine_analysis": "6%"}) == 10

    def test_zero_premine(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_risk_score({"premine_analysis": "5%"}) == 0

    def test_red_flags_capped_at_30(self):
        analyzer = make_analyzer(":memory:")
        flags = ["a", "b", "c", "d", "e", "f"]
        assert analyzer.calculate_risk_score({"technical_red_flags": flags}) == 30

    def test_fork_bonus(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_risk_score({"is_fork": True}) == 10

    def test_irréalisme_bonus(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_risk_score({"realism_assessment": "irréaliste"}) == 20

    def test_combined_capped(self):
        analyzer = make_analyzer(":memory:")
        analysis = {
            "premine_analysis": "50%",
            "technical_red_flags": ["a", "b", "c"],
            "is_fork": True,
            "realism_assessment": "irréaliste",
        }
        assert analyzer.calculate_risk_score(analysis) == 90


class TestFinalScore:
    def test_empty_analysis(self):
        analyzer = make_analyzer(":memory:")
        assert analyzer.calculate_final_score(None, False, False) == 0
        assert analyzer.calculate_final_score({}, False, False) == 0

    def test_weighted_scores(self):
        analyzer = make_analyzer(":memory:")
        analysis = {
            "innovation_score": 100,
            "technical_score": 100,
            "disruptiveness_score": 100,
            "is_fork": False,
        }
        expected = 100 + 10 + 5 + 5
        assert analyzer.calculate_final_score(analysis, True, True) == min(100, expected)

    def test_no_links_no_fork_bonus(self):
        analyzer = make_analyzer(":memory:")
        analysis = {"innovation_score": 100, "is_fork": False}
        assert analyzer.calculate_final_score(analysis, False, False) == 35 + 10

    def test_fork_removes_bonus(self):
        analyzer = make_analyzer(":memory:")
        analysis = {"innovation_score": 100, "is_fork": True}
        assert analyzer.calculate_final_score(analysis, False, False) == 35

    def test_high_premine_penalty(self):
        analyzer = make_analyzer(":memory:")
        analysis = {"innovation_score": 100, "is_fork": False, "premine_analysis": "30%"}
        assert analyzer.calculate_final_score(analysis, False, False) == 35 + 10 - 25

    def test_clamped_to_100(self):
        analyzer = make_analyzer(":memory:")
        analysis = {
            "innovation_score": 100,
            "technical_score": 100,
            "disruptiveness_score": 100,
            "is_fork": False,
        }
        assert analyzer.calculate_final_score(analysis, True, True) == 100


class TestExtractLinks:
    def test_github_link(self):
        analyzer = make_analyzer(":memory:")
        text = "Code déposé sur https://github.com/org/repo et www.example.com"
        links = analyzer.extract_links(text)
        assert any("github.com/org/repo" in u for u in links["github"])

    def test_whitepaper_link(self):
        analyzer = make_analyzer(":memory:")
        text = "Voir https://example.com/whitepaper.pdf"
        links = analyzer.extract_links(text)
        assert any("whitepaper" in u for u in links["whitepaper"])

    def test_generic_website(self):
        analyzer = make_analyzer(":memory:")
        text = "Site officiel: https://mon-projet.io"
        links = analyzer.extract_links(text)
        assert any("mon-projet.io" in u for u in links["website"])

    def test_no_links(self):
        analyzer = make_analyzer(":memory:")
        links = analyzer.extract_links("Aucun lien ici")
        assert links == {"github": [], "whitepaper": [], "website": [], "other": []}

    def test_all_buckets_present(self):
        analyzer = make_analyzer(":memory:")
        links = analyzer.extract_links("github https://github.com/a/b white https://x.io/wp site https://y.fr")
        assert set(links.keys()) == {"github", "whitepaper", "website", "other"}


class TestSaveProject:
    def test_round_trip(self, tmp_path):
        db = tmp_path / "rt.db"
        analyzer = make_analyzer(db)
        insert_project(analyzer, topic_id=42, score=80, github_link="https://github.com/a/b")
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT topic_id, title, author, final_score, is_promising FROM projects WHERE topic_id=42"
        ).fetchone()
        conn.close()
        assert row is not None
        topic_id, title, author, final_score, is_promising = row
        assert topic_id == 42
        assert title == "Test project 42"
        assert author == "tester"
        assert final_score == 80
        assert bool(is_promising) is True

    def test_not_promising_below_75(self, tmp_path):
        db = tmp_path / "np.db"
        analyzer = make_analyzer(db)
        insert_project(analyzer, topic_id=43, score=74)
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT is_promising FROM projects WHERE topic_id=43").fetchone()
        conn.close()
        assert row is not None and bool(row[0]) is False

    def test_analysis_history_written(self, tmp_path):
        db = tmp_path / "hist.db"
        analyzer = make_analyzer(db)
        insert_project(analyzer, topic_id=44, score=50)
        conn = sqlite3.connect(str(db))
        count = conn.execute("SELECT COUNT(*) FROM analysis_history WHERE topic_id=44").fetchone()[0]
        conn.close()
        assert count == 1

    def test_upsert_replaces(self, tmp_path):
        db = tmp_path / "up.db"
        analyzer = make_analyzer(db)
        insert_project(analyzer, topic_id=45, score=50)
        insert_project(analyzer, topic_id=45, score=99, author="second")
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT final_score, author FROM projects WHERE topic_id=45").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == 99
        assert rows[0][1] == "second"
