"""Nettoyage des URL extraites des annonces BitcoinTalk.

Les annonces sont du texte brut colle : une URL se colle au mot suivant, un
emoji suit le lien, deux URL se suivent sans espace. Ce module transforme une
fragment de texte brut en URL réellement joignable, ou la rejette.
"""

import re
import string
from typing import List
from urllib.parse import urlsplit

# Caractères autorisés par la RFC 3986. Le premier caractère hors de cet
# ensemble termine l'URL : c'est ce qui coupe les collages.
_URL_CHARS = frozenset(string.ascii_letters + string.digits + "-._~:/?#[]@!$&'()*+,;=%")

# Ponctuation de phrase collée en fin de lien.
_TRAILING = ".,;:!?)]}'\"*>"

# Deux URL peuvent se suivre sans espace, y compris avec un autre schéma au
# milieu (`stratum+tcp://`) : le découpage part de chaque `://`.
_SCHEME_MARKER_RE = re.compile(r"://")
_SCHEME_TAIL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*$")

# Mot unique collé à la fin d'un chemin (`bitfuc` + `https`, `Mempool` +
# `https`). Deux majuscules dans le même mot (`0xabcPresale`) prouveraient au
# contraire qu'on est au milieu d'une adresse : l'URL doit rester intacte.
_GLUED_WORD_RE = re.compile(r"[A-Za-z][a-z0-9]*(?:[.][a-z0-9]+)*")

# Schémas réellement présents dans une annonce. Un mot collé finit aussi par
# `https` (`bitfuchttps://`) : c'est cette liste qui distingue une seconde URL
# d'un mot de l'agenda qui s'y est accroché.
_KNOWN_SCHEMES = frozenset({
    "http", "https", "ws", "wss", "ftp", "ftps", "sftp", "irc", "ircs",
    "stratum+tcp", "stratum+ssl", "git+https", "git+ssh",
})


def _scheme_start(block: str, marker_start: int) -> int:
    """Position de départ du candidat qui se termine par un `://`.

    Seul un schéma connu ouvre un candidat. `bitfuchttps://bitfuc.com` est une
    seule URL dont le nom de dépôt finit par `https`, pas deux URL : couper
    là-dessus mutilerait le dépôt. Un `://` sans schéma reconnu est laissé dans
    le candidat courant, qui sera rejeté ou tronqué plus loin.
    """
    scheme = _SCHEME_TAIL_RE.search(block, 0, marker_start)
    if not scheme:
        return -1
    tail = scheme.group(0)
    if tail.lower() in _KNOWN_SCHEMES:
        return scheme.start()
    # `bitfuchttps://bitfuc.com` : le nom de dépôt finit aussi par `https`.
    # Seul un mot unique collé à la racine distingue ce cas d'une vraie URL,
    # car `topic=1.0GitHub:https://…` contient lui aussi un suffixe `https`.
    for name in _KNOWN_SCHEMES:
        if len(tail) > len(name) and tail.lower().endswith(name):
            word = tail[: len(tail) - len(name)]
            if word and _GLUED_WORD_RE.fullmatch(word):
                return scheme.start() + len(word)
    return -1


_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")

