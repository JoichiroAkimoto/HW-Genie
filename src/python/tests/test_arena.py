"""Pure-function tests for Arena v1 (parsers, validation, team/favor merge).

All API access is mocked; no live calls and no real account names are used.
"""

import pytest

from hw_genie.commands.arena import (
    ARENA_MODE,
    ArenaError,
    ArenaTeam,
    attackable_window,
    build_attack_args,
    deserialize_team,
    load_arena_presets,
    parse_attack_result,
    parse_candidates_response,
    parse_favor_args,
    parse_range_response,
    parse_top_response,
    remove_arena_preset,
    resolve_team,
    save_arena_preset,
    serialize_team,
    validate_preset_name,
    validate_team,
)
from hw_genie.core.client import ApiAction
from hw_genie.core.session_manager import SessionManager


# --- ApiAction ---


def test_api_action_has_arena_methods():
    assert ApiAction.ARENA_FIND_ENEMIES == "arenaFindEnemies"
    assert ApiAction.ARENA_CHECK_TARGET_RANGE == "arenaCheckTargetRange"
    assert ApiAction.ARENA_ATTACK == "arenaAttack"
    assert ApiAction.GRAND_FIND_ENEMIES == "grandFindEnemies"
    assert ApiAction.GRAND_CHECK_TARGET_RANGE == "grandCheckTargetRange"
    assert ApiAction.GRAND_ATTACK == "grandAttack"
    assert ApiAction.TOP_GET == "topGet"
    assert ApiAction.TEAM_GET_ALL == "teamGetAll"
    assert ApiAction.TEAM_GET_FAVOR == "teamGetFavor"
    assert ApiAction.TEAM_GET_BANNERS == "team_getBanners"


# --- topGet parsing ---


def test_parse_top_response_joins_users_and_splits_pet():
    top = parse_top_response(
        {
            "top": [
                {
                    "place": "1",
                    "userId": "12345678",
                    "power": "1176007",
                    "heroes": [{"id": 46}, {"id": 62}, {"id": 6008, "type": "pet"}],
                    "banners": [{"id": 4}],
                }
            ],
            "place": 22,
            "users": {
                "12345678": {"id": "12345678", "name": "Bob", "clanId": "9", "leagueId": 2}
            },
        }
    )
    assert top.own_place == 22
    assert len(top.entries) == 1
    entry = top.entries[0]
    assert entry.place == 1
    assert entry.user_id == 12345678
    assert entry.name == "Bob"
    assert entry.power == 1176007
    assert entry.heroes == [46, 62]
    assert entry.pet == 6008


def test_parse_top_response_tolerates_missing_users_and_bad_values():
    top = parse_top_response(
        {
            "top": [
                {"place": "not-a-number", "userId": "1"},
                {"place": "2", "userId": "2", "power": "bad", "heroes": []},
            ],
            "place": 22,
        }
    )
    assert [e.place for e in top.entries] == [2]
    assert top.entries[0].name is None
    assert top.entries[0].power is None
    assert top.entries[0].heroes == []


def test_parse_top_response_ignores_non_dict_entries():
    top = parse_top_response({"top": [None, 1, "x"], "place": 3})
    assert top.entries == []
    assert top.own_place == 3


# --- findEnemies parsing ---


def test_parse_candidates_response_accepts_enemies_key():
    entries = parse_candidates_response(
        {
            "enemies": [
                {
                    "userId": "111",
                    "place": "20",
                    "power": "1000",
                    "heroes": [{"id": 10}, {"id": 6008, "type": "pet"}],
                    "user": {"name": "Alice", "clanId": "1"},
                }
            ]
        }
    )
    assert len(entries) == 1
    assert entries[0].user_id == 111
    assert entries[0].place == 20
    assert entries[0].name == "Alice"
    assert entries[0].heroes == [10]
    assert entries[0].pet == 6008


def test_parse_candidates_response_accepts_plain_list_and_skips_invalid():
    entries = parse_candidates_response(
        [{"userId": "5", "place": "4", "user": {"name": "Carol"}}, {"place": "3"}, None]
    )
    assert [(e.user_id, e.place, e.name) for e in entries] == [(5, 4, "Carol")]


