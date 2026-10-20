"""Hand-calculated event fixtures; no database or stochastic workload required."""

import pytest

from app import analytics
from app.models import (
    ActionEvent, Agent, Belief, Claim, ClaimLifecycleEvent, Content,
    ContentExposureEvent, SimulationState,
)


def world(tick=2):
    return SimulationState(
        seed=17, tick=tick, communities={},
        agents={key: Agent(id=key, name=key, age=30, interests=["transit"],
                           ideology=0, skepticism=0.5, curiosity=0.5, trust_baseline=0.5)
                for key in "abcd"},
    )


def item(key, author="a", tick=0, **kwargs):
    return Content(id=key, author_id=author, created_tick=tick, topic="transit",
                   text=key, **{"kind": "post", "claim_truth": False, **kwargs})


def observation(key, actor, content, tick=1, claim_id=None):
    return ContentExposureEvent(event_id=key, semantic_key=key, actor_id=actor,
                                content_id=content, tick=tick, claim_id=claim_id)


def recorded_world():
    state = world()
    state.contents = [
        item("rumor", claim_id="q"),
        item("correction", "b", 1, kind="fact_check", claim_id="q",
             parent_id="rumor", claim_truth=True, is_counterevidence=True),
        item("reply", "c", 1, kind="reply", claim_id="q", parent_id="correction"),
        item("unrelated", "a", 2, claim_id="other"),
    ]
    state.events = [
        ActionEvent(event_id="1", actor_id="a", tick=1, action="post", content_id="rumor"),
        ActionEvent(event_id="2", actor_id="b", tick=1, action="fact_check",
                    content_id="correction", observed_content_id="rumor", claim_id="q"),
        ActionEvent(event_id="3", actor_id="c", tick=1, action="reply",
                    content_id="reply", observed_content_id="correction", claim_id="q"),
        ActionEvent(event_id="4", actor_id="d", tick=1, action="ignore",
                    content_id="correction", observed_content_id="correction", claim_id="q"),
        # Created claim metadata must never reassign the observed claim.
        ActionEvent(event_id="5", actor_id="a", tick=2, action="post",
                    content_id="unrelated", observed_content_id="correction", claim_id="other"),
    ]
    return state


def test_exposure_counts_observations_not_created_content():
    metrics = analytics.calculate_metrics(recorded_world())
    # Four observations, not five content-linked actions, across 4 * 2 slots.
    assert metrics.exposure_rate == 0.5
    # Readers c,d,a saw the correction; b only created it.
    assert metrics.correction_reach == 0.75
    assert metrics.claim_metrics["q"].exposures == 4
    assert metrics.claim_metrics["q"].unique_exposed_agents == 4
    assert metrics.claim_metrics["other"].exposures == 0


def test_canonical_exposures_merge_legacy_actions_without_double_counting():
    state = recorded_world()
    state.content_exposures = [
        observation("x1", "b", "rumor"),
        observation("x2", "c", "correction"),
        observation("x3", "b", "correction", tick=2),
    ]
    before = state.model_dump_json()
    metrics = analytics.calculate_metrics(state)
    assert metrics.exposure_rate == 0.625  # 2 mirrored + 2 action-only + 1 exposure-only
    assert metrics.correction_reach == 1
    assert metrics.claim_metrics["q"].exposures == 5
    assert metrics.claim_metrics["q"].unique_exposed_agents == 4
    assert state.model_dump_json() == before


def test_exposure_only_and_legacy_unambiguous_observations():
    state = world()
    state.contents = [item("rumor", claim_id="q")]
    state.content_exposures = [observation("x1", "b", "rumor"),
                               observation("x2", "b", "rumor", tick=2)]
    metrics = analytics.calculate_metrics(state)
    assert metrics.exposure_rate == 0.25
    assert metrics.claim_metrics["q"].exposures == 2
    assert metrics.claim_metrics["q"].unique_exposed_agents == 1
    state.content_exposures = []
    state.events = [
        ActionEvent(event_id="old-ignore", actor_id="b", tick=1, action="ignore", content_id="rumor"),
        ActionEvent(event_id="old-post", actor_id="a", tick=1, action="post", content_id="rumor"),
    ]
    assert analytics.calculate_metrics(state).exposure_rate == 0.125


def test_claim_ancestry_is_transitive_in_partial_and_legacy_registries():
    state = world()
    origin = item("origin", claim_id="q")
    state.claims = {"q": Claim(claim_id="q", topic="transit", text="q", truth_label=False,
                              origin_content_id="origin", origin_author_id="a", created_tick=0)}
    state.contents = [item("grandchild", parent_id="child", kind="reply"),
                      item("child", parent_id="origin", kind="reply"), origin,
                      item("new-origin", tick=1)]
    state.content_exposures = [observation("x", "b", "grandchild")]
    metrics = analytics.claim_metrics(state)
    assert metrics["q"].replies == 2
    assert metrics["q"].exposures == 1
    assert "claim-new-origin" in metrics
    # No IDs at all: derive the same stable origin ID without mutating state.
    state.claims = {}
    origin.claim_id = None
    assert analytics.claim_metrics(state)["claim-origin"].replies == 2


