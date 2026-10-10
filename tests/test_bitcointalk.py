import asyncio
import sqlite3

import bitcointalk
from conftest import make_analyzer, insert_project, _run


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


class TestLinkVerification:
    """La vérification réseau ne doit dépendre que d'un faux serveur.

    Aucun test n'atteint bitcointalk.org : `verify_link` est intercepté et les
    variantes de `urlclean` sont testées séparément, sans socket.
    """

    def _stub(self, analyzer, reachable):
        """Fait répondre `reachable` à `verify_link`, et note les essais."""
        tried = []

        async def fake_verify(url):
            tried.append(url)
            return url in reachable

        analyzer.verify_link = fake_verify
        return tried

    def test_unambiguous_link_is_not_probed(self):
        analyzer = make_analyzer(":memory:")
        tried = self._stub(analyzer, set())
        result = asyncio.run(analyzer.resolve_links({"github": [], "whitepaper": [],
                                                     "website": ["https://x.io/downloads"],
                                                     "other": []}))
        assert result["website"] == ["https://x.io/downloads"]
        assert tried == []

    def test_longest_variant_wins_when_it_answers(self):
        analyzer = make_analyzer(":memory:")
        both = {"https://x.io/whitepaperWho", "https://x.io/whitepaper"}
        self._stub(analyzer, both)
        result = asyncio.run(analyzer.resolve_links({"github": [], "whitepaper": [
            "https://x.io/whitepaperWho"], "website": [], "other": []}))
        assert result["whitepaper"] == ["https://x.io/whitepaperWho"]

    def test_shorter_variant_is_kept_when_long_one_is_404(self):
        analyzer = make_analyzer(":memory:")
        self._stub(analyzer, {"https://x.io/whitepaper"})
        result = asyncio.run(analyzer.resolve_links({"github": [], "whitepaper": [
            "https://x.io/whitepaperWho"], "website": [], "other": []}))
        assert result["whitepaper"] == ["https://x.io/whitepaper"]

    def test_original_is_kept_when_nothing_answers(self):
        analyzer = make_analyzer(":memory:")
        self._stub(analyzer, set())
        result = asyncio.run(analyzer.resolve_links({"github": [], "whitepaper": [
            "https://x.io/whitepaperWho"], "website": [], "other": []}))
        assert result["whitepaper"] == ["https://x.io/whitepaperWho"]

    def test_all_buckets_are_verified(self):
        analyzer = make_analyzer(":memory:")
        self._stub(analyzer, {"https://x.io/whitepaper", "https://y.io/about"})
        result = asyncio.run(analyzer.resolve_links({
            "github": [], "whitepaper": ["https://x.io/whitepaperWho"],
            "website": ["https://y.io/aboutHere"], "other": [],
        }))
        assert result["whitepaper"] == ["https://x.io/whitepaper"]
        assert result["website"] == ["https://y.io/about"]

    def test_connection_error_is_not_a_link_found(self):
        analyzer = make_analyzer(":memory:")

        class DeadSession:
            closed = False

            def request(self, method, url, **kwargs):
                raise OSError("reseau coupe")

        analyzer.session = DeadSession()
        assert asyncio.run(analyzer.verify_link("https://x.io/a")) is False

    def test_status_404_is_not_reachable(self):
        analyzer = make_analyzer(":memory:")

        class NotFoundSession:
            closed = False

            class Response:
                status = 404

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *exc):
                    return False

            def request(self, method, url, **kwargs):
                return self.Response()

        analyzer.session = NotFoundSession()
        assert asyncio.run(analyzer.verify_link("https://x.io/a")) is False

    def test_head_refused_falls_back_to_get(self):
        analyzer = make_analyzer(":memory:")
        seen = []

        class HeadRefusingSession:
            closed = False

            def __init__(self):
                self.calls = 0

            def request(self, method, url, **kwargs):
                seen.append(method)
                self.calls += 1
                status = 405 if method == "HEAD" else 200
                session = self

                class Response:
                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *exc):
                        return False

                Response.status = status
                return Response()

        analyzer.session = HeadRefusingSession()
        assert asyncio.run(analyzer.verify_link("https://x.io/a")) is True
        assert seen == ["HEAD", "GET"]

    def test_verify_rejects_unusable_url(self):
        analyzer = make_analyzer(":memory:")

        def unreachable(*args, **kwargs):
            raise AssertionError("aucune requete ne doit partir")

        analyzer.session = type("S", (), {"closed": False, "request": unreachable})()
        assert asyncio.run(analyzer.verify_link("javascript:alert(1)")) is False

    def test_private_address_is_never_probed(self):
        analyzer = make_analyzer(":memory:")

        def unreachable(*args, **kwargs):
            raise AssertionError("aucune requete ne doit partir")

        analyzer.session = type("S", (), {"closed": False, "request": unreachable})()
        for url in ("http://127.0.0.1:8080/", "http://[::1]/",
                    "http://169.254.169.254/latest/meta-data/"):
            assert asyncio.run(analyzer.verify_link(url)) is False

    def test_name_pointing_to_a_private_address_is_refused(self):
        analyzer = make_analyzer(":memory:")

        class LoopbackNameSession:
            closed = False

            def request(self, method, url, **kwargs):
                raise AssertionError("aucune requete ne doit partir")

        analyzer.session = LoopbackNameSession()

        async def to_loopback(host):
            return [(2, 1, 6, "", ("127.0.0.1", 80))]

        analyzer._resolve = to_loopback
        assert asyncio.run(analyzer.verify_link("https://annonce.example/wp")) is False

    def test_404_stops_before_the_get_fallback(self):
        analyzer = make_analyzer(":memory:")
        seen = []

        class NotFoundSession:
            closed = False

            def request(self, method, url, **kwargs):
                seen.append(method)

                class Response:
                    status = 404

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *exc):
                        return False

                return Response()

        analyzer.session = NotFoundSession()
        assert asyncio.run(analyzer.verify_link("https://x.io/a")) is False
        assert seen == ["HEAD"]