# --- checkTargetRange parsing ---


def test_parse_range_response_normalizes_keys():
    parsed = parse_range_response(
        {"response": {"111": True, "222": False, "bad": True, "333": 1, "444": 0}}
    )
    assert parsed == {111: True, 222: False, 333: True, 444: False}


def test_parse_range_response_handles_missing_and_non_dict():
    assert parse_range_response(None) == {}
    assert parse_range_response({"response": None}) == {}
    assert parse_range_response({"response": {"1": True}}) == {1: True}


# --- attack window (display only) ---


def test_attackable_window_above_only():
    assert attackable_window(22) == (1, 21)
    assert attackable_window(34) == (13, 33)
    # No upper target when already rank 1.
    assert attackable_window(1) == (1, 0)


# --- team validation ---


def test_validate_team_accepts_valid_team():
    validate_team(
        ArenaTeam(
            heroes=[29, 13, 73, 71, 35],
            pet=6008,
            favor={13: 6002, 29: 6011},
            banners=[2],
        )
    )


def test_validate_team_treats_banner_zero_as_absent():
    validate_team(ArenaTeam(heroes=[1, 2, 3, 4, 5], banners=[0]))


@pytest.mark.parametrize(
    "team",
    [
        ArenaTeam(heroes=[1, 2, 3, 4]),
        ArenaTeam(heroes=[1, 2, 3, 4, 5, 6]),
        ArenaTeam(heroes=[1, 2, 3, 4, 4]),
        ArenaTeam(heroes=[1, 2, 3, 4, 6008]),
        ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=1234),
        ArenaTeam(heroes=[1, 2, 3, 4, 5], favor={99: 6002}),
        ArenaTeam(heroes=[1, 2, 3, 4, 5], banners=["x"]),
        ArenaTeam(heroes=[1, 2, 3, 4, 5], banners=[-1]),
    ],
)
def test_validate_team_rejects_invalid(team):
    with pytest.raises(ArenaError):
        validate_team(team)


def test_validate_team_rejects_favor_key_not_selected():
    with pytest.raises(ArenaError, match="[Ff]avor"):
        validate_team(ArenaTeam(heroes=[1, 2, 3, 4, 5], favor={6: 6002}))


# --- favor args ---


def test_parse_favor_args_returns_none_when_omitted():
    assert parse_favor_args(None) is None


def test_parse_favor_args_parses_hero_patron_pairs():
    assert parse_favor_args(["13=6003", "29=6011"]) == {13: 6003, 29: 6011}


@pytest.mark.parametrize("raw", [["13"], ["13="], ["=6003"], ["a=6003"], ["13=6003=1"]])
def test_parse_favor_args_rejects_malformed(raw):
    with pytest.raises(ArenaError):
        parse_favor_args(raw)


def test_parse_favor_args_rejects_duplicate_hero():
    with pytest.raises(ArenaError, match="[Dd]uplicate"):
        parse_favor_args(["13=6003", "13=6004"])


# --- team resolution priority ---


def test_resolve_team_direct_overrides_preset_and_saved():
    preset = ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=6001, favor={1: 7001}, banners=[1])
    saved = ArenaTeam(heroes=[6, 7, 8, 9, 10], pet=6002, favor={6: 7002}, banners=[2])
    team, missing = resolve_team(
        heroes=[11, 12, 13, 14, 15],
        pet=6003,
        banner=3,
        favor={11: 7003},
        preset=preset,
        saved=saved,
    )
    assert team.heroes == [11, 12, 13, 14, 15]
    assert team.pet == 6003
    assert team.banners == [3]
    assert team.favor == {11: 7003}
    assert missing == [12, 13, 14, 15]


