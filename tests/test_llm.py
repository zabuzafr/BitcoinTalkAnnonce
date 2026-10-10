"""Fournisseurs LLM : configuration, validation, masquage, transport."""

import asyncio
import json
import sys

import pytest

import llm


@pytest.fixture()
def cfg_path(tmp_path):
    return tmp_path / "llm_config.json"


class TestConfig:
    def test_defaults_are_ollama_local(self, cfg_path):
        config = llm.load_config(cfg_path)
        assert config.provider == "ollama"
        assert config.provider == llm.PROVIDERS[0]
        assert config.chat_url.startswith("http")
        assert config.chat_url.endswith("/api/chat")

    def test_roundtrip_through_file(self, cfg_path):
        original = llm.LLMConfig(provider="openai", model="gpt-4o-mini",
                                 api_base="https://api.example.com/v1",
                                 api_key="sk-secret-value", temperature=0.7)
        llm.save_config(original, cfg_path)
        reloaded = llm.load_config(cfg_path)
        assert reloaded.provider == "openai"
        assert reloaded.model == "gpt-4o-mini"
        assert reloaded.api_key == "sk-secret-value"
        assert reloaded.temperature == 0.7

    def test_config_file_is_not_world_readable(self, cfg_path):
        llm.save_config(llm.LLMConfig(api_key="sk-secret"), cfg_path)
        assert cfg_path.stat().st_mode & 0o077 == 0, "le fichier doit être en 0600"

    def test_corrupt_file_falls_back_to_defaults(self, cfg_path):
        cfg_path.write_text("{ ceci n'est pas du json", encoding="utf-8")
        assert llm.load_config(cfg_path).provider == "ollama"

    def test_unknown_keys_are_ignored(self, cfg_path):
        cfg_path.write_text(json.dumps({"provider": "openai", "inconnu": 1}),
                            encoding="utf-8")
        assert llm.load_config(cfg_path).provider == "openai"

    def test_non_numeric_temperature_falls_back(self, cfg_path):
        cfg_path.write_text(json.dumps({"temperature": "chaud"}), encoding="utf-8")
        assert llm.load_config(cfg_path).temperature == 0.2

    def test_environment_wins_over_file(self, cfg_path, monkeypatch):
        cfg_path.write_text(json.dumps({"model": "depuis-fichier"}), encoding="utf-8")
        monkeypatch.setenv("BT_MODEL", "depuis-env")
        assert llm.load_config(cfg_path).model == "depuis-env"

    def test_env_key_is_read_from_environment(self, cfg_path, monkeypatch):
        monkeypatch.setenv("BT_LLM_API_KEY", "sk-via-env")
        assert llm.load_config(cfg_path).api_key == "sk-via-env"

    def test_config_path_follows_env_at_call_time(self, tmp_path, monkeypatch):
        """Le chemin doit etre resolu tardivement, sinon un test ou une CI ne
        peut pas rediriger la configuration sans recharger le module."""
        first, second = tmp_path / "a.json", tmp_path / "b.json"
        llm.save_config(llm.LLMConfig(model="premier"), first)

        monkeypatch.setenv("BT_LLM_CONFIG", str(first))
        assert llm.config_path() == first
        assert llm.load_config().model == "premier"

        monkeypatch.setenv("BT_LLM_CONFIG", str(second))
        assert llm.config_path() == second
        assert llm.load_config().model != "premier"

        monkeypatch.delenv("BT_LLM_CONFIG")
        assert llm.config_path() == llm.BASE_DIR / "llm_config.json"


class TestSecretHygiene:
    def test_config_file_is_git_ignored(self):
        """Le fichier peut contenir une cle API : il ne doit pas etre versionne."""
        ignore = (llm.BASE_DIR / ".gitignore").read_text(encoding="utf-8")
        assert "llm_config.json" in ignore


class TestMasking:
    def test_api_key_is_never_returned(self):
        masked = llm.LLMConfig(provider="openai", api_key="sk-super-secret").masked()
        assert masked["api_key"] == ""
        assert "sk-super-secret" not in json.dumps(masked)

    def test_hint_reveals_at_most_last_four(self):
        masked = llm.LLMConfig(api_key="sk-abcdefgh").masked()
        assert masked["has_api_key"] is True
        assert masked["api_key_hint"] == "...efgh"
        assert "abcdef" not in masked["api_key_hint"]

    def test_absent_key_reports_false(self):
        masked = llm.LLMConfig(api_key="").masked()
        assert masked["has_api_key"] is False
        assert masked["api_key_hint"] == ""