def test_claim_activity_includes_late_exposures_lifecycle_and_nonfactcheck_corrections():
    state = world(tick=8)
    state.contents = [item("origin", claim_id="q"),
                      item("counter", "b", 2, claim_id="q", claim_truth=True,
                           is_counterevidence=True, parent_id="origin")]
    state.claims = {"q": Claim(
        claim_id="q", topic="transit", text="q", truth_label=False,
        origin_content_id="origin", origin_author_id="a", created_tick=0,
        status="corrected", lifecycle=[ClaimLifecycleEvent(status="corrected", tick=4)],
    )}
    state.content_exposures = [observation("late", "c", "counter", tick=7)]
    metric = analytics.claim_metrics(state)["q"]
    assert metric.corrections == 1
    assert metric.fact_checks == 0
    assert metric.last_tick == 7
    # This legacy field is an inactivity duration, not a cessation-time estimate.
    assert analytics.calculate_metrics(state).time_until_claim_stops_spreading == 1
    details = analytics.analytics_diagnostics(state)
    assert details["claims"]["q"] == {
        "first_tick": 0, "last_content_tick": 2, "last_exposure_tick": 7,
        "last_lifecycle_tick": 4, "last_activity_tick": 7,
        "ticks_since_last_activity": 1, "observed_lifetime_ticks": 7,
        "time_to_first_correction": 4, "stopped_spreading": None,
    }


def test_legacy_origin_and_lifecycle_do_not_depend_on_content_order():
    state = world()
    # Both records have the same tick and the correction sorts before origin.
    state.contents = [item("child", "b", kind="fact_check", claim_truth=True,
                           parent_id="origin"), item("origin")]
    before = state.model_dump_json()
    metrics = analytics.claim_metrics(state)["claim-origin"]
    assert metrics.truth_label is False
    assert metrics.status.value == "corrected"
    assert metrics.corrections == 1
    details = analytics.analytics_diagnostics(state)["claims"]["claim-origin"]
    assert details["time_to_first_correction"] == 0
    assert state.model_dump_json() == before


def test_polarization_is_within_topic_and_uses_observed_belief_denominator():
    state = world()
    # Transit: [-1,+1], MAD=1. Gardens: four identical +1 stances, MAD=0.
    # Observation-weighted within-topic MAD = (2*1 + 4*0)/6 = 1/3.
    for agent in state.agents.values():
        agent.beliefs["gardens"] = Belief(topic="gardens", stance=1, confidence=0.5)
    state.agents["a"].beliefs["transit"] = Belief(topic="transit", stance=-1, confidence=0.5)
    state.agents["b"].beliefs["transit"] = Belief(topic="transit", stance=1, confidence=0.5)
    metrics = analytics.calculate_metrics(state)
    assert metrics.polarization == 0.333333
    assert metrics.topic_metrics["transit"]["belief_count"] == 2
    assert metrics.topic_metrics["transit"]["polarization"] == 1
    assert metrics.topic_metrics["gardens"]["belief_count"] == 4
    # Different topic positions with no disagreement are not polarization.
    state.agents["b"].beliefs["transit"].stance = -1
    assert analytics.calculate_metrics(state).polarization == 0


def test_evidence_truth_fraction_does_not_establish_belief_accuracy():
    state = world()
    state.agents["a"].beliefs["transit"] = Belief(
        topic="transit", stance=-1, confidence=1, evidence_for=9, evidence_against=1,
    )
    diagnostics = analytics.analytics_diagnostics(state)
    assert diagnostics["evidence_truth_fraction"] == pytest.approx(0.9)
    assert diagnostics["belief_accuracy"] is None
    assert diagnostics["accuracy_evaluable_beliefs"] == 0
    # Changing the actual belief leaves evidence counts unchanged.
    state.agents["a"].beliefs["transit"].stance = 1
    assert analytics.analytics_diagnostics(state)["belief_accuracy"] is None


def test_diagnostics_api_exposes_nullable_semantics_without_rewriting_legacy_metrics(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import SimulationService, app

    original = app.state.service
    app.state.service = SimulationService(seed=808, database_path=str(tmp_path / "diagnostics.sqlite3"))
    try:
        client = TestClient(app)
        response = client.get("/analytics/diagnostics")
        assert response.status_code == 200
        payload = response.json()
        assert payload["belief_accuracy"] is None
        assert payload["accuracy_evaluable_beliefs"] == 0
        assert "claims" in payload
        legacy = client.get("/analytics")
        assert legacy.status_code == 200
        assert isinstance(legacy.json()["belief_accuracy"], (int, float))
    finally:
        app.state.service = original


def test_empty_state_and_cyclic_ancestry_are_bounded():
    state = SimulationState(seed=0, tick=0, agents={}, communities={})
    metrics = analytics.calculate_metrics(state)
    assert metrics.exposure_rate == metrics.correction_reach == metrics.polarization == 0
    state.contents = [item("a", parent_id="b", claim_truth=None),
                      item("b", parent_id="a", claim_truth=None)]
    assert analytics.claim_metrics(state) == {}
