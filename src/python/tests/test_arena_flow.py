"""Mocked-client flow tests for Arena v1 (no live API calls)."""

import argparse
from unittest.mock import MagicMock

import pytest
from requests.exceptions import Timeout

from hw_genie.commands.arena import (
    ArenaAttackResult,
    ArenaError,
    ArenaSnapshot,
    ArenaStatus,
    ArenaTarget,
    ArenaTeam,
    ArenaTopEntry,
    check_target_range,
    fetch_arena_candidates,
    fetch_arena_status,
    fetch_arena_top,
    fetch_candidate,
    resolve_snapshot_target,
    resolve_target,
    run_arena_attack,
    run_arena_attack_from_snapshot,
    run_arena_find_enemies,
    run_arena_snapshot,
)
from hw_genie.core.client import HWClient, ResponseStatus


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


def _err_detail(name: str, detail: dict) -> MagicMock:
    res = MagicMock()
    res.is_success = False
    res.error_name = name
    res.detail = detail
    return res


def _unexpected(name: str = "network_or_parse_error") -> MagicMock:
    res = MagicMock()
    res.is_success = False
    res.status = ResponseStatus.UNEXPECTED
    res.error_name = name
    res.detail = "timeout"
    return res


def _own_place_response(place: int = 22) -> dict:
    return {"response": {"arenaPlace": str(place)}}


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


def _candidate(
    uid: int = 111, place: int = 10, name: str = "Bob", power: int = 5000
) -> dict:
    return {
        "userId": str(uid),
        "place": str(place),
        "power": str(power),
        "user": {"name": name},
    }


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


def test_fetch_arena_status_treats_saved_banner_zero_as_absent(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_status_detail()),
        _ok(_saved_team_detail(banner=0)),
    ]
    status = fetch_arena_status(client)
    assert status.saved_team is not None
    assert status.saved_team.banners == []


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


def _make_status(place: int = 22, user_id: int = 900, cooldown: int = 0) -> ArenaStatus:
    return ArenaStatus(
        place=place,
        grand_place=34,
        user_id=user_id,
        name="Alice",
        attempts=5,
        cooldown=cooldown,
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
def test_resolve_target_rejects_same_or_below(mock_client, mock_sleep, rank):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_top_response([]))]
    with pytest.raises(ArenaError, match="above"):
        resolve_target(client, _make_status(place=22), rank=rank)
    assert _call_names(mock_call) == ["topGet"]


def test_resolve_target_prefers_fresh_top_place_over_status(mock_client, mock_sleep):
    # status was captured at rank 30, but topGet now reports rank 22. A target
    # at rank 25 is below the fresh rank and must be rejected.
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "25", "userId": "111"}], own_place=22)),
    ]
    with pytest.raises(ArenaError, match="above"):
        resolve_target(client, _make_status(place=30), rank=25)


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


def test_resolve_target_rank_retries_candidates(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([])),
        _ok(_candidates_response([_candidate(uid=999, place=18)])),
        _ok(_candidates_response([_candidate(uid=222, place=17, name="Bob")])),
    ]
    target = resolve_target(
        client, _make_status(), rank=17, candidate_retries=1
    )
    assert (target.user_id, target.place, target.name) == (222, 17, "Bob")
    assert _call_names(mock_call) == [
        "topGet",
        "arenaFindEnemies",
        "arenaFindEnemies",
    ]


def test_resolve_target_user_id_retries_candidates(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([])),
        _ok(_candidates_response([_candidate(uid=999, place=18)])),
        _ok(_candidates_response([_candidate(uid=111, place=17)])),
    ]
    target = resolve_target(
        client, _make_status(), user_id=111, candidate_retries=1
    )
    assert (target.user_id, target.place) == (111, 17)


def test_resolve_target_rank_not_found_suggests_retrying(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_top_response([])), _ok(_candidates_response([]))]
    with pytest.raises(ArenaError) as exc:
        resolve_target(client, _make_status(), rank=1, candidate_retries=0)
    message = str(exc.value)
    assert "--candidate-retries" in message
    assert "top 50" in message.lower()


def test_resolve_target_user_id_found_in_top(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "3", "userId": "111", "user": {"name": "Bob"}}]))
    ]
    target = resolve_target(client, _make_status(), user_id=111)
    assert (target.user_id, target.place) == (111, 3)


def test_resolve_target_user_id_falls_back_to_candidates(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([])),
        _ok(
            _candidates_response(
                [{"userId": "111", "place": "18", "user": {"name": "Bob"}}]
            )
        ),
    ]
    target = resolve_target(client, _make_status(), user_id=111)
    assert (target.user_id, target.place, target.name) == (111, 18, "Bob")
    assert _call_names(mock_call) == ["topGet", "arenaFindEnemies"]


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
        resolve_target(client, _make_status(), user_id=111, candidate_retries=0)