class TestValidation:
    def test_unknown_provider_rejected(self):
        with pytest.raises(llm.LLMError):
            llm.LLMConfig(provider="gemini", model="m").validate()

    def test_empty_model_rejected(self):
        with pytest.raises(llm.LLMError):
            llm.LLMConfig(provider="ollama", model="  ").validate()

    def test_openai_requires_base_and_key(self):
        with pytest.raises(llm.LLMError):
            llm.LLMConfig(provider="openai", model="gpt-4o").validate()
        with pytest.raises(llm.LLMError):
            llm.LLMConfig(provider="openai", model="gpt-4o",
                          api_base="https://x.example/v1").validate()

    def test_openai_requires_http_scheme(self):
        with pytest.raises(llm.LLMError):
            llm.LLMConfig(provider="openai", model="m",
                          api_base="ftp://x.example", api_key="k").validate()

    def test_temperature_out_of_range_rejected(self):
        for value in (-0.1, 2.5):
            with pytest.raises(llm.LLMError):
                llm.LLMConfig(provider="ollama", model="m", temperature=value).validate()

    def test_ollama_needs_no_key(self):
        llm.LLMConfig(provider="ollama", model="llama3.1").validate()

    def test_cloud_metadata_ip_rejected(self):
        """Les metadonnees cloud (IMDS) ne doivent jamais etre sondees."""
        with pytest.raises(llm.LLMError):
            llm.check_target("http://169.254.169.254/latest/meta-data/",
                             field="api_base")

    def test_link_local_ipv6_rejected(self):
        with pytest.raises(llm.LLMError):
            llm.check_target("http://[fe80::1]/", field="ollama_host")

    def test_cloud_metadata_hostname_rejected(self):
        with pytest.raises(llm.LLMError):
            llm.check_target(
                "http://metadata.google.internal/computeMetadata/v1/",
                field="api_base")

    def test_non_http_scheme_rejected(self):
        for url in ("file:///etc/passwd", "ftp://x.example/f", "gopher://x"):
            with pytest.raises(llm.LLMError):
                llm.check_target(url, field="api_base")

    def test_loopback_and_private_allowed(self):
        """Ollama sur localhost et proxys sur le reseau local restent valides."""
        llm.check_target("http://127.0.0.1:11434", field="ollama_host")
        llm.check_target("http://10.0.0.5:8080/v1", field="api_base")
        llm.check_target("https://api.example.com/v1", field="api_base")

    def test_validate_rejects_metadata_api_base(self):
        with pytest.raises(llm.LLMError):
            llm.LLMConfig(provider="openai", model="m", api_key="k",
                          api_base="http://169.254.169.254/v1").validate()

    def test_non_numeric_temperature_is_an_llm_error(self):
        for value in ("abc", [1], None):
            with pytest.raises(llm.LLMError):
                llm.LLMConfig(provider="ollama", model="m",
                              temperature=value).validate()


class TestChatUrl:
    def test_openai_appends_chat_completions(self):
        config = llm.LLMConfig(provider="openai", api_base="https://x.example/v1",
                               api_key="k", model="m")
        assert config.chat_url == "https://x.example/v1/chat/completions"

    def test_trailing_slash_tolerated(self):
        config = llm.LLMConfig(provider="openai", api_base="https://x.example/v1/",
                               api_key="k", model="m")
        assert config.chat_url == "https://x.example/v1/chat/completions"

    def test_full_endpoint_not_duplicated(self):
        config = llm.LLMConfig(provider="openai",
                               api_base="https://x.example/v1/chat/completions",
                               api_key="k", model="m")
        assert config.chat_url.endswith("/v1/chat/completions")
        assert config.chat_url.count("chat/completions") == 1

    def test_ollama_trailing_slash_trimmed(self):
        config = llm.LLMConfig(ollama_host="http://localhost:11434/")
        assert config.chat_url == "http://localhost:11434/api/chat"

    def test_openai_base_without_v1_gains_v1(self):
        """Une base sans /v1 reussit la sonde mais echoue en chat sans egalement."""
        config = llm.LLMConfig(provider="openai", api_base="https://api.openai.com",
                               api_key="k", model="m")
        assert config.chat_url == "https://api.openai.com/v1/chat/completions"


class TestPayloadAndParsing:
    def test_ollama_payload_shape(self):
        client = llm.ChatClient(llm.LLMConfig(provider="ollama", model="llama3.1"))
        payload = client._payload("hi", None, False)
        assert payload["model"] == "llama3.1"
        assert payload["stream"] is False
        assert "options" in payload and "temperature" in payload["options"]

    def test_openai_payload_shape(self):
        client = llm.ChatClient(llm.LLMConfig(provider="openai", model="gpt-4o"))
        payload = client._payload("hi", "sois bref", False)
        assert payload["messages"][0] == {"role": "system", "content": "sois bref"}
        assert "temperature" in payload and "options" not in payload

    def test_bearer_header_only_for_openai(self):
        openai = llm.ChatClient(llm.LLMConfig(provider="openai", api_key="k"))
        assert openai._headers()["Authorization"] == "Bearer k"
        ollama = llm.ChatClient(llm.LLMConfig(provider="ollama"))
        assert "Authorization" not in ollama._headers()

    def test_ollama_response_parsed(self):
        client = llm.ChatClient(llm.LLMConfig(provider="ollama"))
        assert client._parse({"message": {"content": "bonjour"}}) == "bonjour"

    def test_openai_response_parsed(self):
        client = llm.ChatClient(llm.LLMConfig(provider="openai"))
        data = {"choices": [{"message": {"content": "salut"}}]}
        assert client._parse(data) == "salut"

    def test_empty_content_raises(self):
        client = llm.ChatClient(llm.LLMConfig(provider="openai"))
        with pytest.raises(llm.LLMError):
            client._parse({"choices": [{"message": {"content": "  "}}]})


