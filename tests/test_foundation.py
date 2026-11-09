from datetime import date

import pytest
import requests

from leadengine.config import load_context
from leadengine.http_client import PoliteClient
from leadengine.ledger import BudgetExceeded, Ledger
from leadengine.normalize import company_id, is_platform_domain, normalize_domain, normalize_name, valid_postcode


class FakeResponse:
    def __init__(self, status_code=200, text="", url="https://a.example/"):
        self.status_code, self.text, self.url = status_code, text, url
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def get(self, url, **_):
        self.calls.append(url)
        result = self.routes[url]
        if isinstance(result, Exception):
            raise result
        return result


def client(tmp_path, routes):
    session = FakeSession(routes)
    ledger = Ledger(tmp_path / "ledger.json")
    return PoliteClient(tmp_path / "cache", ledger, "TestBot/1.0", session=session, sleep=lambda s: None), session, ledger


def test_berlin_config_loads():
    ctx = load_context("Berlin")
    assert ctx.city["postcode"] == {"min": 10115, "max": 14199}
    assert ctx.city["boundary"]["expected_count"] == 12
    assert sum(k["weight"] for k in ctx.scoring["kpis"].values()) == 100
    assert ctx.pipeline["google"]["monthly_request_budget"] <= 1000


def test_unknown_city_stops_clearly():
    with pytest.raises(SystemExit):
        load_context("Atlantis")


def test_ledger_blocks_at_budget_and_persists(tmp_path):
    path = tmp_path / "l.json"
    ledger = Ledger(path)
    ledger.record("google", 2, budget=3)
    with pytest.raises(BudgetExceeded):
        ledger.record("google", 2, budget=3)
    assert Ledger(path).count("google") == 2


def test_ledger_resets_each_month(tmp_path):
    path = tmp_path / "l.json"
    Ledger(path, today=lambda: date(2026, 11, 30)).record("google", 5)
    assert Ledger(path, today=lambda: date(2026, 12, 1)).count("google") == 0


def test_client_uses_cache_on_second_call(tmp_path):
    c, session, _ = client(tmp_path, {
        "https://a.example/robots.txt": FakeResponse(404),
        "https://a.example/p": FakeResponse(200, "<html>hi</html>", "https://a.example/p"),
    })
    assert c.get("https://a.example/p").text == "<html>hi</html>"
    second = c.get("https://a.example/p")
    assert second.from_cache and session.calls.count("https://a.example/p") == 1


def test_client_respects_robots(tmp_path):
    c, session, _ = client(tmp_path, {
        "https://a.example/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /private"),
    })
    assert c.get("https://a.example/private/x").state == "robots_blocked"
    assert "https://a.example/private/x" not in session.calls


def test_robots_server_error_means_stay_away(tmp_path):
    c, session, _ = client(tmp_path, {"https://a.example/robots.txt": FakeResponse(503)})
    assert c.get("https://a.example/p").state == "robots_blocked"
    assert "https://a.example/p" not in session.calls


def test_timeout_is_retried_then_reported(tmp_path):
    c, session, ledger = client(tmp_path, {
        "https://a.example/robots.txt": FakeResponse(404),
        "https://a.example/p": requests.Timeout(),
    })
    assert c.get("https://a.example/p").state == "timeout"
    assert session.calls.count("https://a.example/p") == 3
    assert ledger.count("crawl") == 4


@pytest.mark.parametrize("url,expected", [
    ("https://www.Musterhaus.example/kontakt", "musterhaus.example"),
    ("musterhaus.example", "musterhaus.example"),
    ("http://sub.musterhaus.example:8080/", "sub.musterhaus.example"),
    ("", None),
    (None, None),
])
def test_normalize_domain(url, expected):
    assert normalize_domain(url) == expected


def test_platform_domain_includes_subdomains():
    assert is_platform_domain("de-de.facebook.com", ["facebook.com"])
    assert not is_platform_domain("myfacebook.com", ["facebook.com"])


def test_normalize_name_removes_legal_form_and_umlauts():
    assert normalize_name("Müller Hausverwaltung GmbH & Co. KG") == "mueller hausverwaltung"


def test_company_id_is_stable():
    assert company_id("a.example", "X", "10115") == company_id("a.example", "Y", "10999")
    assert company_id(None, "X GmbH", "10115") == company_id(None, "X", "10115")


@pytest.mark.parametrize("value,expected", [
    ("10115", True), (14199, True), ("10114", False), ("14200", False), ("ABCDE", False), (None, False),
])
def test_postcode_filter(value, expected):
    assert valid_postcode(value, 10115, 14199) is expected