def test_resolve_target_rejects_self_before_lookup(mock_client, mock_sleep):
    client, mock_call = mock_client
    with pytest.raises(ArenaError, match="yourself"):
        resolve_target(client, _make_status(user_id=900), user_id=900)
    assert mock_call.call_count == 0


@pytest.mark.parametrize("retries", [-1, 1.5, True])
def test_resolve_target_rejects_invalid_retries_without_api_calls(
    mock_client, mock_sleep, retries
):
    client, mock_call = mock_client
    with pytest.raises(ArenaError, match="candidate_retries"):
        resolve_target(
            client, _make_status(), rank=10, candidate_retries=retries
        )
    assert mock_call.call_count == 0


@pytest.mark.parametrize("retries", [-1, 1.5, True])
def test_fetch_candidate_rejects_invalid_retries_without_api_calls(
    mock_client, mock_sleep, retries
):
    client, mock_call = mock_client
    with pytest.raises(ArenaError, match="candidate_retries"):
        fetch_candidate(client, user_id=111, retries=retries)
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


def _entry(
    uid: int = 111, place: int = 10, name: str = "Bob", power: int = 5000
) -> ArenaTopEntry:
    return ArenaTopEntry(user_id=uid, place=place, name=name, power=power)


def test_run_arena_attack_dry_run_does_not_call_attack(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
    ]
    result = run_arena_attack(client, _make_status(), _target(), _team(), dry_run=True)
    assert result.dry_run is True
    assert result.accepted is False
    assert _call_names(mock_call) == [
        "arenaGetAll",
        "arenaFindEnemies",
        "arenaGetAll",
        "arenaCheckTargetRange",
    ]


def test_run_arena_attack_range_false_does_not_attack(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, False)),
    ]
    with pytest.raises(ArenaError) as exc:
        run_arena_attack(client, _make_status(place=22), _target(), _team())
    message = str(exc.value)
    assert "22" in message
    assert "rank/league-dependent" in message
    assert _call_names(mock_call) == [
        "arenaGetAll",
        "arenaFindEnemies",
        "arenaGetAll",
        "arenaCheckTargetRange",
    ]


def test_run_arena_attack_range_missing_target_does_not_attack(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok({"response": {}}),
    ]
    with pytest.raises(ArenaError, match="outside the attack range"):
        run_arena_attack(client, _make_status(), _target(), _team())
    assert "arenaAttack" not in _call_names(mock_call)


def test_run_arena_attack_rejects_target_below_fresh_rank(
    mock_client, mock_sleep
):
    # status said rank 30, but the fresh arenaGetAll says 22: target 25 is
    # below the player now and must not even reach the range check.
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_own_place_response(22))]
    target = ArenaTarget(user_id=111, place=25, name="Bob", power=5000)
    with pytest.raises(ArenaError, match="upper-only"):
        run_arena_attack(client, _make_status(place=30), target, _team())
    assert _call_names(mock_call) == ["arenaGetAll"]


def test_run_arena_attack_uses_fresh_place_for_window_message(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response(18)),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response(18)),
        _ok(_range_response(111, False)),
    ]
    with pytest.raises(ArenaError) as exc:
        run_arena_attack(client, _make_status(place=22), _target(), _team())
    message = str(exc.value)
    assert "rank 18" in message
    assert "rank/league-dependent" in message


def test_run_arena_attack_rereads_own_place_after_candidate(mock_client, mock_sleep):
    # The first read says 22, the candidate is at 15, but the player rose to 10
    # while the candidate list was being refreshed: the target is no longer
    # above the player, so the attack must stop before the range check.
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response(22)),
        _ok(_candidates_response([_candidate(place=15)])),
        _ok(_own_place_response(10)),
    ]
    with pytest.raises(ArenaError, match="upper-only"):
        run_arena_attack(client, _make_status(place=22), _target(), _team())
    assert _call_names(mock_call) == [
        "arenaGetAll",
        "arenaFindEnemies",
        "arenaGetAll",
    ]


def test_run_arena_attack_reuses_resolved_candidate(mock_client, mock_sleep):
    # A target resolved from the candidate list carries its entry, so the
    # attack step must not refresh arenaFindEnemies again.
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _ok(_attack_response()),
    ]
    target = ArenaTarget(
        user_id=111,
        place=15,
        name="Bob",
        power=5000,
        candidate=_entry(place=15),
    )
    result = run_arena_attack(client, _make_status(), target, _team())
    assert result.accepted is True
    assert _call_names(mock_call) == [
        "arenaGetAll",
        "arenaCheckTargetRange",
        "arenaAttack",
    ]


