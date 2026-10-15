from app.models import Intervention, InterventionKind


def test_intervention_model_validates_shape_and_bounds() -> None:
    intervention = Intervention(
        kind=InterventionKind.COUNTEREVIDENCE,
        topic="fictional transit",
        delay_ticks=2,
        strength=0.8,
    )
    assert intervention.topic == "fictional transit"
    assert intervention.delay_ticks == 2
