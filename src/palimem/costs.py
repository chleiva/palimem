"""LLM cost ledger with a hard spending cap.

Every paid model call must be authorised first::

    ledger = CostLedger()
    with ledger.authorize("openai.gpt-oss-20b-1:0", input_tokens=900,
                          max_output_tokens=800, purpose="extractor smoke test") as r:
        resp = call_model(...)
        r.commit(input_tokens=resp.in_tokens, output_tokens=resp.out_tokens)

``authorize`` refuses (raises :class:`BudgetExceeded`) when the worst-case cost of the
call, added to everything already spent or reserved, would exceed the cap, and refuses
(:class:`UnknownModel`) any model that is not in ``prices.json``. The cap is a module
constant, ``CAP_USD``; a ledger can be given a *lower* cap (tests, sub-budgets) but never a
higher one.

Ledger format: append-only JSONL, one record per line, kinds ``reserve`` / ``commit`` /
``cancel``. Exposure = committed cost + open reservations (at their worst-case estimate).
A reservation that is neither committed nor cancelled (crash, exception after the request
was sent) stays charged at its estimate, which is the conservative reading for a hard cap.

Standard library only.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

try:  # POSIX file locking; absent on Windows, where the ledger is single-process only
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

CAP_USD = 20.0
_EPS = 1e-9
PRICES_PATH = Path(__file__).with_name("prices.json")


class CostError(Exception):
    """Base class for ledger refusals."""


class BudgetExceeded(CostError):
    """The call would push total spend over the cap."""


class UnknownModel(CostError):
    """The model has no entry in the price table."""


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float
    verified: bool
    source: str


def load_prices(path: str | os.PathLike[str] | None = None) -> dict[str, Price]:
    raw = json.loads(Path(path or PRICES_PATH).read_text())
    out = {}
    for model, p in raw["models"].items():
        out[model] = Price(
            float(p["input_per_mtok"]),
            float(p["output_per_mtok"]),
            bool(p.get("verified", False)),
            str(p.get("source", "")),
        )
    return out


def cost_usd(price: Price, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1e6


def rough_token_count(text: str) -> int:
    """Deliberately pessimistic token estimate (about 3 characters per token)."""
    return len(text) // 3 + 1


def default_ledger_path() -> Path:
    return Path(os.environ.get("PALIMEM_LEDGER", "ledger/ledger.jsonl"))


class Reservation:
    """Handle for one authorised call. Use as a context manager or call commit/cancel."""

    def __init__(self, ledger: CostLedger, rid: str, model: str, purpose: str,
                 input_tokens: int, max_output_tokens: int, estimate_usd: float):
        self._ledger = ledger
        self.id = rid
        self.model = model
        self.purpose = purpose
        self.input_tokens = input_tokens
        self.max_output_tokens = max_output_tokens
        self.estimate_usd = estimate_usd
        self.closed = False

    def commit(self, input_tokens: int, output_tokens: int) -> float:
        """Record the actual usage. Returns the cost. Raises BudgetExceeded *after* recording
        if actual spend pushed the total over the cap (the money is already spent)."""
        self._require_open()
        self.closed = True
        return self._ledger._commit(self, input_tokens, output_tokens)

    def cancel(self) -> None:
        """Release the reservation. Only call this if no request was sent."""
        self._require_open()
        self.closed = True
        self._ledger._append({"kind": "cancel", "id": self.id})

    def _require_open(self) -> None:
        if self.closed:
            raise CostError(f"reservation {self.id} is already closed")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if not self.closed:
            # Unknown outcome: charge the worst case rather than silently freeing budget.
            self.closed = True
            self._ledger._commit(self, self.input_tokens, self.max_output_tokens,
                                 note="unfinalised reservation charged at estimate", enforce=False)


class CostLedger:
    def __init__(self, path: str | os.PathLike[str] | None = None, cap_usd: float = CAP_USD,
                 prices: dict[str, Price] | None = None):
        if cap_usd > CAP_USD + _EPS:
            raise ValueError(f"cap_usd {cap_usd} exceeds the hard cap CAP_USD={CAP_USD}")
        if cap_usd <= 0:
            raise ValueError("cap_usd must be positive")
        self.path = Path(path) if path else default_ledger_path()
        self.cap_usd = cap_usd
        self.prices = prices if prices is not None else load_prices()

    # ---- public API ----
    def authorize(self, model: str, input_tokens: int, max_output_tokens: int, purpose: str) -> Reservation:
        if not purpose or not purpose.strip():
            raise CostError("purpose is required")
        if input_tokens < 0 or max_output_tokens < 0:
            raise CostError("token counts must be non-negative")
        price = self.prices.get(model)
        if price is None:
            raise UnknownModel(f"model {model!r} is not in the price table; refusing")
        estimate = cost_usd(price, input_tokens, max_output_tokens)
        rid = uuid.uuid4().hex[:12]
        with self._locked():
            state = self._state()
            if state["exposure_usd"] + estimate > self.cap_usd + _EPS:
                raise BudgetExceeded(
                    f"refused: worst-case ${estimate:.4f} for {model} would bring exposure to "
                    f"${state['exposure_usd'] + estimate:.4f} (spent ${state['spent_usd']:.4f}, "
                    f"reserved ${state['reserved_usd']:.4f}, cap ${self.cap_usd:.2f})"
                )
            self._append({"kind": "reserve", "id": rid, "model": model, "purpose": purpose,
                          "input_tokens": input_tokens, "max_output_tokens": max_output_tokens,
                          "estimate_usd": estimate}, locked=True)
        return Reservation(self, rid, model, purpose, input_tokens, max_output_tokens, estimate)

    def estimate(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.prices.get(model)
        if price is None:
            raise UnknownModel(f"model {model!r} is not in the price table")
        return cost_usd(price, input_tokens, output_tokens)

    def summary(self) -> dict[str, Any]:
        with self._locked():
            s = self._state()
        s["cap_usd"] = self.cap_usd
        s["remaining_usd"] = self.cap_usd - s["exposure_usd"]
        return s

    def remaining_usd(self) -> float:
        return float(self.summary()["remaining_usd"])

    # ---- internals ----
    def _commit(self, r: Reservation, input_tokens: int, output_tokens: int,
                note: str | None = None, enforce: bool = True) -> float:
        price = self.prices[r.model]
        cost = cost_usd(price, input_tokens, output_tokens)
        rec = {"kind": "commit", "id": r.id, "model": r.model, "purpose": r.purpose,
               "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}
        if output_tokens > r.max_output_tokens or input_tokens > r.input_tokens:
            rec["over_estimate"] = True
        if note:
            rec["note"] = note
        with self._locked():
            self._append(rec, locked=True)
            total = self._state()["exposure_usd"]
        if enforce and total > self.cap_usd + _EPS:
            raise BudgetExceeded(f"actual spend recorded; total ${total:.4f} now exceeds cap ${self.cap_usd:.2f}")
        return cost

    def _state(self) -> dict[str, Any]:
        open_res: dict[str, float] = {}
        spent = 0.0
        n_calls = 0
        by_model: dict[str, float] = {}
        if self.path.exists():
            with self.path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    kind = rec["kind"]
                    if kind == "reserve":
                        open_res[rec["id"]] = rec["estimate_usd"]
                    elif kind == "commit":
                        open_res.pop(rec["id"], None)
                        spent += rec["cost_usd"]
                        n_calls += 1
                        by_model[rec["model"]] = by_model.get(rec["model"], 0.0) + rec["cost_usd"]
                    elif kind == "cancel":
                        open_res.pop(rec["id"], None)
        reserved = sum(open_res.values())
        return {"spent_usd": spent, "reserved_usd": reserved, "exposure_usd": spent + reserved,
                "n_calls": n_calls, "by_model_usd": by_model, "open_reservations": len(open_res)}

    def _append(self, rec: dict[str, Any], locked: bool = False) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rec}
        line = json.dumps(rec, sort_keys=True) + "\n"
        if locked:
            self._write(line)
        else:
            with self._locked():
                self._write(line)

    def _write(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        if fcntl is None:  # pragma: no cover
            yield
            return
        with lock_path.open("w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m palimem.costs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show spend against the cap")
    e = sub.add_parser("estimate", help="worst-case cost of a call")
    e.add_argument("--model", required=True)
    e.add_argument("--input", type=int, required=True)
    e.add_argument("--output", type=int, required=True)
    a = ap.parse_args(argv)
    led = CostLedger()
    if a.cmd == "status":
        print(json.dumps(led.summary(), indent=2))
    else:
        print(f"${led.estimate(a.model, a.input, a.output):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
