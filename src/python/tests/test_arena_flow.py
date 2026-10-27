"""Mocked-client flow tests for Arena v1 (no live API calls)."""

import argparse
from unittest.mock import MagicMock

import pytest

from hw_genie.commands.arena import (
    ArenaAttackResult,
    ArenaError,
    ArenaStatus,
    ArenaTarget,
    ArenaTeam,
    check_target_range,
    fetch_arena_candidates,
    fetch_arena_status,
    fetch_arena_top,
    resolve_target,
    run_arena_attack,
)


def _ok(detail: dict) -> MagicMock:
    res = MagicMock()
    res.is_success = True
    res.detail = detail
    return res


def _err(name: str) -> MagicMock:
    res = MagicMock()
    res.is_success = False
    res.error_name = name
    res.detail = {}
    return res


def _status_detail(
    place: int = 22,
    grand: int = 34,
    uid: str = "900",
    name: str = "Alice",
    attempts: int = 5,
    cooldown: int = 0,
) -> dict:
    return {
        "user": {
            "result": {
                "response": {
                    "id": uid,
                    "name": name,
                    "refillable": [
                        {"id": 5, "amount": cooldown},
                        {"id": 6, "amount": attempts},
                    ],
                }
            }
        },
        "arena": {
            "result": {
                "response": {"arenaPlace": str(place), "grandPlace": str(grand)}
            }
        },
    }


def _saved_team_detail(
    heroes: list[int] | None = None,
    pet: int | None = 6008,
    banner: int | None = 2,
    favor: dict[int, int] | None = None,
) -> dict:
    heroes = heroes or [29, 13, 73, 71, 35]
    team = list(heroes) + ([pet] if pet is not None else [])
    return {
        "team": {"result": {"response": {"arena": team}}},
        "favor": {
            "result": {
                "response": {
                    "arena": {str(k): v for k, v in (favor or {}).items()}
                }
            }
        },
        "banner": {"result": {"response": {"arena": banner}}},
    }


def _top_response(
    entries: list[dict], own_place: int = 22, users: dict | None = None
) -> dict:
    return {
        "response": {
            "top": entries,
            "place": own_place,
            "users": users or {},
        }
    }


def _candidates_response(entries: list[dict]) -> dict:
    return {"response": {"enemies": entries}}


def _range_response(uid: int, allowed: bool) -> dict:
    return {"response": {str(uid): allowed}}


def _attack_response(win: bool = True, old: int = 22, new: int = 10) -> dict:
    return {
        "response": {
            "battles": [{"result": {"win": win, "oldPlace": old, "newPlace": new}}]
        }
    }


def _call_names(mock_call) -> list[str]:
    return [c.args[0]["calls"][0]["name"] for c in mock_call.call_args_list]


def test_fetch_arena_status_parses_rank_attempts_and_saved_team(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_status_detail(place=22, attempts=4, cooldown=120)),
        _ok(
            _saved_team_detail(
                heroes=[29, 13, 73, 71, 35],
                pet=6008,
                banner=2,
                favor={13: 6002, 29: 6011},
            )
        ),
    ]
    status = fetch_arena_status(client)
    assert status.place == 22
    assert status.grand_place == 34
    assert status.user_id == 900
    assert status.name == "Alice"
    assert status.attempts == 4
    assert status.cooldown == 120
    assert status.saved_team == ArenaTeam(
        heroes=[29, 13, 73, 71, 35],
        pet=6008,
        favor={13: 6002, 29: 6011},
        banners=[2],
    )


def test_fetch_arena_top_returns_raw_and_entries(mock_client, mock_sleep):
    client, mock_call = mock_client
    raw = _top_response(
        [
            {
                "place": "10",
                "userId": "111",
                "power": "5000",
                "heroes": [{"id": 1}],
            }
        ],
        users={"111": {"name": "Bob"}},
    )
    mock_call.side_effect = [_ok(raw)]
    top = fetch_arena_top(client)
    assert top.own_place == 22
    assert top.entries[0].name == "Bob"
    assert top.raw == raw["response"]


def test_fetch_arena_candidates_parses_entries(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(
            _candidates_response(
                [{"userId": "111", "place": "20", "user": {"name": "Bob"}}]
            )
        )
    ]
    entries = fetch_arena_candidates(client)
    assert [(e.user_id, e.place, e.name) for e in entries] == [(111, 20, "Bob")]


# --- target resolution ---