class TestExtractJsonObject:
    def test_plain_object(self):
        assert llm.extract_json_object('{"a": 1}') == {"a": 1}

    def test_markdown_fence_removed(self):
        text = 'Voici:\n```json\n{"a": 1}\n```\nMerci.'
        assert llm.extract_json_object(text) == {"a": 1}

    def test_fence_without_language(self):
        assert llm.extract_json_object('```\n{"a": 2}\n```') == {"a": 2}

    def test_prose_around_object(self):
        assert llm.extract_json_object('Resultat : {"b": 3} fin.') == {"b": 3}

    def test_nested_object_preserved(self):
        assert llm.extract_json_object('{"a": {"b": [1, 2]}}') == {"a": {"b": [1, 2]}}

    def test_no_json_raises(self):
        with pytest.raises(llm.LLMError):
            llm.extract_json_object("pas de json ici")

    def test_invalid_json_raises(self):
        with pytest.raises(llm.LLMError):
            llm.extract_json_object("{pas: json}")

    def test_non_object_json_rejected(self):
        with pytest.raises(llm.LLMError):
            llm.extract_json_object("[1, 2, 3]")


class TestAnalyzerIntegration:
    def test_analyzer_accepts_injected_config(self, tmp_path):
        import bitcointalk

        config = llm.LLMConfig(provider="openai", model="gpt-4o-mini",
                               api_base="https://x.example/v1", api_key="k")
        analyzer = bitcointalk.UltimateBitcointalkAnalyzer(
            db_path=str(tmp_path / "a.db"), llm_config=config)
        assert analyzer.llm_config.model == "gpt-4o-mini"

    def test_analyzer_defaults_to_shared_config(self, tmp_path, monkeypatch):
        import bitcointalk

        monkeypatch.setenv("BT_MODEL", "model-depuis-env")
        analyzer = bitcointalk.UltimateBitcointalkAnalyzer(db_path=str(tmp_path / "b.db"))
        assert analyzer.llm_config.model == "model-depuis-env"

    def test_cli_model_overrides_shared_config_without_writing_it(self, tmp_path,
                                                                  monkeypatch):
        """`--model` est une surcharge ponctuelle : elle gagne au runtime mais le
        fichier de configuration doit rester intact."""
        import bitcointalk

        path = tmp_path / "c.json"
        llm.save_config(llm.LLMConfig(model="depuis-fichier"), path)
        monkeypatch.setenv("BT_LLM_CONFIG", str(path))
        monkeypatch.setattr(sys, "argv", ["bt", "--model", "depuis-cli"])

        config = llm.load_config()
        config.model = bitcointalk.parse_args().model

        assert config.model == "depuis-cli"
        assert llm.load_config().model == "depuis-fichier"

    def test_chat_json_uses_the_configured_client(self, tmp_path, monkeypatch):
        """Le scan doit interroger le fournisseur configure, jamais un global."""
        import bitcointalk

        seen = {}

        def fake_complete_json(prompt, system=None, timeout=None):
            seen["config"] = current.config
            return {"score": 7}

        config = llm.LLMConfig(provider="openai", model="gpt-4o",
                               api_base="https://x.example/v1", api_key="k")
        current = llm.ChatClient(config)
        monkeypatch.setattr(llm.ChatClient, "complete_json", fake_complete_json)

        analyzer = bitcointalk.UltimateBitcointalkAnalyzer(
            db_path=str(tmp_path / "d.db"), llm_config=config)
        assert asyncio.run(analyzer.chat_json("question")) == {"score": 7}
        assert seen["config"].provider == "openai"
        assert seen["config"].model == "gpt-4o"

    def test_chat_json_degrades_to_empty_dict(self, tmp_path, monkeypatch):
        """Un fournisseur injoignable ne doit pas interrompre un scan."""
        import bitcointalk

        def boom(prompt, system=None, timeout=None):
            raise llm.LLMError("injoignable")

        monkeypatch.setattr(llm.ChatClient, "complete_json", boom)
        analyzer = bitcointalk.UltimateBitcointalkAnalyzer(
            db_path=str(tmp_path / "e.db"),
            llm_config=llm.LLMConfig(model="m"))
        assert asyncio.run(analyzer.chat_json("question")) == {}
