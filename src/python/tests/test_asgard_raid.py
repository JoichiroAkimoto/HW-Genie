import logging
import sys
from unittest.mock import MagicMock

import pytest

from hw_genie.commands.asgard_raid import (
    AsgardRaidError,
    AsgardRaidRunResult,
    AsgardRaidTeamResult,
    AsgardTeam,
    detect_boss,
    list_asgard_raid_teams,
    parse_end_detail,
    parse_favor_args,
    parse_heroes,
    remove_asgard_raid_team,
    run_asgard_raid,
    set_asgard_raid_team,
    validate_boss,
    validate_pet,
    validate_slot,
    validate_team,
)
from hw_genie.core.client import ResponseStatus
from hw_genie.core.session_manager import SessionManager
from hw_genie.runner import summarize_asgard_raid


def _register_alice() -> None:
    SessionManager.save(
        "Alice",
        {"headers": {"x-auth-token": "test"}, "player": {"id": "a1", "name": "Alice"}},
    )


def _team(
    slot: int = 1,
    *,
    boss: str = "maestro",
    heroes: tuple[int, ...] = (50, 60, 58, 51, 72),
    pet: int | None = 6005,
    favor: dict[int, int] | None = None,
    enabled: bool = True,
    memo: str | None = None,
) -> AsgardTeam:
    return AsgardTeam(
        boss=boss,
        slot=slot,
        heroes=heroes,
        pet=pet,
        favor={heroes[0]: 6005} if favor is None else favor,
        enabled=enabled,
        memo=memo,
    )


def _info(
    *,
    maestro: bool = True,
    attempts: int = 5,
    damage: int = 0,
    points: int = 920,
) -> dict:
    buff_ids = [113, 114, 115] if maestro else [63, 64]
    shop = {str(index + 6): {"buffId": buff_id} for index, buff_id in enumerate(buff_ids)}
    return {
        "response": {
            "shop": shop,
            "bossAttempts": attempts,
            "userStats": {"damage": str(damage), "points": str(points)},
        }
    }


def _ok(detail: dict) -> MagicMock:
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.status = ResponseStatus.SUCCESS
    res.detail = detail
    return res


def _err(name: str = "NotAvailable") -> MagicMock:
    res = MagicMock()
    res.is_success = False
    res.error_name = name
    res.status = ResponseStatus.ERROR
    res.detail = {"name": name}
    return res


def _start_detail() -> dict:
    return {"response": {"battle": {"type": "clan_raid", "seed": 878872284}}}


def _end_detail(damage: int = 35_790_884, replay: str = "r1") -> dict:
    return {
        "response": {
            "result": {
                "win": False,
                "stars": 0,
                "afterInvalid": True,
                "damage": {"1": damage, "2": 0},
            },
            "replay": replay,
        }
    }


# --- validation -----------------------------------------------------------


@pytest.mark.parametrize("slot", [1, 2, 3, 4, 5])
def test_validate_slot_accepts_1_to_5(slot):
    assert validate_slot(slot) == slot


@pytest.mark.parametrize("slot", [0, 6, -1, "x"])
def test_validate_slot_rejects_out_of_range(slot):
    with pytest.raises(AsgardRaidError):
        validate_slot(slot)


@pytest.mark.parametrize("boss", ["osh", "maestro", " Maestro "])
def test_validate_boss_accepts_known_keys(boss):
    assert validate_boss(boss) == boss.strip().lower()


def test_validate_boss_rejects_unknown():
    with pytest.raises(AsgardRaidError):
        validate_boss("orchestra")


def test_parse_heroes_requires_five_unique_positive_ids():
    assert parse_heroes([1, 2, 3, 4, 5]) == (1, 2, 3, 4, 5)
    with pytest.raises(AsgardRaidError):
        parse_heroes(None)
    with pytest.raises(AsgardRaidError):
        parse_heroes([1, 2, 3, 4])
    with pytest.raises(AsgardRaidError):
        parse_heroes([1, 2, 3, 4, 4])
    with pytest.raises(AsgardRaidError):
        parse_heroes([1, 2, 3, 4, 0])
    with pytest.raises(AsgardRaidError):
        parse_heroes([1, 2, 3, 4, "x"])


def test_parse_favor_args():
    assert parse_favor_args(["1=6001", "2=6002"]) == {1: 6001, 2: 6002}
    assert parse_favor_args(None) == {}
    with pytest.raises(AsgardRaidError):
        parse_favor_args(["1"])
    with pytest.raises(AsgardRaidError):
        parse_favor_args(["1=6001", "1=6002"])


