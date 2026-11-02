"""How the ML score and the graph score are combined into the final risk level."""
import itertools
import pytest
from routes.predict import _combine, THRESHOLDS

FRAUD, REVIEW = THRESHOLDS["fraud"], THRESHOLDS["review"]


def test_thresholds_are_the_documented_ones():
    assert FRAUD == pytest.approx(0.748, abs=1e-3)
    assert REVIEW == pytest.approx(0.4488, abs=1e-3)


@pytest.mark.parametrize("ml, level", [
    (FRAUD, "FRAUD"), (FRAUD + 0.01, "FRAUD"), (FRAUD - 0.001, "REVIEW"),
    (REVIEW, "REVIEW"), (REVIEW - 0.001, "NORMAL"), (0.0, "NORMAL"),
])
def test_risk_level_boundaries_without_history(ml, level):
    score, risk = _combine(ml, 0.0, has_history=False)
    assert risk == level
    assert score == round(ml, 4)


def test_graph_score_is_ignored_without_history():
    assert _combine(0.1, 0.9, has_history=False) == (0.1, "NORMAL")


def test_history_but_clean_graph_does_not_dilute_ml():
    # a plain 0.6*ml + 0.4*0 blend would drag 0.5 down to 0.3
    assert _combine(0.5, 0.0, has_history=True) == (0.5, "REVIEW")


def test_strong_graph_pattern_lifts_a_low_ml_score():
    score, risk = _combine(0.1, 0.9, has_history=True)
    assert score == 0.9 and risk == "FRAUD"


def test_medium_graph_pattern_can_move_normal_to_review():
    score, risk = _combine(0.1, 0.7, has_history=True)
    assert score == 0.7 and risk == "REVIEW"


GRID = [0.0, 0.05, 0.2, 0.4488, 0.6, 0.748, 0.9, 1.0]


@pytest.mark.parametrize("ml, graph, history", list(itertools.product(GRID, GRID, [True, False])))
def test_graph_can_only_raise_the_score_never_lower_it(ml, graph, history):
    score, _ = _combine(ml, graph, history)
    assert score >= round(ml, 4)
