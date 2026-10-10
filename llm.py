"""Fournisseurs LLM : Ollama local et API compatible ChatGPT (OpenAI-like).

Un seul point d'entree, `ChatClient.complete()`, masque le protocole. Les deux
fournisseurs exposent la meme requete/response, donc le reste du code ignore
quel moteur a repondu.

Choix de conception :
- La configuration vit dans un JSON (`llm_config.json`), jamais dans le code.
  Elle est fusionnee avec l'environnement, qui reste prioritaire pour que
  l'outillage et la CI puissent surcharger sans toucher au fichier.
- La cle API n'est jamais renvoyee par l'API REST : seule la presence et les
  derniers caracteres sont exposes, ce qui permet de confirmer qu'une cle est
  enregistree sans la divulguer.
"""

import asyncio
import ipaddress
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from i18n import T

BASE_DIR = Path(__file__).parent

KEY_KEEP = "__KEEP__"


def config_path() -> Path:
    """Chemin du fichier de configuration, resolu a chaque appel.

    La resolution tardive permet a l'environnement (tests, CI, executions
    multiples) de rediriger la configuration sans recharger le module.
    """
    return Path(os.environ.get("BT_LLM_CONFIG") or BASE_DIR / "llm_config.json")

PROVIDERS = ("ollama", "openai")

DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("BT_MODEL", "llama3.1")

ENV_KEYS = {
    "provider": "BT_LLM_PROVIDER",
    "model": "BT_MODEL",
    "ollama_host": "OLLAMA_HOST",
    "api_base": "BT_LLM_API_BASE",
    "api_key": "BT_LLM_API_KEY",
    "temperature": "BT_LLM_TEMPERATURE",
    "timeout": "BT_LLM_TIMEOUT",
}


class LLMError(RuntimeError):
    """Erreur metier du fournisseur LLM, destinee a l'affichage."""


# Points d'entree de metadonnees : repondent en clair sur une IP link-local.
_METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
})


def check_target(url: str, field: str = "url") -> None:
    """Refuse une URL qui ne peut viser qu'un service interne sensible.

    Scheme http(s) exigé et adresses link-local/multidiffusion refusées :
    `http://169.254.169.254/...` (métadonnées cloud) ne doit jamais être sondé,
    surtout avec la clé API enregistrée jointe à la requête. Les adresses
    privées restent acceptées : Ollama et les proxys compatibles ChatGPT
    vivent sur le réseau local.
    """
    raw = str(url).strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LLMError(T.t("llm.err_target", field=field, value=raw[:120]))
    host = parsed.hostname.lower()
    if host in _METADATA_HOSTS:
        raise LLMError(T.t("llm.err_target", field=field, value=host))
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return  # nom d'hôte : résolu par la bibliothèque réseau à la requête
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if (address.is_link_local or address.is_multicast
            or address.is_unspecified or address.is_reserved):
        raise LLMError(T.t("llm.err_target", field=field, value=host))


@dataclass
class LLMConfig:
    """Configuration d'un fournisseur. Aucun secret n'est journalise."""

    provider: str = "ollama"
    model: str = DEFAULT_MODEL
    ollama_host: str = DEFAULT_OLLAMA_HOST
    api_base: str = ""
    api_key: str = ""
    temperature: float = 0.2
    timeout: int = 120

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            raise LLMError(
                T.t("llm.err_provider", value=self.provider, allowed="/".join(PROVIDERS))
            )
        if not self.model.strip():
            raise LLMError(T.t("llm.err_model"))
        if self.provider == "openai":
            if not self.api_base.strip():
                raise LLMError(T.t("llm.err_base"))
            if not self.api_base.startswith(("http://", "https://")):
                raise LLMError(T.t("llm.err_base_scheme"))
            check_target(self.api_base, field="api_base")
            if not self.api_key.strip():
                raise LLMError(T.t("llm.err_key"))
        else:
            check_target(self.ollama_host, field="ollama_host")
        try:
            temperature = float(self.temperature)
        except (TypeError, ValueError):
            raise LLMError(T.t("llm.err_temperature"))
        if not 0.0 <= temperature <= 2.0:
            raise LLMError(T.t("llm.err_temperature"))

    @property
    def chat_url(self) -> str:
        """URL du point de terminaison chat, derivee de la base."""
        if self.provider == "ollama":
            return self.ollama_host.rstrip("/") + "/api/chat"
        base = self.api_base.rstrip("/")
        # Une base qui pointe deja sur /chat/completions est acceptee telle quelle.
        if base.endswith("/chat/completions"):
            return base
        # memes regles que probe()/list_models() : sans /v1, l'analyse finirait
        # en 404 alors que la sonde reussit (openai sans suffixe).
        if not base.endswith("/v1"):
            base += "/v1"
        return base + "/chat/completions"

    def masked(self) -> Dict[str, Any]:
        """Vue sure pour l'API : la cle n'est jamais transmise en clair."""
        key = self.api_key or ""
        data = asdict(self)
        data["api_key"] = ""
        data["has_api_key"] = bool(key.strip())
        data["api_key_hint"] = ("..." + key[-4:]) if len(key) >= 4 else ("..." if key else "")
        return data


