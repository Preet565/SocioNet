from __future__ import annotations

import json
import sqlite3
from collections import Counter
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import multi_experiments as multi
from app.main import SimulationService, app
from app.models import ActionKind, Intervention, SimulationConfig, SimulationState
from app.multi_experiments import (
    ARM_PROVENANCE,
    MAX_CONFIG_BYTES,
    SOURCE_PROVENANCE,
    MultiExperimentConfig,
    MultiExperimentRunner,
    execute_arm,
    json_hash,
    prepare_experiment,
)
from app.persistence import JSONRepository, SCHEMA_VERSION, SQLiteRepository, StateStore
from app.simulation import Simulation, append_content
from app.utils import rng_for


def simulation_config(population=6) -> dict:
    return {
        "population": population,
        "max_relationships": 4,
        "max_following": 2,
        "initial_network": {"structure": "ring", "max_edges_per_agent": 4, "ring_degree": 2},
    }


def config_dict() -> dict:
    return {
        "schema_version": 1, "name": "Shared-source experiment", "hypothesis": "Matched arms agree",
        "seed": 612, "ticks": 2, "source": {"mode": "generated", "universe_id": "prime"},
        "simulation": simulation_config(), "agent_composition": {}, "claims": [],
        "baseline_arm_id": "baseline", "arms": [
            {"id": "baseline", "name": "Baseline", "schedule": []},
            {"id": "treatment", "name": "Treatment", "schedule": []},
        ], "computational_budget": 100,
    }


def shared_claim(claim_id="shared-rumor", **updates) -> dict:
    return {
        "id": claim_id, "topic": "fictional transit", "text": "A fictional shared claim.",
        "truth_label": False, "credibility": 0.5, "author_id": "citizen-001",
        "visibility": 1.0, "reshare_probability": 1.0, **updates,
    }


def prepared(config=None, source=None):
    return prepare_experiment(MultiExperimentConfig.model_validate(config or config_dict()), source)


@pytest.fixture(params=["sqlite", "json"])
def api(tmp_path, monkeypatch, request):
    if request.param == "json":
        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("SQLite unavailable")
        monkeypatch.setattr("app.persistence.SQLiteRepository", unavailable)
    path = str(tmp_path / "phase6.sqlite3")
    old_service = app.state.service
    app.state.service = SimulationService(seed=800, database_path=path, config=SimulationConfig(**simulation_config()))
    try:
        yield TestClient(app), path, request.param
    finally:
        app.state.service = old_service


def test_three_arms_equal_histories_composition_and_source_immutability(tmp_path):
    config = config_dict()
    config["arms"].insert(0, {"id": "third", "name": "Third", "schedule": []})
    config["claims"] = [shared_claim()]
    config["agent_composition"] = {"bridge_builder": 2, "contrarian": 4}
    left = prepared(config)
    right = prepared(deepcopy(config))
    assert Counter(agent.archetype for agent in left.source.agents.values()) == config["agent_composition"]
    original = left.source.model_dump(mode="json")
    assert Simulation(state=left.source.model_copy(deep=True)).state.model_dump(mode="json") == original
    assert original == right.source.model_dump(mode="json")
    states = [execute_arm(left, arm.id) for arm in left.config.arms]
    assert all(state.model_dump(mode="json") == states[0].model_dump(mode="json") for state in states)
    assert states[0].events and states[0].belief_updates and states[0].causal_links
    assert left.source.model_dump(mode="json") == original
    run = MultiExperimentRunner(StateStore(tmp_path / "equal.sqlite3")).create(left)
    assert run["status"] == "completed"
    assert {arm["initial_snapshot_hash"] for arm in run["arms"]} == {json_hash(original)}
    assert all(arm["earliest_divergence"] is None for arm in run["arms"])
    assert all(set(arm["metric_deltas"].values()) == {0.0} for arm in run["arms"])


def test_relative_schedule_explicit_claim_and_immediate_interventions():
    source = Simulation(seed=612, config=SimulationConfig(**simulation_config())).run(3)
    untouched = source.model_dump(mode="json")
    config = config_dict()
    config.update(source={"mode": "universe", "universe_id": "echo"}, seed=None, simulation=None)
    config["claims"] = [shared_claim("first"), shared_claim("second", text="Another same-topic claim.")]
    config["arms"][1]["schedule"] = [
        {"at_tick": 0, "intervention": {"kind": "suppress_citizen", "agent_id": "citizen-006"}},
        {"at_tick": 2, "claim_id": "first", "intervention": {
            "kind": "counterevidence", "topic": "fictional transit", "truth_label": True,
        }},
    ]
    plan = prepared(config, source)
    left = execute_arm(plan, "treatment")
    right = execute_arm(plan, "treatment")
    assert left.model_dump(mode="json") == right.model_dump(mode="json")
    assert source.model_dump(mode="json") == untouched
    assert left.tick == 5
    assert left.intervention_history[-2].applied_tick == 3
    assert left.intervention_history[-1].applied_tick == 5
    evidence = next(content for content in left.contents if content.is_counterevidence)
    assert evidence.claim_id == "first"
    assert evidence.parent_id == plan.source.claims["first"].origin_content_id
    assert evidence.created_tick == 5
    assert not any(event.actor_id == "citizen-006" and event.tick > 3 for event in left.events)
    assert plan.config.seed == 612 and plan.config.simulation.population == 6
    assert len([item for item in plan.source.contents if item.provenance == "multi_experiment_shared"]) == 2


