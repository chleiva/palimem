"""Conformance runner: executes fixtures against a pluggable :class:`Implementation`.

    python -m tests.conformance.runner                      # summary against the built-in ReferenceStub
    python -m tests.conformance.runner --impl pkg.mod:Cls   # against a real implementation
    python -m tests.conformance.runner --list               # one line per fixture with its outcome
    python -m tests.conformance.runner --include-pending    # also run pending-decision / shell fixtures

An implementation speaks JSON only (``start(setup)`` then ``execute(op) -> result``), so a port in
another language can sit behind a thin adapter. ``NotImplemented`` anywhere means "this implementation
does not do that (yet)" and the fixture is reported as skipped with the reason, never as a failure.
Today only the ReferenceStub exists, so every executable fixture is reported as skipped.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .matcher import MISSING, State, add_virtuals, match, resolve_deep

ROOT = Path(__file__).parent
FIXTURE_DIR = ROOT / "fixtures"
TB_DIR = ROOT.parent / "fixtures" / "trust_boundary"

PASS, FAIL, SKIP, PENDING, SHELL, REF = "pass", "fail", "skip", "pending-decision", "shell", "not-run"
OUTCOMES = [PASS, FAIL, SKIP, PENDING, SHELL, REF]


class Implementation(Protocol):
    """What an implementation under test provides."""

    name: str

    def capabilities(self) -> set[str]: ...

    def start(self, setup: dict[str, Any]) -> Any:
        """Create a fresh, empty store from ``setup`` (profile, schema, sources, authority, limits...).
        Return ``None`` on success, ``NotImplemented`` if unsupported, or ``{"error": {...}}`` if the
        setup is refused (the expected outcome when ``setup.expect_load_error`` is present)."""

    def execute(self, op: dict[str, Any]) -> Any:
        """Run one operation (see docs/CONFORMANCE.md) and return its JSON result, or ``NotImplemented``."""

    def close(self) -> None: ...


class ReferenceStub:
    """Implements nothing. Every fixture is therefore reported as skipped (never failed)."""

    name = "ReferenceStub"

    def capabilities(self) -> set[str]:
        return set()

    def start(self, setup: dict[str, Any]) -> Any:
        return NotImplemented

    def execute(self, op: dict[str, Any]) -> Any:
        return NotImplemented

    def close(self) -> None:
        return None


@dataclass
class Outcome:
    id: str
    gate: str
    area: str
    kind: str
    status: str  # one of OUTCOMES
    reason: str = ""
    failures: list[str] = field(default_factory=list)


def load_fixtures() -> list[dict[str, Any]]:
    out = []
    for g in ("independent", "decisions", "security"):
        for p in sorted((FIXTURE_DIR / g).glob("*.json")):
            if p.name != "index.json":
                out.append(json.loads(p.read_text()))
    return out


def load_trust_boundary() -> list[dict[str, Any]]:
    """The trust-boundary fixtures stay where they are (tests/fixtures/trust_boundary); this wraps them."""
    out = []
    for p in sorted(TB_DIR.glob("tb-*.json")):
        d = json.loads(p.read_text())
        out.append({"id": d["id"], "kind": "trust_boundary", "gate": "G1", "area": "trust_boundary", "title": d["title"],
                    "covers": d["covers"], "status": "active", "requires": d.get("requires", []), "path": str(p)})
    return out


def _expand(v: Any, i: int) -> Any:
    if isinstance(v, str):
        return v.replace("{i}", str(i))
    if isinstance(v, list):
        return [_expand(x, i) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x, i) for k, x in v.items()}
    return v


def _bind(op: dict[str, Any], result: Any, st: State) -> None:
    if op["op"] == "append" and isinstance(result, dict) and "report_id" in result:
        st.refs[op["ref"]] = {"id": result["report_id"], "lsn": result.get("lsn"), "generation": result.get("generation")}
    elif op["op"] == "observe_text" and isinstance(result, dict) and result.get("reports"):
        first = result["reports"][0]
        st.refs[op["ref"]] = {"id": first.get("report_id"), "lsn": first.get("lsn"), "generation": first.get("generation")}


class _Subscriber:
    """Reference idempotent subscriber: dedups on event_id, so redelivery produces no second effect."""

    def __init__(self) -> None:
        self.seen = 0
        self.applied: set[str] = set()

    def feed(self, events: list[dict[str, Any]]) -> None:
        for e in events:
            self.seen += 1
            self.applied.add(str(e.get("event_id")))

    def effects(self) -> dict[str, Any]:
        return {"p1": {"events_seen": self.seen, "effects_applied": len(self.applied)}}


def run_scenario(fx: dict[str, Any], impl: Implementation) -> Outcome:
    o = Outcome(fx["id"], fx["gate"], fx["area"], fx["kind"], PASS)
    missing = [c for c in fx["requires"] if c not in impl.capabilities()]
    if missing and not isinstance(impl, ReferenceStub):
        o.status, o.reason = SKIP, f"missing capability: {', '.join(missing)}"
        return o
    setup = copy.deepcopy(fx["setup"])
    try:
        started = impl.start(setup)
    except NotImplementedError as e:
        o.status, o.reason = SKIP, f"{impl.name}: {e}"
        return o
    if started is NotImplemented:
        o.status, o.reason = SKIP, f"{impl.name} does not implement start()"
        return o
    if "expect_load_error" in setup:
        if isinstance(started, dict) and "error" in started:
            return o
        o.status, o.failures = FAIL, ["schema should have been refused at load but was accepted"]
        return o
    if isinstance(started, dict) and "error" in started:
        o.status, o.failures = FAIL, [f"setup refused: {started['error']}"]
        return o
    st, sub = State(), _Subscriber()
    try:
        for op in fx["ops"]:
            n = op.get("repeat", 1)
            for i in range(1, n + 1):
                this = _expand({k: v for k, v in op.items() if k != "repeat"}, i) if "repeat" in op else op
                if not _step(this, impl, st, sub, o):
                    if o.status == SKIP:
                        return o
                    break
        for eq in fx.get("expect", {}).get("equal", []):
            a, b = st.named.get(eq["a"]), st.named.get(eq["b"])
            for fld in eq["fields"]:
                va = _get(a, fld)
                vb = _get(b, fld)
                same = va == vb
                if same == eq.get("negate", False):
                    o.failures.append(f"equal({eq['a']}, {eq['b']}).{fld}: {va!r} vs {vb!r}")
    finally:
        impl.close()
    if o.failures:
        o.status = FAIL
    return o


def _get(res: Any, path: str) -> Any:
    cur = res
    for seg in path.split("."):
        cur = cur.get(seg, MISSING) if isinstance(cur, dict) else MISSING
    return None if cur is MISSING else cur


def _step(op: dict[str, Any], impl: Implementation, st: State, sub: _Subscriber, o: Outcome) -> bool:
    name = op.get("name")
    if op["op"] == "subscriber_effects":
        result: Any = sub.effects()
    else:
        sent = resolve_deep({k: v for k, v in op.items() if k not in ("expect", "name")}, st)
        result = impl.execute(sent)
        if result is NotImplemented:
            o.status, o.reason = SKIP, f"{impl.name} does not implement op '{op['op']}'"
            return False
        if op["op"] == "deliver" and isinstance(result, dict):
            sub.feed(result.get("events", []))
        result = add_virtuals(result)
    _bind(op, result, st)
    if name:
        st.named[name] = result
    exp = op.get("expect")
    if exp is not None:
        errs = match(resolve_deep(exp, st), result)
        if errs:
            label = f"{op['op']}:{name or op.get('ref', '')}"
            o.failures += [f"{label} {e}" for e in errs]
            return False
    return True


# --- non-scenario kinds ---------------------------------------------------------------------------------


def run_harness_check(fx: dict[str, Any]) -> Outcome:
    from . import checks

    o = Outcome(fx["id"], fx["gate"], fx["area"], fx["kind"], PASS)
    fn = checks.REGISTRY.get(fx["id"])
    if fn is None:
        o.status, o.reason = SKIP, "no check registered"
        return o
    ok, msg = fn()
    if ok is None:
        o.status, o.reason = SKIP, msg
    elif not ok:
        o.status, o.failures = FAIL, [msg]
    else:
        o.reason = msg
    return o


def run_all(impl: Implementation, include_pending: bool = False) -> list[Outcome]:
    out: list[Outcome] = []
    for fx in load_fixtures():
        if fx["kind"] == "harness_check":
            out.append(run_harness_check(fx))
        elif fx["status"] == "pending-decision" and not include_pending:
            out.append(Outcome(fx["id"], fx["gate"], fx["area"], fx["kind"], PENDING, fx["status_reason"]))
        elif fx["status"] == "shell" and not include_pending:
            out.append(Outcome(fx["id"], fx["gate"], fx["area"], fx["kind"], SHELL, fx["status_reason"]))
        else:
            out.append(run_scenario(fx, impl))
    for tb in load_trust_boundary():
        out.append(Outcome(tb["id"], tb["gate"], tb["area"], tb["kind"], REF,
                           "specified in tests/fixtures/trust_boundary; its runner lands with the agent tool API (T-F2)"))
    return out


def summary(outcomes: list[Outcome]) -> str:
    lines: list[str] = []
    for title, keyf in (("gate", lambda o: o.gate), ("area", lambda o: o.area)):
        rows: dict[str, Counter[str]] = defaultdict(Counter)
        for o in outcomes:
            rows[keyf(o)][o.status] += 1
        hdr = f"{title:<18}{'total':>6}" + "".join(f"{s:>18}" for s in OUTCOMES)
        lines += [hdr, "-" * len(hdr)]
        for k in sorted(rows):
            c = rows[k]
            lines.append(f"{k:<18}{sum(c.values()):>6}" + "".join(f"{c[s]:>18}" for s in OUTCOMES))
        tot: Counter[str] = Counter(o.status for o in outcomes)
        lines += ["-" * len(hdr), f"{'all':<18}{len(outcomes):>6}" + "".join(f"{tot[s]:>18}" for s in OUTCOMES), ""]
    return "\n".join(lines)


def _load_impl(spec: str | None) -> Implementation:
    spec = spec or os.environ.get("PALIMEM_CONFORMANCE_IMPL")
    if not spec:
        return ReferenceStub()
    mod, _, cls = spec.partition(":")
    return getattr(importlib.import_module(mod), cls or "Implementation")()  # type: ignore[no-any-return]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--impl", help="module:Class of an Implementation (default: env PALIMEM_CONFORMANCE_IMPL, else ReferenceStub)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--include-pending", action="store_true", help="also run pending-decision and shell fixtures")
    args = ap.parse_args(argv)
    impl = _load_impl(args.impl)
    outcomes = run_all(impl, args.include_pending)
    print(f"implementation: {impl.name}\n")
    print(summary(outcomes))
    if args.list:
        for o in outcomes:
            print(f"{o.status:<17} {o.gate} {o.area:<18} {o.id}" + (f"  [{o.reason}]" if o.reason else ""))
    for o in outcomes:
        for f in o.failures:
            print(f"FAIL {o.id}: {f}")
    return 1 if any(o.status == FAIL for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
