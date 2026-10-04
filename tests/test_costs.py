import json

import pytest

from palimem.costs import (
    CAP_USD,
    BudgetExceeded,
    CostError,
    CostLedger,
    Price,
    UnknownModel,
    load_prices,
    rough_token_count,
)

M20 = "openai.gpt-oss-20b-1:0"
PRICES = {"m": Price(1.0, 2.0, True, "test"), M20: load_prices()[M20]}


def ledger(tmp_path, cap=1.0):
    return CostLedger(tmp_path / "ledger.jsonl", cap_usd=cap, prices=PRICES)


def records(tmp_path):
    return [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]


def test_cap_is_twenty_dollars():
    assert CAP_USD == 20.0


def test_cap_cannot_be_raised(tmp_path):
    with pytest.raises(ValueError):
        CostLedger(tmp_path / "l.jsonl", cap_usd=20.01, prices=PRICES)


def test_target_models_are_priced_and_verified():
    prices = load_prices()
    for model in (M20, "mistral.ministral-3-8b-instruct", "mistral.ministral-3-14b-instruct"):
        assert model in prices and prices[model].verified


def test_unknown_model_refused_and_nothing_written(tmp_path):
    led = ledger(tmp_path)
    with pytest.raises(UnknownModel):
        led.authorize("some.other-model", 10, 10, "x")
    assert not (tmp_path / "ledger.jsonl").exists() or records(tmp_path) == []


def test_purpose_required(tmp_path):
    with pytest.raises(CostError):
        ledger(tmp_path).authorize("m", 1, 1, "  ")


def test_commit_records_model_tokens_cost_purpose(tmp_path):
    led = ledger(tmp_path)
    with led.authorize("m", 100_000, 50_000, "smoke") as r:
        cost = r.commit(80_000, 10_000)
    assert cost == pytest.approx((80_000 * 1.0 + 10_000 * 2.0) / 1e6)
    commit = next(x for x in records(tmp_path) if x["kind"] == "commit")
    assert commit["model"] == "m" and commit["purpose"] == "smoke"
    assert commit["input_tokens"] == 80_000 and commit["output_tokens"] == 10_000
    assert led.summary()["spent_usd"] == pytest.approx(cost)


def test_refuses_call_whose_estimate_exceeds_remaining(tmp_path):
    led = ledger(tmp_path, cap=1.0)
    # worst case: 600k in * $1 + 300k out * $2 = $1.20 > $1.00
    with pytest.raises(BudgetExceeded):
        led.authorize("m", 600_000, 300_000, "too big")
    assert led.summary()["exposure_usd"] == 0.0


def test_open_reservations_count_against_the_cap(tmp_path):
    led = ledger(tmp_path, cap=1.0)
    r1 = led.authorize("m", 400_000, 300_000, "a")  # $1.00 worst case... exactly 0.4+0.6
    with pytest.raises(BudgetExceeded):
        led.authorize("m", 1, 1, "b")
    r1.cancel()
    led.authorize("m", 1, 1, "b").cancel()


def test_cancel_frees_budget(tmp_path):
    led = ledger(tmp_path)
    r = led.authorize("m", 100_000, 100_000, "a")
    assert led.summary()["reserved_usd"] > 0
    r.cancel()
    assert led.summary()["exposure_usd"] == 0.0


def test_unfinalised_reservation_charged_at_estimate(tmp_path):
    led = ledger(tmp_path, cap=1.0)
    with pytest.raises(RuntimeError), led.authorize("m", 100_000, 100_000, "crashes"):
        raise RuntimeError("boom after sending request")
    s = led.summary()
    assert s["spent_usd"] == pytest.approx(0.3)
    assert s["open_reservations"] == 0
    note = next(x for x in records(tmp_path) if x["kind"] == "commit")["note"]
    assert "estimate" in note


def test_state_survives_new_ledger_instance(tmp_path):
    led = ledger(tmp_path, cap=1.0)
    with led.authorize("m", 100_000, 100_000, "a") as r:
        r.commit(100_000, 100_000)
    led2 = ledger(tmp_path, cap=1.0)
    assert led2.summary()["spent_usd"] == pytest.approx(0.3)
    assert led2.remaining_usd() == pytest.approx(0.7)


def test_overrun_is_recorded_then_raises(tmp_path):
    led = ledger(tmp_path, cap=0.5)
    r = led.authorize("m", 100_000, 100_000, "a")  # worst case $0.30
    with pytest.raises(BudgetExceeded):
        r.commit(100_000, 400_000)  # actual $0.90, over the estimate and the cap
    assert led.summary()["spent_usd"] == pytest.approx(0.9)
    assert next(x for x in records(tmp_path) if x["kind"] == "commit")["over_estimate"] is True
    with pytest.raises(BudgetExceeded):
        led.authorize("m", 1, 1, "anything after the cap is blown")


def test_double_close_rejected(tmp_path):
    r = ledger(tmp_path).authorize("m", 1, 1, "a")
    r.commit(1, 1)
    with pytest.raises(CostError):
        r.commit(1, 1)


def test_default_budget_arithmetic_for_gpt_oss(tmp_path):
    # A full $20 of gpt-oss-20b is ~285M input tokens; one 1M-in / 1M-out call is $0.37.
    led = CostLedger(tmp_path / "l.jsonl")
    assert led.estimate(M20, 1_000_000, 1_000_000) == pytest.approx(0.37)
    assert led.cap_usd == 20.0


def test_rough_token_count_is_pessimistic():
    assert rough_token_count("a" * 300) >= 100