def test_visibility_and_reshare_policies_and_old_snapshot_defaults():
    config = config_dict()
    config["claims"] = [shared_claim(visibility=0.0)]
    invisible = prepared(config)
    simulation = Simulation(state=invisible.source)
    for agent in simulation.state.agents.values():
        assert simulation._observe(agent, rng_for(0, agent.id)) is None
    config["claims"] = [shared_claim(visibility=0.4, reshare_probability=0.0)]
    plan = prepared(config)
    left = execute_arm(plan, "baseline")
    assert left.model_dump(mode="json") == execute_arm(plan, "baseline").model_dump(mode="json")
    assert not any(content.is_reshare for content in left.contents if content.claim_id == "shared-rumor")
    simulation = Simulation(state=plan.source.model_copy(deep=True))
    content = simulation.state.contents[0]
    actor = simulation.state.agents["citizen-002"]
    result = simulation._act(actor, content, ActionKind.REPLY, rng_for(0), parent_event_ids=[])
    assert result.action is ActionKind.IGNORE
    content.reshare_probability = 1.0
    result = simulation._act(actor, content, ActionKind.REPLY, rng_for(0), parent_event_ids=[])
    reply = next(item for item in simulation.state.contents if item.id == result.content_id)
    assert reply.is_reshare and reply.visibility == content.visibility
    assert reply.reshare_probability == 1.0
    legacy = simulation.state.model_dump(mode="json")
    for item in legacy["contents"]:
        item.pop("visibility")
        item.pop("reshare_probability")
    for claim in legacy["claims"].values():
        claim.pop("visibility")
        claim.pop("reshare_probability")
    legacy.pop("fact_check_policies")
    loaded = Simulation(state=SimulationState.model_validate(legacy))
    assert all(item.visibility == item.reshare_probability == 1.0 for item in loaded.state.contents)


def test_community_targets_expand_captured_members_and_choose_messenger():
    config = config_dict()
    source = prepared(config).source
    community = next(community for community in source.communities.values() if len(community.member_ids) >= 2)
    target = sorted(source.agents)[-1]
    config["arms"][1]["schedule"] = [
        {"at_tick": 0, "target_community_id": community.id, "intervention": {
            "kind": "trust_shift", "target_agent_id": target, "delta": 0.2,
        }},
        {"at_tick": 0, "target_community_id": community.id, "intervention": {
            "kind": "seed_post", "topic": "public art", "text": "A community messenger.",
        }},
    ]
    plan = prepared(config)
    shifts = [item for _, item in plan.schedules["treatment"] if item.kind.value == "trust_shift"]
    assert {item.agent_id for item in shifts} == set(community.member_ids) - {target}
    assert plan.plan["warnings"] and "captured" in plan.plan["warnings"][0]
    branch = execute_arm(plan, "treatment")
    message = next(item for item in branch.contents if item.text == "A community messenger.")
    assert message.author_id == sorted(community.member_ids)[0]
    assert source.model_dump(mode="json") == prepared(config_dict()).source.model_dump(mode="json")


