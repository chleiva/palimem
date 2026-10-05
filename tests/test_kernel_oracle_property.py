"""Property test: the kernel's per-key interpretation sets equal the study's ``oracle_v1`` on random small
streams (n <= 7 reports per key), under both P0c and P0cSU, including the compat admission stub (report and
source withdrawal, A-SELF self-correction, blocked sources).

Needs the study checkout (read-only); skipped locally when absent, a failure in CI (HARNESS_REQUIRED=1).
"""

from __future__ import annotations

import os
import random
from typing import Any

import pytest

from harness import study
from harness.convert import CompatAdmission, to_converted
from palimem.kernel import justify_key
from palimem.kernel.justify import Justification
from palimem.types import Key, Profile, SemanticConfig


@pytest.fixture(scope="module")
def st() -> Any:
    try:
        return study.load()
    except FileNotFoundError as e:
        if os.environ.get("HARNESS_REQUIRED"):
            pytest.fail(f"study checkout required but unavailable: {e}")
        pytest.skip(f"study checkout unavailable: {e}")


def random_stream(rng: random.Random, M: Any) -> Any:
    """A random one-entity stream: four attribute kinds, three origins, withdrawals and corrections."""
    attrs = {
        "emp": M.AttributeSpec("emp", "entity", "single", True),
        "bd": M.AttributeSpec("bd", "date", "single", False),
        "res": M.AttributeSpec("res", "entity", "multi", True),
        "nick": M.AttributeSpec("nick", "string", "multi", False, True, False, False),
    }
    sources = {
        "s1": M.Source("s1", "standard", "gA"),
        "s2": M.Source("s2", "trusted", "gA"),  # shares an origin with s1 (self-update / A-SELF cases)
        "s3": M.Source("s3", "low", "gB"),
        "s4": M.Source("s4", "trusted", "gC"),
        "s5": M.Source("s5", "standard", "gD") if rng.random() < 0.25 else M.Source("s5", "blocked", "gD"),
    }
    n = rng.randint(1, 7)
    obs: list[Any] = []
    asserts: dict[str, Any] = {}
    day = 0
    for i in range(n + rng.randint(0, 3)):
        day += rng.choice([0, 1, 1, 2])
        oid = f"o{i}"
        src = rng.choice(list(sources))
        attr = rng.choice(list(attrs))
        r = rng.random()
        if r < 0.12 and asserts:  # withdrawal of a report, or of a whole source
            if rng.random() < 0.7:
                obs.append(M.Observation(id=oid, t_rep=day, source=src, kind="retract", target=rng.choice(list(asserts))))
            else:
                obs.append(M.Observation(id=oid, t_rep=day, source=src, kind="retract", target=rng.choice(list(sources))))
            continue
        value = rng.choice(["a", "b", "c"])
        valid_cue, valid_t = ("since", rng.randint(0, day + 1)) if rng.random() < 0.4 else ("none", None)
        op_cue, op_from, op_of = "none", None, None
        same_key = [a for a in asserts.values() if a.attr == attr]
        q = rng.random()
        if q < 0.2:
            op_cue = "change"
            if same_key and rng.random() < 0.6:
                op_from = rng.choice(same_key).value
        elif q < 0.35 and same_key:
            op_cue, op_of = "correction", rng.choice(same_key).id
        o = M.Observation(id=oid, t_rep=day, source=src, kind="assert", entity="e", attr=attr, value=value,
                          valid_cue=valid_cue, valid_t=valid_t, op_cue=op_cue, op_from=op_from, op_of=op_of)
        asserts[oid] = o
        obs.append(o)
    return M.Stream(stream_id="rand", entities=["e"], attributes=attrs, rules=[], sources=sources,
                    observations=obs, queries=[])


def _late_assert_after_source_retract(stream: Any) -> bool:
    obs = sorted(stream.observations, key=lambda o: o.t_rep)
    for i, o in enumerate(obs):
        if o.kind == "retract" and o.target in stream.sources and any(
            x.kind == "assert" and x.source == o.target for x in obs[i + 1:]
        ):
            return True
    return False


def _norm_oracle(interps: Any) -> set[Any]:
    return {(frozenset(err), tuple(sorted((s.value, s.start_lo, s.start, s.end_lo, s.end_hi) for s in tl)))
            for err, tl in interps}


def _norm_kernel(j: Justification, conv: Any) -> set[Any]:
    return {(frozenset(conv.study_id[i] for i in err), tuple(sorted((r.value, r.start_lo, r.start, r.end_lo, r.end_hi) for r in tl)))
            for err, tl in j.interpretations}


@pytest.mark.parametrize("mode", ["sidetable", "expand"])
@pytest.mark.parametrize("policy,self_update", [("P0c", False), ("P0cSU", True)])
def test_kernel_interpretations_equal_oracle_v1(st: Any, policy: str, self_update: bool, mode: str) -> None:
    from revise_stream import model as M
    from revise_stream import oracle_v1

    rng = random.Random(20261004 + int(self_update))
    sem = SemanticConfig(self_update=self_update, profile=Profile.REVISE_STREAM_V1)
    checked = nonempty = skipped = 0
    for _ in range(400):
        stream = random_stream(rng, M)
        try:
            stream.admitted(10**9)  # the study raises on unsupported cues; skip those instances
        except NotImplementedError:
            continue
        late = _late_assert_after_source_retract(stream)
        if mode == "expand" and late:
            skipped += 1  # known representation gap (see harness.convert.to_converted)
            continue
        conv = to_converted(stream, mode)
        adm = CompatAdmission(conv)
        by_key = adm.admitted_by_key(len(conv.arrival_ts))
        # the stub's admitted set equals the study's, key by key
        study_adm = stream.admitted_by_key(10**9)
        assert {k.attr: sorted(conv.study_id[e.report.id or ""] for e in es) for k, es in by_key.items()} == \
               {k[1]: sorted(o.id for o in v) for k, v in study_adm.items()}
        for attr in stream.attributes:
            key = Key(entity="e", attr=attr)
            entries = by_key.get(key, [])
            j = justify_key(conv.kschema, key, entries, sem)
            assert isinstance(j, Justification)
            oracle = oracle_v1.key_interpretations(stream, 10**9, ("e", attr, ""), policy)
            assert _norm_kernel(j, conv) == _norm_oracle(oracle), (attr, [vars(o) for o in stream.observations])
            checked += 1
            nonempty += bool(entries)
    assert checked > 300 and nonempty > 200  # the property exercised real keys, not just empty ones
    assert mode == "sidetable" or skipped < 200
