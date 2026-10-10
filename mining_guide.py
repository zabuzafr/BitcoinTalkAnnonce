"""Extraction des informations de minage depuis une annonce ou un site projet.

Les annonces BitcoinTalk décrivent le minage sous forme d'un bloc de lignes
`Libellé  valeur` (Pool, Port, Worker, Password, Payout, Network, Explorer).
Le texte de l'annonce perd les retours à la ligne pendant l'extraction HTML, donc
le découpage ne peut pas compter sur les lignes : il cherche les libellés
connus dans le texte brut, puis prend tout ce qui suit jusqu'au libellé suivant.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

HOST_RE = re.compile(
    r"(?:stratum\+t?cp|stratum\+ssl|https?|ssl|tcp)://[^\s,;()]+"
    r"|(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}(?![a-z0-9-])"
    r"|(?:\d{1,3}\.){3}\d{1,3}(?![a-z0-9.])"
)
IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
URL_RE = re.compile(r"^https?://", re.I)

FIELDS: Dict[str, str] = {
    "network": r"networks?|main ?nets?|chain",
    "explorer": r"(?:block(?:chain)? ?)?explorers?",
    "pool": (
        r"pools?|stratums?"
        r"(?:\s*(?:address|addresses|url|urls|host|hosts|server|servers|serveur))?"
    ),
    "port": r"ports?",
    "worker": r"worker ?names?|workers?|nom ?du ?worker",
    "username": r"user ?names?|usernames?|logins?",
    "password": r"passwords?|pass ?words?|mot ?de ?passe",
    "payout": r"payout(?:\s*address)?|adresse ?de ?paiement",
    "algorithm": r"algorithms?|algo(?:rithmes?)?",
    "miner": r"miners?|clients?|softwares?",
}

_LINE_RE = re.compile(
    r"^[ \t>*\-]*(?P<label>"
    + "|".join(f"(?P<{name}>{pattern})" for name, pattern in FIELDS.items())
    + r")[ \t]*(?P<sep>[:\-–—=])?[ \t]*(?P<value>\S.*)?$",
    re.I | re.M,
)
_CONTINUATION_RE = re.compile(r"^\(?[ \t]*[\w.:/-]+(?:\s+or\s+\S+)?\)?[ \t]*$", re.I)
_CONTINUATION_FIELDS = ("pool", "explorer")


def _line_segments(text: str) -> List[Dict[str, str]]:
    """Lit un tableau `Libellé  valeur` où chaque valeur est sur sa ligne.

    Le texte d'une page web garde ses retours à la ligne, donc le libellé en
    début de ligne est un indice bien plus fort que dans une annonce aplatie.
    Une ligne entre parenthèses prolonge la valeur précédente: les annonces
    écrivent l'hôte de secours sur la ligne d'après.
    """
    runs: List[List[Dict[str, str]]] = [[]]
    for line in (text or "").splitlines():
        stripped = line.strip()
        match = _LINE_RE.match(stripped)
        if match and match.group("value"):
            label = next(name for name in FIELDS if match.group(name))
            runs[-1].append({
                "field": label,
                "value": match.group("value").strip(),
                "sep": match.group("sep") or "",
                "anchored": True,
            })
            continue
        if (
            runs[-1]
            and runs[-1][-1]["field"] in _CONTINUATION_FIELDS
            and _CONTINUATION_RE.match(stripped)
        ):
            runs[-1][-1]["value"] = f"{runs[-1][-1]['value']} {stripped}"
            continue
        runs.append([])
    return max(runs, key=len)


_LABEL_RE = re.compile(
    "(?P<label>"
    + "|".join(f"(?P<{name}>{pattern})" for name, pattern in FIELDS.items())
    + r")"
    r"(?P<sep>\s*[:\-–—=]\s*|\s+)",
    re.I,
)

_STRATUM_SUFFIX_RE = re.compile(r"^(?P<scheme>stratum\+(?:tcp|ssl))://", re.I)
_NODE_PORT_RE = re.compile(r"\b(?:p2p|rpc)\s+$", re.I)
_MAX_HOSTS = 6
_MAX_VALUE_CHARS = 220
_PORT_RE = re.compile(r"\b(\d{2,5})\b")

GUIDE_HINT_RE = re.compile(
    r"mine|mining|miner|pool|how[- ]?to|guide|docs?|documentation|"
    r"network|explorer|faqs?|help|start",
    re.I,
)


_PORT_RE = re.compile(r"\b(\d{2,5})\b")
_GLUED_FIELD_RE = re.compile(
    r"(?P<glue>[\w.])(?P<label>ports?|passwords?|workers?|payouts?|networks?|explorers?)"
    r"(?=\s*[:\-–—=]?\s*\d)",
    re.I,
)


def _scan_port(text: str) -> str:
    """Cherche un numéro de port dans tout le texte, y compris collé.

    `…comport 3335` : le libellé est collé à l'hôte, donc le découpage par
    libellés le rate. Un port est toujours suivi de chiffres, et le mot doit
    commencer un mot, sinon `Report 44` serait lu comme un port.
    """
    match = re.search(
        r"(?<!p2p )(?<!rpc )\bports?\s*[:\-–—=]?\s*(\d{2,5})\b", text or "", re.I
    )
    return match.group(1) if match else ""


def _separate_glued_fields(text: str) -> str:
    """Insère un espace avant un libellé collé à ce qui le précède.

    Le texte d'annonce perd les espaces: `pool.xcoinminer.comport 3335` fait
    croire à un domaine `xcoinminer.comport`. Comme le libellé est suivi d'un
    nombre, la séparation est sûre.
    """
    return _GLUED_FIELD_RE.sub(lambda m: f"{m.group('glue')} {m.group('label')}", text or "")


def _label_matches(text: str) -> List["re.Match[str]"]:
    """Trouve les libellés, en ignorant ceux situés dans une URL ou un hôte.

    Sans ce masque, `pool.example.com` serait lu comme le libellé `pool` suivi
    de `example.com`, et le vrai hôte disparaîtrait de la valeur.
    """
    masked = list(HOST_RE.finditer(text or ""))
    matches = []
    for match in _LABEL_RE.finditer(text or ""):
        inside_host = any(
            m.start() < match.end() and match.start() < m.end() for m in masked
        )
        if inside_host:
            continue
        if _NODE_PORT_RE.search(text[max(0, match.start() - 6):match.start()]):
            continue
        matches.append(match)
    return matches


def _labelled_segments(text: str) -> List[Dict[str, str]]:
    """Découpe le texte en segments (libellé reconnu, valeur).

    Le texte perd ses retours à la ligne, donc deux libellés peuvent se
    toucher: `Network` suivi de `Mainnet` frappe deux fois, la première sans
    valeur. Un segment sans valeur absorbe alors le suivant.
    """
    text = text or ""
    matches = _label_matches(text)
    segments: List[Dict[str, str]] = []
    index = 0
    while index < len(matches):
        match = matches[index]
        label = next(name for name in FIELDS if match.group(name))
        cursor = index + 1
        while cursor < len(matches) and not text[match.end():matches[cursor].start()].strip():
            cursor += 1
        end = matches[cursor].start() if cursor < len(matches) else len(text)
        value = text[match.end():end].strip()
        if value:
            segments.append({"field": label, "value": value, "sep": match.group("sep")})
        index = max(cursor, index + 1)
    return segments


def _is_field_like(segment: Dict[str, str]) -> bool:
    """Vrai si le segment ressemble à un champ et pas à une phrase.

    Trois indices suffisent, du plus fort au plus faible : le libellé est
    ancré en début de ligne, il est suivi d'un deux-points, ou la valeur tient
    en un seul mot. `Network Mainnet` passe par le troisième, `network
    contributors during blocks` par aucun.
    """
    if segment.get("anchored"):
        return True
    if _EXPLICIT_SEP_RE.search(segment.get("sep", "")):
        return True
    return bool(_SINGLE_WORD_RE.match(segment["value"]))


def _segment_score(segment: Dict[str, str]) -> int:
    """Classe la qualité d'un segment : séparateur explicite d'abord.

    `Pool: pool.example.org` est un champ, `pool lives in the repo` est une
    phrase. La présence de `:` après le libellé sépare les deux. Un libellé en
    début de ligne est déjà une ancre forte, même sans deux-points.
    """
    if segment.get("anchored"):
        return 0
    separator = segment.get("sep", "")
    return 0 if re.search(r"[:\-–—=]", separator or "") else 1


def _candidates(text: str) -> Dict[str, List[Dict[str, str]]]:
    return _group(_labelled_segments(text))


def _prose(value: str) -> str:
    """Garde une phrase courte, sans la phrase qui la suit."""
    sentence = re.split(r"(?<=[a-z0-9])\.\s+(?=[A-ZÀ-ÿ])", value or "", maxsplit=1)[0]
    words = sentence.split()
    return " ".join(words[:12]).strip(" .,:;-")[:_MAX_VALUE_CHARS]


_PROSE_MAX_WORDS = 6
_PROSE_MAX_CHARS = 60
_MIN_TABLE_ROWS = 3
_EXPLICIT_SEP_RE = re.compile(r"[:\-–—=]")
_SINGLE_WORD_RE = re.compile(r"^\S{1,40}$")


def _plausible_prose(value: str) -> bool:
    """Rejette les valeurs qui sont de la prose, pas une valeur de champ.

    `chain` est un libellé valide (`Chain: Xid`) mais apparaît aussi au milieu
    d'une phrase. Une vraie valeur de champ tient en quelques mots.
    """
    candidate = _prose(value)
    if not candidate or len(candidate) > _PROSE_MAX_CHARS:
        return False
    if candidate.count("(") != candidate.count(")") or candidate.count("[") != candidate.count("]"):
        return False
    return len(candidate.split()) <= _PROSE_MAX_WORDS


_HOST_STRIP_RE = re.compile(r"[^A-Za-z0-9._:%/-]+")
# Le dernier label reste en minuscules : `pool.bch2.orgWhitepaper` est
# `pool.bch2.org` suivi du libellé `Whitepaper` collé, pas un TLD `orgWhitepaper`.
_HOST_HEAD_RE = re.compile(
    r"(?P<base>(?:[A-Za-z0-9-]+\.)+[a-z]{2,24}|(?:\d{1,3}\.){3}\d{1,3})"
    r"(?::(?P<port>\d{2,5}))?"
)
_VERSION_HOST_RE = re.compile(r"^(?:\d+\.){3,}\d+$")
_FILE_TLD_RE = re.compile(
    r"^(?:exe|msi|dll|zip|tar|gz|7z|apk|dmg|deb|rpm|py|js|ts|cpp|c|h|"
    r"sh|bat|json|yaml|yml|log|txt|md|pdf|so|a|dylib|jar|war)$",
    re.I,
)
_GLUED_SCHEME_RE = re.compile(r"(?P<scheme>(?:stratum\+tcp|stratum\+ssl|https?)://)")


def _is_version_like(base: str) -> bool:
    """Vrai pour un numéro de version lu comme une adresse.

    `2.0.8.5` vient d'une liste de versions de mineurs. Une adresse publique
    a ses quatre octets répartis sur toute la plage, donc une adresse dont
    chaque octet est inférieur à 10 est un numéro.
    """
    if not IPV4_RE.match(base):
        return False
    return all(int(part) < 10 for part in base.split("."))


def _clean_host(host: str) -> str:
    """Ramène un hôte brut à sa forme utilisable.

    `pool.gaelium.ioDiscord` vient d'un texte aplati où le libellé suivant est
    collé, et `:3334Fee` d'un libellé de tableau collé au port. Un hôte utile
    est un nom de domaine ou une IPv4, éventuellement suivi d'un port: tout ce
    qui suit (`/pool/plne`, un fichier, une phrase) est coupé. Un chemin ne
    sert de toute façon pas à connecter un mineur.
    """
    host = _HOST_STRIP_RE.sub("", (host or "").rstrip("/"))
    match = _HOST_HEAD_RE.match(host)
    if not match:
        return ""
    base = match.group("base")
    if IPV4_RE.match(base):
        parts = [int(part) for part in base.split(".")]
        if any(part > 255 for part in parts) or base == "0.0.0.0":
            return ""
    elif _VERSION_HOST_RE.match(base) or _FILE_TLD_RE.match(base.rsplit(".", 1)[-1]):
        return ""
    return f"{base}:{match.group('port')}" if match.group("port") else base


def _split_glued_schemes(chunk: str) -> List[str]:
    """Sépare les URL enchaînées sans espace : `a.comhttps://b.com`.

    Le texte aplati colle les liens voisins. Chaque `://` ouvre une URL, donc
    le fragment se coupe sur ces marqueurs.
    """
    starts = [match.start() for match in _GLUED_SCHEME_RE.finditer(chunk or "")]
    if not starts:
        return [chunk]
    parts = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(chunk)
        parts.append(chunk[start:end])
    return parts


def _hosts(value: str) -> List[str]:
    hosts: List[str] = []
    chunks: List[str] = []
    for match in HOST_RE.finditer(value or ""):
        chunks.extend(_split_glued_schemes(match.group(0).rstrip(".,;:")))
    for chunk in chunks:
        stratum = _STRATUM_SUFFIX_RE.match(chunk)
        scheme = "https://" if URL_RE.match(chunk) else ""
        if stratum:
            chunk = chunk[stratum.end():]
        elif scheme:
            chunk = chunk[len(scheme):]
        chunk = chunk.split("/")[0]
        host = _clean_host(chunk)
        if not host or host in hosts:
            continue
        hosts.append(scheme + host)
        if len(hosts) >= _MAX_HOSTS:
            break
    return hosts


_POOL_HOST_HINT_RE = re.compile(r"pool|mine|mining|miner|stratum|node", re.I)
_CHAT_HOST_RE = re.compile(
    r"^(?:discord(?:app)?\.com|discord\.gg|t\.me|telegram\.me|twitter\.com|x\.com|"
    r"facebook\.com|reddit\.com|github\.com|gitlab\.com|bitcointalk\.org|"
    r"medium\.com|youtube\.com|youtu\.be|ko-fi\.com|patreon\.com|"
    r"buymeacoffee\.com|boosty\.to|odysee\.com|rumble\.com)$",
    re.I,
)


def _pool_hosts(value: str) -> List[str]:
    """Hôtes de piscine d'une valeur de champ `Pool`.

    Une annonce aplatie colle les liens voisins: après `pool.gaelium.io`
    viennent `discord.gg` et `t.me`. Un hôte qui porte un indice de minage est
    retenu, les salons de discussion sont écartés. Sinon on ne garde l'hôte
    que si la valeur n'en contient qu'un: une valeur qui en cite plusieurs est
    une phrase, pas un champ de piscine.
    """
    found = _hosts(value)
    hosts = [host for host in found if not _is_chat_host(host)]
    hinted = [host for host in hosts if _is_pool_host(host)]
    if hinted:
        return hinted
    return hosts if len(found) == 1 and hosts else []


def _is_pool_host(host: str) -> bool:
    """Vrai si l'hôte porte un indice de piscine ou de minage."""
    bare = host.split("//")[-1].split(":")[0]
    return bool(IPV4_RE.match(bare)) or bool(_POOL_HOST_HINT_RE.search(bare))


