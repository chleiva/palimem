"""Orphaned entities are pseudonymised after an erasure (S-13; author ruling 2026-10-05, item 8).

If the erased report was the only evidence about its entity, the entity name is replaced by a keyed pseudonym in every
table that names an entity and inside stored belief, outbox and job JSON. The key stays addressable by its plain name,
beliefs of *other* entities that depended on it keep working, and an entity that comes back is re-identified.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from chain_fakes import ChainReviser, chain_schema
from fakes import FakeAdmitter, make_report
from test_erasure import (
    SECRET_VALUES,
    A,
    B,
    C,
    P,
    add,
    assert_no_secret_values,
    secret_report,
    status,
    tables,
)

from palimem.store import ErasureReason, RevisionContext, StoreView
from palimem.store.backend import LimitedRead
from palimem.store.pseudonym import (
    PREFIX,
    EntityPseudonyms,
    EntityRewrite,
    ref_of,
    rekey_belief,
)
from palimem.types import (
    Attr,
    AttrClass,
    Belief,
    Candidate,
    Dependency,
    Inference,
    KernelStatus,
    Key,
    Pin,
    Rule,
    Schema,
    Segment,
    Support,
    ValueForm,
    ValueType,
    Versions,
)

BOSS = Key(entity="dana", attr="boss_employer")  # a derived belief of ANOTHER entity that depends on A


def cross_schema() -> Schema:
    base = chain_schema()
    boss = Attr(name="boss_employer", attr_class=AttrClass.DERIVED, value_type=ValueType.STRING, rule=Rule(reads=("employer",), fn="f_boss"))
    return replace(base, attrs=(*base.attrs, boss))


class CrossReviser(ChainReviser):
    """ChainReviser plus one belief of another entity (dana) that depends on the secret entity's employer."""

    def _boss(self, view: StoreView, up: Belief | None, *, version: int, lsn: int, gen: int, at: datetime) -> Belief:
        if up is not None and up.segments[0].established is not None:
            est = up.segments[0].established
            c = Candidate(key=BOSS, form=ValueForm(value="boss:" + str(est.form.value)))  # type: ignore[union-attr]
            sup = {c.id: tuple(Support(environment=s.environment) for s in up.segments[0].support[est.id])}
            seg = Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=c, support=sup)
        else:
            seg = Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNKNOWN)
        pins = () if up is None else tuple(p.report_id for p in up.pinned)
        deps = () if up is None else (Dependency(key=up.key, version=up.version),)
        return Belief(
            key=BOSS, version=version, lsn=lsn, required_generation=gen, completed_generation=gen, segments=(seg,),
            pinned=tuple(Pin(report_id=i, admission_version=1) for i in pins), depends_on=deps, invalidated_by=None,
            versions=Versions(schema=1, semantic=1, admission=1), inference=Inference(complete=True), recorded_at=at,
        )

    def revise(self, ctx: RevisionContext) -> Sequence[Belief]:
        out = list(super().revise(ctx))
        if ctx.entry.report.key == A:
            up = next(b for b in out if b.key == A)
            cur = ctx.view.current_belief(BOSS)
            out.append(self._boss(ctx.view, up, version=(cur.version if cur else 0) + 1, lsn=ctx.entry.lsn, gen=ctx.generation, at=ctx.entry.recorded_at))
        return out

    def recompute(self, key: Key, view: StoreView) -> Belief | None:
        if key == BOSS:
            return self._boss(view, view.current_belief(A), version=1, lsn=1, gen=1, at=datetime(2026, 1, 1, tzinfo=UTC))
        return super().recompute(key, view)


def stored_keys(h) -> str:  # type: ignore[no-untyped-def]
    return "".join(tables(h).values())


def test_an_orphaned_entity_name_is_nowhere_in_the_store_after_its_only_evidence_is_erased(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [C])
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    assert P in stored_keys(h)  # sanity: before the erasure the name is in the index tables
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    for name, text in tables(h).items():
        assert P not in text, f"the orphaned entity name survives in table {name}"
    assert P not in "".join(b.export_jsonl())
    assert PREFIX in tables(h)["beliefs"] and PREFIX in tables(h)["current_belief"] and PREFIX in tables(h)["subscriptions"]
    assert_no_secret_values(h)
    # the pseudonym is a keyed hash: it is the same for the same name and changes with the store secret
    assert ref_of(b"s1" * 16, P) == ref_of(b"s1" * 16, P) and ref_of(b"s1" * 16, P) != ref_of(b"s2" * 16, P)
    # the outbox and jobs carry no plain key either (the raw rows, not just the repr of the engine's view)
    assert all(P not in repr(e) for e in b.storage.outbox_all())
    assert all(P not in j.payload for j in b.storage.jobs())


