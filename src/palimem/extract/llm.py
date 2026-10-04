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

import importlib
import os
from dataclasses import dataclass
from typing import Any, Protocol

from palimem.costs import CostLedger, rough_token_count
from palimem.extract.base import ExtractionResult, Usage
from palimem.extract.build import build_reports
from palimem.extract.claims import Rejection
from palimem.extract.context import ExtractionContext
from palimem.extract.parse import ParseResult, parse_claims
from palimem.extract.prompt import PROMPT_VERSION, build_prompt, prompt_hash
from palimem.types.report import Extractor as ExtractorStamp

PAID_CALLS_ENV = "PALIMEM_ALLOW_PAID_CALLS"

#: Output-token ceilings per model. gpt-oss bills reasoning tokens as output, so it needs headroom.
DEFAULT_MAX_OUTPUT_TOKENS = {
    "openai.gpt-oss-20b-1:0": 1500,
    "mistral.ministral-3-8b-instruct": 700,
    "mistral.ministral-3-14b-instruct": 700,
}
FALLBACK_MAX_OUTPUT_TOKENS = 800


class TransportNotSent(Exception):
    """The request was definitely not sent, so the reservation can be cancelled, not charged."""


class PaidCallsDisabled(TransportNotSent):
    """A real transport was used without ``PALIMEM_ALLOW_PAID_CALLS=1``."""


@dataclass(frozen=True)
class TransportResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


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
        return TransportResponse(text=text, input_tokens=usage.get("inputTokens"), output_tokens=usage.get("outputTokens"))


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


class LLMExtractor:
    """Extractor backed by a model behind a :class:`Transport`, gated by a :class:`CostLedger`.

    ``max_repairs`` (0 or 1) allows one re-prompt when the output is unusable as a whole
    (not valid JSON / wrong shape). The re-prompt carries only our own validation message, never
    model text, so it cannot amplify an injection. Each attempt is authorised and billed separately.
    """

    def __init__(
        self,
        model: str,
        transport: Transport,
        ledger: CostLedger,
        *,
        max_output_tokens: int | None = None,
        max_repairs: int = 0,
        purpose: str = "extraction",
    ) -> None:
        if max_repairs not in (0, 1):
            raise ValueError("max_repairs must be 0 or 1")
        self.model = model
        self.transport = transport
        self.ledger = ledger
        self.max_output_tokens = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS.get(model, FALLBACK_MAX_OUTPUT_TOKENS)
        self.max_repairs = max_repairs
        self.purpose = purpose

    def stamp(self, ctx: ExtractionContext) -> ExtractorStamp:
        return ExtractorStamp(model=self.model, version=PROMPT_VERSION, prompt_hash=prompt_hash(ctx.schema))

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

    def extract(self, text: str, ctx: ExtractionContext) -> ExtractionResult:
        prompt = build_prompt(text, ctx)
        user = prompt.user
        tot_in = tot_out = calls = 0
        tot_cost = 0.0
        parsed: ParseResult | None = None
        for attempt in range(self.max_repairs + 1):
            resp, cost, i, o = self._call(prompt.system, user)
            tot_in, tot_out, tot_cost, calls = tot_in + i, tot_out + o, tot_cost + cost, calls + 1
            parsed = parse_claims(resp.text, text=text, require_span=True)
            if not parsed.output_invalid or attempt == self.max_repairs:
                break
            reason = parsed.rejections[0].reason if parsed.rejections else "invalid"
            user = prompt.user + (
                f"\n\n(Your previous reply was rejected: {reason}. "
                'Reply with only the JSON object {"claims": [...]}.)'
            )
        assert parsed is not None
        stamp = self.stamp(ctx)
        reports, rej2, notes = build_reports(parsed.claims, ctx, stamp)
        rejections: tuple[Rejection, ...] = parsed.rejections + rej2
        return ExtractionResult(
            reports=reports, rejections=rejections, notes=notes, claims=parsed.claims,
            identity_fields_seen=parsed.identity_fields_seen, stamp=stamp,
            usage=Usage(tot_in, tot_out, tot_cost), calls=calls,
        )
