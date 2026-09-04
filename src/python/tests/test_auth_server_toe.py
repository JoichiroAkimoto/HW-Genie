import pytest
from fastapi.testclient import TestClient

from hw_genie.commands.auth_server import ToeJobStore, create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def test_toe_job_round_trip(client):
    """A posted job can be claimed via /toe/next and a result POST resolves it."""
    account = "VitaminD"
    battle = {
        "type": "titan_arena",
        "seed": 1,
        "userId": "1",
        "typeId": "-470711",
        "attackers": {"4003": {"id": 4003, "power": 100, "hp": 1000}},
        "defenders": [{"4000": {"id": 4000, "power": 1, "hp": 100}}],
    }
    push = client.post("/toe/job", json={"account": account, "battle": battle})
    assert push.status_code == 200, push.text
    job_id = push.json()["id"]

    poll = client.get(f"/toe/next?account={account}")
    assert poll.status_code == 200
    assert poll.json()["id"] == job_id
    # Re-claim is allowed because /toe/next pops the queue
    poll2 = client.get(f"/toe/next?account={account}")
    assert poll2.status_code == 204

    result_payload = {
        "progress": [
            {
                "attackers": {"heroes": {"4003": {"hp": 100, "energy": 0, "isDead": False}}},
                "defenders": {"heroes": {"4000": {"hp": 0, "energy": 0, "isDead": True}}},
            }
        ],
        "result": {"win": True, "stars": 3},
    }
    submit = client.post(
        f"/toe/job/{job_id}/result",
        json={"account": account, "result": result_payload},
    )
    assert submit.status_code == 200
    fetch = client.get(f"/toe/job/{job_id}?account={account}")
    assert fetch.status_code == 200
    payload = fetch.json()
    assert payload["status"] == "done"
    # The submitted ``result`` is stored verbatim on the job, so the
    # outer ``result`` field of the job IS the payload we sent.
    assert payload["result"]["result"]["win"] is True
    assert payload["result"]["progress"][0]["attackers"]["heroes"]["4003"]["hp"] == 100


def test_toe_job_isolated_by_account(client):
    """Jobs for one account are not visible to another."""
    client.post("/toe/job", json={"account": "A", "battle": {"a": 1}})
    other = client.get("/toe/next?account=B")
    assert other.status_code == 204


def test_toe_job_unknown_id_returns_404(client):
    push = client.post("/toe/job", json={"account": "A", "battle": {"a": 1}})
    job_id = push.json()["id"]
    miss = client.get(f"/toe/job/{job_id}?account=B")
    assert miss.status_code == 404


def test_toe_job_in_flight_reclaim_after_timeout():
    """A job claimed but never finished becomes reclaimable after the timeout.

    Prevents head-of-line blocking when a non-engine frame (or a crashed tab)
    claims a job without submitting a result: the next poll recovers it.
    """
    import time

    store = ToeJobStore(ttl_seconds=300, in_flight_timeout_seconds=100)
    job_id = store.add("A", {"x": 1})
    first = store.claim("A")
    assert first is not None and first["id"] == job_id
    # Still in-flight: immediate reclaim must not return it.
    assert store.claim("A") is None
    # Simulate a stale claim (tab closed mid-calc).
    store._jobs[job_id]["claimed_at"] = time.time() - 200
    second = store.claim("A")
    assert second is not None and second["id"] == job_id


def test_toe_job_store_cleanup():
    """Cleanup removes jobs older than the TTL."""
    import time

    store = ToeJobStore(ttl_seconds=0)
    store.add("A", {"x": 1})
    time.sleep(0.01)
    removed = store.cleanup()
    assert removed == 1
    # The fresh job is also gone because TTL=0 plus sleep > 0
    assert store.add("A", {"x": 2})  # post-cleanup insert