class TestMiningGuideCrawl:
    """Le guide de minage vient de l'annonce, puis du site, jamais du réseau réel."""

    def _offline(self, analyzer, pages):
        calls = []

        async def fake_fetch(url, *args, **kwargs):
            calls.append(url)
            return pages.get(url, "")

        async def fake_resolve(host):
            return [(2, 1, 6, "", ("93.184.216.34", 443))]

        analyzer.fetch_with_retry = fake_fetch
        analyzer._resolve = fake_resolve
        return calls

    def test_announcement_is_enough_and_site_is_not_fetched(self):
        analyzer = make_analyzer(":memory:")
        calls = self._offline(analyzer, {})
        guide = asyncio.run(analyzer.fetch_mining_guide(
            "https://project.example", "Pool: p.example.org\nPort: 3333"))
        assert guide["pool"] == ["p.example.org"]
        assert calls == []

    def test_site_is_read_when_announcement_has_nothing(self):
        analyzer = make_analyzer(":memory:")
        page = (
            "<html><body><pre>Network   Mainnet\nPool   pool.example.org\n"
            "Port   4444\n</pre></body></html>"
        )
        calls = self._offline(analyzer, {"https://project.example": page})
        guide = asyncio.run(analyzer.fetch_mining_guide(
            "https://project.example", "Annonce sans rien sur le minage."))
        assert guide["pool"] == ["pool.example.org"]
        assert guide["port"] == "4444"
        assert calls == ["https://project.example"]

    def test_guide_pages_are_followed_when_home_page_is_empty(self):
        analyzer = make_analyzer(":memory:")
        home = '<html><a href="/mining">Mining</a></html>'
        guide_page = "<html><pre>Pool   pool.example.org\nPort   4444</pre></html>"
        calls = self._offline(analyzer, {
            "https://project.example": home,
            "https://project.example/mining": guide_page,
        })
        guide = asyncio.run(analyzer.fetch_mining_guide(
            "https://project.example", "Rien ici."))
        assert guide["pool"] == ["pool.example.org"]
        assert calls == ["https://project.example", "https://project.example/mining"]

    def test_unreachable_site_leaves_the_guide_empty(self):
        analyzer = make_analyzer(":memory:")
        self._offline(analyzer, {})
        guide = asyncio.run(analyzer.fetch_mining_guide(
            "https://project.example", "Rien ici."))
        assert guide["pool"] == []
        assert guide["port"] == ""

    def test_announcement_is_read_from_the_topic_when_the_stored_text_is_short(self):
        analyzer = make_analyzer(":memory:")
        page = "<html><div class='post'><p>Pool: p.example.org Port: 3333</p></div></html>"
        self._offline(analyzer, {
            "https://bitcointalk.org/index.php?topic=42.0": page,
        })
        assert asyncio.run(analyzer.fetch_announcement(42)).startswith("Pool:")

    def test_unreadable_topic_gives_no_text(self):
        analyzer = make_analyzer(":memory:")
        self._offline(analyzer, {})
        assert asyncio.run(analyzer.fetch_announcement(42)) == ""

    def test_private_site_is_never_fetched(self):
        analyzer = make_analyzer(":memory:")
        calls = self._offline(analyzer, {})

        async def to_loopback(host):
            return [(2, 1, 6, "", ("127.0.0.1", 8080))]

        analyzer._resolve = to_loopback
        guide = asyncio.run(analyzer.fetch_mining_guide(
            "http://127.0.0.1:8080/", "Rien ici."))
        assert guide["pool"] == []
        assert calls == []


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


