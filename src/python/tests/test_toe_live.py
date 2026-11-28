"""Live ToE driver tests (real game-tab battle jobs)."""
from __future__ import annotations

from typing import Any

import pytest

from hw_genie.commands import toe_live


class _FakeLiveClient:
    calls: list[tuple[str, list[int]]] = []
    reloads: int = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
        self.calls.append((str(rival_id), list(titans)))
        return {"ok": True, "result": {"win": True, "stars": 3}}

    def reload(self) -> dict[str, Any]:
        type(self).reloads += 1
        return {"ok": True, "action": "reload"}


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
        user_id="12345678",
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
        user_id="12345678",
        threshold=250,
    )

    assert _FakeLiveClient.calls == [("-1", [1, 2, 3, 4, 5])]
    assert summary["completed_tier"] is False
    assert summary["remaining_rivals"] == 1


def test_run_live_tier_requires_user_id():
    summary = toe_live.run_live_tier(object(), auth_server_url="http://x", user_id="", threshold=250)
    assert summary["errors"]
    assert summary["rival_results"] == []


def test_run_live_tier_repeats_rotation_for_rounds(monkeypatch):
    """rounds>1 retries the full rotation while the rival stays below threshold."""
    status = {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 240}}}
    monkeypatch.setattr(toe_live, "_status", lambda client: status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)
    monkeypatch.setattr(toe_live.time, "sleep", lambda *_: None)
    _FakeLiveClient.calls = []

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
        rounds=3,
    )

    assert len(_FakeLiveClient.calls) == 3
    assert summary["remaining_rivals"] == 1


class _FatalLiveClient:
    calls: list[tuple[str, list[int]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
        self.calls.append((str(rival_id), list(titans)))
        return {"ok": False, "error": "no engine"}


def test_run_live_tier_fatal_error_does_not_retry_rounds(monkeypatch):
    """A fatal error must stop immediately even when rounds>1 is requested."""
    status = {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 220}}}
    monkeypatch.setattr(toe_live, "_status", lambda client: status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "LiveJobClient", _FatalLiveClient)
    monkeypatch.setattr(toe_live.time, "sleep", lambda *_: None)
    _FatalLiveClient.calls = []

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
        rounds=5,
    )

    assert len(_FatalLiveClient.calls) == 1
    assert summary["errors"]


class _FlakyLiveClient:
    calls: list[tuple[str, list[int]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
        self.calls.append((str(rival_id), list(titans)))
        if len(self.calls) == 1:
            return {"ok": False, "error": "rival not found in ToE model"}
        return {"ok": True, "result": {"win": True, "stars": 3}}


def test_battle_with_retry_retries_transient_errors(monkeypatch):
    monkeypatch.setattr(toe_live.time, "sleep", lambda *_: None)
    _FlakyLiveClient.calls = []
    client = _FlakyLiveClient()

    result = toe_live._battle_with_retry(client, "-900001", [1, 2, 3, 4, 5])

    assert result["ok"] is True
    assert len(_FlakyLiveClient.calls) == 2


def test_battle_with_retry_stops_on_non_retryable_error(monkeypatch):
    monkeypatch.setattr(toe_live.time, "sleep", lambda *_: None)

    class _NoEngineClient:
        calls: list[str] = []

        def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
            self.calls.append(str(rival_id))
            return {"ok": False, "error": "no engine"}

    client = _NoEngineClient()
    result = toe_live._battle_with_retry(client, "-1", [1, 2, 3, 4, 5])

    assert result["ok"] is False
    assert len(client.calls) == 1


def test_run_live_tier_refreshes_headers_before_each_status(monkeypatch):
    """Long live sweeps must follow the userscript's token rotation."""
    statuses = [
        {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 0}}},
        {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 250}}},
        {"status": "peace_time", "tier": 5, "rivals": {}},
    ]
    calls = {"i": 0}

    def fake_status(client):
        i = min(calls["i"], len(statuses) - 1)
        calls["i"] += 1
        return statuses[i]

    monkeypatch.setattr(toe_live, "_status", fake_status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "_complete_tier", lambda client, summary, tier=None: True)
    monkeypatch.setattr(toe_live, "_farm_daily_reward", lambda client, summary: True)
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)
    monkeypatch.setattr(
        toe_live,
        "load_session_headers",
        lambda alias, **kwargs: {"x-auth-token": f"fresh-{alias}"} if alias else None,
    )
    _FakeLiveClient.calls = []

    class _Client:
        def __init__(self) -> None:
            self.updated: list[dict[str, Any]] = []

        def update_headers(self, headers: dict[str, Any]) -> None:
            self.updated.append(dict(headers))

    client = _Client()
    toe_live.run_live_tier(
        client,
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
        account_alias="Alice",
    )

    assert client.updated, "headers must be refreshed before status reads"
    assert client.updated[0]["x-auth-token"] == "fresh-Alice"