def test_run_arena_attack_rejects_reused_candidate_that_moved_below(
    mock_client, mock_sleep
):
    # resolve_target captured rank 5, but the reused candidate entry now says
    # rank 10 (= own rank); the reuse path must still re-check upper-only.
    client, mock_call = mock_client
    mock_call.side_effect = [_ok(_own_place_response(10))]
    target = ArenaTarget(
        user_id=111,
        place=5,
        name="Bob",
        power=5000,
        candidate=_entry(place=10),
    )
    with pytest.raises(ArenaError, match="upper-only"):
        run_arena_attack(client, _make_status(place=10), target, _team())
    assert _call_names(mock_call) == ["arenaGetAll"]


def test_resolved_candidate_is_not_refetched_by_attack(mock_client, mock_sleep):
    # --user-id outside the top 50: resolve_target consumes one candidate list,
    # and run_arena_attack reuses it instead of doubling the retry budget.
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([])),
        _ok(_candidates_response([_candidate(place=15)])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _ok(_attack_response()),
    ]
    target = resolve_target(client, _make_status(), user_id=111, candidate_retries=3)
    assert target.candidate is not None
    run_arena_attack(
        client, _make_status(), target, _team(), candidate_retries=3
    )
    names = _call_names(mock_call)
    assert names.count("arenaFindEnemies") == 1
    assert names == [
        "topGet",
        "arenaFindEnemies",
        "arenaGetAll",
        "arenaCheckTargetRange",
        "arenaAttack",
    ]


def test_run_arena_attack_refreshes_candidates_until_target_appears(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate(uid=999, place=18)])),
        _ok(_candidates_response([_candidate(uid=998, place=15)])),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _ok(_attack_response()),
    ]
    result = run_arena_attack(
        client,
        _make_status(),
        _target(),
        _team(),
        dry_run=True,
        candidate_retries=2,
    )
    assert result.dry_run is True
    assert _call_names(mock_call) == [
        "arenaGetAll",
        "arenaFindEnemies",
        "arenaFindEnemies",
        "arenaFindEnemies",
        "arenaGetAll",
        "arenaCheckTargetRange",
    ]


def test_run_arena_attack_candidate_retry_exhaustion_does_not_attack(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate(uid=999, place=18)])),
        _ok(_candidates_response([_candidate(uid=998, place=15)])),
        _ok(_candidates_response([_candidate(uid=997, place=12)])),
    ]
    with pytest.raises(ArenaError) as exc:
        run_arena_attack(
            client, _make_status(), _target(), _team(), candidate_retries=2
        )
    message = str(exc.value)
    assert "did not appear" in message
    assert "no attack was sent" in message
    assert "--candidate-retries" in message
    assert _call_names(mock_call) == [
        "arenaGetAll",
        "arenaFindEnemies",
        "arenaFindEnemies",
        "arenaFindEnemies",
    ]
    assert "arenaAttack" not in _call_names(mock_call)
    assert "arenaCheckTargetRange" not in _call_names(mock_call)


def test_run_arena_attack_zero_candidate_retries_fetches_once(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([])),
    ]
    with pytest.raises(ArenaError, match="after 1 attempt"):
        run_arena_attack(
            client, _make_status(), _target(), _team(), candidate_retries=0
        )
    assert _call_names(mock_call) == ["arenaGetAll", "arenaFindEnemies"]


def test_run_arena_attack_rejects_candidate_that_moved_below(
    mock_client, mock_sleep
):
    # resolved as rank 5 earlier, but the fresh candidate entry says rank 10
    # (= own rank), so the upper-only rule must reject it.
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response(10)),
        _ok(_candidates_response([_candidate(place=10)])),
        _ok(_own_place_response(10)),
    ]
    target = ArenaTarget(user_id=111, place=5, name="Bob", power=5000)
    with pytest.raises(ArenaError, match="upper-only"):
        run_arena_attack(client, _make_status(place=10), target, _team())
    assert _call_names(mock_call) == [
        "arenaGetAll",
        "arenaFindEnemies",
        "arenaGetAll",
    ]


@pytest.mark.parametrize("retries", [-1, 1.5, True])
def test_run_arena_attack_rejects_invalid_candidate_retries(
    mock_client, mock_sleep, retries
):
    client, mock_call = mock_client
    with pytest.raises(ArenaError, match="candidate_retries"):
        run_arena_attack(
            client, _make_status(), _target(), _team(), candidate_retries=retries
        )
    assert mock_call.call_count == 0