def load_config(path: Optional[Path] = None) -> LLMConfig:
    """Charge la configuration du fichier, puis applique l'environnement."""
    target = Path(path) if path else config_path()
    data: Dict[str, Any] = {}
    if target.is_file():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}
    for field_name, env_name in ENV_KEYS.items():
        value = os.environ.get(env_name)
        if value:
            data[field_name] = value
    known = {f for f in LLMConfig.__dataclass_fields__}
    config = LLMConfig(**{k: v for k, v in data.items() if k in known})
    for attr in ("temperature",):
        try:
            setattr(config, attr, float(getattr(config, attr)))
        except (TypeError, ValueError):
            setattr(config, attr, 0.2)
    try:
        config.timeout = int(config.timeout)
    except (TypeError, ValueError):
        config.timeout = 120
    return config


def save_config(config: LLMConfig, path: Optional[Path] = None) -> None:
    target = Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass


def extract_json_object(text: str) -> Dict[str, Any]:
    """Extrait le premier objet JSON d'une reponse, fence de markdown comprise."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        loose = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = loose.group() if loose else ""
    if not candidate:
        raise LLMError(T.t("llm.err_no_json"))
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMError(T.t("llm.err_json", error=exc)) from exc
    if not isinstance(parsed, dict):
        raise LLMError(T.t("llm.err_json_shape"))
    return parsed


class ChatClient:
    """Client de chat synchrone, execute dans un thread hors de la boucle asyncio."""

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config or load_config()

    # -- protocole interne -------------------------------------------------
    def _payload(self, prompt: str, system: Optional[str], stream: bool) -> Dict[str, Any]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        if self.config.provider == "ollama":
            return {
                "model": self.config.model,
                "messages": messages,
                "stream": stream,
                "options": {"temperature": float(self.config.temperature)},
            }
        return {
            "model": self.config.model,
            "messages": messages,
            "stream": stream,
            "temperature": float(self.config.temperature),
        }

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.provider == "openai" and self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _parse(self, data: Dict[str, Any]) -> str:
        """Les deux API convergent vers un contenu texte, mais pas au meme endroit."""
        if self.config.provider == "ollama":
            message = data.get("message") or {}
            content = message.get("content")
        else:
            choices = data.get("choices") or []
            first = choices[0] if choices else {}
            message = first.get("message") or {}
            content = message.get("content")
            if content is None and isinstance(first.get("text"), str):
                content = first["text"]
        if not isinstance(content, str) or not content.strip():
            detail = data.get("error")
            if isinstance(detail, dict):
                detail = detail.get("message") or detail
            raise LLMError(T.t("llm.err_empty", detail=str(detail)[:200] if detail else ""))
        return content

    # -- surface publique --------------------------------------------------
    def complete(
        self, prompt: str, system: Optional[str] = None, timeout: Optional[int] = None
    ) -> str:
        self.config.validate()
        payload = self._payload(prompt, system, stream=False)
        try:
            import requests

            response = requests.post(
                self.config.chat_url,
                json=payload,
                headers=self._headers(),
                timeout=timeout or self.config.timeout,
            )
            if response.status_code >= 400:
                raise LLMError(
                    T.t("llm.err_http", code=response.status_code, body=response.text[:200])
                )
            data = response.json()
        except LLMError:
            raise
        except ImportError:
            raise LLMError(T.t("llm.err_no_requests"))
        except Exception as exc:
            raise LLMError(T.t("llm.err_unreachable", detail=str(exc)[:200])) from exc
        return self._parse(data)

    def complete_json(
        self, prompt: str, system: Optional[str] = None, timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """Retourne un dict strict. L'utilisateur est invite a ne repondre que du JSON."""
        instruction = (
            T.t("llm.json_only") + "\n" if T.t("llm.json_only") else "Reply with JSON only.\n"
        )
        text = self.complete(instruction + prompt, system=system, timeout=timeout)
        return extract_json_object(text)

    async def probe(self) -> Dict[str, Any]:
        """Teste la joignabilite du fournisseur et mesure la latence.

        Le transport etant bloquant, il est execute dans un thread afin de ne
        pas immobiliser la boucle asyncio du dashboard pendant l'appel reseau.
        """
        return await asyncio.to_thread(self.probe_sync)

    def probe_sync(self) -> Dict[str, Any]:
        """Meme test que `probe()`, mais synchrone et reutilisable en CLI."""
        self.config.validate()
        started = time.monotonic()
        try:
            if self.config.provider == "ollama":
                import requests

                url = self.config.ollama_host.rstrip("/") + "/api/tags"
                response = requests.get(url, timeout=min(self.config.timeout, 15))
                if response.status_code >= 400:
                    raise LLMError(T.t("llm.err_http", code=response.status_code,
                                       body=response.text[:200]))
                models = [
                    m.get("name", "")
                    for m in (response.json().get("models") or [])
                    if isinstance(m, dict)
                ]
                return {
                    "ok": True,
                    "models": models,
                    "model_present": self.config.model in models,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
            import requests

            url = self.config.api_base.rstrip("/")
            url += "" if url.endswith("/v1") else "/v1"
            response = requests.get(
                url + "/models",
                headers=self._headers(),
                timeout=min(self.config.timeout, 15),
            )
            if response.status_code >= 400:
                raise LLMError(T.t("llm.err_http", code=response.status_code,
                                   body=response.text[:200]))
            payload = response.json()
            models = [
                m.get("id", "")
                for m in (payload.get("data") or [])
                if isinstance(m, dict)
            ]
            return {
                "ok": True,
                "models": models,
                "model_present": self.config.model in models,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(T.t("llm.err_unreachable", detail=str(exc)[:200])) from exc

    async def list_models(self) -> List[str]:
        return await asyncio.to_thread(self.list_models_sync)

    def list_models_sync(self) -> List[str]:
        return list(self.probe_sync().get("models") or [])


def _describe_ollama_model(entry: Dict[str, Any]) -> Dict[str, Any]:
    details = entry.get("details") or {}
    return {
        "name": entry.get("name") or entry.get("model") or "",
        "size": entry.get("size") or 0,
        "modified_at": entry.get("modified_at") or "",
        "family": details.get("family") or "",
        "parameters": details.get("parameter_size") or "",
        "quantization": details.get("quantization_level") or "",
    }


def _describe_openai_model(entry: Dict[str, Any]) -> Dict[str, Any]:
    owned = entry.get("owned_by") or ""
    return {
        "name": entry.get("id") or "",
        "size": 0,
        "modified_at": str(entry.get("created") or ""),
        "family": owned,
        "parameters": "",
        "quantization": "",
    }


def _headers_for(config: LLMConfig) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.provider == "openai" and config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def list_models_detailed_sync(config: LLMConfig) -> Dict[str, Any]:
    """Liste les modeles du fournisseur avec leurs metadonnees d'affichage.

    Une entree sans metadonnees reste listee : seul le nom est necessaire pour
    configurer un scan. Le nom est la seule donnee exigee, le reste est
    meilleur effort.
    """
    import requests

    started = time.monotonic()
    timeout = min(config.timeout, 15)
    if config.provider == "ollama":
        url = config.ollama_host.rstrip("/") + "/api/tags"
        headers: Dict[str, str] = {}
    else:
        base = config.api_base.rstrip("/")
        base = base[: -len("/chat/completions")] if base.endswith("/chat/completions") else base
        if not base.endswith("/v1"):
            base += "/v1"
        url = base + "/models"
        headers = _headers_for(config)

    # Un fournisseur injoignable doit devenir une erreur métier translated, pas
    # remonter en traceback depuis la route.
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        raise LLMError(T.t("llm.err_unreachable", detail=str(exc)[:200])) from exc

    if response.status_code >= 400:
        raise LLMError(
            T.t("llm.err_http", code=response.status_code, body=response.text[:200])
        )
    payload = response.json()
    raw = payload.get("models") if config.provider == "ollama" else payload.get("data")
    describe = _describe_ollama_model if config.provider == "ollama" else _describe_openai_model

    models: List[Dict[str, Any]] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        item = describe(entry)
        if item["name"]:
            models.append(item)
    models.sort(key=lambda item: item["name"])
    return {
        "provider": config.provider,
        "models": models,
        "count": len(models),
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


async def list_models_detailed(config: LLMConfig) -> Dict[str, Any]:
    """Variante non bloquante pour le dashboard."""
    return await asyncio.to_thread(list_models_detailed_sync, config)