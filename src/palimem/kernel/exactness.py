"""Static exactness check, run at schema load (T-B5).

The per-key kernel is exact under two conditions (design v0.3, Conceptual model):

1. **Same-key relations.** Every admissibility relation (dispute, correction, change-from, self-update)
   relates reports on the same key. This holds by construction of the kernel (it sees one key's evidence);
   admission checks it per report with :func:`palimem.kernel.evidence.cross_key_corrections`.
2. **No base key reached twice on a derivation path.** The rule engine branches over per-key *unions* of
   candidates; if two body literals (or two rules of one head) can read the same base key, the union
   over-generates (the reviewer's counter-example: worlds ``{A, B, empty}`` from two literals that read
   one key whose true worlds are ``{A}`` or ``{B}``, against the correct ``{A, B}``). So the attribute
   closures of the literals of one rule, and of the rules of one head, must be pairwise disjoint.

A schema violating (2) is refused; the global enumeration oracle (the study's ``oracle_v2``) remains
available for audits of such keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from palimem.kernel.derive import base_attrs_closure
from palimem.kernel.spec import KernelSchema, KernelUnsupported


class ExactnessViolation(ValueError):
    """The schema breaks a condition under which the per-key kernel is exact."""


@dataclass(frozen=True)
class Overlap:
    head: str
    where: str
    shared: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.head}: {self.where} both reach base attribute(s) {', '.join(self.shared)}"


def find_overlaps(schema: KernelSchema) -> list[Overlap]:
    out: list[Overlap] = []
    for attr, spec in schema.attrs.items():
        if not spec.derived:
            continue
        per_rule: list[set[str]] = []
        for r in schema.rules_for(attr):
            lits = list(r.body) + list(r.exceptions)
            closures = [base_attrs_closure(schema, la) for la, _x, _y in lits]
            for i in range(len(closures)):
                for j in range(i + 1, len(closures)):
                    shared = closures[i] & closures[j]
                    if shared:
                        out.append(Overlap(attr, f"rule {r.id}: literals {lits[i][0]!r} and {lits[j][0]!r}", tuple(sorted(shared))))
            per_rule.append(set().union(*closures) if closures else set())
        for i in range(len(per_rule)):
            for j in range(i + 1, len(per_rule)):
                shared = per_rule[i] & per_rule[j]
                if shared:
                    out.append(Overlap(attr, f"rules #{i} and #{j}", tuple(sorted(shared))))
    return out


def check_schema(schema: KernelSchema) -> None:
    """Refuse a schema the per-key kernel cannot answer exactly."""
    for r in schema.rules:
        head = r.head[0]
        if head not in schema.attrs or not schema.attrs[head].derived:
            raise KernelUnsupported(f"rule {r.id}: head {head!r} is not a declared derived attribute")
        for la, _x, _y in r.body + r.exceptions:
            if la not in schema.attrs:
                raise KernelUnsupported(f"rule {r.id}: reads undeclared attribute {la!r}")
    for attr, spec in schema.attrs.items():
        if spec.derived:
            base_attrs_closure(schema, attr)  # raises KernelUnsupported on a rule cycle
    overlaps = find_overlaps(schema)
    if overlaps:
        raise ExactnessViolation(
            "schema violates the per-key kernel's exactness condition (a base key can be reached twice on a "
            "derivation path): " + "; ".join(str(o) for o in overlaps)
        )


def check_all(schemas: Sequence[KernelSchema]) -> None:
    for s in schemas:
        check_schema(s)