def test_run_arena_attack_uses_candidate_place_and_name(
    mock_client, mock_sleep, capsys
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(
            _candidates_response(
                [_candidate(place=9, name="Carol", power=9000)]
            )
        ),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
    ]
    run_arena_attack(client, _make_status(), _target(), _team(), dry_run=True)
    out = capsys.readouterr().out
    assert "rank 9" in out
    assert "Carol" in out


def test_run_arena_attack_sends_exact_args_once_without_retry(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _ok(_attack_response()),
    ]
    result = run_arena_attack(client, _make_status(), _target(), _team())
    assert result.accepted is True
    assert result.win is True
    assert result.old_place == 22
    assert result.new_place == 10
    calls = mock_call.call_args_list
    assert [c.args[0]["calls"][0]["name"] for c in calls] == [
        "arenaGetAll",
        "arenaFindEnemies",
        "arenaGetAll",
        "arenaCheckTargetRange",
        "arenaAttack",
    ]
    attack_call = calls[4].args[0]["calls"][0]
    assert attack_call["ident"] == "body"
    assert attack_call["context"] == {"actionTs": 0}
    assert attack_call["args"] == {
        "userId": 111,
        "heroes": [29, 13, 73, 71, 35],
        "pet": 6008,
        "favor": {"13": 6002, "29": 6011},
        "banners": [2],
    }
    # The attack must be sent exactly once (no client-side retry).
    assert calls[4].kwargs == {"max_retries": 0}


def test_run_arena_attack_network_error_is_unknown_outcome(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _unexpected("network_or_parse_error"),
    ]
    with pytest.raises(ArenaError) as exc:
        run_arena_attack(client, _make_status(), _target(), _team())
    message = str(exc.value)
    assert "outcome is unknown" in message
    assert "may have consumed an attempt" in message
    assert _call_names(mock_call) == [
        "arenaGetAll",
        "arenaFindEnemies",
        "arenaGetAll",
        "arenaCheckTargetRange",
        "arenaAttack",
    ]
    assert mock_call.call_args_list[4].kwargs == {"max_retries": 0}


def test_hwclient_call_max_retries_zero_makes_single_attempt(mock_sleep):
    session = MagicMock()
    session.post.side_effect = Timeout("boom")
    client = HWClient(headers={"x-auth-token": "t"}, session=session)
    res = client.call(
        {"calls": [{"name": "arenaAttack", "args": {}, "ident": "body"}]},
        max_retries=0,
    )
    assert res.status == ResponseStatus.UNEXPECTED
    assert session.post.call_count == 1


def test_run_arena_attack_not_enough_returns_stopped(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _err("NotEnough"),
    ]
    result = run_arena_attack(client, _make_status(), _target(), _team())
    assert result.accepted is False
    assert result.stopped == "NotEnough"
    assert result.warning


@pytest.mark.parametrize(
    ("name", "detail", "expected"),
    [
        ("NotEnough", {"name": "NotEnough", "description": "Refillable 5"}, "Refillable"),
        ("NotEnough", {"name": "NotEnough", "description": "Refillable 6"}, "NotEnough"),
        ("Refillable", {"name": "Refillable", "description": "Refillable 6"}, "NotEnough"),
        ("Refillable", {"name": "Refillable", "description": "Refillable 5"}, "Refillable"),
    ],
)
def test_run_arena_attack_classifies_refillable_detail(
    mock_client, mock_sleep, name, detail, expected
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _err_detail(name, detail),
    ]
    result = run_arena_attack(
        client, _make_status(cooldown=120), _target(), _team()
    )
    assert result.accepted is False
    assert result.stopped == expected
    if expected == "Refillable":
        assert "120s" in (result.warning or "")
    else:
        assert "not enough attack attempts" in (result.warning or "")


def test_run_arena_attack_refillable_returns_stopped(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _err("Refillable"),
    ]
    result = run_arena_attack(
        client, _make_status(cooldown=120), _target(), _team()
    )
    assert result.accepted is False
    assert result.stopped == "Refillable"
    assert result.warning
    assert "120s" in result.warning


def test_run_arena_attack_unknown_result_warns_but_accepts(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _ok({"response": {}}),
    ]
    result = run_arena_attack(client, _make_status(), _target(), _team())
    assert result.accepted is True
    assert result.win is None
    assert result.warning


def test_run_arena_attack_other_error_raises(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response()),
        _ok(_candidates_response([_candidate()])),
        _ok(_own_place_response()),
        _ok(_range_response(111, True)),
        _err("SomeError"),
    ]
    with pytest.raises(ArenaError, match="arenaAttack failed"):
        run_arena_attack(client, _make_status(), _target(), _team())


# --- snapshots (manual verification) ---


def _snap_entry(uid: int, place: int, name: str = "Bob") -> ArenaTopEntry:
    return ArenaTopEntry(place=place, user_id=uid, name=name, power=1000)


