"""Internationalisation (i18n) légère.

Charge des catalogues JSON (fr, en, extensible), résout une langue avec
repli (français puis anglais), et expose une méthode `t()` pour traduire
les chaînes avec formatage de paramètres.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_LANG = os.environ.get("APP_LANGUAGE", "fr")
FALLBACK_LANG = "en"

_LANG_RE = re.compile(r"^[a-z]{2}$")
_TRANSLATIONS_DIR = Path(__file__).parent / "locales"


def _norm(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    code = str(code).strip().lower()
    return code if _LANG_RE.match(code) else None


def deep_merge(base: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class Translations:
    def __init__(self, translations_dir: Optional[Path] = None) -> None:
        self.dir = Path(translations_dir) if translations_dir else _TRANSLATIONS_DIR
        self.default_lang = _norm(DEFAULT_LANG) or "fr"
        self.fallback_lang = _norm(FALLBACK_LANG) or "en"
        self._catalogs: Dict[str, Dict[str, Any]] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.dir.is_dir():
            return
        for path in sorted(self.dir.glob("*.json")):
            code = _norm(path.stem)
            if not code:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict):
                self._catalogs[code] = data

    def available_languages(self) -> List[Dict[str, str]]:
        present = [c for c in (self.default_lang, self.fallback_lang) if c in self._catalogs]
        rest = sorted(c for c in self._catalogs if c not in present)
        ordered: List[str] = []
        for code in present + rest:
            if code not in ordered:
                ordered.append(code)
        result: List[Dict[str, str]] = []
        for code in ordered:
            meta = self._catalogs.get(code, {}).get("_meta", {})
            name = meta.get("name") if isinstance(meta, dict) else None
            result.append({"code": code, "name": name or code})
        return result

    def resolved_chain(self, lang: Optional[str] = None) -> List[str]:
        wanted = _norm(lang) or self.default_lang
        chain = [
            c
            for c in (self.default_lang, self.fallback_lang)
            if c != wanted and c in self._catalogs
        ]
        chain.append(wanted)
        return chain

    def resolved_catalog(self, lang: Optional[str] = None) -> Dict[str, Any]:
        catalog: Dict[str, Any] = {}
        for code in self.resolved_chain(lang):
            deep_merge(catalog, self._catalogs.get(code, {}))
        return catalog

    @staticmethod
    def _lookup(catalog: Dict[str, Any], key: str) -> Any:
        current: Any = catalog
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def t(
        self,
        key: str,
        lang: Optional[str] = None,
        default: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        catalog = self.resolved_catalog(lang)
        value = self._lookup(catalog, key)
        if value is None:
            value = default if default is not None else key
        if not isinstance(value, str):
            value = str(value)
        if kwargs:
            try:
                value = value.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                pass
        return value

    def resolve(self, accept_language: Optional[str]) -> str:
        if not accept_language:
            return self.default_lang
        available = set(self._catalogs)
        candidates: List[tuple] = []
        for chunk in accept_language.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            quality = 1.0
            match = re.search(r";\s*q=([0-9.]+)", chunk)
            head = chunk.split(";")[0].strip()
            if match:
                try:
                    quality = float(match.group(1))
                except ValueError:
                    quality = 0.0
            code = _norm(head)
            if code:
                candidates.append((quality, code))
            if "-" in head:
                base = _norm(head.split("-")[0])
                if base:
                    candidates.append((quality * 0.9, base))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for _quality, code in candidates:
            if code in available:
                return code
        return self.default_lang


T = Translations()


def available_languages() -> List[Dict[str, str]]:
    return T.available_languages()