def test_citizen_fact_check_target_is_scoped_and_relationship_target_is_real():
    config = config_dict()
    config["arms"][1]["schedule"] = [{"at_tick": 0, "intervention": {
        "kind": "fact_check", "agent_id": "citizen-002", "topic": "fictional transit", "strength": 1.0,
    }}]
    plan = prepared(config)
    result = execute_arm(plan, "treatment")
    assert result.fact_check_policies == {"citizen-002": {"fictional transit": 0.35}}
    assert result.config.fact_check_probability == plan.source.config.fact_check_probability
    # An existing directional relationship is required by the experiment validator.
    agent = next(agent for agent in plan.source.agents.values() if agent.relationships)
    target = next(iter(agent.relationships))
    config["arms"][1]["schedule"] = [{"at_tick": 0, "intervention": {
        "kind": "suppress_relationship", "agent_id": agent.id, "target_agent_id": target,
    }}]
    result = execute_arm(prepared(config), "treatment")
    assert target not in result.agents[agent.id].relationships
    assert result.suppressed_relationships


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(schema_version=2),
    lambda c: c.update(schema_version=True),
    lambda c: c.update(seed="612"),
    lambda c: c.update(ticks=21),
    lambda c: c.update(ticks=True),
    lambda c: c.update(arms=c["arms"][:1]),
    lambda c: c.update(arms=c["arms"] * 4),
    lambda c: c["arms"][1].update(id="baseline"),
    lambda c: c.update(baseline_arm_id="unknown"),
    lambda c: c.update(computational_budget=2),
    lambda c: c.update(computational_budget=5001),
    lambda c: c["simulation"].update(population=101),
    lambda c: c["simulation"].update(population=100),  # 2*2*100 > requested budget
    lambda c: c["simulation"].update(unrecognized=True),
    lambda c: c.update(agent_composition={"contrarian": 2}),
    lambda c: c.update(agent_composition={"unknown": 6}),
    lambda c: c.update(agent_composition={"contrarian": True}),
    lambda c: c.update(claims=[shared_claim()] * 2),
    lambda c: c.update(claims=[shared_claim(str(i)) for i in range(17)]),
    lambda c: c.update(claims=[shared_claim(credibility=1.1)]),
    lambda c: c.update(claims=[shared_claim(visibility=-0.1)]),
    lambda c: c.update(claims=[shared_claim(reshare_probability="0.5")]),
    lambda c: c.update(claims=[shared_claim(truth_label="false")]),
    lambda c: c["arms"][1].update(schedule=[{"at_tick": 3, "intervention": {"kind": "fact_check", "topic": "x"}}]),
    lambda c: c["arms"][1].update(schedule=[{"at_tick": 0, "intervention": {"kind": "fact_check", "topic": "x", "delay_ticks": 1}}]),
    lambda c: c["arms"][1].update(schedule=[{"at_tick": 0, "intervention": {"kind": "fact_check", "topic": "x"}}] * 17),
])
def test_bad_config_bounds(mutation):
    config = config_dict()
    mutation(config)
    with pytest.raises(ValueError):
        prepared(config)


@pytest.mark.parametrize("entry", [
    {"at_tick": 0, "intervention": {"kind": "counterevidence", "topic": "fictional transit"}},
    {"at_tick": 0, "claim_id": "missing", "intervention": {"kind": "counterevidence", "topic": "fictional transit"}},
    {"at_tick": 0, "claim_id": "shared-rumor", "intervention": {"kind": "counterevidence", "topic": "wrong"}},
    {"at_tick": 0, "claim_id": "shared-rumor", "intervention": {"kind": "seed_post", "topic": "x", "text": "x"}},
    {"at_tick": 0, "intervention": {"kind": "suppress_citizen", "agent_id": "missing"}},
    {"at_tick": 0, "intervention": {"kind": "suppress_post", "content_id": "missing"}},
    {"at_tick": 0, "intervention": {"kind": "trust_shift", "target_agent_id": "missing", "delta": 0.1}},
    {"at_tick": 0, "intervention": {"kind": "trust_shift", "agent_id": "citizen-001", "target_agent_id": "citizen-001", "delta": 0.1}},
    {"at_tick": 0, "intervention": {"kind": "seed_post", "topic": "x", "text": "x", "target_agent_id": "citizen-002"}},
    {"at_tick": 0, "intervention": {"kind": "seed_post", "topic": "x", "text": "x", "agent_id": ""}},
    {"at_tick": 0, "intervention": {"kind": "seed_post", "topic": "x", "text": "x", "truth_label": "false"}},
    {"at_tick": 0, "intervention": {"kind": "fact_check", "topic": "x", "strength": "0.5"}},
    {"at_tick": 0, "target_community_id": "missing", "intervention": {"kind": "seed_post", "topic": "x", "text": "x"}},
    {"at_tick": 0, "target_community_id": "missing", "intervention": {"kind": "fact_check", "topic": "x"}},
])
def test_bad_schedule_targets(entry):
    config = config_dict()
    config["claims"] = [shared_claim()]
    config["arms"][1]["schedule"] = [entry]
    with pytest.raises(ValueError):
        prepared(config)


def test_nonexistent_relationship_and_claim_author_are_rejected():
    config = config_dict()
    config["simulation"]["initial_network"]["structure"] = "empty"
    config["arms"][1]["schedule"] = [{"at_tick": 0, "intervention": {
        "kind": "suppress_relationship", "agent_id": "citizen-001", "target_agent_id": "citizen-002",
    }}]
    with pytest.raises(ValueError, match="existing directional"):
        prepared(config)
    config = config_dict()
    config["claims"] = [shared_claim(author_id="missing")]
    with pytest.raises(ValueError, match="author_id"):
        prepared(config)