def _snapshot(
    own_place: int = 16,
    candidates: list[ArenaTopEntry] | None = None,
) -> ArenaSnapshot:
    default = [
        _snap_entry(111, 4, "Bob"),
        _snap_entry(222, 9, "Carol"),
        _snap_entry(333, 14, "Dave"),
    ]
    return ArenaSnapshot(
        own_place=own_place,
        candidates=candidates if candidates is not None else default,
        saved_at="2026-10-29T16:10:15+09:00",
    )


def test_resolve_snapshot_target_by_rank_and_user_id():
    snapshot = _snapshot()
    assert resolve_snapshot_target(snapshot, rank=9).user_id == 222
    assert resolve_snapshot_target(snapshot, user_id=111).place == 4
    with pytest.raises(ArenaError, match="not in the saved"):
        resolve_snapshot_target(snapshot, rank=3)
    with pytest.raises(ArenaError, match="exactly one"):
        resolve_snapshot_target(snapshot)


def test_run_arena_find_enemies_saves_snapshot(mock_client, mock_sleep, monkeypatch):
    import hw_genie.commands.arena as arena_mod

    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_own_place_response(16)),
        _ok(
            _candidates_response(
                [_candidate(uid=111, place=4), _candidate(uid=222, place=9)]
            )
        ),
    ]
    saved: dict = {}

    def _save(account, snapshot, mode=None):
        saved.update(account=account, snapshot=snapshot)
        return snapshot

    monkeypatch.setattr(arena_mod, "save_arena_snapshot", _save)
    result = run_arena_find_enemies(client, "Alice")
    assert _call_names(mock_call) == ["arenaGetAll", "arenaFindEnemies"]
    assert saved["account"] == "Alice"
    assert saved["snapshot"] is result
    assert result.own_place == 16
    assert [(c.user_id, c.place) for c in result.candidates] == [(111, 4), (222, 9)]


def test_run_arena_snapshot_shows_current_ranks_without_find_enemies(
    mock_client, mock_sleep, monkeypatch, capsys
):
    import hw_genie.commands.arena as arena_mod

    client, mock_call = mock_client
    monkeypatch.setattr(
        arena_mod, "load_arena_snapshot", lambda account, mode=None: _snapshot()
    )
    mock_call.side_effect = [
        _ok(
            _top_response(
                [
                    {"place": "2", "userId": "111"},
                    {"place": "10", "userId": "222"},
                    {"place": "14", "userId": "333"},
                ],
                own_place=16,
            )
        ),
        _ok({"response": {"111": True, "222": False, "333": True}}),
    ]
    run_arena_snapshot(client, "Alice")
    assert "arenaFindEnemies" not in _call_names(mock_call)
    out = capsys.readouterr().out
    assert "Own rank: 16 -> 16" in out
    assert ">13" in out
    assert "yes" in out
    assert "false" in out
    assert "topGet covers the top 50 only" in out


def test_run_arena_snapshot_without_saved_snapshot_raises(
    mock_client, mock_sleep, monkeypatch
):
    import hw_genie.commands.arena as arena_mod

    client, mock_call = mock_client
    monkeypatch.setattr(arena_mod, "load_arena_snapshot", lambda account, mode=None: None)
    with pytest.raises(ArenaError, match="find-enemies"):
        run_arena_snapshot(client, "Alice")
    assert mock_call.call_count == 0


def test_run_arena_attack_from_snapshot_skips_find_enemies(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "14", "userId": "111"}], own_place=16)),
        _ok(_range_response(111, True)),
        _ok(_attack_response(old=16, new=14)),
    ]
    target = ArenaTarget(user_id=111, place=14, name="Bob", power=5000)
    result = run_arena_attack_from_snapshot(
        client, _make_status(place=16), target, _team()
    )
    assert result.accepted is True
    assert _call_names(mock_call) == [
        "topGet",
        "arenaCheckTargetRange",
        "arenaAttack",
    ]
    assert "arenaFindEnemies" not in _call_names(mock_call)


def test_run_arena_attack_from_snapshot_range_false_stops_without_force(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "14", "userId": "111"}], own_place=16)),
        _ok(_range_response(111, False)),
    ]
    target = ArenaTarget(user_id=111, place=14, name="Bob", power=5000)
    with pytest.raises(ArenaError, match="--force"):
        run_arena_attack_from_snapshot(
            client, _make_status(place=16), target, _team()
        )
    assert "arenaAttack" not in _call_names(mock_call)