def test_validate_pet():
    assert validate_pet(None) is None
    assert validate_pet(6005) == 6005
    with pytest.raises(AsgardRaidError):
        validate_pet(0)
    with pytest.raises(AsgardRaidError):
        validate_pet("x")


def test_validate_team_rejects_favor_outside_team():
    team = _team(favor={99: 6001})
    with pytest.raises(AsgardRaidError):
        validate_team(team)


# --- storage --------------------------------------------------------------


def test_team_storage_roundtrip_and_upsert():
    _register_alice()
    team = _team(slot=1, memo="template")
    set_asgard_raid_team("Alice", team)
    assert list_asgard_raid_teams("Alice", "maestro") == [team]

    updated = _team(
        slot=1,
        heroes=(13, 46, 40, 64, 29),
        pet=6002,
        favor={13: 6003},
        enabled=False,
        memo="updated",
    )
    set_asgard_raid_team("Alice", updated)
    teams = list_asgard_raid_teams("Alice", "maestro")
    assert teams == [updated]

    set_asgard_raid_team("Alice", _team(slot=2, heroes=(1, 2, 3, 4, 5)))
    assert [t.slot for t in list_asgard_raid_teams("Alice", "maestro")] == [1, 2]


def test_team_storage_is_per_boss():
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1, boss="osh"))
    set_asgard_raid_team("Alice", _team(slot=1, boss="maestro"))
    assert len(list_asgard_raid_teams("Alice")) == 2
    assert len(list_asgard_raid_teams("Alice", "osh")) == 1
    assert len(list_asgard_raid_teams("Alice", "maestro")) == 1


def test_remove_team_and_unknown_raises():
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1))
    remove_asgard_raid_team("Alice", "maestro", 1)
    assert list_asgard_raid_teams("Alice", "maestro") == []
    with pytest.raises(AsgardRaidError, match="No Maestro team"):
        remove_asgard_raid_team("Alice", "maestro", 1)


# --- detection / parsing --------------------------------------------------


def test_detect_boss_from_shop_signature():
    assert detect_boss(_info(maestro=True)["response"]) == "maestro"
    assert detect_boss(_info(maestro=False)["response"]) == "osh"
    assert detect_boss({"shop": {}}) is None
    assert detect_boss({"shop": {"6": {"buffId": 999}}}) is None


def test_detect_boss_falls_back_to_active_buffs():
    assert detect_boss({"shop": {}, "buffs": {"113": {"id": 113}, "114": {"id": 114}}}) == "maestro"
    assert detect_boss({"buffs": {"63": {"id": 63}, "64": {"id": 64}}}) == "osh"
    assert detect_boss({"buffs": {"30": {"id": 30}}}) is None


def test_parse_end_detail_sums_damage_and_reads_replay():
    damage, win, stars, replay = parse_end_detail(_end_detail(12345, "r9"))
    assert (damage, win, stars, replay) == (12345, False, 0, "r9")


def test_parse_end_detail_tolerates_string_damage_and_int_replay():
    detail = {
        "response": {
            "result": {"win": True, "stars": 3, "damage": {"1": "123", "2": 0}},
            "replay": 456,
        }
    }
    assert parse_end_detail(detail) == (123, True, 3, "456")


def test_parse_end_detail_tolerates_empty_detail():
    assert parse_end_detail(None) == (0, False, 0, None)


# --- run flow -------------------------------------------------------------


def test_run_starts_and_auto_ends_each_enabled_team(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1))
    set_asgard_raid_team("Alice", _team(slot=2, heroes=(13, 46, 40, 64, 29)))
    mock_call.side_effect = [
        _ok(_info(attempts=5, damage=0)),
        _ok(_start_detail()),
        _ok(_end_detail(10, "r1")),
        _ok(_start_detail()),
        _ok(_end_detail(20, "r2")),
        _ok(_info(attempts=3, damage=30)),
    ]

    result = run_asgard_raid(client, account_alias="Alice")

    assert result.failed_count == 0
    assert [t.status for t in result.teams] == [ResponseStatus.SUCCESS] * 2
    assert result.total_damage == 30
    assert result.attempts_before == 5
    assert result.attempts_after == 3
    assert result.damage_after == 30

    calls = [call.args[0]["calls"][0] for call in mock_call.call_args_list]
    assert [c["name"] for c in calls] == [
        "clanRaid_getInfo",
        "clanRaid_startBossBattle",
        "clanRaid_endBossBattle",
        "clanRaid_startBossBattle",
        "clanRaid_endBossBattle",
        "clanRaid_getInfo",
    ]
    assert calls[2]["args"] == {"result": {"win": False, "stars": 0}, "progress": []}
    # start/end are non-idempotent: never retried.
    assert mock_call.call_args_list[1].kwargs == {"max_retries": 0}
    assert mock_call.call_args_list[2].kwargs == {"max_retries": 0}


