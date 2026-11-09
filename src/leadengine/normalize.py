"""Normalisation helpers shared by the stages."""

import hashlib
import re
from urllib.parse import urlparse

LEGAL_FORMS = re.compile(
    r"\b(gmbh\s*&\s*co\.?\s*kg|gmbh\s*&\s*co|gmbh|ug|haftungsbeschr[aä]nkt|ag|kg|ohg|gbr|e\.?\s?k\.?|mbh|ltd|se|ek)\b\.?"
)
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def normalize_domain(url: str | None) -> str | None:
    """Return the lower-case host without 'www.', or None."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if "//" not in url:
        url = "//" + url
    host = urlparse(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def is_platform_domain(domain: str | None, platforms: list[str]) -> bool:
    """True if the domain is a shared host rather than the company's own site."""
    if not domain:
        return False
    return any(domain == p or domain.endswith("." + p) for p in platforms)


def normalize_name(name: str | None) -> str:
    """Lower-case name without legal forms or punctuation, for matching."""
    if not name or not isinstance(name, str):
        return ""
    text = name.lower().translate(UMLAUTS)
    text = LEGAL_FORMS.sub(" ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def company_id(domain: str | None, name: str | None, postcode: str | None) -> str:
    """Stable ID from the domain, or from name and postcode when there is none."""
    basis = domain or f"{normalize_name(name)}|{postcode or ''}"
    return "hv_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def valid_postcode(postcode, low: int, high: int) -> bool:
    text = str(postcode).strip() if postcode is not None else ""
    return bool(re.fullmatch(r"\d{5}", text)) and low <= int(text) <= high