def test_agent_ticks_hard_cap_and_source_size_cap():
    config = config_dict()
    config.update(ticks=20, computational_budget=5000, simulation=simulation_config(100))
    config["arms"].append({"id": "third", "name": "Third", "schedule": []})
    with pytest.raises(ValueError, match="agent_ticks"):
        prepared(config)
    config["arms"] = config["arms"][:2]
    config["ticks"] = 11
    with pytest.raises(ValueError, match="hard cap 2000"):
        prepared(config)
    config["ticks"] = 10
    assert prepared(config).plan["agent_ticks"] == 2000
    config = config_dict()
    source = prepared(config).source
    source.agents["citizen-001"].name = "x" * (multi.MAX_SOURCE_BYTES + 1)
    config.update(source={"mode": "universe"}, seed=None, simulation=None)
    with pytest.raises(ValueError, match="source snapshot"):
        prepared(config, source)


def test_api_contract_lifecycle_restart_rerun_export_import_and_isolation(api):
    client, path, backend = api
    service = app.state.service
    canonical = service.state.model_dump(mode="json")
    echo = service.universe("echo").state.model_dump(mode="json")
    config = config_dict()
    config["claims"] = [shared_claim()]
    config["arms"][1]["schedule"] = [{"at_tick": 1, "claim_id": "shared-rumor", "intervention": {
        "kind": "counterevidence", "topic": "fictional transit", "truth_label": True,
    }}]
    schema = client.get("/multi-experiments/schema")
    assert schema.status_code == 200 and schema.json()["properties"]["schema_version"]["const"] == 1
    preview = client.post("/multi-experiments/validate", json=config)
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid"] is True
    assert preview.json()["plan"] == {
        "source_tick": 0, "population": 6, "ticks": 2, "arm_count": 2, "agent_ticks": 24,
        "source_hash": json_hash(prepared(config).source.model_dump(mode="json")),
        "baseline_arm_id": "baseline", "arms": [
            {"id": "baseline", "name": "Baseline", "intervention_count": 0},
            {"id": "treatment", "name": "Treatment", "intervention_count": 1},
        ], "warnings": [],
    }
    assert service.store.list_multi_experiments() == []
    response = client.post("/multi-experiments", json=config)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "completed"
    assert run["config"] == preview.json()["config"]
    assert run["config_hash"] == json_hash(run["config"])
    assert run["source_hash"] == preview.json()["plan"]["source_hash"]
    assert run["arms"][1]["earliest_divergence"]
    assert service.store.backend_name == backend
    assert service.state.model_dump(mode="json") == canonical
    assert service.universe("echo").state.model_dump(mode="json") == echo
    assert service.store.load().model_dump(mode="json") == canonical
    listed = client.get("/multi-experiments?universe_id=prime&limit=1").json()["experiments"]
    assert len(listed) == 1 and listed[0]["id"] == run["id"]
    assert "config" not in listed[0] and "arms" not in listed[0] and "state" not in listed[0]
    assert client.get("/multi-experiments?universe_id=echo").json() == {"experiments": []}
    assert client.get(f"/multi-experiments/{run['id']}").json() == run
    attachment = client.get(f"/multi-experiments/{run['id']}/config")
    assert "attachment" in attachment.headers["content-disposition"]
    assert attachment.json() == run["config"]
    branch = client.get(f"/multi-experiments/{run['id']}/arms/treatment?from_tick=1&to_tick=2&limit=1&event_limit=2")
    assert branch.status_code == 200
    payload = branch.json()
    assert len(payload["samples"]) == 1 and payload["samples"][0]["tick"] == 1
    assert len(payload["events"]) == len(payload["state"]["events"]) == 2
    assert len(payload["state"]["traces"]) <= 2
    assert payload["state"]["tick"] == 2
    bundle = client.get(f"/multi-experiments/{run['id']}/export").json()
    assert json_hash(bundle["source_snapshot"]) == run["source_hash"]
    assert bundle["results"]["arms"] == run["arms"]
    # A live reset cannot affect either rerun path.
    service.reset(999, config=SimulationConfig(**simulation_config()))
    rerun = client.post(f"/multi-experiments/{run['id']}/rerun").json()
    imported_response = client.post("/multi-experiments/import", json=bundle)
    assert imported_response.status_code == 200, imported_response.text
    imported = imported_response.json()
    for repeat in (rerun, imported):
        assert repeat["id"] != run["id"] and repeat["status"] == "completed"
        assert repeat["source_hash"] == run["source_hash"]
        assert repeat["config_hash"] == run["config_hash"]
        assert repeat["source_snapshot_id"] != run["source_snapshot_id"]
        for arm, original in zip(repeat["arms"], run["arms"]):
            assert arm["metrics"] == original["metrics"]
            assert arm["earliest_divergence"] == original["earliest_divergence"]
            assert service.store.load(arm["final_snapshot_id"], provenance=ARM_PROVENANCE).model_dump(mode="json") == service.store.load(original["final_snapshot_id"], provenance=ARM_PROVENANCE).model_dump(mode="json")
    app.state.service = SimulationService(seed=1000, database_path=path, config=SimulationConfig(**simulation_config()))
    assert client.get(f"/multi-experiments/{run['id']}").json() == run
    assert client.get(f"/multi-experiments/{run['id']}/arms/baseline").status_code == 200
    assert client.post(f"/multi-experiments/{run['id']}/rerun").json()["source_hash"] == run["source_hash"]
    assert len(client.get("/multi-experiments").json()["experiments"]) == 4


