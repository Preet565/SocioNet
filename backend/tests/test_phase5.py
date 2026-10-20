from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.causality import compare_branches, causal_graph
from app.experiments import run_side_by_side
from app.interventions import InterventionValidationError, apply_intervention
from app.main import SimulationService, app
from app.models import Intervention, PerturbationRequest, SimulationConfig
from app.simulation import Simulation, append_content


def _config() -> SimulationConfig:
    return SimulationConfig(
        population=10,
        max_relationships=4,
        max_following=2,
        initial_network={"structure": "ring", "max_edges_per_agent": 4, "ring_degree": 2},
    )


def test_no_intervention_branches_have_identical_histories() -> None:
    source = Simulation(seed=700, config=_config()).state
    result = run_side_by_side(source, ticks=3, control_interventions=[], treatment_interventions=[])

    assert result.control.model_dump(mode="json") == result.treatment.model_dump(mode="json")
    assert result.branch_comparison["equivalence"] == "identical"
    assert result.branch_comparison["earliest_divergence"] is None


def test_suppression_perturbation_is_reproducible_and_semantically_diverges() -> None:
    source = Simulation(seed=701, config=_config()).state
    perturbation = Intervention(kind="suppress_citizen", agent_id="citizen-001")
    left = run_side_by_side(source, ticks=3, control_interventions=[], treatment_interventions=[perturbation])
    right = run_side_by_side(source, ticks=3, control_interventions=[], treatment_interventions=[perturbation])

    assert left.model_dump(mode="json") == right.model_dump(mode="json")
    comparison = left.branch_comparison
    assert comparison["equivalence"] == "diverged"
    assert comparison["earliest_divergence"]["semantic_position"]["tick"] == 1
    assert comparison["id_policy"]


def test_semantic_comparison_ignores_incidental_event_ids() -> None:
    source = Simulation(seed=702, config=_config()).state
    left = Simulation(state=deepcopy(source)).run(2)
    right = Simulation(state=deepcopy(source)).run(2)
    for index, event in enumerate(left.events):
        event.event_id = f"sqlite-left-{index}"
    for index, event in enumerate(right.events):
        event.event_id = f"sqlite-right-{index}"

    comparison = compare_branches(left, right)
    assert comparison["equivalence"] == "identical"
    assert comparison["earliest_divergence"] is None


def test_semantic_comparison_ignores_event_list_order_and_exposes_evidence_labels() -> None:
    source = Simulation(seed=7021, config=_config()).state
    left = Simulation(state=deepcopy(source)).run(2)
    right = Simulation(state=deepcopy(source)).run(2)
    right.events.reverse()

    comparison = compare_branches(left, right)
    assert comparison["equivalence"] == "identical"
    assert comparison["evidence"]["modeled_dependency_label"] == "modeled dependency"
    assert comparison["evidence"]["statistical_effect_label"] == "not statistically established effect"


def test_ancestry_links_cover_exposures_decisions_beliefs_and_actions() -> None:
    state = Simulation(seed=703, config=_config()).run(2)
    link_pairs = {(link.parent_event_id, link.child_event_id, link.relation) for link in state.causal_links}

    assert state.content_exposures
    assert state.belief_updates
    assert any(relation == "content_exposed" for _, _, relation in link_pairs)
    assert any(relation == "decision_dependency" for _, _, relation in link_pairs)
    assert any(relation == "informed_belief_update" for _, _, relation in link_pairs)
    assert any(relation == "caused_action" for _, _, relation in link_pairs)
    assert any(relation == "claim_origin" for _, _, relation in link_pairs)
    graph = causal_graph(state)
    assert graph["node_count"] > 0
    assert graph["edge_count"] >= len(state.causal_links)
    assert graph["evidence"]["modeled_dependency_label"] == "modeled dependency"
    assert any(node["node_type"] == "claim" for node in graph["nodes"])