class TestGuessCoinName:
    def test_strips_announcement_prefix(self):
        guess = bitcointalk.UltimateBitcointalkAnalyzer.guess_coin_name
        assert guess("hippoxOS - A Real LLM Native Operating System.") == "hippoxOS"

    def test_skips_generic_segment(self):
        guess = bitcointalk.UltimateBitcointalkAnalyzer.guess_coin_name
        assert guess("New Coin: PepeCoin (PEPE) - Fair Launch") == "PepeCoin"

    def test_drops_parenthetical_ticker(self):
        guess = bitcointalk.UltimateBitcointalkAnalyzer.guess_coin_name
        assert guess("[ANN] Satoshi Nakamoto Bitcoin (BTC)") == "Satoshi Nakamoto Bitcoin"

    def test_returns_empty_without_candidate(self):
        guess = bitcointalk.UltimateBitcointalkAnalyzer.guess_coin_name
        assert guess("[ANN] The New") == ""
        assert guess("") == ""


class TestMiningLinks:
    def test_deep_link_uses_slug(self, tmp_path):
        analyzer = make_analyzer(tmp_path / "m.db")
        assert analyzer.miningpoolstats_url("SHA-256") == (
            "https://miningpoolstats.stream/?a=sha-256"
        )

    def test_no_link_without_algorithm(self, tmp_path):
        analyzer = make_analyzer(tmp_path / "m2.db")
        assert analyzer.miningpoolstats_url("") == ""

    def test_mempool_reserved_for_bitcoin_algorithms(self, tmp_path, monkeypatch):
        analyzer = make_analyzer(tmp_path / "m3.db")
        seen = []

        async def fake_mempool():
            seen.append(True)
            return {"hashrate": "1 EH/s", "active_pools": 1}

        monkeypatch.setattr(analyzer, "_fetch_mempool_mining", fake_mempool)
        monkeypatch.setattr(analyzer, "fetch_with_retry", _null_fetch)

        for algorithm in ("Scrypt", "RandomX", "Ethash"):
            result = _run(analyzer.fetch_miningpool_stats(algorithm))
            assert result["hashrate"] == ""
            assert result["link"].startswith("https://miningpoolstats.stream/?a=")

        bitcoin = _run(analyzer.fetch_miningpool_stats("SHA-256"))
        assert bitcoin["hashrate"] == "1 EH/s"
        assert seen == [True]


async def _null_fetch(url, retries=3):
    return None


class TestProcessAnnouncementSession:
    def test_creates_session_on_demand(self, tmp_path, monkeypatch):
        analyzer = make_analyzer(tmp_path / "s.db")
        monkeypatch.setattr(analyzer, "fetch_with_retry", _null_fetch)
        assert analyzer.session is None
        _run(analyzer.process_announcement(777, "https://bitcointalk.org/index.php?topic=777"))
        assert analyzer.session is None or analyzer.session.closed

    def test_missing_page_does_not_raise(self, tmp_path, monkeypatch):
        analyzer = make_analyzer(tmp_path / "s2.db")
        monkeypatch.setattr(analyzer, "fetch_with_retry", _null_fetch)
        _run(analyzer.process_announcement(778, "https://bitcointalk.org/index.php?topic=778"))
        conn = sqlite3.connect(str(tmp_path / "s2.db"))
        count = conn.execute("SELECT COUNT(*) FROM projects WHERE topic_id=778").fetchone()[0]
        conn.close()
        assert count == 0


class TestScanProgress:
    """Le compteur de sujets ignorés compte les sauts de CE scan, pas la
    taille de la base (avant : skipped > found, ce qui était absurde)."""

    def test_skipped_count_is_per_scan_not_db_size(self, tmp_path, monkeypatch):
        db = tmp_path / "scan.db"
        analyzer = make_analyzer(db)
        # 999 existe en base mais n'apparait pas sur la page scannée.
        for topic_id in (999, 101, 102):
            insert_project(analyzer, topic_id, 70)

        page = (
            '<a href="index.php?topic=101.msg1">a</a>'
            '<a href="index.php?topic=102.msg2">b</a>'
            '<a href="index.php?topic=103.msg3">c</a>'
        )

        async def fake_init():
            return None

        async def fake_close():
            return None

        async def fake_fetch(url, retries=3):
            return page

        processed = []

        async def fake_process(topic_id, url):
            processed.append(topic_id)
            analyzer.analyzed_count += 1

        monkeypatch.setattr(analyzer, "init_session", fake_init)
        monkeypatch.setattr(analyzer, "close_session", fake_close)
        monkeypatch.setattr(analyzer, "fetch_with_retry", fake_fetch)
        monkeypatch.setattr(analyzer, "process_announcement", fake_process)

        events = []
        _run(analyzer.scan_bitcointalk_section(
            section_id=159, pages=1, progress_cb=events.append))

        state = {}
        for event in events:
            state.update(event)
        assert state["topics_found"] == 3
        assert state["skipped_count"] == 2, "101 et 102, pas 999 ni tout le reste"
        assert processed == [103]
        assert state["analyzed_count"] == 1