def test_universe_preview_rejects_overrides_and_captures_actual_source(api):
    client, _, _ = api
    service = app.state.service
    service.tick_universe("echo", 2)
    config = config_dict()
    config.update(source={"mode": "universe", "universe_id": "echo"})
    assert client.post("/multi-experiments/validate", json=config).status_code == 422  # wrong seed
    config.pop("seed")
    config.pop("simulation")
    response = client.post("/multi-experiments/validate", json=config)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["config"]["seed"] == 800 and preview["plan"]["source_tick"] == 2
    assert preview["config"]["simulation"] == service.universe("echo").state.config.model_dump(mode="json")
    # Matching captured values are accepted so downloaded configs remain portable.
    assert client.post("/multi-experiments/validate", json=preview["config"]).status_code == 200
    for update in ({"seed": 0}, {"simulation": simulation_config(5)}, {"agent_composition": {"contrarian": 6}}):
        assert client.post("/multi-experiments/validate", json=config | update).status_code == 422
    captured = service.universe("echo").state.model_dump(mode="json")
    run = client.post("/multi-experiments", json=config).json()
    assert run["status"] == "completed"
    assert service.universe("echo").state.model_dump(mode="json") == captured
    service.reset_universe("echo", seed=99)
    repeat = client.post(f"/multi-experiments/{run['id']}/rerun").json()
    assert repeat["source_hash"] == run["source_hash"] and repeat["source_tick"] == 2
    bundle = client.get(f"/multi-experiments/{run['id']}/export").json()
    imported = client.post("/multi-experiments/import", json=bundle)
    assert imported.status_code == 200, imported.text
    assert imported.json()["source_hash"] == run["source_hash"]
    assert imported.json()["arms"][0]["metrics"] == run["arms"][0]["metrics"]


def test_api_validation_import_limits_hashes_and_missing_records(api):
    client, _, _ = api
    assert client.get("/multi-experiments/missing").status_code == 404
    assert client.post("/multi-experiments/missing/rerun").status_code == 404
    assert client.get("/multi-experiments?limit=101").status_code == 422
    assert client.get("/multi-experiments?universe_id=unknown").status_code == 422
    for body in ({}, config_dict() | {"ticks": 21}, config_dict() | {"unknown": 1}):
        response = client.post("/multi-experiments", json=body)
        assert response.status_code == 422 and isinstance(response.json()["detail"], list)
    oversized = json.dumps(config_dict() | {"padding": "x" * MAX_CONFIG_BYTES})
    assert client.post("/multi-experiments/validate", content=oversized).status_code == 422
    assert client.post("/multi-experiments", content='{"schema_version":1,"schema_version":1}').status_code == 422
    run = client.post("/multi-experiments", json=config_dict()).json()
    assert client.get(f"/multi-experiments/{run['id']}/arms/missing").status_code == 404
    assert client.get(f"/multi-experiments/{run['id']}/arms/baseline?from_tick=2&to_tick=1").status_code == 422
    assert client.get(f"/multi-experiments/{run['id']}/arms/baseline?event_limit=1001").status_code == 422
    bundle = client.get(f"/multi-experiments/{run['id']}/export").json()
    for field, value in (("bundle_version", 2), ("engine_version", "unknown"), ("config_hash", "0" * 64), ("source_hash", "0" * 64)):
        assert client.post("/multi-experiments/import", json=bundle | {field: value}).status_code == 422
    assert client.post("/multi-experiments/import", json=bundle | {"bundle_version": True}).status_code == 422
    assert client.post("/multi-experiments/import", json=bundle | {"results": {}}).status_code == 422
    bad = deepcopy(bundle)
    bad["source_snapshot"]["agents"]["citizen-001"]["following"] = ["missing"]
    bad["source_hash"] = json_hash(bad["source_snapshot"])
    assert client.post("/multi-experiments/import", json=bad).status_code == 422
    bad = deepcopy(bundle)
    bad["source_snapshot"]["agents"]["citizen-001"]["ideology"] *= -1
    bad["source_hash"] = json_hash(bad["source_snapshot"])
    bad["results"]["source_hash"] = bad["source_hash"]
    for arm in bad["results"]["arms"]:
        arm["initial_snapshot_hash"] = bad["source_hash"]
    assert client.post("/multi-experiments/import", json=bad).status_code == 422
    bad = deepcopy(bundle)
    bad["config"]["ticks"] = 21
    bad["config_hash"] = json_hash(bad["config"])
    assert client.post("/multi-experiments/import", json=bad).status_code == 422
    assert len(client.get("/multi-experiments").json()["experiments"]) == 1