def _make_status(place: int = 22, user_id: int = 900) -> ArenaStatus:
    return ArenaStatus(
        place=place,
        grand_place=34,
        user_id=user_id,
        name="Alice",
        attempts=5,
        cooldown=0,
        saved_team=None,
    )


def test_resolve_target_rank_above_from_top(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "10", "userId": "111", "power": "5000"}])),
    ]
    target = resolve_target(client, _make_status(), rank=10)
    assert target == ArenaTarget(user_id=111, place=10, name=None, power=5000)
    assert _call_names(mock_call) == ["topGet"]


@pytest.mark.parametrize("rank", [22, 30])
def test_resolve_target_rejects_same_or_below_without_lookup(
    mock_client, mock_sleep, rank
):
    client, mock_call = mock_client
    with pytest.raises(ArenaError, match="above"):
        resolve_target(client, _make_status(place=22), rank=rank)
    assert mock_call.call_count == 0


def test_resolve_target_rank_falls_back_to_candidates(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([])),
        _ok(
            _candidates_response(
                [{"userId": "222", "place": "18", "user": {"name": "Bob"}}]
            )
        ),
    ]
    target = resolve_target(client, _make_status(), rank=18)
    assert (target.user_id, target.place, target.name) == (222, 18, "Bob")
    assert _call_names(mock_call) == ["topGet", "arenaFindEnemies"]


def test_resolve_target_rank_not_found_suggests_user_id(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_top_response([])), _ok(_candidates_response([]))]
    with pytest.raises(ArenaError) as exc:
        resolve_target(client, _make_status(), rank=1)
    message = str(exc.value)
    assert "--user-id" in message
    assert "top 50" in message.lower()


def test_resolve_target_user_id_found_in_top(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "3", "userId": "111", "user": {"name": "Bob"}}]))
    ]
    target = resolve_target(client, _make_status(), user_id=111)
    assert (target.user_id, target.place) == (111, 3)


@pytest.mark.parametrize("place", [22, 30])
def test_resolve_target_user_id_at_or_below_own_rank_rejected(
    mock_client, mock_sleep, place
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": str(place), "userId": "111"}]))
    ]
    with pytest.raises(ArenaError, match="above"):
        resolve_target(client, _make_status(place=22), user_id=111)


def test_resolve_target_unknown_user_id_rejected(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_top_response([])), _ok(_candidates_response([]))]
    with pytest.raises(ArenaError, match="Could not resolve"):
        resolve_target(client, _make_status(), user_id=111)


def test_resolve_target_rejects_self_before_lookup(mock_client, mock_sleep):
    client, mock_call = mock_client
    with pytest.raises(ArenaError, match="yourself"):
        resolve_target(client, _make_status(user_id=900), user_id=900)
    assert mock_call.call_count == 0


# --- range check batching ---


def test_check_target_range_batches_at_most_three(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok({"response": {"1": True, "2": False, "3": True}}),
        _ok({"response": {"4": True}}),
    ]
    result = check_target_range(client, [1, 2, 3, 4])
    assert result == {1: True, 2: False, 3: True, 4: True}
    sent_ids = [c.args[0]["calls"][0]["args"]["ids"] for c in mock_call.call_args_list]
    assert [len(ids) for ids in sent_ids] == [3, 1]
    assert all(len(ids) <= 3 for ids in sent_ids)


def test_check_target_range_too_many_raises(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_err("TooMany")]
    with pytest.raises(ArenaError, match="TooMany"):
        check_target_range(client, [1, 2, 3])


def test_check_target_range_empty_sends_nothing(mock_client, mock_sleep):
    client, mock_call = mock_client
    assert check_target_range(client, []) == {}
    assert mock_call.call_count == 0


# --- attack flow ---


def _team() -> ArenaTeam:
    return ArenaTeam(
        heroes=[29, 13, 73, 71, 35],
        pet=6008,
        favor={13: 6002, 29: 6011},
        banners=[2],
    )


def _target() -> ArenaTarget:
    return ArenaTarget(user_id=111, place=10, name="Bob", power=5000)


def test_run_arena_attack_dry_run_does_not_call_attack(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_range_response(111, True))]
    result = run_arena_attack(client, _make_status(), _target(), _team(), dry_run=True)
    assert result.dry_run is True
    assert result.accepted is False
    assert _call_names(mock_call) == ["arenaCheckTargetRange"]


def test_run_arena_attack_range_false_does_not_attack(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_range_response(111, False))]
    with pytest.raises(ArenaError) as exc:
        run_arena_attack(client, _make_status(place=22), _target(), _team())
    message = str(exc.value)
    assert "22" in message
    assert "1-21" in message
    assert _call_names(mock_call) == ["arenaCheckTargetRange"]


