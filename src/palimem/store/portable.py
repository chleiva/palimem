"""JSONL export/import of the evidence log (T-C9): the line formats.

One JSON object per line (canonical JSON), in this order::

    {"type": "header", "format": "palimem-log", "version": 1, "store_format": 2, "chain": true, "head": {...}}
    {"type": "input", "kind": ..., "version": ..., "effective_lsn": ..., "recorded_us": ..., "payload": {...}}  *
    {"type": "log", "lsn": ..., "report_id": ..., "recorded_us": ..., "generation": ..., "content": "...",
     "salt": "<hex>", "commitment": ..., "prev_hash": ..., "entry_hash": ..., "idem_key": ..., "tomb": null}  *
    {"type": "admission", "seq": ..., "lsn": ..., "report_id": ..., "record": "...", "report_entry_hash": ...,
     "prev_hash": ..., "entry_hash": ...}  *
    {"type": "footer", "head": {...}, "log_rows": n, "admissions": n, "inputs": n}

Erased rows are exported as tombstones (no content, no salt, no key). Beliefs are NOT exported: an import
replays the revision stage with the same Reviser, which is what makes the result checkable. Salts are
exported with the content because ``verify_log`` needs them; treat an export like the database itself.
"""

from __future__ import annotations

from typing import Any

from palimem.store._storage import AdmRow, InputRow, LogRow
from palimem.store.backend import StoreError
from palimem.types import Report, canonical_json, parse_json

FORMAT_NAME = "palimem-log"


def dumps(obj: dict[str, Any]) -> str:
    return canonical_json(obj)


def log_line(row: LogRow) -> str:
    return dumps(
        {
            "type": "log",
            "lsn": row.lsn,
            "report_id": row.report_id,
            "recorded_us": row.recorded_us,
            "generation": row.generation,
            "content": row.content,
            "salt": None if row.salt is None else row.salt.hex(),
            "commitment": row.commitment,
            "prev_hash": row.prev_hash,
            "entry_hash": row.entry_hash,
            "idem_key": row.idem_key,
            "tomb": None if row.tomb is None else parse_json(row.tomb),
        }
    )


def adm_line(row: AdmRow) -> str:
    return dumps(
        {
            "type": "admission",
            "seq": row.seq,
            "lsn": row.lsn,
            "report_id": row.report_id,
            "record": row.record,
            "report_entry_hash": row.report_entry_hash,
            "prev_hash": row.prev_hash,
            "entry_hash": row.entry_hash,
        }
    )


def input_line(row: InputRow) -> str:
    return dumps(
        {
            "type": "input",
            "kind": row.kind,
            "version": row.version,
            "effective_lsn": row.effective_lsn,
            "recorded_us": row.recorded_us,
            "payload": parse_json(row.payload),
        }
    )


def to_log_row(d: dict[str, Any]) -> LogRow:
    try:
        content = d["content"]
        tomb = d["tomb"]
        key = None
        if tomb is None:
            if not isinstance(content, str):
                raise StoreError(f"import: log row {d['lsn']} has neither content nor a tombstone")
            key = Report.from_dict(parse_json(content)).key
        return LogRow(
            lsn=int(d["lsn"]),
            report_id=str(d["report_id"]),
            recorded_us=int(d["recorded_us"]),
            generation=int(d["generation"]),
            key=key,
            content=None if tomb is not None else content,
            salt=None if d["salt"] is None else bytes.fromhex(d["salt"]),
            commitment=d["commitment"],
            prev_hash=d["prev_hash"],
            entry_hash=d["entry_hash"],
            idem_key=str(d["idem_key"]),
            tomb=None if tomb is None else canonical_json(tomb),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise StoreError(f"import: malformed log line: {e}") from e


def to_adm_row(d: dict[str, Any]) -> AdmRow:
    try:
        return AdmRow(
            seq=int(d["seq"]),
            lsn=int(d["lsn"]),
            report_id=str(d["report_id"]),
            record=str(d["record"]),
            report_entry_hash=d["report_entry_hash"],
            prev_hash=d["prev_hash"],
            entry_hash=d["entry_hash"],
        )
    except (KeyError, TypeError, ValueError) as e:
        raise StoreError(f"import: malformed admission line: {e}") from e


def to_input_row(d: dict[str, Any]) -> InputRow:
    try:
        return InputRow(
            kind=str(d["kind"]),
            version=int(d["version"]),
            effective_lsn=int(d["effective_lsn"]),
            recorded_us=int(d["recorded_us"]),
            payload=canonical_json(d["payload"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise StoreError(f"import: malformed input line: {e}") from e