def test_run_arena_attack_from_snapshot_force_sends_when_range_false(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "14", "userId": "111"}], own_place=16)),
        _ok(_range_response(111, False)),
        _ok(_attack_response(old=16, new=14)),
    ]
    target = ArenaTarget(user_id=111, place=14, name="Bob", power=5000)
    result = run_arena_attack_from_snapshot(
        client, _make_status(place=16), target, _team(), force=True
    )
    assert result.accepted is True
    assert _call_names(mock_call)[-1] == "arenaAttack"


def test_run_arena_attack_from_snapshot_rejects_now_below(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "10", "userId": "111"}], own_place=10)),
    ]
    target = ArenaTarget(user_id=111, place=5, name="Bob", power=5000)
    with pytest.raises(ArenaError, match="upper-only"):
        run_arena_attack_from_snapshot(
            client, _make_status(place=10), target, _team()
        )
    assert _call_names(mock_call) == ["topGet"]


def test_run_arena_attack_from_snapshot_dry_run_sends_nothing(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "14", "userId": "111"}], own_place=16)),
        _ok(_range_response(111, True)),
    ]
    target = ArenaTarget(user_id=111, place=14, name="Bob", power=5000)
    result = run_arena_attack_from_snapshot(
        client, _make_status(place=16), target, _team(), dry_run=True
    )
    assert result.dry_run is True
    assert "arenaAttack" not in _call_names(mock_call)


def test_run_arena_attack_from_snapshot_rejects_target_absent_from_top(
    mock_client, mock_sleep
):
    # A target that left the top 50 has an unverifiable current rank, so the
    # stale snapshot place must not be used to bypass the upper-only rule.
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([], own_place=16)),
    ]
    target = ArenaTarget(user_id=111, place=14, name="Bob", power=5000)
    with pytest.raises(ArenaError, match="not in the current top 50"):
        run_arena_attack_from_snapshot(
            client, _make_status(place=16), target, _team()
        )
    names = _call_names(mock_call)
    assert names == ["topGet"]
    assert "arenaCheckTargetRange" not in names
    assert "arenaAttack" not in names


def test_run_arena_attack_from_snapshot_force_still_enforces_upper_only(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "10", "userId": "111"}], own_place=10)),
    ]
    target = ArenaTarget(user_id=111, place=5, name="Bob", power=5000)
    with pytest.raises(ArenaError, match="upper-only"):
        run_arena_attack_from_snapshot(
            client, _make_status(place=10), target, _team(), force=True
        )
    names = _call_names(mock_call)
    assert names == ["topGet"]
    assert "arenaCheckTargetRange" not in names
    assert "arenaAttack" not in names


def test_run_arena_attack_from_snapshot_uses_current_place_over_snapshot(
    mock_client, mock_sleep
):
    # Snapshot said own 16 / target 14, but both moved up: own 10, target 8.
    # The stale snapshot pre-check must not reject this valid attack.
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(_top_response([{"place": "8", "userId": "111"}], own_place=10)),
        _ok(_range_response(111, True)),
    ]
    target = ArenaTarget(user_id=111, place=14, name="Bob", power=5000)
    result = run_arena_attack_from_snapshot(
        client, _make_status(place=16), target, _team(), dry_run=True
    )
    assert result.dry_run is True
    assert _call_names(mock_call) == [
        "topGet",
        "arenaCheckTargetRange",
    ]


def test_run_arena_attack_from_snapshot_falls_back_to_arena_get_all(
    mock_client, mock_sleep
):
    # topGet normally carries the own rank; when it does not, arenaGetAll is
    # used as the fallback before the upper-only check.
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(
            {
                "response": {
                    "top": [{"place": "14", "userId": "111"}],
                    "users": {},
                }
            }
        ),
        _ok(_own_place_response(16)),
        _ok(_range_response(111, True)),
    ]
    target = ArenaTarget(user_id=111, place=14, name="Bob", power=5000)
    result = run_arena_attack_from_snapshot(
        client, _make_status(place=16), target, _team(), dry_run=True
    )
    assert result.dry_run is True
    assert _call_names(mock_call) == [
        "topGet",
        "arenaGetAll",
        "arenaCheckTargetRange",
    ]


