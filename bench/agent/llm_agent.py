"""LLM-in-the-loop RETRACT-ACT: the agent loop, the Bedrock transport, the cache, the ledger and the runner.

One model call per decision point (the decision is the unit of measurement; no tool is executed). The model sees a
fixed system prompt plus the memory context produced by the system under test (``llm_systems``) and answers with one
JSON object ``{"action", "value", "reason"}``. Replies that cannot be used get ONE repair re-prompt (it carries only our
own validation message, never model text); a reply still unusable is a *missing response* (scored ``abstain``).

Money: every live call goes through ``palimem.costs.CostLedger`` (the shared ledger named by ``PALIMEM_LEDGER``, a FILE
path), after a written estimate (``estimate``), inside a task budget recorded once in ``runs/task_budget.json``.
Raw model replies are cached (``--cache``), so scoring and ``rescore`` are free and offline.

    python bench/agent/llm_agent.py estimate --split dev --model gpt-oss-20b --system llm+palimem
    PALIMEM_ALLOW_PAID_CALLS=1 PALIMEM_LEDGER=/abs/ledger.jsonl python bench/agent/llm_agent.py run \
        --split dev --model gpt-oss-20b --system llm+palimem --prompt v1 --out OUT.json --cache CACHE.jsonl --execute
    python bench/agent/llm_agent.py rescore OUT.json --cache CACHE.jsonl      # offline: must reproduce OUT.json

The test split is run ONCE per (model, system): ``results/llm_test_runs.json`` records the prompt, adapter and scenario
hashes and refuses a second test run unless ``--allow-test-rerun`` is given (and then it must be disclosed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # direct use: python bench/agent/llm_agent.py
    sys.path.insert(0, str(HERE))

import llm_systems as _sys
import score as _score

from palimem.costs import CAP_USD, BudgetExceeded, CostLedger

RESULTS_DIR = HERE / "results"
RUNS_DIR = HERE / "runs"
TEST_REGISTRY = RESULTS_DIR / "llm_test_runs.json"
TASK_BUDGET = RUNS_DIR / "task_budget.json"
PAID_ENV = "PALIMEM_ALLOW_PAID_CALLS"
LEDGER_ENV = "PALIMEM_LEDGER"
TASK_CAP_USD = 3.0

MODELS: dict[str, dict[str, Any]] = {
    "gpt-oss-20b": {"id": "openai.gpt-oss-20b-1:0", "max_tokens": 3000},
    "ministral-14b": {"id": "mistral.ministral-3-14b-instruct", "max_tokens": 700},
}
# (sample index, temperature): the primary analysis uses the temperature-0.0 samples; the 0.7 sample is a robustness check
DEFAULT_SAMPLES: tuple[tuple[int, float], ...] = ((0, 0.0), (1, 0.0), (2, 0.0), (3, 0.7))
ACTIONS = ("act", "abstain", "ask", "revalidate")

# ------------------------------------------------------------------------------------------------- prompt (frozen text)

SYSTEM_V1 = """You are an assistant that must decide what to do next in a business task. Before acting you consult a memory. A mistaken action causes harm that is expensive to undo, asking someone costs a little, and you should not ask when the memory clearly supports a value.

You choose exactly one action:
- act: carry out the task now using the value the memory justifies. Put that value in "value".
- ask: do not act; ask the source or person who can supply the missing or deciding evidence.
- abstain: do not act and do not interrupt anyone.
- revalidate: you formed a plan earlier and your memory has changed since; re-read the memory and act on the current value. Put the current value in "value".

A required task must be completed: if you cannot act safely, ask. An optional task can be dropped: if you cannot act safely, abstain.
When you are asked to review an action that has already been taken, answer "act" to let it stand, or "ask" to raise a problem with the people who must decide (the action rests on something that is no longer justified).

{memory_note}