def test_execution_failure_is_durable_and_restart_marks_interrupted(api, monkeypatch):
    client, path, _ = api
    original = multi.execute_arm
    def fail_treatment(plan, arm_id):
        if arm_id == "treatment":
            raise RuntimeError("deliberate arm failure")
        return original(plan, arm_id)
    monkeypatch.setattr(multi, "execute_arm", fail_treatment)
    run = client.post("/multi-experiments", json=config_dict()).json()
    assert run["status"] == "failed"
    assert run["arms"][0]["status"] == "completed"
    assert run["arms"][1]["final_snapshot_id"] is None
    assert "deliberate arm failure" in run["arms"][1]["error"]
    assert client.get(f"/multi-experiments/{run['id']}").json() == run
    failed = client.get(f"/multi-experiments/{run['id']}/arms/treatment").json()
    assert failed["state"] is None and failed["events"] == []
    record = app.state.service.store.get_multi_experiment(run["id"])
    record["status"] = "running"
    record["arms"][1]["status"] = "running"
    app.state.service.store.save_multi_experiment(record)
    app.state.service = SimulationService(database_path=path, config=SimulationConfig(**simulation_config()))
    recovered = client.get(f"/multi-experiments/{run['id']}").json()
    assert recovered["status"] == "failed" and "interrupted" in recovered["error"]
    assert recovered["arms"][0]["final_snapshot_id"] == run["arms"][0]["final_snapshot_id"]
    monkeypatch.setattr(multi, "execute_arm", original)
    assert client.post(f"/multi-experiments/{run['id']}/rerun").json()["status"] == "completed"


def test_phase4_database_migration_preserves_old_snapshots_and_records(tmp_path):
    path = tmp_path / "old.sqlite3"
    payload = prepared().source.model_dump(mode="json")
    payload.pop("fact_check_policies")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES ('schema_version', '4')")
        connection.execute("CREATE TABLE snapshots (snapshot_id INTEGER PRIMARY KEY, seed INTEGER, tick INTEGER, payload TEXT, created_at TEXT)")
        connection.execute("INSERT INTO snapshots VALUES (1, 612, 0, ?, 'old')", (json.dumps(payload),))
    store = StateStore(path)
    assert store.load().seed == 612
    run = MultiExperimentRunner(store).create(prepared())
    assert run["status"] == "completed"
    assert store.load().tick == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        assert json.loads(connection.execute("SELECT payload FROM snapshots WHERE snapshot_id=1").fetchone()[0]) == payload
    assert StateStore(path).get_multi_experiment(run["id"])["source_hash"] == run["source_hash"]


def test_corruption_is_not_hidden_by_fallback_or_empty_lists(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"This is not a SQLite database")
    with pytest.raises(sqlite3.DatabaseError):
        StateStore(path)
    store = StateStore(tmp_path / "valid.sqlite3")
    run = MultiExperimentRunner(store).create(prepared())
    with sqlite3.connect(store.repository.path) as connection:
        connection.execute("UPDATE multi_experiments SET payload='not json' WHERE id=?", (run["id"],))
    with pytest.raises(json.JSONDecodeError):
        store.get_multi_experiment(run["id"])
    repository = JSONRepository(tmp_path / "fallback.json")
    repository._multi_experiments_path.write_text("broken JSON")
    with pytest.raises(json.JSONDecodeError):
        repository.list_multi_experiments()


def test_tampered_durable_source_rejected_on_rerun(api):
    client, _, _ = api
    run = client.post("/multi-experiments", json=config_dict()).json()
    store = app.state.service.store
    source = store.load(run["source_snapshot_id"], provenance=SOURCE_PROVENANCE)
    source.seed += 1
    replacement = store.save(source, provenance=SOURCE_PROVENANCE, experiment_run_id=run["id"])
    record = store.get_multi_experiment(run["id"])
    record["source_snapshot_id"] = replacement
    store.save_multi_experiment(record)
    with pytest.raises(RuntimeError, match="source hash mismatch"):
        client.post(f"/multi-experiments/{run['id']}/rerun")


