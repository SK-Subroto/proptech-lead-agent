"""Counts external requests per service and month, and enforces budgets."""

import json
import threading
from datetime import date
from pathlib import Path


class BudgetExceeded(Exception):
    """Raised before a request that would exceed a service's monthly budget."""


class Ledger:
    def __init__(self, path: Path, today=date.today):
        self.path = Path(path)
        self._today = today
        self._lock = threading.Lock()
        self._data = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def _month(self) -> str:
        return self._today().strftime("%Y-%m")

    def count(self, service: str) -> int:
        with self._lock:
            return self._data.get(self._month(), {}).get(service, 0)

    def record(self, service: str, n: int = 1, budget: int | None = None) -> None:
        """Add n requests. With a budget, refuse if the total would pass it.

        Call this BEFORE sending the request, so a crash can never leave a
        billable request uncounted.
        """
        with self._lock:
            month = self._data.setdefault(self._month(), {})
            current = month.get(service, 0)
            if budget is not None and current + n > budget:
                raise BudgetExceeded(f"{service}: {current} of {budget} used this month")
            month[service] = current + n
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=1), encoding="utf-8")

    def summary(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))