def test_suppression_shapes_and_references_are_validated() -> None:
    with pytest.raises(ValidationError):
        Intervention(kind="suppress_citizen")
    with pytest.raises(ValidationError):
        Intervention(kind="suppress_post")
    with pytest.raises(ValidationError):
        Intervention(kind="suppress_relationship", agent_id="citizen-001")

    simulation = Simulation(seed=704, config=_config())
    with pytest.raises(InterventionValidationError):
        apply_intervention(
            simulation.state,
            Intervention(kind="suppress_post", content_id="content-missing"),
        )

    relationship = Intervention(
        kind="suppress_relationship",
        agent_id="citizen-001",
        target_agent_id="citizen-002",
    )
    apply_intervention(simulation.state, relationship)
    assert ("citizen-001", "citizen-002") == (
        simulation.state.suppressed_relationships[0].source_agent_id,
        simulation.state.suppressed_relationships[0].target_agent_id,
    )


def test_post_and_relationship_suppression_have_explicit_deterministic_effects() -> None:
    source = Simulation(seed=7041, config=_config()).state
    post = append_content(
        source,
        author_id="citizen-001",
        kind="post",
        topic="fictional transit",
        text="A fictional post that can be suppressed.",
        claim_truth=False,
    )
    post_result = run_side_by_side(
        source,
        ticks=1,
        control_interventions=[],
        treatment_interventions=[Intervention(kind="suppress_post", content_id=post.id)],
    )
    suppressed = next(content for content in post_result.treatment.contents if content.id == post.id)
    assert suppressed.suppressed is True
    assert post.id in post_result.treatment.suppressed_post_ids
    assert post_result.branch_comparison["equivalence"] == "diverged"

    relationship_result = run_side_by_side(
        Simulation(seed=7042, config=_config()).state,
        ticks=1,
        control_interventions=[],
        treatment_interventions=[
            Intervention(
                kind="suppress_relationship",
                agent_id="citizen-001",
                target_agent_id="citizen-002",
            )
        ],
    )
    assert "citizen-002" not in relationship_result.treatment.agents["citizen-001"].relationships
    assert relationship_result.treatment.suppressed_relationships[0].source_agent_id == "citizen-001"


def test_single_variable_perturbation_rejects_unrelated_parameters() -> None:
    with pytest.raises(ValidationError):
        PerturbationRequest(
            perturbation=Intervention(
                kind="suppress_citizen",
                agent_id="citizen-001",
                delta=0.1,
            )
        )


def test_phase5_apis_and_restart_expose_real_causal_payloads(tmp_path) -> None:
    database_path = str(tmp_path / "phase5.sqlite3")
    original = app.state.service
    app.state.service = SimulationService(seed=705, database_path=database_path, config=_config())
    try:
        client = TestClient(app)
        created = client.post(
            "/perturbations",
            json={
                "universe_id": "prime",
                "ticks": 2,
                "perturbation": {"kind": "suppress_citizen", "agent_id": "citizen-001"},
            },
        )
        assert created.status_code == 200
        run = created.json()
        run_id = run["run_id"]
        assert run["result"]["branch_comparison"]["equivalence"] == "diverged"

        comparison = client.get(f"/causal/branch-comparison/{run_id}")
        impact = client.get(f"/causal/impact-summary/{run_id}")
        graph = client.get(f"/causal/graph/{run_id}?branch=treatment")
        assert comparison.status_code == impact.status_code == graph.status_code == 200
        assert comparison.json()["earliest_divergence"]
        assert "citizens" in impact.json()
        assert graph.json()["edge_count"] > 0
    finally:
        app.state.service = original

    app.state.service = SimulationService(seed=999, database_path=database_path, config=_config())
    try:
        client = TestClient(app)
        runs = client.get("/experiment-runs").json()["runs"]
        assert runs and runs[0]["run_id"] == run_id
        assert client.get(f"/causal/impact-summary/{run_id}").status_code == 200
        assert client.get(f"/causal/graph/{run_id}?branch=control").status_code == 200
    finally:
        app.state.service = original
