"""Pre-post quality gates. A failed gate blocks that format; it never posts a flawed one."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateResult:
    """Outcome of one gate."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class GateReport:
    """All gate results for one rendered item."""

    results: list[GateResult] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> GateResult:
        """Record a result and return it."""
        result = GateResult(name, passed, detail)
        self.results.append(result)
        return result

    def extend(self, other: GateReport) -> None:
        """Append another report's results."""
        self.results.extend(other.results)

    @property
    def passed(self) -> bool:
        """True when every gate passed."""
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[GateResult]:
        """Gates that failed."""
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        """One-line description such as ``12/12 passed``."""
        failed = ", ".join(f"{r.name} ({r.detail})" for r in self.failures)
        n = len(self.results)
        return f"{n - len(self.failures)}/{n} passed" + (f"; failed: {failed}" if failed else "")
