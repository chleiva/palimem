"""The declared targets in ``bench.perf.targets`` must equal the table in docs/PERFORMANCE.md (declared before any run).

A target is changed only by a dated amendment in the document that explains what was observed; this test makes silent
drift between the code and the document fail the build.
"""

from __future__ import annotations

from pathlib import Path

from bench.perf import targets

DOC = (Path(__file__).resolve().parents[2] / "docs" / "PERFORMANCE.md").read_text()


def _row(tid: str) -> str:
    for line in DOC.splitlines():
        if line.startswith(f"| **{tid}**"):
            return line
    raise AssertionError(f"no declared row for {tid}")


def test_t1_query_latency() -> None:
    row = _row("T1")
    assert f"p50 ≤ {targets.T1_QUERY_P50_MS:g} ms, p99 ≤ {targets.T1_QUERY_P99_MS:g} ms" in row


def test_t2_append_latency() -> None:
    row = _row("T2")
    assert f"p50 ≤ {targets.T2_APPEND_P50_MS:g} ms, p99 ≤ {targets.T2_APPEND_P99_MS:g} ms" in row


def test_t3_memory() -> None:
    row = _row("T3")
    assert f"≤ {targets.T3_BYTES_PER_REPORT // 1024} KiB per report" in row
    assert f"RSS ≤ {targets.T3_RSS_AT_REFERENCE_MB:g} MB" in row


def test_t4_recovery() -> None:
    assert f"≤ {targets.T4_RECOVERY_S:g} s at 10^5 reports" in _row("T4")


def test_t5_crossover() -> None:
    assert f"r\\* ≤ {targets.T5_CROSSOVER_R}" in _row("T5")


def test_t6_flatness() -> None:
    assert f"≤ {targets.T6_FLATNESS_RATIO:g}**" in _row("T6")


def test_t7_disk() -> None:
    assert f"≤ {targets.T7_DISK_BYTES_PER_REPORT // 1024} KiB per report" in _row("T7")


def test_reference_sizes() -> None:
    assert targets.REFERENCE_SIZE == 100_000 and "**10^5 reports**" in DOC
    assert targets.CROSSOVER_SIZE == 10_000 and "at 10^4 reports" in _row("T5")
