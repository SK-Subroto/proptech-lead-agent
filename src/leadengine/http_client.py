"""Polite HTTP fetching: disk cache, robots.txt, per-domain rate limit, retries."""

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    state: str  # ok | robots_blocked | timeout | error
    status: int | None = None
    final_url: str | None = None
    text: str | None = None
    from_cache: bool = False


class PoliteClient:
    def __init__(self, cache_dir: Path, ledger, user_agent: str, timeout: float = 20,
                 max_retries: int = 2, min_interval: float = 2.0, service: str = "crawl",
                 session=None, sleep=time.sleep, clock=time.monotonic):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.service = service
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.user_agent = user_agent
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_call: dict[str, float] = {}
        self._domain_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha1(url.encode()).hexdigest()}.json"

    def _domain_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._domain_locks.setdefault(host, threading.Lock())

    def _throttle(self, host: str) -> None:
        last = self._last_call.get(host)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[host] = self._clock()

    def _robots_for(self, parts) -> RobotFileParser | None:
        """Return a parser, or None when the site must be treated as disallowed."""
        host = parts.netloc
        if host in self._robots:
            return self._robots[host]

        parser = RobotFileParser()
        robots_url = f"{parts.scheme}://{host}/robots.txt"
        cached = self._read_cache(robots_url)
        status, body = None, ""
        if cached:
            status, body = cached.status, cached.text or ""
        else:
            self._throttle(host)
            self.ledger.record(self.service)
            try:
                resp = self.session.get(robots_url, timeout=self.timeout)
                status, body = resp.status_code, resp.text
                if status not in RETRYABLE_STATUS:
                    self._write_cache(robots_url, FetchResult(robots_url, "ok", status, robots_url, body))
            except requests.RequestException:
                self._robots[host] = None
                return None

        if status is not None and status >= 500:
            parser = None  # server trouble: stay away during this run
        elif status is not None and status >= 400:
            parser.parse([])  # no robots.txt: everything is allowed
        else:
            parser.parse(body.splitlines())
        self._robots[host] = parser
        return parser

    def _read_cache(self, url: str) -> FetchResult | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return FetchResult(**{**data, "from_cache": True})

    def _write_cache(self, url: str, result: FetchResult) -> None:
        payload = {**result.__dict__, "from_cache": False}
        self._cache_path(url).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def get(self, url: str) -> FetchResult:
        cached = self._read_cache(url)
        if cached:
            return cached

        parts = urlparse(url)
        with self._domain_lock(parts.netloc):
            parser = self._robots_for(parts)
            if parser is None or not parser.can_fetch(self.user_agent, url):
                return FetchResult(url, "robots_blocked")

            state = "error"
            for attempt in range(self.max_retries + 1):
                self._throttle(parts.netloc)
                self.ledger.record(self.service)
                try:
                    resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                except requests.Timeout:
                    state = "timeout"
                    self._sleep(2 ** attempt)
                    continue
                except requests.RequestException:
                    state = "error"
                    break

                if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                    self._sleep(2 ** attempt)
                    continue
                if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding
                ok = resp.status_code == 200
                result = FetchResult(url, "ok" if ok else "error", resp.status_code,
                                     resp.url, resp.text if ok else None)
                if resp.status_code not in RETRYABLE_STATUS:
                    self._write_cache(url, result)
                return result
            return FetchResult(url, state)
