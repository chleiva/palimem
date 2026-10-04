"""Fixture ``compat-authority-coincide`` (decision S-02, verification note of 2026-10-04).

Claim to guard: on the frozen Setting 1 streams, **origin-based and source-based authority give
identical A-SELF results**, so the compat profile ``revise-stream-v1`` (origin-based, to reproduce
the deposited numbers) cannot silently drift from the product default (source-based).

The premise "every report has one source and that source is its origin" is *not* what holds: every
frozen stream has several sources sharing an origin (the correlated mirrors). What holds is
the stronger-for-this-purpose property asserted below: no correction is made by a *different* source
of the *same origin* as its target.

Also checked: the SDK's admission (compat profile) reproduces the study's A-SELF exactly
(``Stream.self_corrections``) and, on streams without source-level retracts (which a report-id
target cannot express, see ``admitter`` docstring), the study's admitted set (``Stream.admitted``).

**Settings 2 and 3 are NOT verified here**: only Setting 1 is available to the harness. When the
Setting 2 inputs are re-run through the service for G2, repeat this check there and on the Setting 3
Wikidata streams, which have real corrections and shared origins (task T-J5).

Skips (does not fail) when the study checkout or the frozen data is absent; with
``HARNESS_REQUIRED=1`` absence is a failure, as for the differential harness.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from harness import frozen, study
from palimem.admission import (
    EVIDENCE_CUES,
    AdmissionConfig,
    Admitter,
    ListLog,
    derive_ulid,
)
from palimem.types import (
    AdmissionOutcome,
    AuthorityRule,
    Cue,
    Key,
    LogEntry,
    Origin,
    Power,
    Profile,
    Report,
    Source,
    ValidationError,
    ValueProp,
    Who,
    WhoKind,
)

ORIGIN_BASED = AdmissionConfig(profile=Profile.REVISE_STREAM_V1)  # the paper's rules (origin-group correct)
SOURCE_BASED = AdmissionConfig(
    profile=Profile.REVISE_STREAM_V1,
    rules=(
        AuthorityRule(who=Who(kind=WhoKind.ANY), may=(Power.WITHDRAW,)),
        AuthorityRule(who=Who(kind=WhoKind.TARGET_SOURCE), may=(Power.CORRECT,)),
    ),
)
T0 = datetime(2026, 10, 4, tzinfo=UTC)


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@/\-]", "_", s)


def _log_of(stream) -> tuple[ListLog, dict]:
    """Map a study stream to typed reports. Source-level retracts are counted, not mapped."""
    log = ListLog()
    stats = {"skipped_values": 0, "source_retracts": 0, "target_after_actor": 0}
    ids = {o.id: derive_ulid(stream.stream_id, o.id) for o in stream.observations}
    pos = {o.id: i for i, o in enumerate(stream.observations)}
    for i, o in enumerate(stream.observations):
        src = stream.sources[o.source]
        base: dict[str, Any] = {
            "id": ids[o.id],
            "source": Source(id=o.source, cls=src.cls),
            "origin": Origin.EXTERNAL_OBSERVATION,
            "origin_group": src.origin,
            "actor": f"connector:{_safe(o.source)}",
        }
        if o.kind == "retract":
            if o.target in stream.sources:
                stats["source_retracts"] += 1
                continue
            if o.target not in ids:
                continue
            if pos[o.target] >= i:
                stats["target_after_actor"] += 1
            report = Report(key=Key(entity="x", attr="x"), cue=Cue.WITHDRAW, target=ids[o.target], **base)
        else:
            key = Key(entity=o.entity, attr=o.attr + (f"@{o.ctx}" if o.ctx else ""))
            try:
                prop = ValueProp(value=o.value)
            except ValidationError:
                stats["skipped_values"] += 1
                continue
            if o.op_cue == "correction" and o.op_of in ids:
                if pos[o.op_of] >= i:
                    stats["target_after_actor"] += 1
                report = Report(key=key, cue=Cue.CORRECT, proposition=prop, target=ids[o.op_of], **base)
            else:
                cue = Cue.CHANGE if o.op_cue == "change" else Cue.ASSERT
                report = Report(key=key, cue=cue, proposition=prop, **base)
        log.add(LogEntry(lsn=i + 1, recorded_at=T0, report=report))
    return log, stats


@pytest.fixture(scope="module")
def results():
    try:
        st = study.load()
        root = frozen.locate_frozen(st.dir)
    except (FileNotFoundError, frozen.FrozenError) as e:
        if os.environ.get("HARNESS_REQUIRED"):
            pytest.fail(f"harness inputs required but unavailable: {e}")
        pytest.skip(f"study checkout or frozen data unavailable: {e}")
    files = sorted(root.glob("s1_[0-9][0-9][0-9][0-9].json"))
    if not files:
        pytest.skip("no frozen Setting 1 streams found")
    out = {
        "streams": 0, "corrections": 0, "cross_source_same_origin": 0, "retracted_corrections": 0,
        "self_corr_mismatch": [], "coincide_mismatch": [], "admitted_mismatch": [], "admitted_compared": 0,
        "skipped_values": 0, "source_retract_streams": 0, "target_after_actor": 0,
    }
    for f in files:
        stream = st.load_stream(str(f))
        out["streams"] += 1
        by_id = {o.id: o for o in stream.observations}
        retracted = {o.target for o in stream.observations if o.kind == "retract"}
        for o in stream.observations:
            if o.kind == "assert" and o.op_cue == "correction" and o.op_of in by_id:
                out["corrections"] += 1
                t = by_id[o.op_of]
                if t.source != o.source and stream.sources[t.source].origin == stream.sources[o.source].origin:
                    out["cross_source_same_origin"] += 1
                if o.id in retracted:
                    out["retracted_corrections"] += 1
        log, stats = _log_of(stream)
        out["skipped_values"] += stats["skipped_values"]
        out["target_after_actor"] += stats["target_after_actor"]
        out["source_retract_streams"] += bool(stats["source_retracts"])
        tau = max(o.t_rep for o in stream.observations)
        paper = {ids for ids in stream.self_corrections(tau)}
        paper_ulid = {derive_ulid(stream.stream_id, i) for i in paper}

        evs = {}
        for name, cfg in (("origin", ORIGIN_BASED), ("source", SOURCE_BASED)):
            evs[name] = Admitter(cfg).evaluate(log)
        mine = {}
        for name, ev in evs.items():
            mine[name] = {
                t
                for e in ev.entries
                if e.report.cue is Cue.CORRECT
                for t in ev.decisions[e.report.id].withdraws
            }
        if mine["origin"] != paper_ulid:
            out["self_corr_mismatch"].append(stream.stream_id)
        if mine["origin"] != mine["source"]:
            out["coincide_mismatch"].append(stream.stream_id)

        if not stats["source_retracts"]:
            ev = evs["origin"]
            admitted = {
                e.report.id
                for e in ev.entries
                if ev.decisions[e.report.id].record.outcome is AdmissionOutcome.ADMISSIBLE
                and e.report.cue in EVIDENCE_CUES
                and e.report.id not in ev.withdrawn
            }
            paper_admitted = {derive_ulid(stream.stream_id, o.id) for o in stream.admitted(tau)}
            out["admitted_compared"] += 1
            if admitted != paper_admitted:
                out["admitted_mismatch"].append(stream.stream_id)
    return out


def test_no_correction_crosses_sources_within_an_origin(results):
    assert results["corrections"] > 0
    assert results["cross_source_same_origin"] == 0, results


def test_origin_based_and_source_based_authority_give_identical_a_self(results):
    assert results["coincide_mismatch"] == [], results["coincide_mismatch"][:5]


def test_sdk_a_self_reproduces_the_studys_self_corrections_on_every_stream(results):
    assert results["self_corr_mismatch"] == [], results["self_corr_mismatch"][:5]
    assert results["streams"] >= 1
    assert results["target_after_actor"] == 0  # every target precedes the report acting on it


def test_sdk_admitted_set_reproduces_the_studys_where_expressible(results):
    assert results["admitted_compared"] > 0
    assert results["admitted_mismatch"] == [], results["admitted_mismatch"][:5]
    assert results["skipped_values"] == 0


def test_summary_for_the_record(results):
    """Prints the counts behind the S-02 verification note (run with ``-s``)."""
    keys = ["streams", "corrections", "cross_source_same_origin", "retracted_corrections", "source_retract_streams", "admitted_compared"]
    print("\ncompat-authority-coincide:", {k: results[k] for k in keys})
