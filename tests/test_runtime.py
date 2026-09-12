from app.runtime import RuntimeManager
from app.providers import MockProvider
from app.states import can_transition


def test_plan_fans_out_to_both_specialists():
    plan = RuntimeManager._plan("diff --git a/a.py b/a.py\n+++ b/a.py\n@@\n+return value\n")
    assert set(plan["roles"]) == {"security", "reliability"}
    assert plan["max_rework_rounds"] == 2


def test_aggregate_deduplicates_by_fingerprint():
    one = {"fingerprint": "same", "severity": "high"}
    result = RuntimeManager._aggregate({"security": {"findings": [one]}, "reliability": {"findings": [one]}})
    assert result["count"] == 1


def test_risk_level_prioritizes_critical():
    assert RuntimeManager._risk_level([{"severity": "medium"}, {"severity": "critical"}]) == "critical"


def test_critic_requests_bounded_rework_for_medium_confidence():
    result = MockProvider().critic([{
        "review_id": "review-1",
        "fingerprint": "fp-1",
        "confidence": 0.78,
        "evidence": "requests.get(url)",
    }])
    assert result["decision"] == "rework"
    assert result["rework"][0]["review_id"] == "review-1"


def test_model_budget_is_not_reset_by_reserve_call():
    budget = {"max_model_calls": 1, "used_model_calls": 0}
    RuntimeManager._reserve_call(budget)
    assert budget["used_model_calls"] == 1


def test_run_state_machine_rejects_terminal_mutation():
    assert can_transition("queued", "running")
    assert can_transition("failed", "queued")
    assert not can_transition("completed", "queued")
    assert not can_transition("completed", "cancelled")
