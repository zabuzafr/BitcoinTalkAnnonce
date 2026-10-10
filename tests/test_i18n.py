from pathlib import Path

import pytest

from i18n import T, Translations, _norm, deep_merge


class TestNorm:
    def test_none(self):
        assert _norm(None) is None

    def test_empty(self):
        assert _norm("") is None

    def test_strips_and_lowercases(self):
        assert _norm("  FR ") == "fr"

    def test_invalid_code(self):
        assert _norm("french") is None

    def test_two_letters_passthrough(self):
        assert _norm("zz") == "zz"

    def test_non_string(self):
        assert _norm(123) is None


class TestDeepMerge:
    def test_mutates_base_and_returns_it(self):
        base = {"a": 1}
        out = deep_merge(base, {"b": 2})
        assert out is base
        assert base == {"a": 1, "b": 2}

    def test_nested_dict_merged(self):
        base = {"k": {"x": 1}}
        deep_merge(base, {"k": {"y": 2}})
        assert base == {"k": {"x": 1, "y": 2}}

    def test_scalar_overwritten(self):
        base = {"a": 1}
        deep_merge(base, {"a": 9})
        assert base == {"a": 9}


class TestTranslations:
    def test_available_languages_contains_fr_and_en(self):
        langs = {item["code"] for item in T.available_languages()}
        assert "fr" in langs
        assert "en" in langs

    def test_available_languages_structure(self):
        for item in T.available_languages():
            assert set(item.keys()) == {"code", "name"}
            assert item["code"] == item["code"].lower()

    def test_default_language_first(self):
        assert T.available_languages()[0]["code"] == "fr"

    def test_resolved_chain_unknown_lang(self):
        chain = T.resolved_chain("zz")
        assert chain[0] == "fr"
        assert chain[-1] == "zz"
        assert "en" in chain

    def test_resolved_chain_known_lang(self):
        assert T.resolved_chain("en") == ["fr", "en"]

    def test_t_known_key_fr(self):
        assert T.t("api.lang_not_found", lang="fr") == "Langue non disponible"

    def test_t_known_key_en(self):
        assert T.t("api.lang_not_found", lang="en") == "Language not available"

    def test_t_falls_back_for_missing_lang(self):
        value = T.t("api.lang_not_found", lang="zz")
        assert value == "Langue non disponible"

    def test_t_default_for_missing_key(self):
        assert T.t("api.does_not_exist", lang="fr", default="FB") == "FB"

    def test_t_key_returned_when_no_default(self):
        assert T.t("api.does_not_exist", lang="fr") == "api.does_not_exist"

    def test_t_format_kwargs(self):
        value = T.t("_meta.name", lang="fr")
        assert value == "Français"

    def test_resolve_none_defaults(self):
        assert T.resolve(None) == "fr"

    def test_resolve_valid(self):
        assert T.resolve("en") == "en"

    def test_resolve_invalid_falls_back(self):
        assert T.resolve("FRENCH") == "fr"

    def test_resolve_accepts_region_variant(self):
        assert T.resolve("en-US") in ("en", "fr")

    def test_custom_dir(self, tmp_path):
        tr = Translations(translations_dir=tmp_path)
        assert tr.resolve("zz") == "fr"