Reply with one JSON object and nothing else:
{{"action": "act" | "ask" | "abstain" | "revalidate", "value": "<the value you act on, or null>", "reason": "<one sentence>"}}"""

SYSTEM_V2 = SYSTEM_V1.replace(
    "- act: carry out the task now using the value the memory justifies. Put that value in \"value\".",
    "- act: carry out the task now using the value the memory justifies. Put in \"value\" the exact value from the memory "
    "that the task depends on (a city, an address, an account number...), copied as the memory states it; never a "
    "description of the task.",
).replace(
    "When you are asked to review an action that has already been taken, answer \"act\" to let it stand, or \"ask\" to raise "
    "a problem with the people who must decide (the action rests on something that is no longer justified).",
    "When you are asked to review an action that has already been taken, answer \"act\" only if the action still stands "
    "(the memory still supports what it rested on); answer \"ask\" if the memory now says something different or no "
    "longer supports it, so that the people who must decide are told.",
)

PROMPTS: dict[str, str] = {"v1": SYSTEM_V1, "v2": SYSTEM_V2}


def prompt_hash(version: str) -> str:
    notes = f"{_sys.NOTE_LWW}\n{_sys.NOTE_RAW_LOG}\n{_sys.NOTE_PALIMEM}"
    return hashlib.sha256(f"{PROMPTS[version]}\n--\n{notes}".encode()).hexdigest()


def system_prompt(version: str, memory_note: str) -> str:
    return PROMPTS[version].format(memory_note=memory_note)


def user_prompt(scn: dict, dp: dict, today: int, ctx_now: str, ctx_then: str | None, action_note: str | None) -> str:
    lines = [f"Today is day {today}.", f"Task ({dp['mode']}): {dp['task']}"]
    if dp.get("valid_at") is not None:
        lines.append(f"The task concerns day {dp['valid_at']}.")
    if dp["kind"] == "post_hoc_review":
        lines.append(f"An action has already been taken: {action_note}")
        lines.append("Review it.")
    elif dp.get("plan_formed_after"):
        lines.append("You formed a plan for this task earlier.")
    if ctx_then is not None:
        what = "when the action was taken" if dp["kind"] == "post_hoc_review" else "when you planned"
        lines += ["", f"Memory {what}:", ctx_then, "", "Memory now:", ctx_now]
    else:
        lines += ["", "Memory:", ctx_now]
    lines += ["", "Decide."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------- reply parsing

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
PARSER_VERSION = "p2"  # p1: value taken verbatim; p2: one pair of surrounding quotes is stripped (both memories render values quoted)


def unquote(value: str) -> str:
    """Strip whitespace and ONE pair of matching surrounding quotes: a rendering artifact of the memory text
    (``ESTABLISHED = 'globex'``, ``'globex'``), not part of the value. Applied identically to every system."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1].strip()
    return v


def parse_reply(text: str) -> tuple[dict | None, str | None]:
    """(response, None) or (None, why). ``response`` is ``{"action", "value", "reason"}``."""
    s = text.strip()
    m = _FENCE.search(s)
    if m:
        s = m.group(1).strip()
    obj = None
    try:
        obj = json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                obj = json.loads(s[i : j + 1])
            except ValueError:
                obj = None
    if not isinstance(obj, dict):
        return None, "no JSON object found"
    action = obj.get("action")
    if not isinstance(action, str) or action.strip().lower() not in ACTIONS:
        return None, f"action must be one of {', '.join(ACTIONS)}"
    action = action.strip().lower()
    value = obj.get("value")
    if value is not None and not isinstance(value, str | int | float):
        return None, "value must be a string or null"
    if value is not None:
        value = unquote(str(value))
    if action == "act" and (value is None or not value.strip() or value.strip().lower() in ("null", "none")):
        return None, "action act needs a value"
    if action in ("ask", "abstain"):
        value = None
    return {"action": action, "value": value, "reason": str(obj.get("reason", ""))[:500]}, None


# ----------------------------------------------------------------------------------------------- transport and cache


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None


class CacheMiss(Exception):
    pass