def test_run_arena_attack_from_snapshot_rejects_self_without_api_calls(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    target = ArenaTarget(user_id=900, place=5, name="Alice", power=5000)
    with pytest.raises(ArenaError, match="yourself"):
        run_arena_attack_from_snapshot(
            client, _make_status(user_id=900), target, _team()
        )
    assert mock_call.call_count == 0


def test_resolve_snapshot_target_empty_snapshot_raises():
    snapshot = _snapshot(candidates=[])
    assert snapshot.candidates == []
    with pytest.raises(ArenaError, match="not in the saved"):
        resolve_snapshot_target(snapshot, rank=4)


def test_run_arena_snapshot_handles_candidate_outside_top50(
    mock_client, mock_sleep, monkeypatch, capsys
):
    import hw_genie.commands.arena as arena_mod

    client, mock_call = mock_client
    monkeypatch.setattr(
        arena_mod,
        "load_arena_snapshot",
        lambda account, mode=None: _snapshot(
            candidates=[_snap_entry(111, 4), _snap_entry(222, 9)]
        ),
    )
    mock_call.side_effect = [
        _ok(_top_response([{"place": "2", "userId": "111"}], own_place=16)),
        _ok({"response": {"111": True, "222": True}}),
    ]
    run_arena_snapshot(client, "Alice")
    assert "arenaFindEnemies" not in _call_names(mock_call)
    assert "?" in capsys.readouterr().out


def test_run_arena_snapshot_falls_back_to_arena_get_all_when_top_place_missing(
    mock_client, mock_sleep, monkeypatch, capsys
):
    # topGet normally carries the own rank; when it does not, arenaGetAll is
    # used before printing the snapshot delta column.
    import hw_genie.commands.arena as arena_mod

    client, mock_call = mock_client
    monkeypatch.setattr(
        arena_mod, "load_arena_snapshot", lambda account, mode=None: _snapshot()
    )
    mock_call.side_effect = [
        _ok(
            {
                "response": {
                    "top": [{"place": "4", "userId": "111"}],
                    "users": {},
                }
            }
        ),
        _ok(_own_place_response(16)),
        _ok({"response": {"111": True, "222": False, "333": True}}),
    ]
    run_arena_snapshot(client, "Alice")
    assert _call_names(mock_call) == [
        "topGet",
        "arenaGetAll",
        "arenaCheckTargetRange",
    ]
    assert "Own rank: 16 -> 16" in capsys.readouterr().out


def test_run_arena_snapshot_with_empty_snapshot_prints_no_candidates(
    mock_client, mock_sleep, monkeypatch, capsys
):
    import hw_genie.commands.arena as arena_mod

    client, mock_call = mock_client
    monkeypatch.setattr(
        arena_mod,
        "load_arena_snapshot",
        lambda account, mode=None: _snapshot(candidates=[]),
    )
    mock_call.side_effect = [
        _ok(_top_response([], own_place=16)),
    ]
    run_arena_snapshot(client, "Alice")
    assert "arenaCheckTargetRange" not in _call_names(mock_call)
    assert "No candidates in the snapshot." in capsys.readouterr().out


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
        from_snapshot=False,
        force=False,
        candidate_retries=10,
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


def test_cmd_arena_attack_passes_candidate_retries(monkeypatch):
    main_mod, arena_mod = _patch_attack_handler(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *args, **kwargs: captured.update(kwargs),
    )
    main_mod.cmd_arena_attack(_attack_args(candidate_retries=3))
    assert captured["candidate_retries"] == 3


def test_cmd_arena_attack_passes_dry_run_and_favor(monkeypatch):
    main_mod, arena_mod = _patch_attack_handler(monkeypatch)
    attack_kwargs: dict = {}
    team_kwargs: dict = {}
    monkeypatch.setattr(arena_mod, "parse_favor_args", lambda raw: {13: 6002})
    monkeypatch.setattr(
        arena_mod,
        "resolve_team",
        lambda **kwargs: (team_kwargs.update(kwargs), (_team(), []))[1],
    )
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *args, **kwargs: attack_kwargs.update(kwargs),
    )
    main_mod.cmd_arena_attack(
        _attack_args(dry_run=True, candidate_retries=4, favor=["13=6002"])
    )
    assert attack_kwargs["dry_run"] is True
    assert attack_kwargs["candidate_retries"] == 4
    assert team_kwargs["favor"] == {13: 6002}


def test_cmd_arena_attack_warns_when_saved_team_missing(monkeypatch, capsys):
    main_mod, arena_mod = _patch_attack_handler(monkeypatch)
    monkeypatch.setattr(arena_mod, "run_arena_attack", lambda *args, **kwargs: None)
    main_mod.cmd_arena_attack(_attack_args(heroes=[1, 2, 3, 4, 5]))
    assert "saved Arena team" in capsys.readouterr().err


def test_cmd_arena_attack_unknown_preset_fails_before_status(monkeypatch):
    main_mod, arena_mod = _patch_attack_handler(monkeypatch)
    called: dict = {}
    monkeypatch.setattr(arena_mod, "load_arena_presets", lambda account: {})
    monkeypatch.setattr(
        arena_mod,
        "fetch_arena_status",
        lambda client: called.setdefault("status", True),
    )
    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_arena_attack(_attack_args(team="missing"))
    assert exc.value.code == 1
    assert "status" not in called


