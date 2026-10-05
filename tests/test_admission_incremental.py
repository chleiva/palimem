"""Incremental admission == whole-log admission, decision for decision, after EVERY append (Lane O2).

The whole-log evaluator (:meth:`Admitter.evaluate`) is the audit oracle: it is evaluated on every prefix by a *fresh*
Admitter (no shared memo) and compared with the incrementally maintained state: every report's decision (outcome,
reason, admission version, confirmers, effective cue, withdrawals, authority), the withdrawal map (``by`` and
``kind`` included), the direct evidence per key, the attributions per key, the entity universe, and the admission
records the append emits. Streams are random and dense in interactions (confirmations, quarantine, origin groups,
corrections, withdrawals of actors, source-level withdrawals, allege downgrades, grants, agent origins, attributions,
effects reaching far back), under six admission configurations.
"""

from __future__ import annotations

import os
import random
from dataclasses import replace

import pytest

from palimem.admission import (
    AdmissionConfig,
    Admitter,
    IncrementalAdmission,
    ListLog,
    supports_incremental,
)
from palimem.engine.pipeline import incremental_mismatches, whole_log_records
from palimem.types import AdmissionOutcome, AdmissionReason, Cue, LogEntry
from tests._adm_random import SOURCES, Variant, random_log, variants

N_STREAMS = int(os.environ.get("PALIMEM_EQUIV_STREAMS", "340"))  # x 6 variants = 2,040 streams by default
STREAM_LEN = 40


def check_stream(entries: list[LogEntry], cfg: AdmissionConfig, *, rollbacks: random.Random | None = None) -> dict[str, int]:
    """Replay ``entries`` incrementally and compare with the whole-log oracle after every append. With ``rollbacks`` a
    random append is rolled back (applied, not committed) and the stream continues, the way a failed transaction
    behaves; the LSN of the next report is reused."""
    admitter = Admitter(cfg)
    inc = IncrementalAdmission(admitter)
    oracle = Admitter(cfg)  # fresh: shares no memo with the incremental path
    log = ListLog()
    prev = None
    stats = {"appends": 0, "rolled_back": 0, "quarantined_confirmed": 0, "withdrawn": 0}
    for src in entries:
        e = replace(src, lsn=(log.head_lsn() + 1))
        inc.sync(log, before_lsn=e.lsn)
        # after a rollback the state must be exactly the committed prefix again
        ev_before = oracle.evaluate(log)
        assert incremental_mismatches(inc, ev_before) == [], "state after sync differs from the committed prefix"
        delta = inc.append(e)
        if rollbacks is not None and rollbacks.random() < 0.15:
            stats["rolled_back"] += 1
            continue  # not committed: the next sync must undo it
        log.add(e)
        ev = oracle.evaluate(log)
        problems = incremental_mismatches(inc, ev)
        assert problems == [], (e.lsn, problems)
        assert inc.audit() == [], (e.lsn, inc.audit())
        assert list(delta.records) == whole_log_records(prev, ev, e.report.id or ""), (e.lsn, "records")
        prev = ev
        stats["appends"] += 1
        stats["withdrawn"] = len(ev.withdrawn)
        stats["quarantined_confirmed"] += sum(
            1 for d in ev.decisions.values() if d.record.reason is AdmissionReason.CONFIRMED
        )
    if rollbacks is not None:
        assert inc.rebuilds == 0, "a rolled-back append must be undone from the journal, not by rebuilding"
    return stats


@pytest.mark.parametrize("variant", variants(), ids=lambda v: v.name)
def test_incremental_equals_whole_log_after_every_append(variant: Variant) -> None:
    totals = {"appends": 0, "quarantined_confirmed": 0, "withdrawn": 0}
    for seed in range(N_STREAMS):
        s = check_stream(random_log(seed * 7 + 1, STREAM_LEN), variant.config)
        totals["appends"] += s["appends"]
        totals["quarantined_confirmed"] += s["quarantined_confirmed"]
        totals["withdrawn"] += s["withdrawn"]
    assert totals["appends"] == N_STREAMS * STREAM_LEN