def test_run_skips_disabled_team(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1, enabled=False))
    set_asgard_raid_team("Alice", _team(slot=2))
    mock_call.side_effect = [
        _ok(_info(attempts=5)),
        _ok(_start_detail()),
        _ok(_end_detail(10)),
        _ok(_info(attempts=4, damage=10)),
    ]

    result = run_asgard_raid(client, account_alias="Alice")

    assert result.teams[0].status == ResponseStatus.SKIPPED
    assert result.teams[0].enabled is False
    assert result.teams[1].status == ResponseStatus.SUCCESS
    starts = [
        call
        for call in mock_call.call_args_list
        if call.args[0]["calls"][0]["name"] == "clanRaid_startBossBattle"
    ]
    assert len(starts) == 1


def test_run_stops_without_attempts(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1))
    mock_call.side_effect = [_ok(_info(attempts=0))]

    result = run_asgard_raid(client, account_alias="Alice")

    assert result.failed_count == 0
    assert result.teams[0].status == ResponseStatus.SKIPPED
    assert mock_call.call_count == 1


def test_run_skips_remaining_when_attempts_run_out_mid_list(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1))
    set_asgard_raid_team("Alice", _team(slot=2))
    mock_call.side_effect = [
        _ok(_info(attempts=1)),
        _ok(_start_detail()),
        _ok(_end_detail(10)),
        _ok(_info(attempts=0, damage=10)),
    ]

    result = run_asgard_raid(client, account_alias="Alice")

    assert [t.status for t in result.teams] == [
        ResponseStatus.SUCCESS,
        ResponseStatus.SKIPPED,
    ]
    assert result.total_damage == 10
    starts = [
        call
        for call in mock_call.call_args_list
        if call.args[0]["calls"][0]["name"] == "clanRaid_startBossBattle"
    ]
    assert len(starts) == 1


def test_run_stops_on_start_failure(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1))
    set_asgard_raid_team("Alice", _team(slot=2))
    mock_call.side_effect = [
        _ok(_info(attempts=5)),
        _err("NotAvailable"),
        _ok(_info(attempts=5)),
    ]

    result = run_asgard_raid(client, account_alias="Alice")

    assert result.failed_count == 1
    assert "start failed" in (result.error or "")
    assert len(result.teams) == 1
    assert result.teams[0].status == ResponseStatus.ERROR


def test_run_stops_on_end_failure(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1))
    set_asgard_raid_team("Alice", _team(slot=2))
    mock_call.side_effect = [
        _ok(_info(attempts=5)),
        _ok(_start_detail()),
        _err("Invalid battle"),
        _ok(_info(attempts=4)),
    ]

    result = run_asgard_raid(client, account_alias="Alice")

    assert result.failed_count == 1
    assert "end failed" in (result.error or "")
    assert len(result.teams) == 1
    assert result.teams[0].status == ResponseStatus.ERROR


def test_run_dry_run_does_not_start(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1))
    mock_call.side_effect = [_ok(_info(attempts=5))]

    result = run_asgard_raid(client, account_alias="Alice", dry_run=True)

    assert result.dry_run is True
    assert result.teams[0].status == ResponseStatus.SKIPPED
    assert mock_call.call_count == 1


def test_run_unknown_week_skips_without_teams(mock_client):
    client, mock_call = mock_client
    _register_alice()
    mock_call.side_effect = [_ok({"response": {"shop": {}, "userStats": {}}})]

    result = run_asgard_raid(client, account_alias="Alice")

    assert result.boss is None
    assert result.teams == []
    assert mock_call.call_count == 1


def test_run_no_registered_teams(mock_client):
    client, mock_call = mock_client
    _register_alice()
    mock_call.side_effect = [_ok(_info(attempts=5))]

    result = run_asgard_raid(client, account_alias="Alice")

    assert result.boss == "maestro"
    assert result.teams == []
    assert mock_call.call_count == 1


