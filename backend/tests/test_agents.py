from app.agents import create_citizens
from app.communities import create_communities
from app.models import Content, ContentKind
from app.memory import remember, retrieve
from app.relationships import record_interaction, set_following


def test_seeded_population_has_100_citizens_and_communities() -> None:
    citizens = create_citizens(17)
    communities = create_communities(17, citizens)
    assert len(citizens) == 100
    assert all(citizen.beliefs for citizen in citizens.values())
    assert sum(len(community.member_ids) for community in communities.values()) >= 100


def test_memory_retrieval_and_relationship_updates_are_stateful() -> None:
    citizens = create_citizens(3, count=2)
    first, second = citizens.values()
    post = Content(
        id="content-1",
        author_id=second.id,
        kind=ContentKind.POST,
        topic="public art",
        text="A fictional mural opens on Saturday.",
        created_tick=1,
    )
    remember(first, content=post, kind="observation", summary=post.text, tick=1, credibility=0.8)
    assert retrieve(first, "public art")[0].content_id == "content-1"
    record_interaction(first, second.id, positive=True)
    set_following(first, second.id, True)
    assert first.relationships[second.id].interactions == 1
    assert first.relationships[second.id].follows is True
    assert second.id in first.following
