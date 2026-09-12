from app.evolution import passes_gate


BASELINE = {
    "f1": 0.80,
    "high_risk_recall": 0.90,
    "clean_pr_specificity": 0.85,
}


def test_first_version_passes_without_baseline():
    assert passes_gate(BASELINE, {})


def test_equal_candidate_does_not_pass():
    assert not passes_gate(BASELINE, BASELINE)


def test_candidate_requires_improvement_without_regression():
    improved = {**BASELINE, "f1": 0.82}
    regressed = {**improved, "clean_pr_specificity": 0.70}
    assert passes_gate(improved, BASELINE)
    assert not passes_gate(regressed, BASELINE)
