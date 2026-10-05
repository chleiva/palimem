"""The ``palimem`` command line (T-F6): inspect, explain, diff, export / import and verify an existing store, and
serve it to an agent over MCP. Standard library only; output is plain text (``--json`` for tools), stable enough for
golden tests.

Read commands never create a database: a missing file is an error, not an empty store.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO, cast

from palimem import __version__
from palimem.agent import answer_json, answer_text, explanation_json, explanation_text
from palimem.facade import Memory
from palimem.store import Backend, SQLiteBackend
from palimem.types import (
    Answer,
    ExplainMode,
    ExplainQuery,
    Key,
    LogEntry,
    Query,
    Schema,
)
from palimem.types._codec import ts_from_str
from palimem.types.answer import belief_as_of_from_json

EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2


class CliError(Exception):
    pass


def _open(path: str, *, create: bool = False) -> Memory:
    p = Path(path)
    if not create and not p.exists():
        raise CliError(f"no such database: {path}")
    return Memory(p)


def _as_of(s: str | None) -> int | str | None:
    if s is None:
        return None
    return int(s) if s.isdigit() else s


def _asof_value(s: str | None) -> Any:
    v = _as_of(s)
    return None if v is None else belief_as_of_from_json(v)


def _answer(m: Memory, key: Key, valid_at: str | None, as_of: str | None) -> tuple[Answer, dict[str, Any]]:
    va: datetime | None = None if valid_at is None else ts_from_str(valid_at, "valid_at")
    asof = _asof_value(as_of)
    ans = m.host.query(Query(key=key, valid_at=va, belief_as_of=asof, profile=m.core.semantic.profile))
    d = answer_json(ans, host=m.host, key=key, as_of=asof, policy_label=m.core.policy.name or "policy", max_alternatives=50)
    return ans, d


# --------------------------------------------------------------------------- commands


def cmd_inspect(args: argparse.Namespace, out: TextIO) -> int:
    with _open(args.db) as m:
        be = m.backend
        entries = [e for e in be.scan() if isinstance(e, LogEntry)]
        keys = sorted({(e.report.key.entity, e.report.key.attr) for e in entries})
        ver = be.verify_log()
        data: dict[str, Any] = {
            "database": args.db, "head_lsn": be.head().lsn, "reports": len(entries),
            "chain_ok": ver.ok, "schema_version": m.schema.version,
            "attributes": [a.name for a in m.schema.attrs],
            "admission_version": m.core.admission.admission_version, "policy": m.core.policy.name or "policy",
            "by_origin": dict(sorted(Counter(e.report.origin.value for e in entries).items())),
            "by_cue": dict(sorted(Counter(e.report.cue.value for e in entries).items())),
            "keys": [],
        }
        for entity, attr in keys:
            _, d = _answer(m, Key(entity=entity, attr=attr), None, None)
            row: dict[str, Any] = {"entity": entity, "attr": attr, "kernel_status": d.get("kernel_status", d["kind"])}
            if d["kind"] == "resolved":
                row["decision"] = d["decision"]
                row["single_origin"] = d["single_origin"]
                row["assertion"] = d["assertion"]
            data["keys"].append(row)
    if args.json:
        out.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return EXIT_OK
    out.write(f"database        {data['database']}\n")
    out.write(f"head lsn        {data['head_lsn']} ({data['reports']} reports)\n")
    out.write(f"hash chain      {'ok' if data['chain_ok'] else 'BROKEN (run: palimem verify)'}\n")
    out.write(f"schema          v{data['schema_version']}: {', '.join(data['attributes']) or '(none)'}\n")
    out.write(f"admission       v{data['admission_version']}   policy {data['policy']}\n")
    out.write("by origin       " + (", ".join(f"{k}={v}" for k, v in data["by_origin"].items()) or "-") + "\n")
    out.write("by cue          " + (", ".join(f"{k}={v}" for k, v in data["by_cue"].items()) or "-") + "\n")
    out.write("beliefs now:\n")
    for r in data["keys"]:
        mark = " [single-origin]" if r.get("single_origin") else ""
        val = "" if not r.get("assertion") else " = " + _short(r["assertion"])
        out.write(f"  {r['entity']}/{r['attr']}: {r['kernel_status']}{val}{mark}\n")
    return EXIT_OK


def _short(a: dict[str, Any]) -> str:
    if a["form"] == "value":
        return repr(a["v"])
    if a["form"] == "set":
        return "{" + ", ".join(repr(v) for v in a["values"]) + "}"
    return str(a["form"])


def cmd_explain(args: argparse.Namespace, out: TextIO) -> int:
    with _open(args.db) as m:
        key = Key(entity=args.entity, attr=args.attr)
        ans, d = _answer(m, key, args.valid_at, args.as_of)
        va = None if args.valid_at is None else ts_from_str(args.valid_at, "valid_at")
        exp = m.host.explain(ExplainQuery(
            key=key, valid_at=va, belief_as_of=_asof_value(args.as_of), mode=ExplainMode(args.mode), depth=args.depth,
        ))
        e = explanation_json(exp, host=m.host, depth_applied=args.depth or 8, key=key, answer=ans)
    if args.json:
        out.write(json.dumps({"answer": d, "explanation": e}, indent=2, sort_keys=True) + "\n")
    else:
        out.write(answer_text(d) + "\n" + explanation_text(e) + "\n")
    return EXIT_OK


def cmd_diff(args: argparse.Namespace, out: TextIO) -> int:
    with _open(args.db) as m:
        key = Key(entity=args.entity, attr=args.attr)
        _, a = _answer(m, key, args.valid_at, args.a)
        _, b = _answer(m, key, args.valid_at, args.b)
    fields = ("kind", "kernel_status", "decision", "assertion", "alternatives")
    changed = [f for f in fields if a.get(f) != b.get(f)]
    if args.json:
        out.write(json.dumps({"a": a, "b": b, "changed": changed}, indent=2, sort_keys=True) + "\n")
    else:
        out.write(f"{args.entity}/{args.attr}\n")
        for label, d in (("a", a), ("b", b)):
            ref = args.a if label == "a" else args.b
            val = "" if not d.get("assertion") else " = " + _short(d["assertion"])
            out.write(f"  as of {ref}: {d.get('kernel_status', d['kind'])}{val} (decision {d.get('decision', '-')})\n")
        out.write("  changed: " + (", ".join(changed) if changed else "nothing") + "\n")
    return EXIT_PROBLEM if (changed and args.exit_code) else EXIT_OK


def cmd_export(args: argparse.Namespace, out: TextIO) -> int:
    with _open(args.db) as m:
        lines = list(m.backend.export_jsonl())
    if args.output:
        Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        out.write("\n".join(lines) + "\n")
    return EXIT_OK


def cmd_import(args: argparse.Namespace, out: TextIO) -> int:
    src = Path(args.file)
    if not src.exists():
        raise CliError(f"no such file: {args.file}")
    lines = [ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip()]
    dest = Path(args.db)
    if dest.exists() and dest.stat().st_size > 0:
        raise CliError(f"{args.db} already exists: import needs a new or empty database")
    schema = _schema_from_export(lines)
    # The store must be empty for an import, but a Memory writes its inputs when it is built. So the kernel stages
    # are built over a scratch store and bound to the target for the replay; reopening the result adopts what the
    # export carried (schema, admission, policy).
    with Memory(":memory:", schema=schema) as scratch:
        target = cast(Backend, SQLiteBackend(dest))
        try:
            scratch.core.pipeline.bind(target)
            rep = target.import_jsonl(lines, reviser=scratch.core.reviser)
        finally:
            target.close()
    with Memory(dest) as m:
        ok = m.verify().ok and m.backend.verify_beliefs(m.core.reviser).ok
    out.write(f"imported {rep.log_rows} log rows, {rep.admissions} admissions; verified {'ok' if ok else 'BROKEN'}\n")
    return EXIT_OK if ok else EXIT_PROBLEM


def _schema_from_export(lines: Sequence[str]) -> Schema:
    best: tuple[int, Schema] | None = None
    for ln in lines:
        d = json.loads(ln)
        if d.get("type") == "input" and d.get("kind") == "schema":
            s = Schema.from_dict(d["payload"])
            if best is None or s.version >= best[0]:
                best = (s.version, s)
    if best is None:
        raise CliError("the export carries no schema; cannot rebuild the store")
    return best[1]


def cmd_verify(args: argparse.Namespace, out: TextIO) -> int:
    scope = args.scope
    with _open(args.db) as m:
        log = m.core.verify("log") if scope in ("log", "all") else None
        bel = m.core.verify("beliefs", incremental=args.incremental) if scope in ("beliefs", "all") else None
    problems = [f"log: {p.detail}" for p in (log.problems if log else ())] + [
        f"beliefs: {p.detail}" for p in (bel.problems if bel else ())
    ]
    log_ok = None if log is None else log.ok
    bel_ok = None if bel is None else bel.ok
    data: dict[str, Any] = {"scope": scope, "log_ok": log_ok, "beliefs_ok": bel_ok, "problems": problems}
    if args.json:
        out.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    else:
        if log is not None:
            out.write(f"evidence log   {'ok' if log.ok else 'BROKEN'}\n")
        if bel is not None:
            out.write(f"stored beliefs {'ok' if bel.ok else 'BROKEN'}\n")
        out.writelines(f"  - {p}\n" for p in problems)
    return EXIT_OK if (log_ok is not False and bel_ok is not False) else EXIT_PROBLEM


def cmd_mcp(args: argparse.Namespace, out: TextIO) -> int:
    from palimem.mcp import McpServer

    attrs = tuple(a for a in (args.attrs or "").split(",") if a) or None
    with Memory(args.db) as m:
        tools = m.agent_session(args.principal, session_id=args.session_id, allowed_attrs=attrs)
        McpServer(tools, read_only=args.read_only).serve(sys.stdin, out)
    return EXIT_OK


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="palimem", description="Inspect, verify and serve a palimem store.")
    p.add_argument("--version", action="version", version=f"palimem {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def db(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("db", help="path to the SQLite store")

    sp = sub.add_parser("inspect", help="head, hash chain, schema and the current belief of every key")
    db(sp)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_inspect)

    sp = sub.add_parser("explain", help="what justifies the answer for one key")
    db(sp)
    sp.add_argument("entity")
    sp.add_argument("attr")
    sp.add_argument("--valid-at", help="ISO timestamp in the world (default now)")
    sp.add_argument("--as-of", help="log position (integer) or ISO timestamp (default now)")
    sp.add_argument("--mode", choices=["one", "all"], default="all")
    sp.add_argument("--depth", type=int)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_explain)

    sp = sub.add_parser("diff", help="compare the belief about one key at two points in the log")
    db(sp)
    sp.add_argument("entity")
    sp.add_argument("attr")
    sp.add_argument("--a", required=True, help="first belief_as_of (LSN or timestamp)")
    sp.add_argument("--b", required=True, help="second belief_as_of (LSN or timestamp)")
    sp.add_argument("--valid-at")
    sp.add_argument("--exit-code", action="store_true", help="exit 1 when the beliefs differ")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_diff)

    sp = sub.add_parser("export", help="the evidence log as JSONL (treat it like the database: it carries salts)")
    db(sp)
    sp.add_argument("-o", "--output")
    sp.set_defaults(fn=cmd_export)

    sp = sub.add_parser("import", help="rebuild a store from an export (the target must be new or empty)")
    db(sp)
    sp.add_argument("file")
    sp.set_defaults(fn=cmd_import)

    sp = sub.add_parser("verify", help="check the hash chain and/or recompute the stored beliefs (offline)")
    db(sp)
    sp.add_argument(
        "--scope", choices=["log", "beliefs", "all"], default="all",
        help="log: the hash chain; beliefs: recompute each stored belief from the log, offline (no network, no model); "
             "all: both (default)",
    )
    sp.add_argument("--incremental", action="store_true",
                    help="beliefs scope: check only what changed since the last successful incremental run")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_verify)

    sp = sub.add_parser("mcp", help="serve the agent tool API to one MCP client over stdio")
    db(sp)
    sp.add_argument("--principal", required=True, help="the agent principal this server binds, e.g. agent:support")
    sp.add_argument("--session-id")
    sp.add_argument("--attrs", help="comma-separated attributes the session may touch (default: all)")
    sp.add_argument("--read-only", action="store_true", help="expose recall and explain only")
    sp.set_defaults(fn=cmd_mcp)
    return p


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    try:
        args = build_parser().parse_args(argv)
        return int(args.fn(args, out))
    except CliError as e:
        sys.stderr.write(f"palimem: {e}\n")
        return EXIT_USAGE
    except (ValueError, KeyError) as e:
        sys.stderr.write(f"palimem: {e}\n")
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