def test_run_arena_attack_sends_exact_args(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_range_response(111, True)), _ok(_attack_response())]
    result = run_arena_attack(client, _make_status(), _target(), _team())
    assert result.accepted is True
    assert result.win is True
    assert result.old_place == 22
    assert result.new_place == 10
    calls = mock_call.call_args_list
    assert [c.args[0]["calls"][0]["name"] for c in calls] == [
        "arenaCheckTargetRange",
        "arenaAttack",
    ]
    attack_call = calls[1].args[0]["calls"][0]
    assert attack_call["ident"] == "body"
    assert attack_call["context"] == {"actionTs": 0}
    assert attack_call["args"] == {
        "userId": 111,
        "heroes": [29, 13, 73, 71, 35],
        "pet": 6008,
        "favor": {"13": 6002, "29": 6011},
        "banners": [2],
    }


def test_run_arena_attack_not_enough_returns_stopped(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_range_response(111, True)), _err("NotEnough")]
    result = run_arena_attack(client, _make_status(), _target(), _team())
    assert result.accepted is False
    assert result.stopped == "NotEnough"
    assert result.warning


def test_run_arena_attack_refillable_returns_stopped(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_range_response(111, True)), _err("Refillable")]
    result = run_arena_attack(client, _make_status(), _target(), _team())
    assert result.accepted is False
    assert result.stopped == "Refillable"
    assert result.warning


def test_run_arena_attack_unknown_result_warns_but_accepts(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_range_response(111, True)), _ok({"response": {}})]
    result = run_arena_attack(client, _make_status(), _target(), _team())
    assert result.accepted is True
    assert result.win is None
    assert result.warning


def test_run_arena_attack_other_error_raises(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_range_response(111, True)), _err("SomeError")]
    with pytest.raises(ArenaError, match="arenaAttack failed"):
        run_arena_attack(client, _make_status(), _target(), _team())


# --- CLI handler behavior (light) ---


def _attack_args(**overrides) -> argparse.Namespace:
    base = dict(
        account="Alice",
        rank=10,
        user_id=None,
        team=None,
        heroes=None,
        pet=None,
        banner=None,
        favor=None,
        dry_run=False,
        debug=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _patch_attack_handler(monkeypatch):
    import hw_genie.main as main_mod
    import hw_genie.commands.arena as arena_mod

    monkeypatch.setattr(main_mod, "_ensure_session", lambda args: {"x-auth-token": "t"})
    monkeypatch.setattr(main_mod, "HWClient", lambda headers: MagicMock())
    monkeypatch.setattr(main_mod, "resolve_account", lambda account: "Alice")
    monkeypatch.setattr(
        arena_mod, "fetch_arena_status", lambda client: _make_status()
    )
    monkeypatch.setattr(arena_mod, "load_arena_presets", lambda account: {})
    monkeypatch.setattr(arena_mod, "parse_favor_args", lambda raw: None)
    monkeypatch.setattr(
        arena_mod,
        "resolve_team",
        lambda **kwargs: (_team(), []),
    )
    monkeypatch.setattr(
        arena_mod,
        "resolve_target",
        lambda client, status, **kwargs: _target(),
    )
    return main_mod, arena_mod


def test_cmd_arena_attack_error_exits_1(monkeypatch):
    main_mod, arena_mod = _patch_attack_handler(monkeypatch)

    def _raise(*args, **kwargs):
        raise ArenaError("target out of range")

    monkeypatch.setattr(arena_mod, "run_arena_attack", _raise)
    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_arena_attack(_attack_args())
    assert exc.value.code == 1


@pytest.mark.parametrize("stopped", ["NotEnough", "Refillable"])
def test_cmd_arena_attack_consumption_stop_exits_0(monkeypatch, stopped):
    main_mod, arena_mod = _patch_attack_handler(monkeypatch)
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *args, **kwargs: ArenaAttackResult(
            dry_run=False, accepted=False, stopped=stopped, warning="stop"
        ),
    )
    # No SystemExit: a consumed-attempt/cooldown stop is a clean exit 0.
    main_mod.cmd_arena_attack(_attack_args())


def test_arena_cli_registered(monkeypatch, capsys):
    import sys

    import hw_genie.main as main_mod

    monkeypatch.setattr(sys, "argv", ["hw-genie", "arena", "attack", "--help"])
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--rank" in out
    assert "--user-id" in out
    assert "--dry-run" in out
    assert "--team" in out
