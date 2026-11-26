"""Live ToE driver tests (real game-tab battle jobs)."""
from __future__ import annotations

from typing import Any

from hw_genie.commands import toe_live


class _FakeLiveClient:
    calls: list[tuple[str, list[int]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
        self.calls.append((str(rival_id), list(titans)))
        return {"ok": True, "result": {"win": True, "stars": 3}}


def test_run_live_tier_clears_rival_and_completes(monkeypatch):
    statuses = [
        {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 0}}},
        {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 250}}},
        {"status": "battle", "tier": 6, "canRaid": False, "rivals": {"2": {"attackScore": 0}}},
        {"status": "peace_time", "tier": 6, "rivals": {}},
    ]
    calls = {"i": 0}

    def fake_status(client):
        i = min(calls["i"], len(statuses) - 1)
        calls["i"] += 1
        return statuses[i]

    cleared: list[Any] = []

    def fake_complete(client, summary, tier=None):
        cleared.append(tier)
        summary["completed_tier"] = True
        return True

    monkeypatch.setattr(toe_live, "_status", fake_status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[4003, 4023, 4024, 4004, 4000]])
    monkeypatch.setattr(toe_live, "_complete_tier", fake_complete)
    monkeypatch.setattr(toe_live, "_farm_daily_reward", lambda client, summary: True)
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)
    _FakeLiveClient.calls = []

    summary = toe_live.run_live_tier(
        object(),  # client is not used with the patched helpers
        auth_server_url="http://127.0.0.1:8765",
        user_id="59057403",
        threshold=250,
    )

    assert _FakeLiveClient.calls == [("1", [4003, 4023, 4024, 4004, 4000])]
    assert summary["rival_results"] and summary["rival_results"][0]["win"] is True
    assert summary["completed_tier"] is True
    assert cleared == [5]
    assert summary["remaining_rivals"] == 0


def test_run_live_tier_stops_on_wall(monkeypatch):
    """A rival that never reaches the threshold stops the tier instead of looping."""
    statuses = [
        {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 220}}},
        {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 220}}},
        {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 220}}},
    ]
    calls = {"i": 0}

    def fake_status(client):
        i = min(calls["i"], len(statuses) - 1)
        calls["i"] += 1
        return statuses[i]

    monkeypatch.setattr(toe_live, "_status", fake_status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)
    _FakeLiveClient.calls = []

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="59057403",
        threshold=250,
    )

    assert _FakeLiveClient.calls == [("-1", [1, 2, 3, 4, 5])]
    assert summary["completed_tier"] is False
    assert summary["remaining_rivals"] == 1


def test_run_live_tier_requires_user_id():
    summary = toe_live.run_live_tier(object(), auth_server_url="http://x", user_id="", threshold=250)
    assert summary["errors"]
    assert summary["rival_results"] == []
