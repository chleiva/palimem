"""Raw-response cache: RecordingTransport / ReplayTransport (no network, no money)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palimem.costs import CostLedger
from palimem.extract import (
    CacheMiss,
    LLMExtractor,
    RecordingTransport,
    ReplayTransport,
    TransportNotSent,
    TransportResponse,
)
from palimem.extract.llm import cache_key


class _Fake:
    def __init__(self, text: str = '{"claims": []}') -> None:
        self.text = text
        self.calls = 0

    def complete(self, *, model: str, system: str, user: str, max_output_tokens: int) -> TransportResponse:
        self.calls += 1
        return TransportResponse(self.text, 120, 8, "end_turn")


def test_record_then_replay_returns_the_same_response_and_sends_nothing(tmp_path: Path) -> None:
    cache = tmp_path / "raw.jsonl"
    inner = _Fake('{"claims": [1]}')
    rec = RecordingTransport(inner, cache)
    first = rec.complete(model="m", system="s", user="u", max_output_tokens=50)
    assert inner.calls == 1
    replay = ReplayTransport(cache)
    again = replay.complete(model="m", system="s", user="u", max_output_tokens=50)
    assert again == first
    assert again.stop_reason == "end_turn"
    assert inner.calls == 1, "replay must never reach the inner transport"


def test_cache_holds_model_output_only(tmp_path: Path) -> None:
    cache = tmp_path / "raw.jsonl"
    RecordingTransport(_Fake(), cache).complete(model="m", system="SYSTEM-PROMPT", user="USER-TEXT", max_output_tokens=5)
    raw = cache.read_text(encoding="utf-8")
    assert "SYSTEM-PROMPT" not in raw and "USER-TEXT" not in raw
    rec = json.loads(raw)
    assert set(rec) == {"key", "model", "text", "input_tokens", "output_tokens", "stop_reason"}


def test_replay_miss_is_a_not_sent_error(tmp_path: Path) -> None:
    cache = tmp_path / "raw.jsonl"
    RecordingTransport(_Fake(), cache).complete(model="m", system="s", user="u", max_output_tokens=5)
    replay = ReplayTransport(cache)
    with pytest.raises(CacheMiss) as ei:
        replay.complete(model="m", system="s", user="OTHER", max_output_tokens=5)
    assert isinstance(ei.value, TransportNotSent)


def test_key_depends_on_every_part_of_the_request() -> None:
    base = cache_key("m", "s", "u", 5)
    assert cache_key("m2", "s", "u", 5) != base
    assert cache_key("m", "s2", "u", 5) != base
    assert cache_key("m", "s", "u2", 5) != base
    assert cache_key("m", "s", "u", 6) != base


def test_last_record_wins_after_a_retry(tmp_path: Path) -> None:
    cache = tmp_path / "raw.jsonl"
    RecordingTransport(_Fake("first"), cache).complete(model="m", system="s", user="u", max_output_tokens=5)
    RecordingTransport(_Fake("second"), cache).complete(model="m", system="s", user="u", max_output_tokens=5)
    assert ReplayTransport(cache).complete(model="m", system="s", user="u", max_output_tokens=5).text == "second"


def test_extractor_replays_a_recorded_item_through_a_scratch_ledger(tmp_path: Path) -> None:
    """Replay still goes through LLMExtractor and a ledger, so the offline re-score uses a throw-away
    ledger file: it can never touch (or be blocked by) the shared one."""
    from datetime import UTC, datetime

    from palimem.extract import ExtractionContext
    from palimem.types.report import Source

    ctx = ExtractionContext(source=Source(id="t", cls="standard"), origin_group="t", actor="connector:t",
                            observed_at=datetime(2026, 3, 15, tzinfo=UTC), subject_entity=None, schema=None)
    cache = tmp_path / "raw.jsonl"
    model = "openai.gpt-oss-20b-1:0"
    live = LLMExtractor(model, RecordingTransport(_Fake('{"claims": []}'), cache),
                        CostLedger(path=tmp_path / "live.jsonl"))
    first = live.extract("Alice lives in Paris.", ctx)
    scratch = CostLedger(path=tmp_path / "scratch.jsonl")
    replayed = LLMExtractor(model, ReplayTransport(cache), scratch).extract("Alice lives in Paris.", ctx)
    assert replayed.claims == first.claims == ()
    assert replayed.usage is not None and first.usage is not None
    assert replayed.usage.input_tokens == first.usage.input_tokens == 120
