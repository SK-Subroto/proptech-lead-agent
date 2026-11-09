"""Stage 2: fetch each company's key pages and save clean text."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import pandas as pd
import trafilatura
from bs4 import BeautifulSoup

from leadengine.http_client import PoliteClient
from leadengine.normalize import normalize_domain
from leadengine.store import blank, read_candidates, write_json

log = logging.getLogger("leadengine")

IMPRESSUM = re.compile(r"impressum|imprint|legal[-_ ]?notice", re.I)
# Priority order: pages most likely to state KPIs come first.
PAGE_KINDS = [
    ("ueber_uns", re.compile(r"[üu]e?ber[-_ ]?uns|about|unternehmen|philosophie|geschichte|historie", re.I)),
    ("leistungen", re.compile(r"leistung|angebot|services?\b|hausverwaltung|mietverwaltung|weg[-_ ]?verwaltung", re.I)),
    ("team", re.compile(r"team|ansprechpartner|mitarbeiter", re.I)),
    ("karriere", re.compile(r"karriere|jobs?\b|stellen|bewerb", re.I)),
    ("kontakt", re.compile(r"kontakt|contact", re.I)),
]
SKIP_LINK = re.compile(r"datenschutz|privacy|cookie|agb|\.(pdf|jpe?g|png|gif|zip|docx?)($|\?)", re.I)
BOILERPLATE = re.compile(r"haftung|urheberrecht|datenschutz|cookie|streitbeilegung|schlichtung|alle rechte", re.I)
RELEVANT_LINE = re.compile(
    r"einheiten|wohnungen|objekte|mitarbeit|gegr[üu]ndet|seit\s+\d{4}|\bjahre|standort|niederlassung|"
    r"portal|\bapp\b|digital|software|\bweg\b|mietverwaltung|sondereigentum|gewerbe",
    re.I,
)
IMPRESSUM_CHAR_LIMIT = 700


def decode_cfemail(hex_string: str) -> str:
    """Cloudflare hides addresses as XOR-encoded hex."""
    key = int(hex_string[:2], 16)
    return "".join(chr(int(hex_string[i:i + 2], 16) ^ key) for i in range(2, len(hex_string), 2))


def find_mailtos(soup: BeautifulSoup) -> list[str]:
    found = set()
    for a in soup.select('a[href^="mailto:"]'):
        found.add(a["href"][7:].split("?")[0].strip().lower())
    for tag in soup.select("[data-cfemail]"):
        try:
            found.add(decode_cfemail(tag["data-cfemail"]).lower())
        except (ValueError, IndexError):
            continue
    return sorted(found)


def plain_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n").replace("\xad", "")  # soft hyphens split words invisibly
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def main_text(html: str, fallback: str) -> str:
    extracted = trafilatura.extract(html, include_tables=True, favor_recall=True) or ""
    return extracted if len(extracted) >= 200 else fallback


def discover_links(html: str, base_url: str) -> dict[str, str]:
    """Return {kind: url} for the Impressum and the relevant internal pages."""
    soup = BeautifulSoup(html, "lxml")
    site = normalize_domain(base_url)
    candidates: dict[str, list[str]] = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(url).scheme not in ("http", "https") or normalize_domain(url) != site:
            continue
        haystack = f"{urlparse(url).path} {a.get_text(' ', strip=True)}"
        if SKIP_LINK.search(url):
            continue
        if IMPRESSUM.search(haystack):
            candidates.setdefault("impressum", []).append(url)
            continue
        for kind, pattern in PAGE_KINDS:
            if pattern.search(haystack):
                candidates.setdefault(kind, []).append(url)
                break
    return {kind: min(urls, key=len) for kind, urls in candidates.items()}


def select_pages(links: dict[str, str], max_relevant: int) -> list[tuple[str, str]]:
    chosen = [("impressum", links["impressum"])] if "impressum" in links else []
    relevant = [(kind, links[kind]) for kind, _ in PAGE_KINDS if kind in links]
    return chosen + relevant[:max_relevant]


def trim_pages(pages: list[dict], limit: int) -> str:
    """Build the Markdown file, keeping the lines most likely to state KPIs."""
    entries = []
    for p_index, page in enumerate(pages):
        used = 0
        for l_index, line in enumerate(page["text"].splitlines()):
            if len(line) < 3 or BOILERPLATE.search(line):
                continue
            if page["kind"] == "impressum":
                used += len(line) + 1
                if used > IMPRESSUM_CHAR_LIMIT:
                    break
            score = 1 if RELEVANT_LINE.search(line) else 0
            entries.append((score, p_index, l_index, line))

    headers = sum(len(p["url"]) + len(p["kind"]) + 12 for p in pages) + 40
    budget = max(limit - headers, 0)
    kept, used = [], 0
    for entry in sorted(entries, key=lambda e: (-e[0], e[1], e[2])):
        if used + len(entry[3]) + 1 > budget:
            continue
        kept.append(entry)
        used += len(entry[3]) + 1

    sections = []
    for p_index, page in enumerate(pages):
        lines = [e[3] for e in sorted(kept, key=lambda e: e[2]) if e[1] == p_index]
        sections.append(f"## {page['kind']}\nURL: {page['url']}\n\n" + "\n".join(lines))
    return "\n\n".join(sections)


def fetch_page(client: PoliteClient, kind: str, url: str) -> dict | None:
    result = client.get(url)
    if result.state != "ok" or not result.text:
        return None
    soup = BeautifulSoup(result.text, "lxml")
    mailtos = find_mailtos(soup)
    raw_text = plain_text(soup)
    text = raw_text if kind == "impressum" else main_text(result.text, raw_text).replace("\xad", "")
    return {"kind": kind, "url": result.final_url or url, "text": text, "full_text": raw_text, "mailtos": mailtos}


def crawl_company(company: dict, client: PoliteClient, ctx) -> dict:
    company_id = company["company_id"]
    settings = ctx.pipeline["crawl"]
    if blank(company.get("website")):
        return {"company_id": company_id, "crawl_status": "no_website", "pages_fetched": 0}

    website = company["website"]
    if "//" not in website:
        website = "https://" + website
    parts = urlparse(website)
    home_url = f"{parts.scheme}://{parts.netloc}/"

    result = client.get(home_url)
    if result.state != "ok" and website != home_url:
        result = client.get(website)
    if result.state != "ok" or not result.text:
        return {"company_id": company_id, "crawl_status": result.state, "pages_fetched": 0}

    base = result.final_url or home_url
    soup = BeautifulSoup(result.text, "lxml")
    mailtos = find_mailtos(soup)
    raw_text = plain_text(BeautifulSoup(result.text, "lxml"))
    pages = [{"kind": "home", "url": base, "text": main_text(result.text, raw_text).replace("\xad", ""),
              "full_text": raw_text, "mailtos": mailtos}]

    links = discover_links(result.text, base)
    selected = select_pages(links, settings["max_relevant_pages"])
    if "impressum" not in links:
        selected.insert(0, ("impressum", urljoin(base, "/impressum")))
    for kind, url in selected:
        page = fetch_page(client, kind, url)
        if page:
            pages.append(page)

    write_json(ctx.paths.pages / f"{company_id}.json",
               {"company_id": company_id, "website": base, "pages": pages})
    markdown = f"# {company_id}\n\n" + trim_pages(
        [{**p, "text": p["text"]} for p in pages], settings["max_chars_per_company"])
    (ctx.paths.pages / f"{company_id}.md").write_text(markdown, encoding="utf-8")
    return {"company_id": company_id, "crawl_status": "ok", "pages_fetched": len(pages),
            "final_url": base}


def run(ctx) -> dict:
    candidates = read_candidates(ctx).to_dict("records")
    http = ctx.pipeline["http"]
    client = PoliteClient(
        ctx.paths.raw_html, ctx.ledger, http["user_agent"], http["timeout_seconds"],
        http["max_retries"], ctx.pipeline["crawl"]["min_seconds_between_requests"],
    )

    def task(company):
        try:
            return crawl_company(company, client, ctx)
        except Exception as exc:  # one broken site must not stop the run
            log.warning("crawl failed for %s: %s", company["company_id"], exc)
            return {"company_id": company["company_id"], "crawl_status": "error", "pages_fetched": 0}

    with ThreadPoolExecutor(ctx.pipeline["crawl"]["workers"]) as pool:
        results = list(pool.map(task, candidates))

    status = pd.DataFrame(results)
    status.to_csv(ctx.paths.processed / "crawl_status.csv", index=False, encoding="utf-8-sig")
    counts = status["crawl_status"].value_counts().to_dict()
    return {"companies": len(status), **{f"status_{k}": int(v) for k, v in counts.items()},
            "pages_fetched": int(status["pages_fetched"].sum()), "requests_this_month": ctx.ledger.count("crawl")}
