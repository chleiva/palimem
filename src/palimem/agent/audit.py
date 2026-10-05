"""The audit log of the trust boundary (docs/API_TRUST_BOUNDARY.md R2, R11).

Append-only and separate from the evidence log. It records every downgrade, denial and refusal as
``{seq, event, tool, session, fields, ...}``. It never stores the *content* of a rejected text beyond a
hash (R11), so an injection payload cannot be recovered from the audit trail.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AuditRow:
    seq: int
    event: str
    at: datetime
    tool: str | None = None
    session: str | None = None
    fields: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"seq": self.seq, "event": self.event, "at": self.at.isoformat()}
        if self.tool is not None:
            out["tool"] = self.tool
        if self.session is not None:
            out["session"] = self.session
        out["fields"] = list(self.fields)
        out.update(dict(self.detail))
        return out


class AuditLog:
    """In-memory by default; with a ``path`` every row is also appended to a JSONL file (reloaded on open)."""

    def __init__(self, path: str | Path | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or _utcnow
        self._rows: list[AuditRow] = []
        self._lock = threading.Lock()
        self._path = Path(path) if path is not None else None
        if self._path is not None and self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    base = {"seq", "event", "at", "tool", "session", "fields"}
                    self._rows.append(AuditRow(
                        seq=d["seq"], event=d["event"], at=datetime.fromisoformat(d["at"]), tool=d.get("tool"),
                        session=d.get("session"), fields=tuple(d.get("fields", ())),
                        detail=MappingProxyType({k: v for k, v in d.items() if k not in base}),
                    ))

    def append(
        self, event: str, *, tool: str | None = None, session: str | None = None, fields: Sequence[str] = (),
        content: str | None = None, **detail: Any,
    ) -> AuditRow:
        """Record an event. ``content`` (a rejected text, say) is stored only as a SHA-256 hash."""
        d = dict(detail)
        if content is not None:
            d["content_hash"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            row = AuditRow(
                seq=len(self._rows) + 1, event=event, at=self._clock(), tool=tool, session=session,
                fields=tuple(fields), detail=MappingProxyType(d),
            )
            self._rows.append(row)
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
        return row

    def rows(self, since: int = 0) -> list[AuditRow]:
        """Rows with ``seq > since``."""
        with self._lock:
            return [r for r in self._rows if r.seq > since]

    def __len__(self) -> int:
        return len(self._rows)