def test_cmd_arena_attack_force_without_snapshot_errors(monkeypatch, capsys):
    main_mod, _arena_mod = _patch_attack_handler(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_arena_attack(_attack_args(force=True))
    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err


def test_cmd_arena_attack_from_snapshot_uses_snapshot_runner(monkeypatch):
    main_mod, arena_mod = _patch_attack_handler(monkeypatch)
    monkeypatch.setattr(
        arena_mod, "load_arena_snapshot", lambda account, mode=None: _snapshot()
    )
    monkeypatch.setattr(
        arena_mod, "resolve_snapshot_target", lambda snapshot, **kwargs: _target()
    )

    def _no_resolve(*args, **kwargs):
        raise AssertionError("resolve_target must not run with --from-snapshot")

    monkeypatch.setattr(arena_mod, "resolve_target", _no_resolve)
    captured: dict = {}
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack_from_snapshot",
        lambda *args, **kwargs: captured.update(kwargs),
    )
    main_mod.cmd_arena_attack(_attack_args(from_snapshot=True, force=True))
    assert captured["force"] is True
    assert captured["dry_run"] is False


def test_cmd_arena_attack_from_snapshot_without_snapshot_errors(monkeypatch):
    main_mod, arena_mod = _patch_attack_handler(monkeypatch)
    monkeypatch.setattr(arena_mod, "load_arena_snapshot", lambda account, mode=None: None)
    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_arena_attack(_attack_args(from_snapshot=True))
    assert exc.value.code == 1


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
    assert "--candidate-retries" in out
    assert "--from-snapshot" in out
    assert "--force" in out


@pytest.mark.parametrize(
    "command", [["arena", "find-enemies"], ["arena", "snapshot"]]
)
def test_arena_snapshot_commands_registered(monkeypatch, capsys, command):
    import sys

    import hw_genie.main as main_mod

    monkeypatch.setattr(sys, "argv", ["hw-genie", *command, "--help"])
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()


# --- parser `-a` placement (finding 7) ---


def _team_args(**overrides) -> argparse.Namespace:
    base = dict(
        account="Alice",
        name="atk1",
        from_saved=False,
        heroes=[1, 2, 3, 4, 5],
        pet=None,
        banner=None,
        favor=None,
        debug=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_arena_parser_keeps_account_when_after_leaf(monkeypatch):
    import sys

    import hw_genie.main as main_mod

    captured: dict = {}
    monkeypatch.setattr(
        sys,
        "argv",
        ["hw-genie", "arena", "attack", "-a", "Alice", "--rank", "1"],
    )
    monkeypatch.setattr(
        main_mod, "cmd_arena_attack", lambda args: captured.update(vars(args))
    )
    monkeypatch.setattr("hw_genie.core.database.init_db", lambda: None)
    monkeypatch.setattr("hw_genie.core.database.install_token_masking_filter", lambda: None)
    monkeypatch.setattr(main_mod, "setup_logging", lambda debug=False: None)
    main_mod.main()
    assert captured["account"] == "Alice"
    assert captured["rank"] == 1


def test_arena_parser_rejects_account_before_leaf(monkeypatch):
    import sys

    import hw_genie.main as main_mod

    monkeypatch.setattr(
        sys,
        "argv",
        ["hw-genie", "arena", "-a", "Alice", "attack", "--rank", "1"],
    )
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2


def test_cmd_arena_team_set_direct_heroes_needs_no_session(monkeypatch):
    import hw_genie.commands.arena as arena_mod
    import hw_genie.main as main_mod
    from hw_genie.core.session_manager import SessionManager

    captured: dict = {}
    monkeypatch.setattr(main_mod, "resolve_account", lambda account: "Alice")
    monkeypatch.setattr(SessionManager, "load", lambda account: {"headers": {}})

    def _no_session(_args):
        raise AssertionError("_ensure_session must not run for direct --heroes")

    monkeypatch.setattr(main_mod, "_ensure_session", _no_session)
    monkeypatch.setattr(
        arena_mod,
        "run_arena_team_set",
        lambda account, name, **kwargs: captured.update(kwargs),
    )
    main_mod.cmd_arena_team_set(_team_args())
    assert captured["client"] is None
    assert captured["from_saved"] is False


def test_cmd_arena_team_list_unregistered_account_exits_1(monkeypatch, capsys):
    import hw_genie.main as main_mod
    from hw_genie.core.session_manager import SessionManager

    monkeypatch.setattr(main_mod, "resolve_account", lambda account: "Ghost")
    monkeypatch.setattr(SessionManager, "load", lambda account: None)
    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_arena_team_list(_team_args(account="Ghost"))
    assert exc.value.code == 1
    assert "not registered" in capsys.readouterr().err