class TestV2Parity:
    def _catalog(self, lang):
        return T._catalogs[lang]["dashboard"]

    def test_dashboard_key_sets_equal(self):
        assert set(self._catalog("en")) == set(self._catalog("fr"))

    def test_key_count(self):
        assert len(self._catalog("en")) == len(self._catalog("fr"))

    def test_every_section_present_both_locales(self):
        for section in ("nav", "scan", "ts", "algo", "projects", "enrich", "tok", "src", "mine"):
            assert section in T._catalogs["en"], section
            assert set(T._catalogs["en"][section]) == set(T._catalogs["fr"][section]), section

    def test_v2_keys_present_both_locales(self):
        expected = {
            "dashboard": [
                "stat_total", "stat_promising", "stat_avg", "stat_forks", "stat_max",
                "algo_unknown", "result_count", "list_empty", "no_results",
            ],
            "ts": ["title", "week", "month", "year"],
            "algo": ["title", "empty"],
            "scan": ["section", "pages_label", "start", "idle", "running", "done", "failed"],
            "nav": [
                "overview", "projects", "enrichment", "tokenomics", "mining",
                "group_analysis", "group_tools", "tablist",
            ],
            "enrich": ["matrix", "col_project", "kpi_github", "kpi_whitepaper"],
            "tok": ["title", "empty"],
            "src": ["title", "empty"],
            "mine": ["title", "kpi_projects", "kpi_hashrate"],
        }
        for section, keys in expected.items():
            for key in keys:
                assert key in T._catalogs["en"][section], f"{section}.{key}"
                assert key in T._catalogs["fr"][section], f"{section}.{key}"

    def test_no_em_dash_in_any_value(self):
        def walk(catalog, path=""):
            for key, value in catalog.items():
                if isinstance(value, dict):
                    walk(value, f"{path}{key}.")
                else:
                    assert "\u2014" not in value and "\u2013" not in value, f"{path}{key}"

        for lang in ("en", "fr"):
            walk(T._catalogs[lang])

    def test_static_assets_only_reference_existing_keys(self):
        import re
        root = Path(__file__).resolve().parent.parent
        js = (root / "static" / "app.js").read_text(encoding="utf-8")
        html = (root / "static" / "index.html").read_text(encoding="utf-8")

        # Le lookahead exclut les clés construites par concaténation
        # (ex. t("dashboard.act_" + action + "d")).
        referenced = {(s, k) for s, k in re.findall(
            r't(?:fmt)?\("([a-z_]+)\.([a-z_0-9]+)"(?!\s*\+)', js)}
        referenced |= {(s, k) for s, k in re.findall(
            r'data-i18n(?:-ph|-aria)?="([a-z_]+)\.([a-z_0-9]+)"', html)}
        # Seules enable/disable sont construits par concaténation
        # dans t("dashboard.act_" + action + "d"). reanalyze est un littéral.
        dynamic = {("dashboard", "act_enabled"), ("dashboard", "act_disabled")}
        referenced |= dynamic
        # Référencés via des tables (TAB_KEYS, SOURCES) plutôt que des littéraux
        referenced |= {("nav", k) for k in
                       ("note_overview", "note_projects", "note_enrichment",
                        "note_tokenomics", "note_mining")}
        referenced |= {("enrich", k) for k in
                       ("kpi_github", "kpi_whitepaper", "kpi_coingecko",
                        "kpi_social", "kpi_mining")}

        for section, key in sorted(referenced):
            assert section in T._catalogs["fr"], section
            assert key in T._catalogs["fr"][section], f"{section}.{key}"
            assert key in T._catalogs["en"][section], f"{section}.{key}"

    def test_no_orphan_dashboard_keys(self):
        import re
        root = Path(__file__).resolve().parent.parent
        js = (root / "static" / "app.js").read_text(encoding="utf-8")
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r't(?:fmt)?\("dashboard\.([a-z_0-9]+)"', js))
        used |= set(re.findall(r'data-i18n(?:-ph|-aria)?="dashboard\.([a-z_0-9]+)"', html))
        used |= {"act_enable", "act_disable", "act_enabled", "act_disabled",
                 "act_queued", "act_reanalyze"}
        orphans = sorted(set(self._catalog("en")) - used)
        assert orphans == [], f"cles dashboard orphelines : {orphans}"

    def test_python_t_literals_exist(self):
        """T.t() sur une cle absente renvoie la cle elle-meme : la ligne de log
        perd ses arguments et l'utilisateur voit `errors.xxx` brut dans un
        toast. Un seul test couvre tous les modules Python du projet."""
        import re
        root = Path(__file__).resolve().parent.parent
        used = set()
        for path in sorted(root.glob("*.py")):
            used |= set(re.findall(r'T\.t\(\s*"([a-z0-9_.]+)"',
                                   path.read_text(encoding="utf-8")))

        def flatten(catalog, prefix=""):
            keys = set()
            for key, value in catalog.items():
                if isinstance(value, dict):
                    keys |= flatten(value, prefix + key + ".")
                else:
                    keys.add(prefix + key)
            return keys

        assert used, "aucun appel T.t() literal trouve : regex cassee ?"
        for lang in ("en", "fr"):
            keys = flatten(T._catalogs[lang])
            missing = sorted(used - keys)
            assert missing == [], f"cles T.t() absentes ({lang}) : {missing}"

    def test_values_nonempty(self):
        for lang in ("en", "fr"):
            def walk(catalog):
                for key, value in catalog.items():
                    if isinstance(value, dict):
                        walk(value)
                    else:
                        assert isinstance(value, str)
                        assert value.strip(), key
            walk(T._catalogs[lang])