def test_the_key_stays_addressable_by_its_plain_name_and_the_store_still_verifies(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [C])
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    for k in (A, B, C):
        got = b.read_belief(k)
        assert isinstance(got, Belief) and status(got) is KernelStatus.UNKNOWN and got.version == 2
        assert got.key.entity.startswith(PREFIX) and got.key.attr == k.attr
        assert b.current_belief(k) is not None and b.belief_version(k, 2) is not None
    assert b.subscriptions("plan") == (Key(entity=b.current_belief(C).key.entity, attr="tax_city"),)  # type: ignore[union-attr]
    assert b._s.plans_for_key(C) == ["plan"]  # the plain key finds its subscribers through the translating view
    assert b.verify_beliefs(ChainReviser()).ok and b.verify_log().ok and b.recover().ok
    assert b.complete_pending(ChainReviser()).jobs_pending == 0


def test_a_belief_of_another_entity_that_depended_on_the_erased_one_keeps_working(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(cross_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding", CrossReviser())
    boss = b.read_belief(BOSS)
    assert isinstance(boss, Belief) and boss.segments[0].established is not None and boss.depends_on[0].key == A
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=CrossReviser())

    for name, text in tables(h).items():
        assert P not in text, f"the orphaned entity name survives in table {name} (dana's dependency included)"
    after = b.read_belief(BOSS)  # dana has no live evidence of her own here, so she is orphaned too
    assert isinstance(after, Belief) and status(after) is KernelStatus.UNKNOWN
    assert after.depends_on and after.depends_on[0].key.entity.startswith(PREFIX)  # the dependency moved to the stored form
    assert b.verify_beliefs(CrossReviser()).ok  # a recomputed belief compares equal in stored form
    assert b.recover().ok and b.verify_log().ok


def test_a_dependant_with_live_evidence_keeps_its_own_name_and_only_the_dependency_moves(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(cross_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding", CrossReviser())
    add(b, make_report(BOSS.entity, "nickname", "dee", source="hr"), "k-dana", CrossReviser())  # dana gets live evidence
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=CrossReviser())
    text = stored_keys(h)
    assert P not in text  # the orphaned entity is gone everywhere...
    assert "dana" in text  # ...while dana, who still has evidence, keeps her name
    dep_rows = b.read_belief(BOSS)
    assert isinstance(dep_rows, Belief) and dep_rows.depends_on[0].key.entity.startswith(PREFIX)
    assert b.verify_beliefs(CrossReviser()).ok


def test_an_entity_with_remaining_live_evidence_is_not_pseudonymised(h) -> None:  # type: ignore[no-untyped-def]
    """Two reports about the same entity: erasing one leaves the entity (and its key text) in the index tables."""
    b = h.backend
    b.put_schema(chain_schema())
    r1 = add(b, secret_report(), "idem-secretcorp-onboarding")
    add(b, make_report(P, "nickname", "pat", source="hr"), "k2")
    b.erase(r1.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    t = tables(h)
    assert P in t["beliefs"] and all(PREFIX not in t[n] for n in ("beliefs", "current_belief", "marks", "outbox", "subscriptions"))
    assert b.verify_beliefs(ChainReviser()).ok


def test_an_entity_that_comes_back_is_re_identified_and_works_normally(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    assert PREFIX in tables(h)["beliefs"]
    add(b, make_report(P, "employer", "Globex", source="s2"), "k2")  # a new report about the same entity
    t = tables(h)
    assert PREFIX not in t["beliefs"] and PREFIX not in t["current_belief"] and PREFIX not in t["marks"]
    assert P in t["beliefs"]  # the entity is live again: its name is legitimately in the log and the index
    a = b.read_belief(A)
    assert isinstance(a, Belief) and status(a) is KernelStatus.ESTABLISHED and a.key == A
    assert b.read_belief(B).segments[0].established.form.value == "derived:Globex"  # type: ignore[union-attr]
    assert b.verify_beliefs(ChainReviser()).ok and b.recover().ok and b.verify_log().ok
    # the history across the erasure is still not reconstructable, and that is reported as such
    assert b.belief_version(A, 1) is None


def test_pseudonymising_the_same_entity_twice_is_a_no_op_and_a_second_erasure_still_works(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    r1 = add(b, secret_report(), "idem-secretcorp-onboarding")
    b.erase(r1.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    refs_before = set(b._pseudo.refs)  # type: ignore[attr-defined]
    assert len(refs_before) == 1
    r2 = add(b, make_report(P, "employer", "Globex", source="s2"), "k2")  # re-identified
    assert not b._pseudo.refs  # type: ignore[attr-defined]
    b.erase(r2.entry.report.id, ErasureReason.RETENTION_EXPIRY, reviser=ChainReviser())
    assert set(b._pseudo.refs) == refs_before  # the same name gets the same pseudonym again  # type: ignore[attr-defined]
    for name, text in tables(h).items():
        assert P not in text and "Globex" not in text, name
    assert b.verify_beliefs(ChainReviser()).ok


def test_a_failure_during_the_erasure_rolls_the_rename_back(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    before = tables(h)

    class Boom(Exception):
        pass

    def crash(step: str) -> None:
        if step == "erase_after_repair":  # after the repair and the rename, before the commit
            raise Boom

    b.set_fault_hook(crash)
    with pytest.raises(Boom):
        b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    b.set_fault_hook(None)
    assert not b._pseudo.refs  # type: ignore[attr-defined]  # the in-memory set rolled back with the transaction
    assert tables(h) == before  # nothing changed, nothing renamed
    assert b.read_belief(A).version == 1 and b.verify_beliefs(ChainReviser()).ok  # type: ignore[union-attr]
    if h.kind == "sqlite":
        con = sqlite3.connect(h.path)
        names = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        con.close()
        assert "beliefs_flag_only" in names  # the append-only trigger was restored by the rollback
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())  # and the retry succeeds
    assert P not in stored_keys(h)


def test_the_append_only_trigger_is_in_place_after_a_successful_rename(h) -> None:  # type: ignore[no-untyped-def]
    if h.kind != "sqlite":
        pytest.skip("triggers are an SQLite feature")
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    con = sqlite3.connect(h.path)
    try:
        assert "beliefs_flag_only" in {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        with pytest.raises(sqlite3.DatabaseError):  # still append-only for anything but a redaction
            con.execute("UPDATE beliefs SET lsn = lsn + 1")
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("UPDATE beliefs SET entity = 'x'")
    finally:
        con.close()


def test_the_pseudonyms_survive_a_reopen_and_hold_no_plain_name(h) -> None:  # type: ignore[no-untyped-def]
    if h.kind != "sqlite":
        pytest.skip("reopening needs a file")
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    b.close()
    con = sqlite3.connect(h.path)
    (meta,) = con.execute("SELECT value FROM meta WHERE name = 'entity_pseudonyms'").fetchone()
    con.close()
    assert P not in meta and json.loads(meta)[0].startswith(PREFIX)
    b2 = h.make(path=h.path)
    h.backend = b2
    got = b2.read_belief(A)  # still addressable by the plain name after a reopen (the store secret is supplied)
    assert isinstance(got, Belief) and status(got) is KernelStatus.UNKNOWN
    assert b2.verify_beliefs(ChainReviser()).ok


def test_deferred_repair_jobs_carry_no_plain_key_either(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    b._traversal_budget = 2  # type: ignore[attr-defined]  # over budget: the repair becomes a durable job
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    (job,) = b.storage.jobs()
    assert job.state == "pending" and P not in job.payload and PREFIX in job.payload
    assert P not in stored_keys(h)
    rep = b.complete_pending(ChainReviser())  # the job runs on stored-form keys and finishes
    assert (rep.jobs_done, rep.jobs_pending) == (1, 0)
    for k in (A, B, C):
        got = b.read_belief(k)
        assert isinstance(got, Belief) and status(got) is KernelStatus.UNKNOWN
    assert not isinstance(b.read_belief(A), LimitedRead)
    assert P not in stored_keys(h) and b.verify_beliefs(ChainReviser()).ok


# ---------------------------------------------------------------- the pure helpers


def test_translation_is_the_identity_until_something_is_pseudonymised() -> None:
    ps = EntityPseudonyms(b"k" * 32)
    k = Key(entity="alice", attr="employer")
    assert not ps.active and ps.key(k) is k
    ref = ps.ref_of("alice")
    ps.add(ref)
    assert ps.active and ps.key(k) == Key(entity=ref, attr="employer") and ps.key(Key(entity=ref, attr="x")).entity == ref
    assert ps.key(Key(entity="bob", attr="employer")).entity == "bob"
    assert EntityPseudonyms.load(b"k" * 32, ps.dump()).key(k) == ps.key(k)
    ps.discard(ref)
    assert ps.key(k) is k
    with pytest.raises(ValueError):
        EntityPseudonyms(None).ref_of("alice")


def test_rekeying_a_belief_moves_candidate_ids_and_support_consistently() -> None:
    key = Key(entity=P, attr="employer")
    c = Candidate(key=key, form=ValueForm(value="SecretCorp"))
    seg = Segment(
        valid_from=None, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=c,
        support={c.id: (Support(environment=("01JZ0000000000000000000001",)),)},
    )
    b = Belief(
        key=key, version=1, lsn=1, required_generation=2, completed_generation=1, segments=(seg,), pinned=(), depends_on=(Dependency(key=Key(entity=P, attr="hq_city"), version=1),),
        invalidated_by=None, versions=Versions(schema=1, semantic=1, admission=1), inference=Inference(complete=False, reason=f"stale: {P}/employer"),
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rw = EntityRewrite(P, PREFIX + "abc")
    nb = rekey_belief(b, rw.key, rw.text)
    assert nb.key.entity == PREFIX + "abc" and nb.depends_on[0].key.entity == PREFIX + "abc"
    nc = nb.segments[0].established
    assert nc is not None and nc.key == nb.key and nc.id != c.id  # the id is a hash of the key: it moved with it
    assert list(nb.segments[0].support) == [nc.id]
    assert P not in nb.to_json() and Belief.from_json(nb.to_json()) == nb  # decodes (the id is verified) and round-trips
    assert rekey_belief(nb, EntityRewrite(PREFIX + "abc", P).key, EntityRewrite(PREFIX + "abc", P).text).key.entity == P


def test_the_secret_values_list_is_unchanged() -> None:
    assert "SecretCorp" in SECRET_VALUES  # guards the shared fixture this module imports


FakeAdmitter  # noqa: B018  (re-exported fixture dependency of test_erasure)