def test_run_live_tier_completes_when_rival_disappears(monkeypatch):
    """Clearing the last rival removes it from the battle status."""
    statuses = [
        {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 0}}},
        {"status": "battle", "tier": 7, "canRaid": False, "rivals": {}},
    ]
    calls = {"i": 0}

    def fake_status(client):
        i = min(calls["i"], len(statuses) - 1)
        calls["i"] += 1
        return statuses[i]

    completed: list[Any] = []

    def fake_complete(client, summary, tier=None):
        completed.append(tier)
        summary["completed_tier"] = True
        return True

    monkeypatch.setattr(toe_live, "_status", fake_status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "_complete_tier", fake_complete)
    monkeypatch.setattr(toe_live, "_farm_daily_reward", lambda client, summary: True)
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)
    _FakeLiveClient.calls = []
    _FakeLiveClient.reloads = 0

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
    )

    assert len(_FakeLiveClient.calls) == 1
    assert _FakeLiveClient.reloads >= 1, "the tab must reload after CompleteTier"
    assert summary["completed_tier"] is True
    # CompleteTier is the advancing call, not an invented status shortcut.
    assert completed == [7]


def test_run_live_tier_stops_on_canraid_with_hybrid_instruction(monkeypatch):
    """The live path cannot produce raid results: stop and tell the user."""
    status = {"status": "battle", "tier": 8, "canRaid": True, "rivals": {"-1": {"attackScore": 0}}}
    monkeypatch.setattr(toe_live, "_status", lambda client: status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)
    _FakeLiveClient.calls = []

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
    )

    assert _FakeLiveClient.calls == [], "must not drive live battles while a raid is available"
    assert summary["errors"] == [], "the two-step workflow must not fail the run"
    assert summary["action_required"], "must record the hybrid-first instruction"
    assert "hybrid" in str(summary["action_required"][0]["message"])


class _TransientTeamClient:
    """teamGetAll fails (transient), so the static rotation is a safe fallback."""

    def call(self, payload, **kwargs):
        class _Response:
            is_success = False
            error_name = "network_or_parse_error"

        return _Response()


def test_run_live_tier_falls_back_to_static_rotation_on_transient_team_error(monkeypatch):
    """Only a transient teamGetAll failure falls back to the static rotation."""
    statuses = [
        {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 0}}},
        {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 250}}},
        {"status": "peace_time", "tier": 5, "rivals": {}},
    ]
    calls = {"i": 0}

    def fake_status(client):
        i = min(calls["i"], len(statuses) - 1)
        calls["i"] += 1
        return statuses[i]

    def boom(client, titans):
        raise RuntimeError("Could not resolve titan team")

    monkeypatch.setattr(toe_live, "_status", fake_status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", boom)
    monkeypatch.setattr(toe_live, "_complete_tier", lambda client, summary, tier=None: True)
    monkeypatch.setattr(toe_live, "_farm_daily_reward", lambda client, summary: True)
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)
    _FakeLiveClient.calls = []

    summary = toe_live.run_live_tier(
        _TransientTeamClient(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
    )

    assert _FakeLiveClient.calls, "must try a static rotation team"
    assert summary["errors"] == []


def test_run_live_tier_records_reload_failure(monkeypatch):
    """A rejected reload must be visible, not swallowed."""

    class _BadReloadClient(_FakeLiveClient):
        def reload(self) -> dict[str, Any]:
            return {"ok": False, "error": "HTTP 400"}

    statuses = [
        {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 0}}},
        {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 250}}},
        {"status": "peace_time", "tier": 5, "rivals": {}},
    ]
    calls = {"i": 0}

    def fake_status(client):
        i = min(calls["i"], len(statuses) - 1)
        calls["i"] += 1
        return statuses[i]

    monkeypatch.setattr(toe_live, "_status", fake_status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "_complete_tier", lambda client, summary, tier=None: True)
    monkeypatch.setattr(toe_live, "_farm_daily_reward", lambda client, summary: True)
    monkeypatch.setattr(toe_live, "LiveJobClient", _BadReloadClient)

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
    )

    assert any(err.get("stage") == "live_reload" for err in summary["errors"])


