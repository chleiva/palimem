"""LLM extractor: prompt -> model -> strict parse -> host binding, every call through the ledger.

Cost safety (the $20 cap is a hard cap): :class:`LLMExtractor` calls ``CostLedger.authorize``
*before* any request and commits actual usage afterwards; an unknown model or a call that
would exceed the cap is refused before anything is sent. The real transports additionally
refuse to run unless ``PALIMEM_ALLOW_PAID_CALLS=1`` is set, so a stray script cannot spend
money by accident. Tests use an injected fake transport and never touch a network.

Optional extras: ``boto3`` for Bedrock, ``openai`` for OpenAI-compatible endpoints. Both are
imported lazily, so the core stays standard-library only.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from palimem.costs import CostLedger, rough_token_count
from palimem.extract.base import ExtractionResult, Usage
from palimem.extract.build import build_reports
from palimem.extract.claims import Rejection
from palimem.extract.context import ExtractionContext
from palimem.extract.parse import ParseResult, parse_claims
from palimem.extract.prompt import (
    PROMPT_VERSION,
    PROMPT_VERSIONS,
    build_prompt,
    prompt_hash,
)
from palimem.types.report import Extractor as ExtractorStamp

PAID_CALLS_ENV = "PALIMEM_ALLOW_PAID_CALLS"

#: Output-token ceilings per model. gpt-oss bills reasoning tokens as output, so it needs headroom.
DEFAULT_MAX_OUTPUT_TOKENS = {
    "openai.gpt-oss-20b-1:0": 1500,
    "mistral.ministral-3-8b-instruct": 700,
    "mistral.ministral-3-14b-instruct": 700,
}
FALLBACK_MAX_OUTPUT_TOKENS = 800
#: Prompt revision 2: gpt-oss ran out of tokens while reasoning on 3 of 69 dev requests at 1,500 (empty answers),
#: so its ceiling is doubled for the revisions; the Ministral ceilings are unchanged. The frozen v1 keeps its own.
REVISION_MAX_OUTPUT_TOKENS = {"openai.gpt-oss-20b-1:0": 3000}
#: Versions that carry the higher gpt-oss ceiling (revision 0, ``/1c``, is a contract change only and keeps v1's).
HIGHER_CEILING_VERSIONS = frozenset({"palimem-extract/2-pilot", "palimem-extract/2", "palimem-extract/3"})

#: Repair scopes and the rejection reasons that count as *format* errors (never span or identity errors).
REPAIR_SCOPES = ("output", "output_and_claims")
FORMAT_REASONS = frozenset({"invalid_json", "invalid_shape", "invalid_claim"})


def default_max_output_tokens(model: str, prompt_version: str = PROMPT_VERSION) -> int:
    if prompt_version in HIGHER_CEILING_VERSIONS and model in REVISION_MAX_OUTPUT_TOKENS:
        return REVISION_MAX_OUTPUT_TOKENS[model]
    return DEFAULT_MAX_OUTPUT_TOKENS.get(model, FALLBACK_MAX_OUTPUT_TOKENS)


def _format_errors(parsed: ParseResult) -> int:
    """How badly a reply breaks the grammar: an unusable reply is worse than any number of bad claims."""
    if parsed.output_invalid:
        return 1_000_000
    return sum(1 for r in parsed.rejections if r.reason in FORMAT_REASONS)


class TransportNotSent(Exception):
    """The request was definitely not sent, so the reservation can be cancelled, not charged."""


class PaidCallsDisabled(TransportNotSent):
    """A real transport was used without ``PALIMEM_ALLOW_PAID_CALLS=1``."""


@dataclass(frozen=True)
class TransportResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None


class Transport(Protocol):
    def complete(self, *, model: str, system: str, user: str, max_output_tokens: int) -> TransportResponse: ...


def _require_paid_opt_in() -> None:
    if os.environ.get(PAID_CALLS_ENV) != "1":
        raise PaidCallsDisabled(f"real model calls are disabled; set {PAID_CALLS_ENV}=1 to allow them")


class BedrockConverseTransport:
    """Amazon Bedrock ``converse`` (default region us-west-2). Pass ``client=`` to inject a client."""

    def __init__(self, region: str = "us-west-2", client: Any = None) -> None:
        self.region = region
        self._client = client

    def _make_client(self) -> Any:
        _require_paid_opt_in()
        boto3 = importlib.import_module("boto3")  # optional extra: pip install palimem[bedrock]
        return boto3.client("bedrock-runtime", region_name=self.region)

    def complete(self, *, model: str, system: str, user: str, max_output_tokens: int) -> TransportResponse:
        if self._client is None:
            self._client = self._make_client()
        resp = self._client.converse(
            modelId=model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_output_tokens, "temperature": 0},
        )
        blocks = resp["output"]["message"]["content"]
        # gpt-oss returns a separate `reasoningContent` block; only text blocks are the answer.
        text = "".join(b["text"] for b in blocks if "text" in b)
        usage = resp.get("usage", {})
        return TransportResponse(
            text=text,
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
            stop_reason=resp.get("stopReason"),
        )


class OpenAICompatTransport:
    """Any OpenAI-compatible chat endpoint. Pass ``client=`` to inject a client."""

    def __init__(self, base_url: str | None = None, api_key_env: str = "OPENAI_API_KEY", client: Any = None) -> None:
        self.base_url = base_url
        self.api_key_env = api_key_env
        self._client = client

    def _make_client(self) -> Any:
        _require_paid_opt_in()
        openai = importlib.import_module("openai")  # optional extra: pip install palimem[openai-compat]
        key = os.environ.get(self.api_key_env)
        if not key:
            raise TransportNotSent(f"environment variable {self.api_key_env} is not set")
        return openai.OpenAI(base_url=self.base_url, api_key=key)

    def complete(self, *, model: str, system: str, user: str, max_output_tokens: int) -> TransportResponse:
        if self._client is None:
            self._client = self._make_client()
        resp = self._client.chat.completions.create(
            model=model, max_tokens=max_output_tokens, temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        usage = getattr(resp, "usage", None)
        return TransportResponse(
            text=resp.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )


def cache_key(model: str, system: str, user: str, max_output_tokens: int) -> str:
    """Identity of one model request (everything that can change the answer)."""
    blob = "\x1f".join([model, system, user, str(max_output_tokens)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class CacheMiss(TransportNotSent):
    """A :class:`ReplayTransport` was asked for a request that was never recorded."""


class RecordingTransport:
    """Wrap a transport and append every raw response to a JSONL cache.

    One record per request: the request key, model, the response text exactly as the transport
    returned it, token counts and the stop reason. Prompts are not stored (they are a pure function of
    the item and the prompt template), and neither are credentials: the cache holds model output only.
    """

    def __init__(self, inner: Transport, path: str | os.PathLike[str]) -> None:
        self.inner = inner
        self.path = Path(path)

    def complete(self, *, model: str, system: str, user: str, max_output_tokens: int) -> TransportResponse:
        resp = self.inner.complete(model=model, system=system, user=user, max_output_tokens=max_output_tokens)
        rec = {
            "key": cache_key(model, system, user, max_output_tokens), "model": model, "text": resp.text,
            "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens, "stop_reason": resp.stop_reason,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return resp


class ReplayTransport:
    """Serve responses from a cache written by :class:`RecordingTransport`: free, offline, deterministic.

    If a request was recorded more than once (a retry), the last record wins. A request that is not in the
    cache raises :class:`CacheMiss`; nothing is ever sent.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._by_key: dict[str, dict[str, Any]] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                self._by_key[rec["key"]] = rec

    def __len__(self) -> int:
        return len(self._by_key)

    def complete(self, *, model: str, system: str, user: str, max_output_tokens: int) -> TransportResponse:
        rec = self._by_key.get(cache_key(model, system, user, max_output_tokens))
        if rec is None:
            raise CacheMiss("request not in the recorded cache")
        return TransportResponse(rec["text"], rec.get("input_tokens"), rec.get("output_tokens"), rec.get("stop_reason"))