@pytest.mark.parametrize("variant", variants(), ids=lambda v: v.name)
def test_rolled_back_appends_leave_no_trace(variant: Variant) -> None:
    for seed in range(60):
        check_stream(random_log(seed * 13 + 5, STREAM_LEN), variant.config, rollbacks=random.Random(seed))


def test_the_random_streams_exercise_every_rule() -> None:
    """The generator would be a weak safety net if it never reached the rules: count what the streams contain."""
    seen_reason: set[AdmissionReason] = set()
    seen_outcome: set[AdmissionOutcome] = set()
    kinds: set[str] = set()
    cues: set[Cue] = set()
    for v in variants():
        for seed in range(80):
            log = ListLog(replace(e, lsn=i + 1) for i, e in enumerate(random_log(seed * 7 + 1, STREAM_LEN)))
            ev = Admitter(v.config).evaluate(log)
            for d in ev.decisions.values():
                seen_reason.add(d.record.reason)
                seen_outcome.add(d.record.outcome)
                cues.add(d.effective_cue)
            kinds.update(w.kind for w in ev.withdrawn.values())
    assert {AdmissionReason.ADMITTED, AdmissionReason.CONFIRMED, AdmissionReason.SOURCE_QUARANTINED,
            AdmissionReason.SOURCE_BLOCKED, AdmissionReason.AUTHORITY_FAILED, AdmissionReason.TARGET_MISSING,
            AdmissionReason.ORIGIN_NOT_ADMISSIBLE} <= seen_reason
    assert seen_outcome == set(AdmissionOutcome)
    assert {"withdraw", "self_correction", "source_withdraw"} <= kinds
    assert {Cue.ALLEGE, Cue.DISPUTE, Cue.WITHDRAW, Cue.CORRECT} <= cues
    assert len(SOURCES) >= 6


def test_a_fresh_state_catches_up_to_an_existing_log() -> None:
    entries = [replace(e, lsn=i + 1) for i, e in enumerate(random_log(99, 60))]
    for v in variants():
        log = ListLog(entries)
        inc = IncrementalAdmission(Admitter(v.config))
        inc.sync(log, before_lsn=61)  # cold start: replays the log
        assert incremental_mismatches(inc, Admitter(v.config).evaluate(log)) == []
        inc.sync(log, before_lsn=31)  # asked for an older prefix: rebuilds
        assert incremental_mismatches(inc, Admitter(v.config).evaluate(log, as_of_lsn=30)) == []


def test_a_rewritten_log_is_detected_and_rebuilt() -> None:
    a = [replace(e, lsn=i + 1) for i, e in enumerate(random_log(3, 30))]
    b = [replace(e, lsn=i + 1) for i, e in enumerate(random_log(4, 30))]
    cfg = AdmissionConfig()
    inc = IncrementalAdmission(Admitter(cfg))
    inc.sync(ListLog(a), before_lsn=31)
    other = ListLog(b)  # same LSNs, different reports (a restore, an erasure that replaced rows)
    inc.sync(other, before_lsn=31)
    assert incremental_mismatches(inc, Admitter(cfg).evaluate(other)) == []


def test_appending_below_the_head_is_refused_and_an_unsettled_append_blocks_the_next() -> None:
    entries = [replace(e, lsn=i + 1) for i, e in enumerate(random_log(1, 5))]
    inc = IncrementalAdmission(Admitter(AdmissionConfig()))
    inc.append(entries[0])
    with pytest.raises(RuntimeError):
        inc.append(entries[1])  # the previous append was never settled
    log = ListLog([entries[0]])
    inc.sync(log, before_lsn=2)
    inc.append(entries[1])
    with pytest.raises(ValueError):
        inc.settle(log) or inc.append(entries[0])


def test_subclasses_that_bypass_the_incremental_path_are_detected() -> None:
    class Plain(Admitter):
        pass

    class Overrides(Admitter):
        def _evaluate(self, entries, as_of):  # type: ignore[no-untyped-def]
            return super()._evaluate(entries, as_of)

    class OverridesWithHooks(Overrides):
        def incremental_overlay(self, inc, base):  # type: ignore[no-untyped-def]
            return base

        def incremental_overlay_trigger(self, inc, entry):  # type: ignore[no-untyped-def]
            return False

    class Withdrawals(Admitter):
        def _withdrawals(self, entries, base):  # type: ignore[no-untyped-def]
            return super()._withdrawals(entries, base)

    cfg = AdmissionConfig()
    assert supports_incremental(Admitter(cfg)) and supports_incremental(Plain(cfg))
    assert not supports_incremental(Overrides(cfg))
    assert supports_incremental(OverridesWithHooks(cfg))
    assert not supports_incremental(Withdrawals(cfg))