def test_run_live_tier_unparsable_score_is_not_cleared(monkeypatch):
    """A present rival with a bad attackScore must not be treated as cleared."""
    status = {
        "status": "battle",
        "tier": 7,
        "canRaid": False,
        "rivals": {"-1": {"attackScore": "???"}},
    }
    monkeypatch.setattr(toe_live, "_status", lambda client: status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)
    _FakeLiveClient.calls = []

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
    )

    assert any(err.get("stage") == "live_score" for err in summary["errors"])
    assert summary["completed_tier"] is False
    assert summary["remaining_rivals"] == 1


def test_run_live_tier_battle_timeout_only_skips_that_rival(monkeypatch):
    """A timed-out battle is recorded per rival; the sweep continues."""

    class _TimeoutFirstClient:
        calls: list[str] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
            self.calls.append(str(rival_id))
            if str(rival_id) == "-1":
                return {"ok": False, "error": "live battle timeout (vis=hidden)"}
            return {"ok": True, "result": {"win": True, "stars": 3}}

    statuses = [
        {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 0}, "-2": {"attackScore": 0}}},
        {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 0}, "-2": {"attackScore": 250}}},
        {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 0}}},
    ]
    calls = {"i": 0}

    def fake_status(client):
        i = min(calls["i"], len(statuses) - 1)
        calls["i"] += 1
        return statuses[i]

    monkeypatch.setattr(toe_live, "_status", fake_status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", lambda client, titans: [[1, 2, 3, 4, 5]])
    monkeypatch.setattr(toe_live, "LiveJobClient", _TimeoutFirstClient)
    _TimeoutFirstClient.calls = []

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
    )

    assert _TimeoutFirstClient.calls == ["-1", "-2"]
    assert any(err.get("stage") == "live_battle" for err in summary["errors"])
    assert summary["remaining_rivals"] == 1


class _JobTimeoutClient:
    calls: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
        self.calls.append(str(rival_id))
        return {"ok": False, "error": "live job timeout (no result)"}


def test_run_live_tier_job_timeout_attempts_each_rival_once(monkeypatch):
    """A tab that never answers must not burn the whole team rotation."""
    status = {"status": "battle", "tier": 7, "canRaid": False, "rivals": {"-1": {"attackScore": 220}}}
    monkeypatch.setattr(toe_live, "_status", lambda client: status)
    monkeypatch.setattr(
        toe_live,
        "_resolve_team_rotation",
        lambda client, titans: [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13, 14, 15]],
    )
    monkeypatch.setattr(toe_live, "LiveJobClient", _JobTimeoutClient)
    _JobTimeoutClient.calls = []

    summary = toe_live.run_live_tier(
        object(),
        auth_server_url="http://127.0.0.1:8765",
        user_id="12345678",
        threshold=250,
    )

    assert _JobTimeoutClient.calls == ["-1"], "one timed-out job must stop the rival's rotation"
    assert any(err.get("stage") == "live_battle" for err in summary["errors"])
    assert summary["remaining_rivals"] == 1


def test_run_live_tier_does_not_guess_when_saved_team_is_missing(monkeypatch):
    """A successful-but-empty teamGetAll is not transient: re-raise."""
    status = {"status": "battle", "tier": 5, "canRaid": False, "rivals": {"1": {"attackScore": 0}}}

    class _EmptyTeamClient:
        def call(self, payload, **kwargs):
            class _Response:
                is_success = True

            return _Response()

    def boom(client, titans):
        raise RuntimeError("Could not resolve titan team")

    monkeypatch.setattr(toe_live, "_status", lambda client: status)
    monkeypatch.setattr(toe_live, "_resolve_team_rotation", boom)
    monkeypatch.setattr(toe_live, "LiveJobClient", _FakeLiveClient)

    with pytest.raises(RuntimeError, match="Could not resolve titan team"):
        toe_live.run_live_tier(
            _EmptyTeamClient(),
            auth_server_url="http://127.0.0.1:8765",
            user_id="12345678",
            threshold=250,
        )