def test_resolve_team_preset_overrides_saved_and_fills_missing_favor_from_saved():
    preset = ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=6001, favor={1: 7001}, banners=[1])
    saved = ArenaTeam(
        heroes=[6, 7, 8, 9, 10],
        pet=6002,
        favor={1: 9999, 2: 7002, 6: 7006},
        banners=[2],
    )
    team, missing = resolve_team(preset=preset, saved=saved)
    assert team.heroes == [1, 2, 3, 4, 5]
    assert team.pet == 6001
    assert team.banners == [1]
    # Preset favor wins for hero 1; saved favor only fills matching selected heroes.
    assert team.favor == {1: 7001, 2: 7002}
    assert missing == [3, 4, 5]


def test_resolve_team_saved_default_keeps_saved_fields():
    saved = ArenaTeam(
        heroes=[29, 13, 73, 71, 35],
        pet=6008,
        favor={13: 6002},
        banners=[2],
    )
    team, missing = resolve_team(saved=saved)
    assert team == saved
    assert missing == [29, 73, 71, 35]


def test_resolve_team_direct_heroes_can_reuse_saved_pet_and_banner():
    saved = ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=6008, favor={}, banners=[2])
    team, _ = resolve_team(heroes=[6, 7, 8, 9, 10], saved=saved)
    assert team.heroes == [6, 7, 8, 9, 10]
    assert team.pet == 6008
    assert team.banners == [2]


def test_resolve_team_preset_heroes_inherit_saved_pet_and_banner():
    # A preset with heroes only must not suppress the saved pet/banner.
    preset = ArenaTeam(heroes=[1, 2, 3, 4, 5])
    saved = ArenaTeam(
        heroes=[6, 7, 8, 9, 10],
        pet=6002,
        favor={1: 7002, 6: 7006},
        banners=[2],
    )
    team, missing = resolve_team(preset=preset, saved=saved)
    assert team.heroes == [1, 2, 3, 4, 5]
    assert team.pet == 6002
    assert team.banners == [2]
    assert team.favor == {1: 7002}
    assert missing == [2, 3, 4, 5]


def test_resolve_team_preset_fields_win_over_saved_individually():
    preset = ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=6001, banners=[1])
    saved = ArenaTeam(heroes=[6, 7, 8, 9, 10], pet=6002, banners=[2])
    team, _ = resolve_team(preset=preset, saved=saved)
    assert team.pet == 6001
    assert team.banners == [1]


def test_resolve_team_explicit_banner_zero_suppresses_banner():
    saved = ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=6008, banners=[2])
    team, _ = resolve_team(banner=0, saved=saved)
    assert team.banners == []
    assert "banners" not in build_attack_args(1, team)


def test_resolve_team_saved_banner_zero_is_treated_as_absent():
    saved = ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=6008, banners=[0])
    team, _ = resolve_team(saved=saved)
    assert team.banners == []
    assert "banners" not in build_attack_args(1, team)


def test_resolve_team_without_any_team_raises():
    with pytest.raises(ArenaError, match="team"):
        resolve_team()


def test_resolve_team_rejects_invalid_direct_team_before_return():
    with pytest.raises(ArenaError):
        resolve_team(heroes=[1, 2, 3, 4, 4])


# --- preset storage ---


def _register_alice():
    SessionManager.save(
        "Alice",
        {"headers": {"x-auth-token": "test"}, "player": {"id": "a1", "name": "Alice"}},
    )


def test_preset_save_load_and_remove_roundtrip():
    _register_alice()
    team = ArenaTeam(
        heroes=[29, 13, 73, 71, 35],
        pet=6008,
        favor={13: 6002, 29: 6011},
        banners=[2],
    )
    save_arena_preset("Alice", "atk1", team)

    presets = load_arena_presets("Alice")
    assert set(presets) == {"atk1"}
    assert presets["atk1"] == team

    raw = SessionManager.load("Alice")["arena_team_presets"]
    assert raw["arena"]["atk1"]["heroes"] == [29, 13, 73, 71, 35]
    assert raw["arena"]["atk1"]["favor"] == {"13": 6002, "29": 6011}

    remove_arena_preset("Alice", "atk1")
    assert load_arena_presets("Alice") == {}


def test_remove_unknown_preset_raises():
    _register_alice()
    with pytest.raises(ArenaError, match="not found"):
        remove_arena_preset("Alice", "missing")