_GITHUB_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([^/?#\s]*)(?:/([^/?#\s]*))?", re.IGNORECASE
)
_GITHUB_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_GITHUB_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")

# Chemins de github.com qui ne sont pas un dépôt.
_GITHUB_RESERVED = frozenset({
    "about", "apps", "codespaces", "collections", "contact", "dashboard", "events",
    "explore", "features", "issues", "join", "login", "logout", "marketplace",
    "new", "notifications", "organizations", "orgs", "pricing", "pulls", "readme",
    "search", "security", "settings", "site", "sponsors", "stars", "topics",
    "trending", "users",
})

_WHITEPAPER_WORDS = re.compile(
    r"white[\s_.-]?paper|lite[\s_.-]?paper|tech(?:nical)?[\s_.-]?paper|"
    r"light[\s_.-]?paper|documentation|docs?[/._-]",
    re.IGNORECASE,
)
_DOCUMENT_EXT_RE = re.compile(r"\.(?:pdf|md|txt|docx?|rtf|odt|epub)(?:$|[?#])", re.IGNORECASE)

# Après une URL, l'annonce enchaîne sur un libellé de rubrique, capitalisé
# comme un début de phrase : `…/Source:`, `…/Mempoolhttps://…`, `/WhitepaperBlock`.
# Le chemin qui commence par l'un de ces mots n'appartient pas au lien.
_PATH_LABEL_WORDS = (
    "source", "src", "explorer", "whitepaper", "litepaper", "roadmap", "specs",
    "specification", "algorithm", "algo", "blockchain", "exchange", "pool",
    "mempool", "website", "github", "gitlab", "discord", "telegram", "twitter",
    "wallet", "presale", "mining", "masternode", "contract", "audit", "chart",
    "price", "team", "faq", "download", "downloads", "release", "releases",
    "buy", "note", "notes", "details", "info", "document", "documents",
    "block", "this", "notable", "discord", "t.me",
)
_GLUED_LABEL_RE = re.compile(rf"({'|'.join(_PATH_LABEL_WORDS)})$", re.IGNORECASE)


def _strip_glued_label(segment: str) -> str:
    """Retire la rubrique d'annonce agglutinée à la fin d'un segment.

    `explorerWhitepaper` devient `explorer`, `Source` devient vide. Seul un mot
    capitalisé est retiré : `/downloads/` est un chemin du site et survit.
    """
    if _DOCUMENT_EXT_RE.search(segment):
        return segment
    current = segment
    # `0xabc…UArzhttps` puis `/Mempool` : le second lien commence par le mot
    # `https`, collé à la fin du segment précédent. Le chemin s'arrête là, puis
    # la rubrique d'annonce qui precedait ce second lien tombe à son tour.
    for name in ("https", "http"):
        if current.lower().endswith(name) and len(current) > len(name):
            current = current[: -len(name)]
    label = _GLUED_LABEL_RE.search(current)
    if label and label.group(1)[:1].isupper():
        current = current[: label.start(1)]
    return current

# Ce que « site web » ne doit jamais designer : le fil lui-même, un réseau
# social, un explorateur ou un frontal de pool sont des liens de service.
_NOT_A_SITE = frozenset({
    "bitcointalk.org", "discord.com", "discord.gg", "discordapp.com", "t.me",
    "telegram.me", "twitter.com", "x.com", "reddit.com", "youtube.com",
    "youtu.be", "coingecko.com", "github.com", "gitlab.com", "medium.com",
    "facebook.com", "linkedin.com", "steemit.com", "notion.so", "docs.google.com",
    "solscan.io", "bscscan.io", "etherscan.io", "blockchair.com",
    "chainz.cryptoid.info", "altexplorer.online", "miningpoolstats.stream",
})

# Sous-domaines d'infrastructure : un pool ou un explorateur n'est pas le site
# du projet, même si l'annonce n'en donne pas d'autre.
_INFRA_LABELS = frozenset({
    "pool", "pools", "explorer", "explorers", "scan", "node", "nodes", "rpc",
    "api", "stats", "status",
})

MAX_URL_LENGTH = 512

# Une URL qui a été amputée d'un mot collé doit être confirmée par le réseau.
# Chaque frontière de mot aggénère une variante, de la plus longue à la plus
# courte : la vérification s'arrête à la première qui répond.
_VARIANT_LIMIT = 6


def link_variants(url: str) -> List[str]:
    """Variantes plausibles d'une URL dont un mot reste peut-être collé.

    `https://xcoinproject.com/whitepaperWho` peut désigner `/whitepaper` suivi
    du mot `Who`, ou une page qui s'appelle réellement `whitepaperWho`. Aucun
    lexique ne tranche : les frontières de mot de la dernière composante
    donnent les candidats, du plus long au plus court. Sans ambiguïté
    détectable, la liste ne contient que l'URL elle-même.
    """
    cleaned = clean_url(url)
    if not cleaned:
        return []
    parts = urlsplit(cleaned)
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return [cleaned]
    last = segments[-1]
    words = _word_boundaries(last)
    if len(words) < 2:
        return [cleaned]
    origin = f"{parts.scheme}://{parts.netloc}"
    prefix = "/".join(segments[:-1])
    variants = [cleaned]
    for index in range(len(words) - 1, 0, -1):
        head = "".join(words[:index])
        kept = f"{prefix}/{head}" if prefix else head
        variants.append(f"{origin}/{kept}")
    if prefix:
        variants.append(f"{origin}/{prefix}")
    if len(segments) > 1:
        variants.append(origin)
    unique: List[str] = []
    for variant in variants:
        candidate = clean_url(variant)
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique[:_VARIANT_LIMIT]


def _word_boundaries(segment: str) -> List[str]:
    """Découpe une composante de chemin à ses frontières de mot.

    Seule une majuscule placée après une minuscule ouvre un nouveau mot : c'est
    la signature d'un texte d'annonce agglutiné à un chemin. Les tirets et les
    points sont laissés entiers, sinon `Zagros-Wallet` — un vrai nom de dépôt —
    deviendrait `Zagros`. Un nom de fichier garde son extension et ne donne
    qu'un seul mot.
    """
    if _DOCUMENT_EXT_RE.search(segment):
        return [segment]
    words: List[str] = []
    current = ""
    for index, char in enumerate(segment):
        previous = segment[index - 1] if index else ""
        if (
            char.isupper()
            and index > 0
            and current
            and not previous.isupper()
            and previous not in "-._~"
        ):
            words.append(current)
            current = char
            continue
        current += char
    if current:
        words.append(current)
    return words


def _valid_host(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    if host in ("localhost",):
        return True
    if _IPV4_RE.fullmatch(host):
        return True
    host = host[:-1] if host.endswith(".") else host
    labels = host.split(".")
    if len(labels) < 2:
        return False
    tld = labels[-1]
    # Un TLD est alphabétique ou punycode. Un libellé qui mêle majuscules et
    # texte est un mot collé au nom de domaine, pas un domaine.
    if not (tld.isalpha() or tld.startswith("xn--")):
        return False
    return all(_HOST_RE.fullmatch(label) for label in labels)


_LABEL_PREFIX_RE = re.compile(r"[a-z0-9-]*")


def _repair_host(head: str) -> str:
    """Retire le mot collé au domaine par l'annonce.

    `noj.viporlab.netStratum:Code:` désigne le site `noj.viporlab.net`, le
    reste étant du texte de l'annonce. Chaque libellé est ramené à son plus
    long préfixe valide ; la réparation s'arrête au premier libellé vidé.
    """
    labels = head.split(".")
    repaired: List[str] = []
    for label in labels:
        prefix = _LABEL_PREFIX_RE.match(label).group(0)
        if not prefix:
            break
        repaired.append(prefix)
    return ".".join(repaired)


def _trim_glued_host(url: str) -> str:
    """Répare l'hôte seulement s'il n'est pas valide tel quel.

    `https://example.com:8080/` est valide et doit garder son port ; seule une
    URL déjà rejetée par `_valid_host` est soumise à la réparation.
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    head, slash, path = rest.partition("/")
    host = head.rsplit("@", 1)[-1]
    if not host.startswith("["):
        host = host.split(":", 1)[0]
    if _valid_host(host):
        return url
    repaired = _repair_host(head)
    if not _valid_host(repaired):
        return url
    if slash and path:
        # `…netStratum:Code:stratum+tcp://lab.viporlab.net:5090Region:` : le
        # chemin appartient à un autre hôte, c'est un second lien de service
        # collé au premier. Le garder fabriquerait une URL qui n'existe pas.
        other = path.lstrip("/").split("/", 1)[0].split(":", 1)[0].lower()
        if other != repaired and _valid_host(other):
            slash, path = "", ""
    return scheme + sep + repaired + (slash + path if slash else "")


def clean_url(raw: str) -> str:
    """Retourne une URL absolue et joignable, ou une chaîne vide."""
    token = str(raw or "").strip()
    if not token:
        return ""
    if token.lower().startswith("www."):
        token = "https://" + token
    kept: List[str] = []
    for char in token:
        if char in _URL_CHARS:
            kept.append(char)
        else:
            break
    token = "".join(kept).rstrip(_TRAILING)
    # Un filet de séparation ou un crochet marquent la fin du lien :
    # `…genesis------[UPDATE:`. Un tiret isolé appartient en revanche au chemin.
    token = re.sub(r"[-_]{2,}$", "", token)
    token = token[: token.find("[")] if "[" in token.split("/", 1)[-1] else token
    token = _trim_glued_host(token)
    # Un `://` résiduel dans le chemin signifie qu'une URL a été collée à la
    # précédente : celle-ci s'arrête là, l'autre est sans valeur ici.
    residual = token.find("://", token.find("://") + 3)
    if residual > 0:
        token = token[:residual]
    if len(token) > MAX_URL_LENGTH:
        token = token[:MAX_URL_LENGTH].rstrip(_TRAILING)
    scheme, _, rest = token.partition("://")
    if not rest or scheme.lower() not in ("http", "https"):
        return ""
    netloc = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host[1:host.find("]")] if "]" in host else ""
    else:
        host = host.split(":", 1)[0]
    if not _valid_host(host):
        return ""
    try:
        parts = urlsplit(token)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    if parts.path:
        # Deux URL collées laissent un `//` au milieu du chemin.
        path = re.sub(r"/{2,}", "/", parts.path)
        # ...et le domaine de la seconde se retrouve dans le chemin de la
        # première : `comWhitepaperhttps://com/whitepaper` → `/whitepaper`.
        host = (parts.hostname or "").lower()
        marker = host[4:] if host.startswith("www.") else host
        if marker:
            glued = re.search(rf"/{re.escape(marker)}/", path, re.IGNORECASE)
            if glued:
                path = path[glued.end() - 1:]
        # `…/Source:`, `…/explorerWhitepaper` : la dernière composante est une
        # rubrique de l'annonce, pas un chemin. Un mot capitalisé la distingue
        # d'un vrai segment, et `/downloads/` reste intact. Sur github.com le
        # chemin est un dépôt (`CmusicWallet`) et reste intact aussi.
        segments = [s for s in path.split("/") if s]
        if segments and host not in ("github.com", "www.github.com"):
            trimmed = _strip_glued_label(segments[-1])
            if trimmed != segments[-1] and trimmed.lower() in _INFRA_LABELS:
                trimmed = ""
            if trimmed != segments[-1]:
                segments = segments[:-1] + [trimmed] if trimmed else segments[:-1]
        path = ("/" + "/".join(segments)) if segments else ""
        token = f"{parts.scheme}://{parts.netloc}{path}"
        if parts.query:
            token += f"?{parts.query}"
        if parts.fragment:
            token += f"#{parts.fragment}"
    return token


def _split_runs(text: str) -> List[str]:
    """Isole les zones denses de caractères, là où des URL peuvent se suivre."""
    blocks = re.findall(r"[^\s<>\"']+", text or "")
    return [block for block in blocks if "://" in block or block.lower().startswith("www.")]


def split_candidates(text: str) -> List[str]:
    """Découpe le texte en fragments d'URL, y compris les URL collées."""
    segments: List[str] = []
    for block in _split_runs(text):
        starts = sorted({_scheme_start(block, m.start()) for m in _SCHEME_MARKER_RE.finditer(block)})
        if not starts:
            segments.append(block)
            continue
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(block)
            candidate = block[start:end]
            if candidate:
                segments.append(candidate)
    return segments


def canonical_github(raw: str) -> str:
    """Réduit un lien GitHub à l'adresse canonique du dépôt source.

    Le lien stocké doit pointer vers le code, pas vers une annexe de release,
    un fichier ou un profil. Un chemin de github.com qui ne désigne pas un
    dépôt renvoie une chaîne vide.
    """
    for candidate in _github_candidates(raw):
        match = _GITHUB_RE.match(candidate)
        if not match:
            continue
        owner_raw, repo_raw = match.group(1), match.group(2) or ""
        canonical = _github_owner_repo(owner_raw, repo_raw)
        if canonical:
            return canonical
    return ""


def _github_candidates(raw: str) -> List[str]:
    """Fragments d'une annonce pouvant contenir le dépôt GitHub du projet.

    `…/bitfuchttps://bitfuc.com` et `topic=1.0GitHub:https://github.com/o/r`
    contiennent chacun une URL collée à la précédente : le dépôt n'est pas
    forcément dans le premier fragment.
    """
    fragments = split_candidates(str(raw or ""))
    if not fragments:
        fragments = [str(raw or "").strip()]
    cleaned = clean_url(fragments[0])
    return [cleaned or fragments[0]] + fragments[1:]


def _github_owner_repo(owner_raw: str, repo_raw: str) -> str:
    """Adresse canonique d'un couple (propriétaire, dépôt), ou chaîne vide."""
    owner_match = _GITHUB_OWNER_RE.fullmatch(owner_raw or "")
    if not owner_match:
        return ""
    owner = owner_match.group(0)
    if owner.lower() in _GITHUB_RESERVED:
        return ""

    repo_match = _GITHUB_REPO_RE.match(repo_raw or "")
    if not repo_match:
        return ""
    repo = repo_match.group(0)
    # `.git` souvent collé à la suite : `aperod-node.gitcd`.
    head, dot, tail = repo.partition(".git")
    if dot and tail:
        repo = head
    if not repo:
        return ""
    return f"https://github.com/{owner}/{repo}"


def github_slug(raw: str) -> tuple:
    """Renvoie (propriétaire, dépôt) d'un lien GitHub, sinon ("", "")."""
    canonical = canonical_github(raw)
    if not canonical:
        return "", ""
    parts = canonical.split("/")
    return parts[3], parts[4]


def is_whitepaper_url(url: str) -> bool:
    """Un document announced comme tel : mot-clé dans le chemin, ou extension."""
    parts = urlsplit(url)
    haystack = f"{parts.path}{parts.query}"
    if _DOCUMENT_EXT_RE.search(haystack):
        return True
    return bool(_WHITEPAPER_WORDS.search(haystack))


def is_project_site(url: str) -> bool:
    """Un lien de service n'est pas le site du projet."""
    host = host_of(url)
    if not host:
        return False
    if host in _NOT_A_SITE:
        return False
    labels = host.split(".")
    if len(labels) > 2 and labels[0] in _INFRA_LABELS:
        return False
    return True


def host_of(url: str) -> str:
    """Hôte en minuscules, ou chaîne vide si l'URL est inexploitable."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def looks_like_glued_word(url: str) -> bool:
    """Un libellé d'hôte qui mélange majuscules et texte est un mot collé.

    `noj.viporlab.netStratum:Code:` n'est pas un domaine : c'est le site
    `noj.viporlab.net` suivi des mots de l'annonce.
    """
    host = host_of(url)
    return bool(host) and not host.islower()