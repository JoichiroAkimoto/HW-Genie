"""Tests for `arena plan` (corpus-driven lineup recommendation)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from hw_genie.commands import arena as arena_mod
from hw_genie.commands.arena import (
    ArenaTarget,
    ArenaTopEntry,
    parse_candidates_response,
    run_arena_plan,
)
from hw_genie.core.battle_log import PlanCandidate, lineup_key_from_ids
from hw_genie.core.client import ApiAction, HWResponse, ResponseStatus


def _plan(**overrides) -> PlanCandidate:
    values = {
        "attacker_lineup_key": "k",
        "heroes_summary": "1,2,3,4,5",
        "pet_id": 6001,
        "banner_id": 2,
        "battles": 3,
        "wins": 3,
        "win_rate": 1.0,
        "wilson_low": 0.4,
        "last_seen": None,
        "tier": "same_defender",
        "recommended": True,
    }
    values.update(overrides)
    return PlanCandidate(**values)


def test_parse_candidates_includes_banner():
    response = {
        "enemies": [
            {
                "userId": "222",
                "place": "5",
                "heroes": [
                    {"id": 11},
                    {"id": 12},
                    {"id": 13},
                    {"id": 14},
                    {"id": 15},
                    {"id": 6002},
                ],
                "banners": [{"id": 3, "slots": [1, 2, 3]}],
            }
        ]
    }
    entries = parse_candidates_response(response)

    assert len(entries) == 1
    assert entries[0].heroes == [11, 12, 13, 14, 15]
    assert entries[0].pet == 6002
    assert entries[0].banner == 3


def test_run_arena_plan_prints_recommended_attack(monkeypatch, capsys):
    entry = ArenaTopEntry(
        place=5, user_id=222, heroes=[11, 12, 13, 14, 15], pet=6002, banner=1
    )
    target = ArenaTarget(user_id=222, place=5, candidate=entry)
    captured: dict = {}

    monkeypatch.setattr(arena_mod, "fetch_arena_status", lambda *a, **k: MagicMock())
    monkeypatch.setattr(arena_mod, "resolve_target", lambda *a, **k: target)

    def fake_query_plans(**kwargs):
        captured.update(kwargs)
        return [_plan()]

    monkeypatch.setattr(arena_mod, "query_plans", fake_query_plans)

    plans = run_arena_plan(MagicMock(), user_id=222)

    out = capsys.readouterr().out
    assert len(plans) == 1
    assert "Defender lineup: 11,12,13,14,15 pet 6002 banner 1" in out
    assert "Recommended:" in out
    assert "--heroes 1 2 3 4 5" in out
    assert "--pet 6001" in out
    assert "--banner 2" in out
    assert captured["defender_id"] == "222"
    assert captured["battle_type"] == "arena"
    assert captured["defender_lineup_key"]


def test_run_arena_plan_warns_when_gate_fails(monkeypatch, capsys):
    entry = ArenaTopEntry(
        place=5, user_id=222, heroes=[11, 12, 13, 14, 15], pet=6002, banner=1
    )
    target = ArenaTarget(user_id=222, place=5, candidate=entry)

    monkeypatch.setattr(arena_mod, "fetch_arena_status", lambda *a, **k: MagicMock())
    monkeypatch.setattr(arena_mod, "resolve_target", lambda *a, **k: target)
    monkeypatch.setattr(
        arena_mod,
        "query_plans",
        lambda **k: [_plan(battles=1, wins=1, recommended=False, wilson_low=0.2)],
    )

    run_arena_plan(MagicMock(), user_id=222, min_samples=3)

    out = capsys.readouterr().out
    assert "No plan meets the gate" in out
    assert "Recommended:" not in out


def test_run_arena_plan_fetches_candidate_when_resolved_from_top(monkeypatch, capsys):
    """topGet lacks the lineup: fall back to a quiet candidate lookup."""
    entry = ArenaTopEntry(
        place=5, user_id=222, heroes=[11, 12, 13, 14, 15], pet=6002, banner=1
    )
    target = ArenaTarget(user_id=222, place=5, candidate=None)
    captured: dict = {}
    candidate_calls: list[dict] = []

    monkeypatch.setattr(arena_mod, "fetch_arena_status", lambda *a, **k: MagicMock())
    monkeypatch.setattr(arena_mod, "resolve_target", lambda *a, **k: target)

    def fake_fetch_candidate(*args, **kwargs):
        candidate_calls.append(kwargs)
        return entry

    monkeypatch.setattr(arena_mod, "fetch_candidate", fake_fetch_candidate)

    def fake_query_plans(**kwargs):
        captured.update(kwargs)
        return [_plan()]

    monkeypatch.setattr(arena_mod, "query_plans", fake_query_plans)

    plans = run_arena_plan(MagicMock(), user_id=222, game_version="298")

    assert len(plans) == 1
    assert candidate_calls and candidate_calls[0]["quiet"] is True
    assert captured["defender_id"] == "222"
    assert captured["defender_lineup_key"] == lineup_key_from_ids(
        [11, 12, 13, 14, 15], 6002, 1
    )
    assert captured["game_version"] == "298"
    assert "Defender lineup: 11,12,13,14,15 pet 6002 banner 1" in capsys.readouterr().out


def test_run_arena_plan_json_uses_top_without_candidate_retries(monkeypatch, capsys):
    """topGet-resolved targets are planned from topGet; --json stays valid."""
    entry = ArenaTopEntry(
        place=5, user_id=222, heroes=[11, 12, 13, 14, 15], pet=6002, banner=1
    )
    target = ArenaTarget(user_id=222, place=5, candidate=None, top_entry=entry)

    monkeypatch.setattr(arena_mod, "fetch_arena_status", lambda *a, **k: MagicMock())
    monkeypatch.setattr(arena_mod, "resolve_target", lambda *a, **k: target)

    def fail_fetch_candidate(*args, **kwargs):
        raise AssertionError("plan must not retry the candidate list when topGet has the target")

    monkeypatch.setattr(arena_mod, "fetch_candidate", fail_fetch_candidate)
    monkeypatch.setattr(arena_mod, "query_plans", lambda **k: [_plan()])

    run_arena_plan(MagicMock(), user_id=222, as_json=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["target"]["user_id"] == 222
    assert payload["target"]["heroes"] == [11, 12, 13, 14, 15]
    assert payload["target"]["pet"] == 6002
    assert payload["target"]["banner"] == 1


class _FakeArenaClient:
    """Minimal HWClient stand-in returning canned RPC responses."""

    def __init__(self) -> None:
        self.actions: list[str] = []

    def call(self, payload, **_kwargs):
        names = [
            call["name"].value if hasattr(call["name"], "value") else str(call["name"])
            for call in payload["calls"]
        ]
        self.actions.extend(names)
        if ApiAction.TOP_GET.value in names:
            return HWResponse(
                ResponseStatus.SUCCESS,
                detail={
                    "response": {
                        "top": [
                            {
                                "place": "10",
                                "userId": "222",
                                "power": "5000",
                                "heroes": [
                                    {"id": 11},
                                    {"id": 12},
                                    {"id": 13},
                                    {"id": 14},
                                    {"id": 15},
                                    {"id": 6002},
                                ],
                                "banners": [{"id": 1}],
                            }
                        ],
                        "place": 20,
                        "users": {},
                    }
                },
            )
        if ApiAction.ARENA_GET_ALL.value in names:
            return HWResponse(
                ResponseStatus.SUCCESS,
                detail={
                    "user": {
                        "result": {
                            "response": {"id": "900", "name": "Alice"}
                        }
                    },
                    "arena": {
                        "result": {
                            "response": {"arenaPlace": "20", "grandPlace": "34"}
                        }
                    },
                },
            )
        if ApiAction.ARENA_FIND_ENEMIES.value in names:
            return HWResponse(
                ResponseStatus.SUCCESS, detail={"response": {"enemies": []}}
            )
        return HWResponse(ResponseStatus.SUCCESS, detail={})

    def sleep(self) -> None:
        return None


def test_run_arena_plan_integration_resolves_from_top_without_attack(capsys):
    client = _FakeArenaClient()

    plans = run_arena_plan(client, rank=10, as_json=True)

    assert plans == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == {
        "user_id": 222,
        "place": 10,
        "name": None,
        "heroes": [11, 12, 13, 14, 15],
        "pet": 6002,
        "banner": 1,
    }
    assert ApiAction.ARENA_ATTACK.value not in client.actions
    assert ApiAction.GRAND_ATTACK.value not in client.actions


def test_run_arena_plan_sends_no_state_changing_rpc(monkeypatch):
    entry = ArenaTopEntry(
        place=5, user_id=222, heroes=[11, 12, 13, 14, 15], pet=6002, banner=1
    )
    target = ArenaTarget(user_id=222, place=5, candidate=entry)

    class ExplodingClient:
        def call(self, *_args, **_kwargs):
            raise AssertionError("arena plan must not call the RPC layer")

        def sleep(self, *_args, **_kwargs):
            raise AssertionError("arena plan must not sleep/call the RPC layer")

    monkeypatch.setattr(arena_mod, "fetch_arena_status", lambda *a, **k: MagicMock())
    monkeypatch.setattr(arena_mod, "resolve_target", lambda *a, **k: target)
    monkeypatch.setattr(arena_mod, "fetch_candidate", lambda *a, **k: entry)
    monkeypatch.setattr(arena_mod, "query_plans", lambda **k: [_plan()])

    plans = run_arena_plan(ExplodingClient(), user_id=222)

    assert len(plans) == 1


def test_run_arena_plan_resolves_from_top_with_single_top_get(capsys):
    """A topGet-resolved target must not trigger a second topGet in the plan step."""
    client = _FakeArenaClient()

    run_arena_plan(client, rank=10, as_json=True)
    capsys.readouterr()

    assert client.actions.count(ApiAction.TOP_GET.value) == 1


class _FakeOutsideTopArenaClient:
    """Target outside the top 50, appearing only on the 2nd candidate fetch."""

    def __init__(self) -> None:
        self.actions: list[str] = []
        self.find_enemies_calls = 0

    def call(self, payload, **_kwargs):
        names = [
            call["name"].value if hasattr(call["name"], "value") else str(call["name"])
            for call in payload["calls"]
        ]
        self.actions.extend(names)
        if ApiAction.TOP_GET.value in names:
            return HWResponse(
                ResponseStatus.SUCCESS,
                detail={
                    "response": {
                        "top": [
                            {
                                "place": "5",
                                "userId": "444",
                                "power": "7000",
                                "heroes": [
                                    {"id": 31},
                                    {"id": 32},
                                    {"id": 33},
                                    {"id": 34},
                                    {"id": 35},
                                    {"id": 6003},
                                ],
                                "banners": [{"id": 2}],
                            }
                        ],
                        "place": 100,
                        "users": {},
                    }
                },
            )
        if ApiAction.ARENA_GET_ALL.value in names:
            return HWResponse(
                ResponseStatus.SUCCESS,
                detail={
                    "user": {
                        "result": {"response": {"id": "900", "name": "Alice"}}
                    },
                    "arena": {
                        "result": {
                            "response": {"arenaPlace": "100", "grandPlace": "340"}
                        }
                    },
                },
            )
        if ApiAction.ARENA_FIND_ENEMIES.value in names:
            self.find_enemies_calls += 1
            enemies = []
            if self.find_enemies_calls == 2:
                enemies = [
                    {
                        "userId": "555",
                        "place": "51",
                        "power": "6000",
                        "heroes": [
                            {"id": 21},
                            {"id": 22},
                            {"id": 23},
                            {"id": 24},
                            {"id": 25},
                            {"id": 6002},
                        ],
                        "banners": [{"id": 1}],
                    }
                ]
            return HWResponse(
                ResponseStatus.SUCCESS,
                detail={"response": {"enemies": enemies}},
            )
        return HWResponse(ResponseStatus.SUCCESS, detail={})

    def sleep(self) -> None:
        return None


def test_run_arena_plan_json_candidate_resolved_is_clean(monkeypatch, capsys):
    """Outside-top targets must not pollute --json with refresh status lines."""
    client = _FakeOutsideTopArenaClient()
    monkeypatch.setattr(arena_mod, "query_plans", lambda **k: [])

    plans = run_arena_plan(client, rank=51, as_json=True)

    assert plans == []
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == {
        "user_id": 555,
        "place": 51,
        "name": None,
        "heroes": [21, 22, 23, 24, 25],
        "pet": 6002,
        "banner": 1,
    }
    assert client.find_enemies_calls == 2
    assert ApiAction.ARENA_ATTACK.value not in client.actions
    assert ApiAction.GRAND_ATTACK.value not in client.actions