def cache_key(model: str, system: str, user: str, max_tokens: int, temperature: float, sample: int, attempt: int) -> str:
    blob = "\x1f".join([model, system, user, str(max_tokens), repr(float(temperature)), str(sample), str(attempt)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ReplyCache:
    """JSONL cache of raw model replies (model output only: no prompts, no credentials)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._by_key: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._by_key[rec["key"]] = rec

    def get(self, key: str) -> Reply | None:
        rec = self._by_key.get(key)
        return None if rec is None else Reply(rec["text"], rec.get("input_tokens"), rec.get("output_tokens"), rec.get("stop_reason"))

    def put(self, key: str, model: str, reply: Reply) -> None:
        rec = {"key": key, "model": model, "text": reply.text, "input_tokens": reply.input_tokens,
               "output_tokens": reply.output_tokens, "stop_reason": reply.stop_reason}
        with self._lock:
            self._by_key[key] = rec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

    def __len__(self) -> int:
        return len(self._by_key)


class Chat(Protocol):
    def complete(self, *, model: str, system: str, user: str, max_tokens: int, temperature: float) -> Reply: ...


class BedrockChat:
    """Amazon Bedrock Converse in us-west-2; needs PALIMEM_ALLOW_PAID_CALLS=1. One transport retry on throttling."""

    THROTTLE = ("ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException", "InternalServerException")

    def __init__(self, region: str = "us-west-2", client: Any = None, sleep: float = 6.0) -> None:
        self.region, self._client, self._sleep = region, client, sleep

    def _make(self) -> Any:
        if os.environ.get(PAID_ENV) != "1":
            raise RuntimeError(f"real model calls are disabled; set {PAID_ENV}=1 to allow them")
        import boto3  # optional extra

        return boto3.client("bedrock-runtime", region_name=self.region)

    def complete(self, *, model: str, system: str, user: str, max_tokens: int, temperature: float) -> Reply:
        if self._client is None:
            self._client = self._make()
        last: Exception | None = None
        for attempt in range(2):  # the call plus at most one retry, on throttling only
            try:
                resp = self._client.converse(
                    modelId=model, system=[{"text": system}],
                    messages=[{"role": "user", "content": [{"text": user}]}],
                    inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
                )
                blocks = resp["output"]["message"]["content"]
                text = "".join(b["text"] for b in blocks if "text" in b)  # reasoning blocks are not the answer
                usage = resp.get("usage", {})
                return Reply(text, usage.get("inputTokens"), usage.get("outputTokens"), resp.get("stopReason"))
            except Exception as e:
                code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
                if code in self.THROTTLE and attempt == 0:
                    last = e
                    time.sleep(self._sleep)
                    continue
                raise
        raise RuntimeError(f"unreachable: {last}")


def _never_billed(e: Exception) -> bool:
    """True only when the request provably never reached the model (disabled, throttled, rejected before inference)."""
    if isinstance(e, RuntimeError) and PAID_ENV in str(e):
        return True
    code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
    return code in (*BedrockChat.THROTTLE, "ValidationException", "AccessDeniedException", "ResourceNotFoundException")


class LedgerGate:
    """Cache first, then the ledger, then the model: the only way a live call is made."""

    def __init__(self, chat: Chat | None, cache: ReplyCache, ledger: CostLedger | None, purpose: str) -> None:
        self.chat, self.cache, self.ledger, self.purpose = chat, cache, ledger, purpose
        self.calls = self.cache_hits = 0
        self.cost = 0.0
        self.tokens_in = self.tokens_out = 0
        self._lock = threading.Lock()

    def complete(self, *, model: str, system: str, user: str, max_tokens: int, temperature: float, sample: int,
                 attempt: int) -> Reply:
        key = cache_key(model, system, user, max_tokens, temperature, sample, attempt)
        hit = self.cache.get(key)
        if hit is not None:
            with self._lock:
                self.cache_hits += 1
            return hit
        if self.chat is None or self.ledger is None:
            raise CacheMiss("request not in the cache and live calls are not enabled")
        est_in = math.ceil((len(system) + len(user)) / 3.0)
        res = self.ledger.authorize(model, est_in, max_tokens, f"{self.purpose} s{sample}a{attempt}")
        with res:  # an unfinalised reservation is charged at its worst case: never free budget on an unknown outcome
            try:
                reply = self.chat.complete(model=model, system=system, user=user, max_tokens=max_tokens, temperature=temperature)
            except Exception as e:
                if _never_billed(e):
                    res.cancel()
                raise
            cost = res.commit(reply.input_tokens or est_in, reply.output_tokens or max_tokens)
        self.cache.put(key, model, reply)
        with self._lock:
            self.calls += 1
            self.cost += cost
            self.tokens_in += reply.input_tokens or 0
            self.tokens_out += reply.output_tokens or 0
        return reply


# ------------------------------------------------------------------------------------------------------ the decision


def repair_message(why: str) -> str:
    return (f"\n\nYour previous reply could not be used ({why}). Reply with only the JSON object described above, "
            "with a valid action and, for act, a value.")


def decide(gate: LedgerGate, model_id: str, max_tokens: int, system: str, user: str, sample: int, temperature: float) -> dict:
    """One decision with at most one repair. Returns the record the runner stores."""
    rec: dict[str, Any] = {"response": None, "reason": None, "repaired": False, "missing": True, "parse_error": None,
                           "stop_reasons": [], "error": None}
    why = ""
    for attempt in range(2):
        u = user if attempt == 0 else user + repair_message(why)
        try:
            reply = gate.complete(model=model_id, system=system, user=u, max_tokens=max_tokens, temperature=temperature,
                                  sample=sample, attempt=attempt)
        except BudgetExceeded:
            raise
        except CacheMiss:
            rec["error"] = "CacheMiss"
            return rec
        except Exception as e:  # noqa: BLE001 - a failed call is a missing response, recorded, never retried silently
            rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            return rec
        rec["stop_reasons"].append(reply.stop_reason)
        parsed, why_or_none = parse_reply(reply.text)
        if parsed is not None:
            rec.update(response={"action": parsed["action"], "value": parsed["value"]}, reason=parsed["reason"],
                       repaired=attempt == 1, missing=False, parse_error=None)
            return rec
        why = why_or_none or "unusable"
        rec["parse_error"] = why
        rec["repaired"] = attempt == 1
    return rec


# ----------------------------------------------------------------------------------------------------- the run loop


@dataclass
class Point:
    scenario: str
    point: str
    system: str
    user: str
    ingest_error: bool


def collect_points(system_name: str, scenarios: list[dict], prompt_version: str) -> tuple[list[Point], dict]:
    """Replay each scenario into the system under test (free, deterministic) and build every prompt."""
    points: list[Point] = []
    ingest_errors: dict[str, dict[str, str]] = {}
    for scn in scenarios:
        memsys = _sys.make_system(system_name, scn)
        sysmsg = system_prompt(prompt_version, memsys.note)
        by_id = {r["id"]: r for r in scn["reports"]}
        executed = list(scn.get("executed_actions", []))
        for r in scn["reports"]:
            err = memsys.ingest(r)
            if err:
                ingest_errors.setdefault(scn["id"], {})[r["id"]] = err
            for dp in scn["decision_points"]:
                if dp["after_report"] != r["id"]:
                    continue
                today = r["recorded_at"]
                ctx_now = memsys.context(dp)
                ctx_then, note = None, None
                if dp["kind"] == "post_hoc_review":
                    done = [e for e in executed if _order(scn, e["after_report"]) <= _order(scn, dp["after_report"])]
                    if done:
                        ex = max(done, key=lambda e: _order(scn, e["after_report"]))
                        day = by_id[ex["after_report"]]["recorded_at"]
                        note = f"{ex['note']} (executed on day {day})"
                        ctx_then = memsys.context(dp, ex["after_report"])
                    else:
                        note = "(no executed action is recorded for this review)"
                elif dp.get("plan_formed_after"):
                    ctx_then = memsys.context(dp, dp["plan_formed_after"])
                points.append(Point(scn["id"], dp["id"], sysmsg, user_prompt(scn, dp, today, ctx_now, ctx_then, note),
                                    bool(ingest_errors.get(scn["id"]))))
        memsys.close()
    return points, ingest_errors


def _order(scn: dict, rid: str) -> int:
    return [r["id"] for r in scn["reports"]].index(rid)


def sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def adapter_hash() -> str:
    h = hashlib.sha256()
    for name in ("llm_agent.py", "llm_systems.py"):
        h.update((HERE / name).read_bytes())
    return h.hexdigest()


def parse_samples(spec: str) -> tuple[tuple[int, float], ...]:
    return tuple((int(a), float(b)) for a, b in (x.split(":") for x in spec.split(",")))


def run(split: str, model_key: str, system_name: str, prompt_version: str, samples: tuple[tuple[int, float], ...],
        gate: LedgerGate, workers: int = 4, scenarios: list[dict] | None = None) -> dict:
    scns = scenarios if scenarios is not None else _score.load_scenarios(split=split)
    points, ingest_errors = collect_points(system_name, scns, prompt_version)
    model = MODELS[model_key]
    jobs = [(p, s, t) for p in points for (s, t) in samples]
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def work(job: tuple[Point, int, float]) -> tuple[Point, int, float, dict]:
        p, s, t = job
        return p, s, t, decide(gate, model["id"], model["max_tokens"], p.system, p.user, s, t)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        done = list(ex.map(work, jobs))
    out_samples: dict[str, dict] = {str(s): {"temperature": t, "responses": {}} for s, t in samples}
    records: list[dict] = []
    for p, s, t, rec in done:
        resp = out_samples[str(s)]["responses"].setdefault(p.scenario, {})
        if rec["response"] is not None:
            resp[p.point] = rec["response"]
        records.append({"scenario": p.scenario, "point": p.point, "sample": s, "temperature": t,
                        "ingest_error": p.ingest_error, **rec})
    return {
        "system": system_name, "model": model_key, "model_id": model["id"], "split": split, "prompt_version": prompt_version,
        "prompt_sha256": prompt_hash(prompt_version), "parser_version": PARSER_VERSION, "adapter_sha256": adapter_hash(), "scenarios_sha256": sha(scns),
        "samples": out_samples, "records": records, "ingest_errors": ingest_errors, "started_utc": started,
        "calls": {"live": gate.calls, "cache_hits": gate.cache_hits, "cost_usd": round(gate.cost, 6),
                  "input_tokens": gate.tokens_in, "output_tokens": gate.tokens_out},
    }


# ------------------------------------------------------------------------------------------------- budget and registry


def task_budget_cap(ledger_path: Path) -> float:
    """The ledger cap for this task: the exposure when the task started plus TASK_CAP_USD (recorded once, then fixed)."""
    if TASK_BUDGET.exists():
        b = json.loads(TASK_BUDGET.read_text())
    else:
        start = CostLedger(path=ledger_path).summary()["exposure_usd"]
        b = {"start_exposure_usd": round(start, 6), "task_cap_usd": TASK_CAP_USD, "recorded_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        RUNS_DIR.mkdir(exist_ok=True)
        TASK_BUDGET.write_text(json.dumps(b, indent=1, sort_keys=True) + "\n")
    return min(CAP_USD, b["start_exposure_usd"] + b["task_cap_usd"])


def estimate(split: str, model_key: str, system_name: str, prompt_version: str, samples: tuple[tuple[int, float], ...],
             scenarios: list[dict] | None = None) -> dict:
    """A written estimate, no API call: worst case at the ledger's prices (every call reserves max tokens), plus the
    expected cost at 1,400 reasoning tokens per gpt-oss reply and 80 for ministral."""
    scns = scenarios if scenarios is not None else _score.load_scenarios(split=split)
    points, _ = collect_points(system_name, scns, prompt_version)
    led = CostLedger(path=Path(os.environ.get(LEDGER_ENV, "ledger/ledger.jsonl")), cap_usd=CAP_USD)
    model = MODELS[model_key]
    n_calls = len(points) * len(samples)
    in_tok = sum(math.ceil((len(p.system) + len(p.user)) / 3.0) for p in points) * len(samples)
    worst = n_calls * 1.0 * led.estimate(model["id"], 0, model["max_tokens"]) + led.estimate(model["id"], in_tok, 0)
    exp_out = 1400 if model_key == "gpt-oss-20b" else 80
    expected = led.estimate(model["id"], in_tok, n_calls * exp_out)
    return {"split": split, "model": model_key, "system": system_name, "calls": n_calls, "input_tokens_est": in_tok,
            "worst_case_usd": round(worst, 5), "expected_usd": round(expected, 5), "repairs_not_included": True}


def _registry() -> dict:
    return json.loads(TEST_REGISTRY.read_text()) if TEST_REGISTRY.exists() else {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "estimate"):
        p = sub.add_parser(name)
        p.add_argument("--split", choices=["dev", "test"], required=True)
        p.add_argument("--model", choices=sorted(MODELS), required=True)
        p.add_argument("--system", choices=list(_sys.SYSTEM_NAMES), required=True)
        p.add_argument("--prompt", choices=sorted(PROMPTS), default="v1")
        p.add_argument("--samples", default=",".join(f"{s}:{t}" for s, t in DEFAULT_SAMPLES))
    runp = sub.choices["run"]
    runp.add_argument("--out", required=True)
    runp.add_argument("--cache", required=True)
    runp.add_argument("--execute", action="store_true", help="make live (paid) calls; without it only the cache is used")
    runp.add_argument("--workers", type=int, default=4)
    runp.add_argument("--allow-test-rerun", action="store_true")
    rs = sub.add_parser("rescore")
    rs.add_argument("out")
    rs.add_argument("--cache", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "estimate":
        print(json.dumps(estimate(a.split, a.model, a.system, a.prompt, parse_samples(a.samples)), indent=1))
        return 0
    if a.cmd == "rescore":
        old = json.loads(Path(a.out).read_text())
        gate = LedgerGate(None, ReplyCache(Path(a.cache)), None, "rescore")
        new = run(old["split"], old["model"], old["system"], old["prompt_version"],
                  tuple((int(s), v["temperature"]) for s, v in old["samples"].items()), gate, workers=1)
        same = new["samples"] == old["samples"]
        print("offline rescore reproduces the stored responses:", same)
        return 0 if same else 1

    samples = parse_samples(a.samples)
    key = f"{a.model}|{a.system}"
    if a.split == "test":
        reg = _registry()
        if key in reg and not a.allow_test_rerun:
            print(f"REFUSED: {key} already ran on the test split ({reg[key]['utc']}); the protocol allows one run. "
                  "Use --allow-test-rerun and disclose it.", file=sys.stderr)
            return 2
    est = estimate(a.split, a.model, a.system, a.prompt, samples)
    print("estimate:", json.dumps(est))
    ledger = chat = None
    if a.execute:
        lp = os.environ.get(LEDGER_ENV)
        if not lp or Path(lp).is_dir():
            print(f"refusing: set {LEDGER_ENV} to the shared ledger FILE path", file=sys.stderr)
            return 2
        ledger = CostLedger(path=Path(lp), cap_usd=task_budget_cap(Path(lp)))
        if est["worst_case_usd"] > ledger.remaining_usd():
            print(f"refusing: worst case ${est['worst_case_usd']} exceeds the remaining task budget ${ledger.remaining_usd():.4f}", file=sys.stderr)
            return 2
        chat = BedrockChat()
    gate = LedgerGate(chat, ReplyCache(Path(a.cache)), ledger, f"RETRACT-ACT {a.split} {a.model} {a.system} {a.prompt}")
    out = run(a.split, a.model, a.system, a.prompt, samples, gate, workers=a.workers)
    out["estimate"] = est
    out["cache"] = Path(a.cache).name
    if ledger is not None:
        out["ledger_after"] = ledger.summary()
    if a.split == "test":
        reg = _registry()
        prev = reg.get(key)
        reg[key] = {"utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "prompt_sha256": out["prompt_sha256"],
                    "adapter_sha256": out["adapter_sha256"], "scenarios_sha256": out["scenarios_sha256"],
                    "reruns": 0 if prev is None else prev.get("reruns", 0) + 1}
        RESULTS_DIR.mkdir(exist_ok=True)
        TEST_REGISTRY.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    miss = sum(r["missing"] for r in out["records"])
    rep = sum(r["repaired"] for r in out["records"])
    print(f"done: {len(out['records'])} decisions, live calls {out['calls']['live']}, cache hits {out['calls']['cache_hits']}, "
          f"cost ${out['calls']['cost_usd']}, repaired {rep}, missing {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