def _is_chat_host(host: str) -> bool:
    """Vrai pour un service de discussion ou de code, jamais une piscine.

    `Pool Discord: discord.gg/x` est un libellé de minage suivi du lien du
    salon. Le lien reste dans la valeur mais l'hôte ne doit pas être gardé
    comme hôte de piscine.
    """
    bare = host.split("//")[-1].split(":")[0].split("/")[0]
    return bool(_CHAT_HOST_RE.match(bare))


def _dedupe_ports(hosts: List[str]) -> List[str]:
    """Fusionne les doublons d'un hôte : `pool.io` et `pool.io:3638`.

    L'annonce nomme souvent la piscine sans port puis la redonne dans l'URL
    stratum. Une seule entrée suffit, avec son port.
    """
    merged: Dict[str, str] = {}
    order: List[str] = []
    for host in hosts:
        base, _, port = host.partition(":")
        key = base.lower()
        if key not in merged:
            merged[key] = host
            order.append(key)
        elif port and ":" not in merged[key]:
            merged[key] = host
    return [merged[key] for key in order]


def _port_in_host(host: str) -> str:
    """Port porté par l'hôte lui-même, comme `pool.gaelium.io:3638`."""
    match = re.search(r":(\d{2,5})$", host or "")
    return match.group(1) if match else ""