def test_metric_persistence_failure_never_publishes_completed_references(api, monkeypatch):
    client, path, _ = api
    service = app.state.service
    def broken_storage(*args, **kwargs):
        raise OSError("metric storage unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(service.store, "save_metric_history", broken_storage)
        with pytest.raises(OSError, match="metric storage unavailable"):
            client.post("/multi-experiments", json=config_dict())
    [record] = service.store.list_multi_experiments()
    assert record["status"] == "running"
    assert all(arm["final_snapshot_id"] is None for arm in record["arms"])
    app.state.service = SimulationService(database_path=path, config=SimulationConfig(**simulation_config()))
    recovered = app.state.service.store.get_multi_experiment(record["id"])
    assert recovered["status"] == "failed" and "interrupted" in recovered["error"]


def test_comparison_failure_is_a_durable_failed_record(api, monkeypatch):
    client, _, _ = api
    def broken_comparison(*args, **kwargs):
        raise RuntimeError("comparison failed")
    monkeypatch.setattr(multi, "compare_branches", broken_comparison)
    run = client.post("/multi-experiments", json=config_dict()).json()
    assert run["status"] == "failed" and "Comparison failed" in run["arms"][1]["error"]
    assert all(arm["final_snapshot_id"] is not None for arm in run["arms"])
    assert client.get(f"/multi-experiments/{run['id']}").json() == run


def test_sqlite_lock_and_future_schema_are_not_treated_as_unavailability(tmp_path, monkeypatch):
    path = tmp_path / "locked.sqlite3"
    SQLiteRepository(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION + 1),))
    with pytest.raises(RuntimeError, match="newer"):
        StateStore(path)
    def locked(*args, **kwargs):
        error = sqlite3.OperationalError("database is locked")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise error
    monkeypatch.setattr("app.persistence.SQLiteRepository", locked)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        StateStore(path)


def test_json_publish_failure_keeps_previous_record_and_missing_sqlite_uses_fallback(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "unused.sqlite3")
    store.repository = JSONRepository(tmp_path / "fallback.json")
    run = MultiExperimentRunner(store).create(prepared())
    original = store.get_multi_experiment(run["id"])
    def failed_replace(*args, **kwargs):
        raise OSError("atomic publication failed")
    with monkeypatch.context() as patch:
        patch.setattr("app.persistence.os.replace", failed_replace)
        with pytest.raises(OSError, match="atomic publication failed"):
            store.save_multi_experiment(original | {"status": "failed"})
    assert store.get_multi_experiment(run["id"])["status"] == "completed"
    monkeypatch.setattr("app.persistence.sqlite3", None)
    assert StateStore(tmp_path / "missing-extension.sqlite3").backend_name == "json"


def forbid_execution_and_persistence(patch, service):
    def forbidden(*args, **kwargs):
        pytest.fail("read-only request attempted persistence or execution")
    for method in ("save", "save_multi_experiment", "save_metric_history"):
        patch.setattr(service.store, method, forbidden)
    patch.setattr(service.multi, "create", forbidden)
    patch.setattr(multi, "execute_arm", forbidden)
    patch.setattr(Simulation, "run", forbidden)


def test_generated_create_pins_prepared_source_including_shared_claims(api):
    client, _, _ = api
    config = config_dict()
    empty_hash = client.post("/multi-experiments/validate", json=config).json()["plan"]["source_hash"]
    config["claims"] = [shared_claim()]
    preview = client.post("/multi-experiments/validate", json=config).json()
    expected_hash = preview["plan"]["source_hash"]
    assert expected_hash != empty_hash
    assert app.state.service.store.list_multi_experiments() == []
    response = client.post("/multi-experiments", params={"expected_source_hash": expected_hash}, json=preview["config"])
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "completed" and run["source_hash"] == expected_hash
    source = app.state.service.store.load(run["source_snapshot_id"], provenance=SOURCE_PROVENANCE)
    assert json_hash(source.model_dump(mode="json")) == expected_hash
    assert len([content for content in source.contents if content.claim_id == "shared-rumor"]) == 1


@pytest.mark.parametrize("mutation", ["content", "intervention"])
def test_pinned_create_rejects_same_tick_source_mutation_before_writes(api, monkeypatch, mutation):
    client, _, _ = api
    service = app.state.service
    config = config_dict()
    config.update(source={"mode": "universe", "universe_id": "echo"}, seed=None, simulation=None)
    config["claims"] = [shared_claim()]
    preview = client.post("/multi-experiments/validate", json=config).json()
    source = service.universe("echo").state
    with service.lock:
        if mutation == "content":
            append_content(source, author_id="citizen-002", kind="post", topic="public art",
                           text="A new same-tick fictional observation.", claim_truth=True)
        else:
            service.apply_universe("echo", Intervention(kind="trust_shift", target_agent_id="citizen-001", delta=0.1))
    changed_source = source.model_dump(mode="json")
    assert source.tick == preview["plan"]["source_tick"]
    with monkeypatch.context() as patch:
        forbid_execution_and_persistence(patch, service)
        response = client.post("/multi-experiments", params={"expected_source_hash": preview["plan"]["source_hash"]},
                               json=preview["config"])
    assert response.status_code == 409
    assert response.json() == {"detail": "Source changed since validation; validate again."}
    assert service.store.list_multi_experiments() == []
    assert source.model_dump(mode="json") == changed_source
    refreshed = client.post("/multi-experiments/validate", json=preview["config"]).json()
    assert refreshed["plan"]["source_hash"] != preview["plan"]["source_hash"]
    assert refreshed["plan"]["source_tick"] == preview["plan"]["source_tick"]
    response = client.post("/multi-experiments", params={"expected_source_hash": refreshed["plan"]["source_hash"]},
                           json=refreshed["config"])
    assert response.status_code == 200 and response.json()["status"] == "completed"
    assert response.json()["source_hash"] == refreshed["plan"]["source_hash"]


