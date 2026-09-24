"""REST/SSE API, exports, retention, and REST ↔ CLI ↔ service equivalence."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.api.app import create_app
from app.domain.identity import IdentityHints
from app.domain.image import ImageDiscoveryResult, ImageMatchType
from tests.conftest import make_settings
from tests.fakes import JANE, JANE_OTHER, OTHER, FakeReverseProvider, FakeSearchProvider, make_image

REFERENCE = make_image([(JANE, (100, 80, 110))], seed=1)


def setup_world(world):
    world.page(
        "https://example.org/team/jane",
        """<html><head><title>Jane Doe <script>alert(1)</script></title>
        <meta property="og:image" content="https://cdn.example.org/jane.png"></head><body><h1>Jane Doe</h1>Vienna</body></html>""",
    )
    world.image("https://cdn.example.org/jane.png", REFERENCE)
    world.page(
        "https://social.example/janedoe93",
        """<html><head><title>Jane Doe</title>
        <meta property="og:image" content="https://social.example/a.png"></head><body>Jane Doe, Vienna</body></html>""",
    )
    world.image("https://social.example/a.png", make_image([(JANE_OTHER, (60, 60, 120))], seed=2))


def providers():
    return {
        "reverse_providers": [
            FakeReverseProvider(
                [
                    ImageDiscoveryResult(
                        provider="fake_reverse",
                        match_type=ImageMatchType.EXACT,
                        image_url="https://cdn.example.org/jane.png",
                        page_url="https://example.org/team/jane",
                    )
                ]
            )
        ],
        "search_providers": [
            FakeSearchProvider({'"Jane Doe"': [("https://social.example/janedoe93", "Jane Doe", "Vienna")]})
        ],
    }


@pytest.fixture
def client(make_container, world):
    setup_world(world)
    container = make_container(**providers())
    with TestClient(create_app(container)) as tc:
        tc.container = container
        yield tc


def wait_done(client, inv_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        inv = client.get(f"/api/investigations/{inv_id}").json()["investigation"]
        if inv["status"] not in ("QUEUED", "RUNNING"):
            return inv
        time.sleep(0.05)
    raise AssertionError("investigation did not finish")


def create(client, **fields):
    files = [("images", ("ref.png", REFERENCE, "image/png"))]
    return client.post("/api/investigations", files=files, data={k: str(v) for k, v in fields.items()})


def test_full_flow_create_run_events_results_exports(client):
    resp = create(client, name="Jane Doe", location="Vienna")
    assert resp.status_code == 201, resp.text
    inv = resp.json()["investigation"]
    assert inv["status"] == "DRAFT" and len(inv["reference_images"]) == 1
    ref = inv["reference_images"][0]
    assert ref["faces"] and "embedding" not in json.dumps(ref)
    img = client.get(f"/api/investigations/{inv['id']}/reference-images/{ref['id']}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    run = client.post(f"/api/investigations/{inv['id']}/run")
    assert run.status_code == 202
    done = wait_done(client, inv["id"])
    assert done["status"] == "COMPLETED"
    result = done["result"]
    assert result["candidates"]
    assert "probability" not in json.dumps(result["candidates"]).lower().replace("not calibrated probabilities", "")

    # SSE replays persisted events after completion
    with client.stream("GET", f"/api/investigations/{inv['id']}/events") as stream:
        body = "".join(stream.iter_text())
    events = [json.loads(line[6:])["type"] for line in body.splitlines() if line.startswith('data: {"seq')]
    assert events[0] == "INVESTIGATION_STARTED" and events[-1] == "INVESTIGATION_COMPLETED"
    assert "REVERSE_IMAGE_RESULT_FOUND" in events and "FACE_MATCH_FOUND" in events

    cands = client.get(f"/api/investigations/{inv['id']}/candidates").json()["candidates"]
    detail = client.get(f"/api/investigations/{inv['id']}/candidates/{cands[0]['id']}").json()
    assert detail["pages"]
    evidence = client.get(f"/api/investigations/{inv['id']}/evidence?type=EXACT_IMAGE").json()["evidence"]
    assert evidence and all(e["type"] == "EXACT_IMAGE" for e in evidence)

    client.post(f"/api/investigations/{inv['id']}/notes", json={"text": "=cmd|' /C calc'!A0"})
    client.post(f"/api/investigations/{inv['id']}/tags", json={"tag": "Verified"})
    for fmt, marker in [
        ("json", b'"candidates"'),
        ("md", b"## Candidates"),
        ("html", b"<h2>Candidates</h2>"),
        ("csv", b"evidence_type"),
        ("pdf", b"%PDF"),
        ("zip", b"PK"),
    ]:
        r = client.get(f"/api/investigations/{inv['id']}/export?format={fmt}")
        assert r.status_code == 200, fmt
        assert marker in r.content, fmt
        assert b"thumbnail" not in r.content and b"embedding" not in r.content
    html = client.get(f"/api/investigations/{inv['id']}/export?format=html").text
    assert "<script>alert(1)</script>" not in html  # remote page text is escaped
    csv_text = client.get(f"/api/investigations/{inv['id']}/export?format=csv").text
    assert "'=cmd" in csv_text  # formula injection neutralised
    assert (
        "verified" in client.get(f"/api/investigations/{inv['id']}/export?format=json").json()["investigation"]["tags"]
    )

    listing = client.get("/api/investigations").json()["investigations"]
    assert listing[0]["id"] == inv["id"] and listing[0]["candidate_count"] >= 1


def test_upload_validation_and_limits(client):
    bad = client.post("/api/investigations", files=[("images", ("x.png", b"<svg onload=alert(1)>", "image/png"))])
    assert bad.status_code == 400
    assert client.post("/api/investigations", data={}).status_code == 400
    ok = client.post("/api/investigations", data={"name": "Jane Doe"})
    assert ok.status_code == 201 and ok.json()["investigation"]["reference_images"] == []
    assert client.post("/api/investigations", data={"emails": "not-an-email"}).status_code == 400


def test_face_selection_and_running_lock(client):
    group = make_image([(OTHER, (10, 10, 120)), (JANE, (170, 150, 100))], seed=5)
    inv = client.post("/api/investigations", files=[("images", ("g.png", group, "image/png"))]).json()["investigation"]
    ref = inv["reference_images"][0]
    target = next(f for f in ref["faces"] if f["bbox"]["x"] == 170)
    r = client.put(f"/api/investigations/{inv['id']}/reference-images/{ref['id']}/face", json={"face_id": target["id"]})
    assert r.json()["reference_image"]["selected_face_id"] == target["id"]
    assert (
        client.put(
            f"/api/investigations/{inv['id']}/reference-images/{ref['id']}/face", json={"face_id": "nope"}
        ).status_code
        == 400
    )


def test_security_headers_and_session_endpoints_disabled(client):
    r = client.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/api/sessions").status_code == 404
    assert client.get("/api/investigations/../../etc/passwd").status_code == 404
    assert client.get("/api/investigations/zzz!").status_code == 404


def test_api_token_required_when_configured(make_container, tmp_path):
    container = make_container(make_settings(tmp_path, app_api_token="s3cret"))
    with TestClient(create_app(container)) as tc:
        assert tc.get("/api/health").status_code == 200
        assert tc.get("/api/investigations").status_code == 401
        assert tc.get("/api/investigations", headers={"X-API-Token": "s3cret"}).status_code == 200
        assert tc.post("/api/auth/token", json={"token": "wrong"}).status_code == 401
        assert tc.post("/api/auth/token", json={"token": "s3cret"}).status_code == 200
        assert tc.get("/api/investigations").status_code == 200  # cookie set


def test_rest_cli_and_service_produce_equivalent_results(make_container, world, tmp_path, monkeypatch):
    setup_world(world)
    settings = make_settings(tmp_path)
    hints = IdentityHints(name="Jane Doe", location="Vienna")

    def summary(result):
        return sorted((c.display_name, c.assessment.level.value, tuple(sorted(c.urls))) for c in result.candidates)

    # 1) service directly
    import asyncio

    c1 = make_container(settings, **providers())

    async def direct():
        inv = c1.investigations.create(hints)
        await c1.investigations.add_reference_image(inv.id, "r.png", REFERENCE)
        return await c1.investigations.investigate(inv.id)

    direct_result = asyncio.run(direct())

    # 2) REST
    rest_container = make_container(settings, **providers())
    with TestClient(create_app(rest_container)) as tc:
        inv = create(tc, name="Jane Doe", location="Vienna").json()["investigation"]
        tc.post(f"/api/investigations/{inv['id']}/run")
        wait_done(tc, inv["id"])
    rest_result = rest_container.repo.get_result(inv["id"])

    # 3) CLI
    from app import cli as cli_module

    monkeypatch.setattr("app.container.build_container", lambda *a, **k: make_container(settings, **providers()))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(REFERENCE)
    out = CliRunner().invoke(
        cli_module.cli,
        [
            "investigate",
            "-i",
            str(ref_path),
            "--name",
            "Jane Doe",
            "--location",
            "Vienna",
            "--export",
            "json",
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert out.exit_code == 0, out.output
    cli_json = json.loads(next((tmp_path / "out").glob("*.json")).read_text())
    cli_summary = sorted(
        (c["display_name"], c["assessment"]["level"], tuple(sorted(c["urls"])))
        for c in cli_json["result"]["candidates"]
    )

    assert summary(direct_result) == summary(rest_result) == cli_summary
    assert direct_result.fingerprint == rest_result.fingerprint == cli_json["result"]["fingerprint"]


def test_restart_keeps_investigations_and_retention_purges(make_container, world, tmp_path):
    setup_world(world)
    settings = make_settings(tmp_path, reference_image_retention_hours=1, reference_embedding_retention_hours=1)
    c1 = make_container(settings, **providers())
    with TestClient(create_app(c1)) as tc:
        inv = create(tc, name="Jane Doe").json()["investigation"]
        tc.post(f"/api/investigations/{inv['id']}/run")
        wait_done(tc, inv["id"])
    c1.repo.close()

    c2 = make_container(settings)  # new process, same database
    restored = c2.repo.get(inv["id"])
    assert restored is not None and restored.result is not None and restored.result.candidates
    ref = restored.reference_images[0]
    assert os.path.exists(ref.stored_path)
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    c2.repo._exec("UPDATE reference_images SET created_at=?", (old,))
    stats = c2.retention.purge()
    assert stats["reference_files"] == 1 and stats["embeddings"] == 1
    purged = c2.repo.get_reference_images(inv["id"], with_embeddings=True)[0]
    assert not purged.image_available and purged.faces[0].embedding is None and purged.faces[0].thumbnail is None
    assert not os.path.exists(ref.stored_path)