def _first_port(value: str) -> Optional[str]:
    """Port en début de valeur.

    `8766SLIP-44 coin type` contient 44 mais 8766 est le port. Le port suit
    toujours son libellé, donc il est cherché seulement en tête.
    """
    match = re.match(r"[:\-–—=]?\s*(\d{2,5})\b", (value or "").lstrip())
    return match.group(1) if match else None


def _clean_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip(" .,:;-")
    return cleaned[:_MAX_VALUE_CHARS]


def parse_mining_guide(text: str) -> Dict[str, object]:
    """Extrait les paramètres de minage d'un texte d'annonce ou de page web.

    Une page web garde ses retours à la ligne, donc le tableau
    `Libellé  valeur` est lu ligne par ligne. Une annonce aplatie par
    l'extraction HTML perd ces retours, le texte entier est alors balayé par
    libellés. Retourne toujours les mêmes clés : une valeur absente est une
    chaîne vide ou une liste vide.
    """
    if not text:
        return empty_guide()

    text = _separate_glued_fields(text)
    per_line = _line_segments(text)
    if len(per_line) >= _MIN_TABLE_ROWS and guide_is_usable(_from_segments(_group(per_line), "")):
        return _from_segments(_group(per_line), "")
    return _from_segments(_candidates(text), text)