def test_run_explicit_boss_overrides_detected_week(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1, boss="osh"))
    mock_call.side_effect = [
        _ok(_info(maestro=True, attempts=5)),
        _ok(_start_detail()),
        _ok(_end_detail(10)),
        _ok(_info(maestro=True, attempts=4, damage=10)),
    ]

    result = run_asgard_raid(client, account_alias="Alice", boss="osh")

    assert result.boss == "osh"
    assert result.failed_count == 0
    start = mock_call.call_args_list[1].args[0]["calls"][0]
    assert start["args"]["heroes"] == [50, 60, 58, 51, 72]


def test_run_explicit_boss_when_week_is_unknown(mock_client):
    client, mock_call = mock_client
    _register_alice()
    set_asgard_raid_team("Alice", _team(slot=1, boss="osh"))
    unknown = {"response": {"shop": {}, "buffs": {}, "bossAttempts": 5, "userStats": {}}}
    mock_call.side_effect = [
        _ok(unknown),
        _ok(_start_detail()),
        _ok(_end_detail(10)),
        _ok(unknown),
    ]

    result = run_asgard_raid(client, account_alias="Alice", boss="osh")

    assert result.boss == "osh"
    assert result.teams[0].status == ResponseStatus.SUCCESS


# --- multi summary --------------------------------------------------------


def test_summarize_asgard_raid_counts_errors(capsys):
    result = AsgardRaidRunResult(
        boss="maestro",
        dry_run=False,
        teams=[
            AsgardRaidTeamResult(
                slot=1,
                memo=None,
                enabled=True,
                status=ResponseStatus.ERROR,
                error="start failed",
            )
        ],
        error="start failed for slot 1",
    )
    failed = summarize_asgard_raid([("Alice", (result, None))])
    assert failed == 1
    assert "asgard-raid" in capsys.readouterr().out


def test_summarize_asgard_raid_dry_run_is_ok(capsys):
    result = AsgardRaidRunResult(boss="maestro", dry_run=True, teams=[])
    assert summarize_asgard_raid([("Alice", (result, None))], dry_run=True) == 0
    assert "no teams registered" in capsys.readouterr().out


# --- CLI wiring -----------------------------------------------------------


def _invoke_main(argv: list[str], monkeypatch, *, expect_exit: bool = False):
    from hw_genie.main import main

    previous = logging.getLogger().level
    monkeypatch.setattr(sys, "argv", argv)
    try:
        if not expect_exit:
            main()
            return None
        with pytest.raises(SystemExit) as exc:
            main()
        return exc
    finally:
        logging.getLogger().setLevel(previous)


def test_cli_team_set_list_and_remove(monkeypatch, capsys):
    _register_alice()
    _invoke_main(
        [
            "hw-genie",
            "asgard-raid",
            "team",
            "set",
            "1",
            "--boss",
            "maestro",
            "-a",
            "Alice",
            "--heroes",
            "50",
            "60",
            "58",
            "51",
            "72",
            "--pet",
            "6005",
            "--favor",
            "50=6005",
            "--memo",
            "template",
        ],
        monkeypatch,
    )
    teams = list_asgard_raid_teams("Alice", "maestro")
    assert teams == [_team(slot=1, memo="template")]

    _invoke_main(
        ["hw-genie", "asgard-raid", "team", "list", "-a", "Alice"],
        monkeypatch,
    )
    out = capsys.readouterr().out
    assert "maestro slot 1 [enabled]" in out
    assert "memo=template" in out

    _invoke_main(
        [
            "hw-genie",
            "asgard-raid",
            "team",
            "remove",
            "1",
            "--boss",
            "maestro",
            "-a",
            "Alice",
        ],
        monkeypatch,
    )
    assert list_asgard_raid_teams("Alice", "maestro") == []


def test_cli_team_set_rejects_wrong_hero_count(monkeypatch, capsys):
    _register_alice()
    result = _invoke_main(
        [
            "hw-genie",
            "asgard-raid",
            "team",
            "set",
            "1",
            "--boss",
            "maestro",
            "-a",
            "Alice",
            "--heroes",
            "1",
            "2",
            "3",
            "4",
        ],
        monkeypatch,
        expect_exit=True,
    )
    assert result.value.code == 2
    assert "expected 5 arguments" in capsys.readouterr().err


def test_cli_multi_asgard_raid_requires_yes(monkeypatch, capsys):
    result = _invoke_main(
        ["hw-genie", "multi", "asgard-raid", "Alice"],
        monkeypatch,
        expect_exit=True,
    )
    assert result.value.code == 2
    assert "Add --yes" in capsys.readouterr().err