# ---------------------------------------------------------------------------------------------- the net must bite


def _first_failure(variant_names: tuple[str, ...], seeds: int = 40, *, rollbacks: bool = False) -> bool:
    for v in variants():
        if v.name not in variant_names:
            continue
        for seed in range(seeds):
            try:
                check_stream(random_log(seed * 7 + 1, STREAM_LEN), v.config, rollbacks=random.Random(seed) if rollbacks else None)
            except AssertionError:
                return True
    return False


def test_mutation_no_confirmation_updates_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IncrementalAdmission, "_reconfirm", lambda self, key: [])
    assert _first_failure(("product", "override-quarantine"))


def test_mutation_never_undoing_a_rolled_back_append_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IncrementalAdmission, "settle", lambda self, log: setattr(self, "_pending", None))
    assert _first_failure(("product-grants",), rollbacks=True)


def test_mutation_withdrawn_actors_keep_acting_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under acting_reports_must_be_live a withdrawn actor no longer acts: never switching actors off must be caught."""
    monkeypatch.setattr(IncrementalAdmission, "_should_act", lambda self, actor_id: True)
    assert _first_failure(("product", "product-grants"), seeds=80)


def test_mutation_oldest_actor_owns_in_live_mode_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """In live mode the newest acting claimant owns a withdrawal; picking the oldest must be caught."""
    original = IncrementalAdmission._owner

    def oldest(self: IncrementalAdmission, target_id: str):  # type: ignore[no-untyped-def]
        c = self.claims.get(target_id)
        if not c:
            return None
        from palimem.admission import Withdrawal

        aid = min(c, key=lambda a: self.by_id[a].lsn)
        return Withdrawal(by=aid, kind=c[aid])

    monkeypatch.setattr(IncrementalAdmission, "_owner", oldest)
    assert _first_failure(("product", "product-grants"), seeds=80)
    monkeypatch.setattr(IncrementalAdmission, "_owner", original)


def test_mutation_cascade_without_the_status_pass_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cascade (an actor switched off restores its targets) is the delicate part: skip the status pass."""
    monkeypatch.setattr(IncrementalAdmission, "_set_inactive", lambda self, actor_id, inactive: None)
    assert _first_failure(("product", "product-grants"), seeds=80)


def test_mutation_ignoring_withdrawn_in_direct_evidence_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    original = IncrementalAdmission._is_direct

    def ignore_withdrawn(self: IncrementalAdmission, e: LogEntry) -> bool:
        w = self.withdrawn
        self.withdrawn = {}
        try:
            return original(self, e)
        finally:
            self.withdrawn = w

    monkeypatch.setattr(IncrementalAdmission, "_is_direct", ignore_withdrawn)
    assert _first_failure(("product", "compat"))


def test_mutation_dropping_attributions_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IncrementalAdmission, "attributions_of", lambda self, key: ())
    assert _first_failure(("product",))


def test_mutation_later_reports_of_a_covered_source_escape_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A report from a source a standing source-level withdrawal covers must itself be withdrawn (the paper's
    retraction also removes what the source says later): never claiming it must be caught."""
    original = IncrementalAdmission._update_withdrawals

    def no_cover(self: IncrementalAdmission, e: LogEntry, is_actor: bool):  # type: ignore[no-untyped-def]
        saved = self.cover_actors
        self.cover_actors = {}
        try:
            return original(self, e, is_actor)
        finally:
            fresh = self.cover_actors
            self.cover_actors = saved
            for k, v in fresh.items():
                saved.setdefault(k, []).extend(v)

    monkeypatch.setattr(IncrementalAdmission, "_update_withdrawals", no_cover)
    assert _first_failure(("product-grants", "product-not-live"), seeds=80)
