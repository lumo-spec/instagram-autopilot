"""Monthly cost cap for paid art calls.

Every paid call is first cleared with :meth:`Ledger.ensure` (refused when it could take the
month over the cap) and then recorded with :meth:`Ledger.add`. Costs are the configured
per-call estimates, rounded up, so the real bill should stay below the ledger.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from autopilot.errors import BudgetError


class Ledger:
    """Running total per calendar month (local time) in a small JSON file."""

    def __init__(
        self,
        path: Path,
        cap_gbp: float,
        usd_to_gbp: float,
        tz: ZoneInfo,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path, self.cap_gbp, self.usd_to_gbp, self.tz, self._now = (
            path,
            cap_gbp,
            usd_to_gbp,
            tz,
            now,
        )

    def month(self) -> str:
        """Current month, e.g. ``2026-10``."""
        return self._now().astimezone(self.tz).strftime("%Y-%m")

    def _read(self) -> dict[str, dict[str, float]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            months = data.get("months", {}) if isinstance(data, dict) else {}
            return {str(k): dict(v) for k, v in months.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            return {}

    def spent_gbp(self) -> float:
        """Estimated spend this month."""
        return float(self._read().get(self.month(), {}).get("gbp", 0.0))

    def left_gbp(self) -> float:
        """What is left under the cap this month."""
        return max(0.0, self.cap_gbp - self.spent_gbp())

    def gbp(self, usd: float) -> float:
        """Convert an estimate in US dollars."""
        return usd * self.usd_to_gbp

    def ensure(self, usd: float) -> None:
        """Raise BudgetError when spending ``usd`` more could go over the cap."""
        if self.spent_gbp() + self.gbp(usd) > self.cap_gbp + 1e-9:
            raise BudgetError(
                f"monthly art budget reached: £{self.spent_gbp():.2f} of £{self.cap_gbp:.2f} "
                f"spent in {self.month()}"
            )

    def add(self, usd: float, kind: str) -> None:
        """Record one paid call (``kind``: images or checks)."""
        months = self._read()
        entry = months.setdefault(self.month(), {"gbp": 0.0, "images": 0, "checks": 0})
        entry["gbp"] = round(float(entry.get("gbp", 0.0)) + self.gbp(usd), 4)
        entry[kind] = int(entry.get(kind, 0)) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"months": months}, indent=1) + "\n", encoding="utf-8")