def _group(segments: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for segment in segments:
        if segment["value"]:
            grouped.setdefault(segment["field"], []).append(segment)
    return grouped


def _from_segments(grouped: Dict[str, List[Dict[str, str]]], text: str) -> Dict[str, object]:
    """Convertit des segments libellé/valeur en guide structuré."""
    guide = empty_guide()
    for field, segments in grouped.items():
        segments.sort(key=_segment_score)
        if field == "pool":
            for segment in segments:
                for host in _pool_hosts(segment["value"]):
                    if host not in guide["pool"]:  # type: ignore[operator]
                        guide["pool"].append(host)  # type: ignore[union-attr]
        elif field == "explorer":
            for segment in segments:
                explorer = next(
                    (
                        h if URL_RE.match(h) else f"https://{h}"
                        for h in _hosts(segment["value"])
                    ),
                    "",
                )
                if explorer:
                    guide["explorer"] = explorer
                    break
        elif field == "port":
            for segment in segments:
                if not _is_field_like(segment):
                    continue
                port = _first_port(segment["value"])
                if port:
                    guide["port"] = port
                    break
            if not guide["port"]:
                guide["port"] = _scan_port(text)
        else:
            for segment in segments:
                if not _plausible_prose(segment["value"]):
                    continue
                if not _is_field_like(segment):
                    continue
                guide[field] = _prose(segment["value"])
                break

    pools = guide["pool"]
    port = guide["port"]
    if isinstance(pools, list) and pools:
        pools = _dedupe_ports(pools)
        guide["pool"] = pools
        if not port:
            guide["port"] = _port_in_host(pools[0])
        port = guide["port"]
    if isinstance(pools, list) and pools and port:
        guide["stratum"] = [
            host if ":" in host.split("//")[-1] else f"{host}:{port}"
            for host in pools
        ]
    return guide


def empty_guide() -> Dict[str, object]:
    """Guide vide, mêmes clés que `parse_mining_guide`."""
    return {
        "network": "",
        "explorer": "",
        "pool": [],
        "port": "",
        "worker": "",
        "username": "",
        "password": "",
        "payout": "",
        "algorithm": "",
        "miner": "",
        "stratum": [],
    }


def merge_mining_guides(*guides: Dict[str, object]) -> Dict[str, object]:
    """Combine plusieurs extractions, la première valeur non vide l'emporte."""
    merged: Dict[str, object] = empty_guide()
    for guide in guides:
        if not guide:
            continue
        for key, value in guide.items():
            if key not in merged:
                continue
            if isinstance(merged[key], list):
                for item in value if isinstance(value, list) else [value]:
                    if item and item not in merged[key]:
                        merged[key].append(item)  # type: ignore[union-attr]
            elif not merged[key] and value:
                merged[key] = value
    return merged


def guide_is_usable(guide: Dict[str, object]) -> bool:
    """Vrai si le guide donne au moins de quoi connecter un mineur.

    Un port seul ne suffit pas : `P2P port 17333` est le port pair-à-pair d'un
    projet sans piscine, et le retenir ferait croire qu'il y a une piscine à
    connecter. Il faut une adresse d'hôte, celle du champ `Pool` ou du stratum.
    """
    return bool(guide.get("pool") or guide.get("stratum"))


def find_guide_pages(html: str, base_url: str) -> List[str]:
    """Repère les pages d'un site susceptibles de décrire le minage."""
    if not html or not base_url:
        return []
    host = urlparse(base_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    pages: List[str] = []
    seen = set()
    for href in re.findall(r'href=["\']([^"\'#]+)["\']', html or "", re.I):
        target = urljoin(base_url, href.strip())
        parsed = urlparse(target)
        if parsed.scheme not in ("http", "https"):
            continue
        link_host = parsed.netloc.lower()
        if link_host.startswith("www."):
            link_host = link_host[4:]
        if link_host != host:
            continue
        key = target.lower().rstrip("/")
        if key in seen:
            continue
        if not GUIDE_HINT_RE.search(parsed.path + " " + parsed.query):
            continue
        seen.add(key)
        pages.append(target)
        if len(pages) >= 6:
            break
    return pages