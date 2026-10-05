"""Extraction (T-G1): text in, typed reports out, identity bound by the host.

* :class:`Extractor`: the protocol. :class:`TypedPassthrough`: the no-LLM path.
* :class:`LLMExtractor`: model-backed, every call through :mod:`palimem.costs`.
* :func:`build_reports`: the only place source, origin, actor and targets are bound.
"""

from palimem.extract.base import ExtractionResult, Extractor, Usage
from palimem.extract.build import build_reports
from palimem.extract.claims import (
    EXTRACTABLE_CUES,
    ExtractedClaim,
    Rejection,
    TargetHint,
    format_stated_date,
    parse_stated_date,
)
from palimem.extract.context import DEFAULT_ALLOWED_CUES, ExtractionContext
from palimem.extract.llm import (
    BedrockConverseTransport,
    CacheMiss,
    LLMExtractor,
    OpenAICompatTransport,
    PaidCallsDisabled,
    RecordingTransport,
    ReplayTransport,
    Transport,
    TransportNotSent,
    TransportResponse,
)
from palimem.extract.parse import (
    CLAIM_FIELDS,
    IDENTITY_FIELDS,
    ParseResult,
    parse_claims,
)
from palimem.extract.passthrough import PASSTHROUGH_STAMP, TypedPassthrough
from palimem.extract.prompt import (
    PROMPT_VERSION,
    PROMPT_VERSIONS,
    Prompt,
    build_prompt,
    prompt_hash,
)

__all__ = [
    "CLAIM_FIELDS", "DEFAULT_ALLOWED_CUES", "EXTRACTABLE_CUES", "IDENTITY_FIELDS", "PASSTHROUGH_STAMP",
    "PROMPT_VERSION", "PROMPT_VERSIONS", "BedrockConverseTransport", "CacheMiss", "ExtractedClaim", "ExtractionContext", "ExtractionResult",
    "Extractor", "LLMExtractor", "OpenAICompatTransport", "PaidCallsDisabled", "ParseResult", "Prompt",
    "RecordingTransport", "Rejection", "ReplayTransport", "TargetHint", "Transport", "TransportNotSent", "TransportResponse", "TypedPassthrough",
    "Usage", "build_prompt", "build_reports", "format_stated_date", "parse_claims", "parse_stated_date",
    "prompt_hash",
]