def test_invalid_expected_source_hash_is_query_validation_error_without_records(api, monkeypatch):
    client, _, _ = api
    service = app.state.service
    with monkeypatch.context() as patch:
        forbid_execution_and_persistence(patch, service)
        for value in ("", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 63 + "\n"):
            response = client.post("/multi-experiments", params={"expected_source_hash": value}, json=config_dict())
            assert response.status_code == 422, response.text
            assert response.json()["detail"][0]["loc"] == ["query", "expected_source_hash"]
    assert service.store.list_multi_experiments() == []


def test_import_validate_is_read_only_portable_preview(api, tmp_path, monkeypatch):
    client, _, _ = api
    app.state.service.tick_universe("echo", 2)
    config = config_dict()
    config.update(source={"mode": "universe", "universe_id": "echo"}, seed=None, simulation=None)
    config["claims"] = [shared_claim()]
    run = client.post("/multi-experiments", json=config).json()
    bundle = client.get(f"/multi-experiments/{run['id']}/export").json()
    # Preview in an empty repository with a different live seed/population.
    service = SimulationService(seed=999, database_path=str(tmp_path / "preview-destination.sqlite3"),
                                config=SimulationConfig(**simulation_config(4)))
    app.state.service = service
    before = {key: simulation.state.model_dump(mode="json") for key, simulation in service.universes.items()}
    with monkeypatch.context() as patch:
        forbid_execution_and_persistence(patch, service)
        response = client.post("/multi-experiments/import/validate", json=bundle)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert set(preview) == {"valid", "config", "plan"} and preview["valid"] is True
    assert preview["config"] == bundle["config"]
    assert preview["plan"]["source_hash"] == bundle["source_hash"]
    assert preview["plan"]["source_tick"] == 2 and preview["plan"]["population"] == 6
    assert preview["plan"]["agent_ticks"] == 24
    assert service.store.list_multi_experiments() == []
    assert {key: simulation.state.model_dump(mode="json") for key, simulation in service.universes.items()} == before
    assert service.store.load().model_dump(mode="json") == before["prime"]


def test_import_validate_rejects_corrupt_bundles_versions_references_and_resources(api, monkeypatch):
    client, _, _ = api
    service = app.state.service
    config = config_dict()
    config["claims"] = [shared_claim()]
    run = client.post("/multi-experiments", json=config).json()
    bundle = client.get(f"/multi-experiments/{run['id']}/export").json()
    records = service.store.list_multi_experiments()
    corrupt = deepcopy(bundle)
    corrupt["source_snapshot"]["seed"] += 1  # Stale source hash.
    invalid_reference = deepcopy(bundle)
    invalid_reference["source_snapshot"]["agents"]["citizen-001"]["following"] = ["missing"]
    invalid_reference["source_hash"] = json_hash(invalid_reference["source_snapshot"])
    invalid_reference["results"]["source_hash"] = invalid_reference["source_hash"]
    for arm in invalid_reference["results"]["arms"]:
        arm["initial_snapshot_hash"] = invalid_reference["source_hash"]
    over_budget = deepcopy(bundle)
    over_budget["config"].update(ticks=11, computational_budget=5000, simulation=simulation_config(100))
    over_budget["config_hash"] = json_hash(over_budget["config"])
    with monkeypatch.context() as patch:
        forbid_execution_and_persistence(patch, service)
        for invalid in (corrupt, invalid_reference, over_budget, bundle | {"engine_version": "unknown"},
                        bundle | {"schema_version": 2}, bundle | {"config_hash": "0" * 64}):
            response = client.post("/multi-experiments/import/validate", json=invalid)
            assert response.status_code == 422 and isinstance(response.json()["detail"], list), response.text
    assert service.store.list_multi_experiments() == records


@pytest.mark.parametrize("endpoint", ["/multi-experiments/import", "/multi-experiments/import/validate"])
def test_import_and_preview_share_bounded_raw_bundle_parser(api, monkeypatch, endpoint):
    client, _, _ = api
    service = app.state.service
    with monkeypatch.context() as patch:
        forbid_execution_and_persistence(patch, service)
        patch.setattr("app.multi_api.MAX_BUNDLE_BYTES", 128)
        oversized = client.post(endpoint, content=b" " * 129)
        duplicate = client.post(endpoint, content='{"bundle_version":1,"bundle_version":1}')
    assert oversized.status_code == duplicate.status_code == 422
    assert "exceeds 128 bytes" in oversized.json()["detail"][0]["msg"]
    assert "duplicate JSON key" in duplicate.json()["detail"][0]["msg"]
    assert service.store.list_multi_experiments() == []