class LLMExtractor:
    """Extractor backed by a model behind a :class:`Transport`, gated by a :class:`CostLedger`.

    ``max_repairs`` (0 or 1) allows one re-prompt when parsing fails. ``repair_scope`` says when:
    ``"output"`` (default) only when the output is unusable as a whole (not valid JSON / wrong shape);
    ``"output_and_claims"`` also when a claim was rejected as ``invalid_claim`` (a grammar violation).
    Never for a missing or unsupported span or an identity field: those are semantic or hostile, not format.
    The re-prompt carries only our own validation message, never model text, so it cannot amplify an
    injection. Each attempt is authorised and billed separately. The repaired reply replaces the original
    only if it has strictly fewer format errors; content is never coerced.

    ``prompt_version`` selects the template (``palimem-extract/1`` is frozen; see ``prompt.PROMPT_VERSIONS``).
    """

    def __init__(
        self,
        model: str,
        transport: Transport,
        ledger: CostLedger,
        *,
        max_output_tokens: int | None = None,
        max_repairs: int = 0,
        repair_scope: str = "output",
        prompt_version: str = PROMPT_VERSION,
        purpose: str = "extraction",
    ) -> None:
        if max_repairs not in (0, 1):
            raise ValueError("max_repairs must be 0 or 1")
        if repair_scope not in REPAIR_SCOPES:
            raise ValueError(f"repair_scope must be one of {REPAIR_SCOPES}")
        if prompt_version not in PROMPT_VERSIONS:
            raise ValueError(f"unknown prompt version {prompt_version!r}; known: {PROMPT_VERSIONS}")
        self.model = model
        self.transport = transport
        self.ledger = ledger
        self.prompt_version = prompt_version
        self.max_output_tokens = max_output_tokens or default_max_output_tokens(model, prompt_version)
        self.max_repairs = max_repairs
        self.repair_scope = repair_scope
        self.purpose = purpose

    def stamp(self, ctx: ExtractionContext) -> ExtractorStamp:
        return ExtractorStamp(
            model=self.model, version=self.prompt_version, prompt_hash=prompt_hash(ctx.schema, self.prompt_version)
        )

    def _call(self, system: str, user: str) -> tuple[TransportResponse, float, int, int]:
        est_in = rough_token_count(system) + rough_token_count(user)
        res = self.ledger.authorize(self.model, est_in, self.max_output_tokens, self.purpose)
        with res:
            try:
                resp = self.transport.complete(
                    model=self.model, system=system, user=user, max_output_tokens=self.max_output_tokens
                )
            except TransportNotSent:
                res.cancel()
                raise
            in_tok = resp.input_tokens if resp.input_tokens is not None else est_in
            out_tok = resp.output_tokens if resp.output_tokens is not None else rough_token_count(resp.text)
            cost = res.commit(in_tok, out_tok)
        return resp, cost, in_tok, out_tok

    def _needs_repair(self, parsed: ParseResult) -> bool:
        if parsed.output_invalid:
            return True
        return self.repair_scope == "output_and_claims" and any(r.reason == "invalid_claim" for r in parsed.rejections)

    def _repair_message(self, parsed: ParseResult) -> str:
        """Our own words only: reason codes and a fixed grammar reminder, never any model text."""
        if self.repair_scope == "output":
            reason = parsed.rejections[0].reason if parsed.rejections else "invalid"
            return f'\n\n(Your previous reply was rejected: {reason}. Reply with only the JSON object {{"claims": [...]}}.)'
        reasons = ", ".join(sorted({r.reason for r in parsed.rejections if r.reason in FORMAT_REASONS})) or "invalid"
        return (
            f"\n\n(Your previous reply could not be used in full: {reasons}. Reply again with only the complete "
            'JSON object {"claims": [...]} in which every claim has all eight keys and follows the grammar for '
            'its cue: a "correct" claim needs a proposition holding the right value; a "withdraw" claim has a null '
            'proposition and a target_hint; a "change" claim has no target_hint; entity and attr are never null.)'
        )

    def extract(self, text: str, ctx: ExtractionContext) -> ExtractionResult:
        prompt = build_prompt(text, ctx, self.prompt_version)
        resp, cost, i, o = self._call(prompt.system, prompt.user)
        tot_in, tot_out, tot_cost, calls = i, o, cost, 1
        parsed = parse_claims(resp.text, text=text, require_span=True)
        unexpected = set(parsed.unexpected_fields)
        triggered = used = False
        if self.max_repairs and self._needs_repair(parsed):
            triggered = True
            resp2, cost2, i2, o2 = self._call(prompt.system, prompt.user + self._repair_message(parsed))
            tot_in, tot_out, tot_cost, calls = tot_in + i2, tot_out + o2, tot_cost + cost2, calls + 1
            second = parse_claims(resp2.text, text=text, require_span=True)
            unexpected |= set(second.unexpected_fields)  # a key outside the grammar on any reply is still a signal
            if _format_errors(second) < _format_errors(parsed):  # never replace a reply with a worse one
                parsed, used = second, True
        stamp = self.stamp(ctx)
        reports, rej2, notes = build_reports(parsed.claims, ctx, stamp)
        rejections: tuple[Rejection, ...] = parsed.rejections + rej2
        return ExtractionResult(
            reports=reports, rejections=rejections, notes=notes, claims=parsed.claims,
            identity_fields_seen=parsed.identity_fields_seen or bool(unexpected), stamp=stamp,
            usage=Usage(tot_in, tot_out, tot_cost), calls=calls,
            unexpected_fields=tuple(sorted(unexpected)), repair_triggered=triggered, repair_used=used,
        )