def test_load_presets_skips_invalid_entries():
    _register_alice()

    def _merge(_existing):
        return {
            "arena": {
                "good": {"heroes": [1, 2, 3, 4, 5], "favor": {}},
                "bad": {"heroes": [1, 2]},
            }
        }

    SessionManager.repo.update_config_merged("Alice", "arena_team_presets", _merge)
    assert set(load_arena_presets("Alice")) == {"good"}


def test_serialize_deserialize_team_roundtrip_with_int_banner():
    team = ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=6008, favor={1: 7001}, banners=[])
    data = serialize_team(team)
    assert "pet" in data and "banners" not in data
    assert deserialize_team(data) == team

    data["banners"] = 2  # legacy/single-int tolerance
    assert deserialize_team(data).banners == [2]


def test_deserialize_team_drops_banner_zero():
    base = {"heroes": [1, 2, 3, 4, 5]}
    assert deserialize_team({**base, "banners": [0]}).banners == []
    assert deserialize_team({**base, "banners": 0}).banners == []
    assert "banners" not in serialize_team(deserialize_team({**base, "banners": [0]}))


@pytest.mark.parametrize("name", ["", "   ", "atk 1", "atk/1", "../x", "a" * 41])
def test_validate_preset_name_rejects_unsafe(name):
    with pytest.raises(ArenaError):
        validate_preset_name(name)


def test_validate_preset_name_accepts_safe():
    assert validate_preset_name("atk1") == "atk1"
    assert validate_preset_name("ga_team-1") == "ga_team-1"


# --- attack args / result ---


def test_build_attack_args_includes_optional_fields():
    team = ArenaTeam(
        heroes=[29, 13, 73, 71, 35],
        pet=6008,
        favor={13: 6002, 29: 6011},
        banners=[2],
    )
    args = build_attack_args(12345678, team)
    assert args == {
        "userId": 12345678,
        "heroes": [29, 13, 73, 71, 35],
        "pet": 6008,
        "favor": {"13": 6002, "29": 6011},
        "banners": [2],
    }


def test_build_attack_args_omits_pet_and_banners_when_absent():
    args = build_attack_args(1, ArenaTeam(heroes=[1, 2, 3, 4, 5]))
    assert "pet" not in args
    assert "banners" not in args
    assert "favor" not in args


def test_build_attack_args_omits_banner_zero():
    args = build_attack_args(1, ArenaTeam(heroes=[1, 2, 3, 4, 5], banners=[0]))
    assert "banners" not in args


def test_parse_attack_result_reads_battles_result():
    parsed = parse_attack_result(
        {"response": {"battles": [{"result": {"win": True, "oldPlace": 22, "newPlace": 10}}]}}
    )
    assert parsed is not None
    assert parsed.win is True
    assert parsed.old_place == 22
    assert parsed.new_place == 10


def test_parse_attack_result_returns_none_for_unknown_shape():
    assert parse_attack_result(None) is None
    assert parse_attack_result({}) is None
    assert parse_attack_result({"response": {"battles": []}}) is None
    assert parse_attack_result({"response": {"battles": [{"result": {}}]}}) is None


def test_arena_mode_uses_arena_methods():
    assert ARENA_MODE.get_all == ApiAction.ARENA_GET_ALL
    assert ARENA_MODE.find_enemies == ApiAction.ARENA_FIND_ENEMIES
    assert ARENA_MODE.check_range == ApiAction.ARENA_CHECK_TARGET_RANGE
    assert ARENA_MODE.attack == ApiAction.ARENA_ATTACK
    assert ARENA_MODE.top_type == "arena"


def test_grand_mode_is_private_and_rejected():
    import hw_genie.commands.arena as arena_mod

    assert not hasattr(arena_mod, "GRAND_MODE")
    assert arena_mod._GRAND_MODE.key == "grand"
    with pytest.raises(ArenaError, match="not implemented"):
        arena_mod.fetch_arena_status(object(), arena_mod._GRAND_MODE)
    with pytest.raises(ArenaError, match="not implemented"):
        arena_mod.fetch_saved_team(object(), arena_mod._GRAND_MODE)
