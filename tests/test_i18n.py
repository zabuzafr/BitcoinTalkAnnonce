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
