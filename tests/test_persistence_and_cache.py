import time

from app.domain.identity import IdentityHints, InvestigationOptions
from app.domain.investigation import Investigation, InvestigationStatus
from app.infrastructure.cache.store import CacheStore, cache_key
from app.infrastructure.persistence.sqlite import SQLiteInvestigationRepository
from tests.fakes import JANE, make_image


def test_cache_key_covers_all_inputs():
    a = cache_key("web_search", provider="searxng", query='"Jane Doe" Vienna')
    assert a == cache_key("web_search", query='"Jane Doe" Vienna', provider="searxng")
    assert a != cache_key("web_search", provider="searxng", query='"Jane Doe" Graz')
    assert a != cache_key("reverse_image", provider="searxng", query='"Jane Doe" Vienna')


def test_cache_store_ttl_and_namespaces(tmp_path):
    cache = CacheStore(tmp_path / "c.db")
    cache.set("page", "k", {"a": 1}, ttl_seconds=60)
    cache.set("web_search", "k", [1], ttl_seconds=60)
    assert cache.get("page", "k") == {"a": 1} and cache.get("web_search", "k") == [1]
    cache.set("face_embedding", "old", [1], ttl_seconds=1)
    cache._conn.execute("UPDATE cache SET expires_at=? WHERE key='old'", (time.time() - 1,))
    assert cache.get("face_embedding", "old") is None
    assert cache.purge_expired() == 1
    assert cache.clear("page") == 1 and cache.get("web_search", "k") == [1]
    assert cache.stats()["page"]["hits"] == 1


async def test_fingerprint_changes_with_every_relevant_input(make_container):
    c = make_container()
    inv = c.investigations.create(IdentityHints(name="Jane Doe", location="Vienna"))
    base = c.investigations.fingerprint(inv, [])
    variants = [
        inv.model_copy(update={"hints": IdentityHints(name="Jane Doe", location="Graz")}),
        inv.model_copy(update={"hints": IdentityHints(name="Jane Doe", location="Vienna", usernames=["jd"])}),
        inv.model_copy(update={"hints": IdentityHints(name="Jane Doe", location="Vienna", employer="X")}),
        inv.model_copy(update={"options": InvestigationOptions(use_reverse_image=False)}),
    ]
    prints = {c.investigations.fingerprint(v, []) for v in variants}
    assert base not in prints and len(prints) == len(variants)
    # identical normalised input → identical fingerprint
    same = inv.model_copy(update={"hints": IdentityHints(name="  jane   DOE ", location="vienna")})
    assert c.investigations.fingerprint(same, []) == base
    ref = await c.investigations.add_reference_image(inv.id, "a.png", make_image([(JANE, (10, 10, 100))], seed=1))
    assert c.investigations.fingerprint(inv, [ref]) != base


def test_repository_roundtrip_and_restart(tmp_path):
    path = tmp_path / "db.sqlite"
    repo = SQLiteInvestigationRepository(path)
    inv = Investigation(id="abc123", hints=IdentityHints(name="Jane Doe", usernames=["jd93"]))
    repo.create(inv)
    repo.add_tag("abc123", "urgent")
    note = repo.add_note("abc123", "check LinkedIn manually")
    repo.set_status("abc123", InvestigationStatus.RUNNING)
    repo.close()

    repo2 = SQLiteInvestigationRepository(path)  # "restart"
    assert repo2.mark_interrupted() == 1
    loaded = repo2.get("abc123")
    assert loaded.hints.usernames == ["jd93"] and loaded.tags == ["urgent"]
    assert loaded.notes[0].id == note.id and loaded.status == InvestigationStatus.INTERRUPTED
    assert [i.id for i in repo2.list_investigations(tag="urgent")] == ["abc123"]
    assert repo2.list_investigations(query="Jane")[0].id == "abc123"
    assert repo2.delete("abc123") and repo2.get("abc123") is None
    assert oct(path.stat().st_mode)[-3:] == "600"
