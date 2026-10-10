"""Endpoints REST de configuration LLM.

Invariant central : la cle API ne franchit jamais la frontiere HTTP.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dashboard
import llm


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "llm_config.json"
    monkeypatch.setenv("BT_LLM_CONFIG", str(path))
    for name in llm.ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    return TestClient(dashboard.app), path


def _tmp_config(monkeypatch):
    import tempfile

    path = Path(tempfile.mkdtemp()) / "llm_config.json"
    monkeypatch.setenv("BT_LLM_CONFIG", str(path))
    for name in llm.ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    return path


class TestReadConfig:
    def test_returns_current_settings(self, client):
        api, _ = client
        body = api.get("/api/llm/config").json()
        assert body["provider"] == "ollama"
        assert body["model"]
        assert body["providers"] == ["ollama", "openai"]

    def test_never_exposes_api_key(self, client, tmp_path):
        api, path = client
        llm.save_config(llm.LLMConfig(provider="openai", model="gpt-4o",
                                       api_base="https://x.example/v1",
                                       api_key="sk-do-not-leak"), path)
        raw = api.get("/api/llm/config").text
        assert "sk-do-not-leak" not in raw
        body = api.get("/api/llm/config").json()
        assert body["api_key"] == ""
        assert body["has_api_key"] is True

    def test_reports_env_overrides(self, client, monkeypatch):
        api, _ = client
        monkeypatch.setenv("BT_MODEL", "depuis-env")
        assert "BT_MODEL" in api.get("/api/llm/config").json()["env_overrides"]


class TestWriteConfig:
    def test_saves_ollama_settings(self, client):
        api, path = client
        r = api.post("/api/llm/config", json={
            "provider": "ollama", "model": "llama3.2",
            "ollama_host": "http://127.0.0.1:11434"})
        assert r.status_code == 200
        assert llm.load_config(path).model == "llama3.2"

    def test_saves_openai_settings(self, client):
        api, path = client
        r = api.post("/api/llm/config", json={
            "provider": "openai", "model": "gpt-4o-mini",
            "api_base": "https://api.example.com/v1", "api_key": "sk-abc"})
        assert r.status_code == 200
        saved = llm.load_config(path)
        assert saved.provider == "openai"
        assert saved.api_key == "sk-abc"

    def test_response_omits_the_key(self, client):
        api, _ = client
        r = api.post("/api/llm/config", json={
            "provider": "openai", "model": "m",
            "api_base": "https://x.example/v1", "api_key": "sk-secret-value"})
        assert "sk-secret-value" not in r.text

    def test_empty_key_keeps_the_stored_one(self, client):
        api, path = client
        api.post("/api/llm/config", json={
            "provider": "openai", "model": "m",
            "api_base": "https://x.example/v1", "api_key": "sk-original"})
        api.post("/api/llm/config", json={
            "provider": "openai", "model": "m2", "api_key": ""})
        assert llm.load_config(path).api_key == "sk-original"

    def test_keep_sentinel_preserves_key(self, client):
        api, path = client
        api.post("/api/llm/config", json={
            "provider": "openai", "model": "m",
            "api_base": "https://x.example/v1", "api_key": "sk-keepme"})
        api.post("/api/llm/config", json={
            "provider": "openai", "model": "m3", "api_key": "__KEEP__"})
        assert llm.load_config(path).api_key == "sk-keepme"

    def test_invalid_provider_rejected(self, client):
        api, path = client
        r = api.post("/api/llm/config", json={"provider": "gemini", "model": "m"})
        assert r.status_code == 400
        assert not path.exists()

    def test_openai_without_key_rejected(self, client):
        api, _ = client
        r = api.post("/api/llm/config", json={
            "provider": "openai", "model": "m", "api_base": "https://x.example/v1"})
        assert r.status_code == 400

    def test_environment_locks_persistence(self, client, monkeypatch):
        api, path = client
        monkeypatch.setenv("BT_LLM_API_KEY", "sk-bloque")
        r = api.post("/api/llm/config", json={
            "provider": "openai", "model": "m",
            "api_base": "https://x.example/v1", "api_key": "sk-autre"})
        assert r.status_code == 409
        assert not path.exists()


class TestProbeEndpoint:
    def test_invalid_body_rejected_before_any_call(self, client):
        api, _ = client
        r = api.post("/api/llm/test", json={"provider": "openai", "model": "m"})
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_unreachable_host_reports_failure_without_500(self, client):
        api, _ = client
        r = api.post("/api/llm/test", json={
            "provider": "ollama", "model": "m",
            "ollama_host": "http://127.0.0.1:1"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error"]

    def test_probe_never_persists(self, client):
        api, path = client
        api.post("/api/llm/test", json={
            "provider": "openai", "model": "gpt-4o",
            "api_base": "https://127.0.0.1:1/v1", "api_key": "sk-jamais-sauve"})
        assert not path.exists()


class TestProviderAvailability:
    """Le pre-flight de ré-analyse doit sonder le fournisseur configuré, pas
    Ollama en dur : sinon une API compatible ChatGPT est rejetée en 503."""

    def test_probes_the_configured_provider(self, monkeypatch):
        llm.save_config(llm.LLMConfig(provider="openai", model="gpt-4o",
                                       api_base="https://x.example/v1",
                                       api_key="k"), _tmp_config(monkeypatch))
        probed = {}

        def fake_probe(self):
            probed["url"] = self.config.chat_url
            probed["provider"] = self.config.provider
            return {"ok": True, "models": [], "latency_ms": 1}

        monkeypatch.setattr(llm.ChatClient, "probe_sync", fake_probe)
        ok, detail = dashboard._llm_available()
        assert ok is True
        assert detail is None
        assert probed["provider"] == "openai"
        assert probed["url"] == "https://x.example/v1/chat/completions"

    def test_reports_failure_without_leaking_the_key(self, monkeypatch):
        llm.save_config(llm.LLMConfig(provider="openai", model="gpt-4o",
                                       api_base="https://x.example/v1",
                                       api_key="sk-ne-pas-fuiter"),
                        _tmp_config(monkeypatch))

        def fake_probe(self):
            raise llm.LLMError("HTTP 401 : Unauthorized")

        monkeypatch.setattr(llm.ChatClient, "probe_sync", fake_probe)
        ok, detail = dashboard._llm_available()
        assert ok is False
        assert "sk-ne-pas-fuiter" not in detail


class TestModelsEndpoint:
    """Le panneau doit proposer les modeles reellement installes."""

    @staticmethod
    def _fake_get(monkeypatch, payload, code=200):
        """Remplace le transport HTTP : aucun test ne doit sortir sur le reseau."""
        import requests

        class Response:
            status_code = code
            text = json.dumps(payload)
            headers = {}

            def json(self):
                return payload

        seen = {}

        def fake_get(url, headers=None, timeout=None):
            seen["url"] = url
            seen["headers"] = headers or {}
            return Response()

        monkeypatch.setattr(requests, "get", fake_get)
        return seen

    def test_lists_detected_models(self, client, monkeypatch):
        api, path = client
        llm.save_config(llm.LLMConfig(provider="ollama", model="m",
                                       ollama_host="http://127.0.0.1:11500"), path)
        seen = self._fake_get(monkeypatch, {"models": [
            {"name": "mistral:7b", "size": 4300000000, "modified_at": "2026-09-15T00:00:00Z",
             "details": {"family": "llama", "parameter_size": "7B",
                         "quantization_level": "Q4_K_M"}},
            {"name": "Qwen3-Coder-Next:latest", "size": 51700000000,
             "details": {"family": "qwen3next", "parameter_size": "79.7B",
                         "quantization_level": "Q4_K_M"}},
        ]})
        body = api.post("/api/llm/models").json()
        assert body["provider"] == "ollama"
        assert body["count"] == 2
        assert seen["url"] == "http://127.0.0.1:11500/api/tags"
        names = [m["name"] for m in body["models"]]
        assert names == ["Qwen3-Coder-Next:latest", "mistral:7b"], "liste triee"
        by_name = {m["name"]: m for m in body["models"]}
        assert by_name["mistral:7b"]["parameters"] == "7B"
        assert by_name["mistral:7b"]["quantization"] == "Q4_K_M"
        assert by_name["mistral:7b"]["family"] == "llama"

    def test_openai_uses_v1_models_with_bearer(self, client, monkeypatch):
        api, path = client
        llm.save_config(llm.LLMConfig(provider="openai", model="gpt-4o",
                                       api_base="https://api.example.com/v1",
                                       api_key="sk-test"), path)
        seen = self._fake_get(monkeypatch, {"data": [
            {"id": "gpt-4o", "owned_by": "openai", "created": 1},
            {"id": "gpt-4o-mini", "owned_by": "openai"},
        ]})
        body = api.post("/api/llm/models").json()
        assert seen["url"] == "https://api.example.com/v1/models"
        assert seen["headers"]["Authorization"] == "Bearer sk-test"
        assert [m["name"] for m in body["models"]] == ["gpt-4o", "gpt-4o-mini"]

    def test_model_without_metadata_is_still_listed(self, client, monkeypatch):
        api, path = client
        llm.save_config(llm.LLMConfig(provider="openai", model="m",
                                       api_base="https://api.example.com",
                                       api_key="k"), path)
        seen = self._fake_get(monkeypatch, {"data": [{"id": "serveur-sans-meta"}]})
        body = api.post("/api/llm/models").json()
        assert seen["url"].endswith("/v1/models")
        assert body["models"][0]["name"] == "serveur-sans-meta"
        assert body["models"][0]["parameters"] == ""

    def test_unsaved_form_fields_are_probed(self, client, monkeypatch):
        """Bascule de fournisseur : la liste doit suivre les champs affiches,
        sans exiger un enregistrement prealable."""
        api, path = client
        llm.save_config(llm.LLMConfig(provider="ollama", model="m",
                                       ollama_host="http://127.0.0.1:11500"), path)
        seen = self._fake_get(monkeypatch, {"data": [{"id": "serveur-vllm"}]})
        body = api.post("/api/llm/models", json={
            "provider": "openai", "api_base": "http://127.0.0.1:11501/v1",
        }).json()
        assert seen["url"] == "http://127.0.0.1:11501/v1/models"
        assert [m["name"] for m in body["models"]] == ["serveur-vllm"]

    def test_listing_does_not_require_a_model_or_a_key(self, client, monkeypatch):
        """L'utilisateur doit pouvoir voir les modeles avant d'en choisir un et
        avant de saisir une cle : valider ici fermerait le panneau."""
        api, path = client
        llm.save_config(llm.LLMConfig(provider="openai", model="m",
                                       api_base="https://api.example.com/v1",
                                       api_key="k"), path)
        seen = self._fake_get(monkeypatch, {"data": [{"id": "n-importe"}]})
        body = api.post("/api/llm/models", json={
            "provider": "openai", "model": "", "api_base": "https://api.example.com/v1",
            "api_key": "",
        }).json()
        assert seen["headers"]["Authorization"] == "Bearer k"
        assert [m["name"] for m in body["models"]] == ["n-importe"]

    def test_unreachable_provider_returns_empty_list_not_500(self, client, monkeypatch):
        import requests

        api, path = client
        llm.save_config(llm.LLMConfig(provider="ollama", model="m",
                                       ollama_host="http://127.0.0.1:1"), path)

        def boom(url, headers=None, timeout=None):
            raise requests.ConnectionError("connexion refusee")

        monkeypatch.setattr(requests, "get", boom)
        r = api.post("/api/llm/models")
        assert r.status_code == 200
        body = r.json()
        assert body["models"] == []
        assert body["error"]

    def test_http_error_is_reported(self, client, monkeypatch):
        api, path = client
        llm.save_config(llm.LLMConfig(provider="ollama", model="m",
                                       ollama_host="http://127.0.0.1:11500"), path)
        self._fake_get(monkeypatch, {"error": "nope"}, code=401)
        body = api.post("/api/llm/models").json()
        assert body["models"] == []
        assert "401" in body["error"]

    def test_unknown_provider_returns_empty_list_not_500(self, client):
        api, _ = client
        r = api.post("/api/llm/models", json={"provider": "llama.cpp"})
        assert r.status_code == 200
        body = r.json()
        assert body["models"] == []
        assert "ollama" in body["error"] and "openai" in body["error"]

    def test_repeated_provider_switch_keeps_the_last_answer(self, client, monkeypatch):
        """Deux réponses peuvent arriver dans l'ordre inverse de leur demande.
        Sans garde, la liste affichée ne correspond plus au serveur choisi."""
        api, path = client
        llm.save_config(llm.LLMConfig(provider="ollama", model="m",
                                       ollama_host="http://127.0.0.1:11500"), path)
        import requests

        class Response:
            status_code = 200
            headers = {}

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        def fake_get(url, headers=None, timeout=None):
            if "11501" in url:
                time.sleep(0.30)
                return Response({"data": [{"id": "lent-vllm"}]})
            return Response({"data": [{"id": "rapide"}]})

        monkeypatch.setattr(requests, "get", fake_get)
        body = api.post("/api/llm/models", json={
            "provider": "openai", "api_base": "http://127.0.0.1:11501/v1"}).json()
        assert [m["name"] for m in body["models"]] == ["lent-vllm"]

    def test_ui_flags_a_configured_model_that_is_not_installed(self):
        """Sans cet avertissement, un scan partirait sur un nom que la machine
        ne sert pas."""
        js = (llm.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        assert "llm.model_not_installed" in js
        assert "llm.models_found" in js

    def test_ui_shows_the_reason_when_no_model_is_listed(self):
        """Liste vide sans motif : l'utilisateur ne peut rien corriger."""
        js = (llm.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        assert "llm.models_error" in js

    def test_ui_ignores_out_of_order_list_responses(self):
        """Le jeton de generation est la seule chose qui empeche une reponse
        lente d'ecraser la liste du serveur choisi."""
        js = (llm.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        assert "modelsRequest" in js
        assert "token !== llmState.modelsRequest" in js

    def test_ui_does_not_overwrite_fields_being_edited(self):
        """Deux chargements concurrents (resume + ouverture) ecrasaient la saisie
        en cours : le panneau affichait des valeurs differentes de celles saisies."""
        js = (llm.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        assert "llmState.editing" in js
        assert "llmConfigRequest" in js

    def test_refresh_button_is_never_disabled(self):
        """Un bouton desactive avale le clic : la liste ne se rafraichit plus
        tant qu'une requete est en vol."""
        js = (llm.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        assert '$("llm-refresh").disabled' not in js
        assert "is-busy" in js


class TestTargetSafety:
    """Aucune requete ne part vers une cible refusee par check_target()."""

    def test_probe_rejects_metadata_ip(self, client):
        api, _ = client
        r = api.post("/api/llm/test", json={
            "provider": "openai", "model": "m",
            "api_base": "http://169.254.169.254/v1", "api_key": "k"})
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_models_never_fetch_metadata(self, client, monkeypatch):
        api, _ = client
        seen = TestModelsEndpoint._fake_get(monkeypatch, {"data": []})
        r = api.post("/api/llm/models", json={
            "provider": "openai", "api_base": "http://169.254.169.254/v1"})
        assert r.status_code == 200
        assert r.json()["models"] == []
        assert r.json()["error"]
        assert seen == {}, "aucune requete sortante vers l'IP de metadonnees"


class TestKeyBindsToBase:
    """La cle enregistree n'est transmise qu'a l'URL de base a laquelle elle
    est rattachee : un hote choisi par l'appelant ne recoit jamais le secret."""

    @staticmethod
    def _save(path):
        llm.save_config(llm.LLMConfig(
            provider="openai", model="gpt-4o",
            api_base="https://api.example.com/v1", api_key="sk-stored"), path)

    def test_models_with_new_base_drops_stored_key(self, client, monkeypatch):
        api, path = client
        self._save(path)
        seen = TestModelsEndpoint._fake_get(monkeypatch, {"data": []})
        api.post("/api/llm/models", json={
            "provider": "openai", "api_base": "https://attacker.example/v1"})
        assert seen["url"] == "https://attacker.example/v1/models"
        assert "Authorization" not in seen["headers"]

    def test_models_same_base_keeps_stored_key(self, client, monkeypatch):
        api, path = client
        self._save(path)
        seen = TestModelsEndpoint._fake_get(monkeypatch, {"data": [{"id": "gpt-4o"}]})
        api.post("/api/llm/models", json={
            "provider": "openai", "api_base": "https://api.example.com/v1"})
        assert seen["headers"]["Authorization"] == "Bearer sk-stored"

    def test_probe_new_base_requires_its_own_key(self, client):
        api, path = client
        self._save(path)
        r = api.post("/api/llm/test", json={
            "provider": "openai", "model": "m",
            "api_base": "https://attacker.example/v1"})
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_save_with_new_base_and_empty_key_is_rejected(self, client):
        api, path = client
        self._save(path)
        r = api.post("/api/llm/config", json={
            "provider": "openai", "model": "gpt-4o",
            "api_base": "https://attacker.example/v1", "api_key": ""})
        assert r.status_code == 400
        assert r.json()["error"]
        config = llm.load_config(path)
        assert config.api_key == "sk-stored"
        assert config.api_base == "https://api.example.com/v1"

    def test_save_with_new_base_and_new_key_succeeds(self, client):
        api, path = client
        self._save(path)
        r = api.post("/api/llm/config", json={
            "provider": "openai", "model": "gpt-4o",
            "api_base": "https://attacker.example/v1", "api_key": "sk-new"})
        assert r.status_code == 200
        config = llm.load_config(path)
        assert config.api_key == "sk-new"
        assert config.api_base == "https://attacker.example/v1"
