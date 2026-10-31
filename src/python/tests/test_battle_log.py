"""Tests for the battle log corpus (hashing, parsing, import, stats)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from hw_genie.core import battle_log
from hw_genie.core.battle_log import (
    BattleLogError,
    ImportStats,
    build_team,
    import_battles,
    lineup_key_from_ids,
    parse_battle_get_response,
    prune_battles,
    query_matchups,
    query_plans,
    wilson_lower_bound,
)
from hw_genie.core.client import ApiAction, HWClient
from hw_genie.core.database import (
    Account,
    BattleComparison,
    BattleRecord,
    TeamConfig,
    get_session_local,
)


def _hero(hero_id: int, *, hp: int = 1000, power: int = 100, level: int = 130) -> dict:
    return {
        "id": hero_id,
        "type": "hero",
        "level": level,
        "star": 6,
        "power": power,
        "hp": hp,
        "slots": [0, 0, 0, 0, 0, 0],
        "artifacts": [{"level": 130, "star": 6}],
        "petId": 6001,
    }


def _pet(pet_id: int = 6001) -> dict:
    return {"id": pet_id, "type": "pet", "level": 130, "star": 6, "power": 500}


def _team(hero_ids: list[int], pet_id: int = 6001) -> dict:
    team = {str(hero_id): _hero(hero_id) for hero_id in hero_ids}
    team[str(pet_id)] = _pet(pet_id)
    return team


def _replay(
    *,
    battle_id: str = "1001",
    seed: int = 1234567890,
    win: bool = True,
    heroes: dict | None = None,
    defender: dict | None = None,
    banner: int = 2,
    defender_id: str = "222",
    game_version: int = 298,
) -> dict:
    heroes = heroes or {
        "1": _hero(1),
        "2": _hero(2),
        "3": _hero(3),
        "4": _hero(4),
        "5": _hero(5),
        "6001": _pet(),
    }
    defender = defender or {
        "11": _hero(11),
        "12": _hero(12),
        "13": _hero(13),
        "14": _hero(14),
        "15": _hero(15),
        "6002": _pet(6002),
    }
    return {
        "id": battle_id,
        "type": "arena",
        "userId": "111",
        "typeId": defender_id,
        "seed": str(seed),
        "startTime": "1793075321",
        "endTime": "1793075322",
        "attackers": heroes,
        "defenders": [defender],
        "effects": {
            "attackersBanner": {"id": banner, "slots": [1, 2, 3]},
            "defendersBanner": {"id": 1, "slots": [4, 5, 6]},
        },
        "result": {"win": win, "stars": 3 if win else 0, "serverVersion": game_version},
    }


def _create_account(alias: str = "Alice", player_id: str = "111") -> None:
    with get_session_local()() as db:
        db.add(Account(player_id=player_id, alias=alias, player_name=alias))
        db.commit()


def test_lineup_key_ignores_stats_but_config_hash_does_not():
    base = {"1": _hero(1), "2": _hero(2), "3": _hero(3), "4": _hero(4), "5": _hero(5), "6001": _pet()}
    stronger = {k: dict(v) for k, v in base.items()}
    stronger["1"]["hp"] = 99999
    stronger["1"]["power"] = 99999

    a = build_team(base, banner_id=2)
    b = build_team(stronger, banner_id=2)

    assert a.lineup_key == b.lineup_key
    assert a.config_hash != b.config_hash


def test_lineup_key_includes_order_pet_and_banner():
    h1, h2, h3, h4, h5 = (_hero(i) for i in range(1, 6))
    in_order = {"1": h1, "2": h2, "3": h3, "4": h4, "5": h5, "6001": _pet()}
    swapped = {"2": h2, "1": h1, "3": h3, "4": h4, "5": h5, "6001": _pet()}

    assert build_team(in_order, 2).lineup_key != build_team(swapped, 2).lineup_key
    assert build_team(in_order, 2).lineup_key != build_team(in_order, 3).lineup_key

    other_pet = dict(in_order)
    other_pet["6001"] = _pet(6002)
    assert build_team(in_order, 2).lineup_key != build_team(other_pet, 2).lineup_key


def test_parse_battle_get_response():
    detail = {"response": {"replays": [_replay()], "users": {}}}
    parsed = parse_battle_get_response(detail)

    assert len(parsed) == 1
    battle = parsed[0]
    assert battle.battle_type == "arena"
    assert battle.win is True
    assert battle.stars == 3
    assert battle.game_version == "298"
    assert battle.seed == 1234567890
    assert battle.attacker_player_id == "111"
    assert battle.defender_player_id == "222"
    assert battle.attacker.heroes_summary == "1,2,3,4,5"
    assert battle.defender.heroes_summary == "11,12,13,14,15"
    assert len(battle.matchup_key) == 64


def test_import_is_idempotent_and_builds_stats():
    _create_account()
    parsed = parse_battle_get_response({"response": {"replays": [_replay()], "users": {}}})

    first = import_battles("Alice", parsed)
    assert first.inserted == 1
    assert first.duplicates == 0
    assert first.team_configs_inserted == 2

    second = import_battles("Alice", parsed)
    assert second.inserted == 0
    assert second.duplicates == 1

    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 1
        assert db.query(TeamConfig).count() == 2

    rows = query_matchups(account_alias="Alice")
    assert len(rows) == 1
    assert rows[0].battles == 1
    assert rows[0].wins == 1
    assert rows[0].win_rate == 1.0
    assert rows[0].defender_player_id == "222"


def test_dry_run_does_not_write():
    _create_account()
    parsed = parse_battle_get_response({"response": {"replays": [_replay()], "users": {}}})

    stats = import_battles("Alice", parsed, dry_run=True)
    assert stats.inserted == 1

    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 0
        assert db.query(TeamConfig).count() == 0


def test_stats_aggregate_win_rate():
    _create_account()
    replays = [
        _replay(battle_id="2001", win=True),
        _replay(battle_id="2002", win=False),
    ]
    parsed = parse_battle_get_response({"response": {"replays": replays, "users": {}}})
    import_battles("Alice", parsed)

    rows = query_matchups(account_alias="Alice", by="matchup")
    assert len(rows) == 1
    assert rows[0].battles == 2
    assert rows[0].wins == 1
    assert rows[0].win_rate == 0.5


def test_parse_titan_arena_replay_without_effects():
    replay = _replay(battle_id="3001")
    replay["type"] = "titan_arena"
    replay["effects"] = []
    replay["attackers"] = {str(4000 + i): _hero(4000 + i) for i in range(5)}
    replay["defenders"] = [{str(4100 + i): _hero(4100 + i) for i in range(5)}]

    parsed = parse_battle_get_response({"response": {"replays": [replay], "users": []}})

    assert len(parsed) == 1
    assert parsed[0].battle_type == "titan_arena"
    assert parsed[0].attacker.banner_id is None
    assert parsed[0].attacker.heroes_summary == "4000,4001,4002,4003,4004"


def test_battle_get_by_type_payload(monkeypatch):
    client = HWClient(headers={"x-auth-token": "test"})
    captured: dict = {}

    def fake_call(payload, **_kwargs):
        captured["payload"] = payload
        return MagicMock(is_success=True, detail={})

    monkeypatch.setattr(client, "call", fake_call)
    client.battle_get_by_type("arena", limit=5, offset=10)

    call = captured["payload"]["calls"][0]
    assert call["name"] == ApiAction.BATTLE_GET_BY_TYPE
    assert call["args"] == {"type": "arena", "limit": 5, "offset": 10}


def test_lineup_key_from_ids_matches_build_team():
    team = _team([1, 2, 3, 4, 5], 6001)
    built = build_team(team, banner_id=2)
    assert lineup_key_from_ids([1, 2, 3, 4, 5], 6001, 2) == built.lineup_key


def test_lineup_key_from_ids_normalizes_zero_pet_and_banner():
    base = lineup_key_from_ids([1, 2, 3, 4, 5], None, None)
    assert lineup_key_from_ids([1, 2, 3, 4, 5], 0, 0) == base
    # Negative ids are the same "absent" sentinel.
    assert lineup_key_from_ids([1, 2, 3, 4, 5], -1, -1) == base


def test_wilson_lower_bound_penalizes_small_samples():
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(1, 1) < 0.5
    assert wilson_lower_bound(10, 10) > wilson_lower_bound(3, 3)
    assert wilson_lower_bound(8, 10) < 1.0


def test_query_plans_prefers_same_defender_tier():
    _create_account()
    replays = [
        _replay(battle_id="3001", win=True),
        _replay(battle_id="3002", win=True),
        _replay(battle_id="3003", win=True),
        _replay(battle_id="3004", win=True, defender_id="333"),
        _replay(battle_id="3005", win=True, heroes=_team([6, 7, 8, 9, 10], 6003)),
        _replay(battle_id="3006", win=True, heroes=_team([6, 7, 8, 9, 10], 6003)),
    ]
    parsed = parse_battle_get_response({"response": {"replays": replays, "users": {}}})
    import_battles("Alice", parsed)

    key = lineup_key_from_ids([11, 12, 13, 14, 15], 6002, 1)
    plans = query_plans(
        defender_lineup_key=key,
        defender_id="222",
        min_samples=3,
        min_win_rate=1.0,
    )

    assert plans[0].heroes_summary == "1,2,3,4,5"
    assert plans[0].tier == "same_defender"
    assert plans[0].battles == 3
    assert plans[0].recommended is True

    weak = next(plan for plan in plans if plan.heroes_summary == "6,7,8,9,10")
    assert weak.tier == "same_defender"
    assert weak.battles == 2
    assert weak.recommended is False


def test_query_plans_uses_latest_observed_favor():
    _create_account()
    old = _replay(battle_id="7001")
    new = _replay(battle_id="7002")
    old["startTime"] = "1793075300"
    new["startTime"] = "1793075400"
    for hero_id in range(1, 6):
        old["attackers"][str(hero_id)]["favorPetId"] = 6002
        new["attackers"][str(hero_id)]["favorPetId"] = 6011
    parsed = parse_battle_get_response(
        {"response": {"replays": [old, new], "users": {}}}
    )
    import_battles("Alice", parsed)

    key = lineup_key_from_ids([11, 12, 13, 14, 15], 6002, 1)
    plans = query_plans(
        defender_lineup_key=key,
        defender_id="222",
        min_samples=1,
    )

    plan = next(p for p in plans if p.heroes_summary == "1,2,3,4,5")
    assert plan.favor == {hero_id: 6011 for hero_id in range(1, 6)}


def test_query_plans_falls_back_to_same_lineup():
    _create_account()
    replays = [
        _replay(battle_id="4001", win=True),
        _replay(battle_id="4002", win=True),
        _replay(battle_id="4003", win=True, defender_id="333"),
    ]
    parsed = parse_battle_get_response({"response": {"replays": replays, "users": {}}})
    import_battles("Alice", parsed)

    key = lineup_key_from_ids([11, 12, 13, 14, 15], 6002, 1)
    plans = query_plans(
        defender_lineup_key=key,
        defender_id="999",
        min_samples=3,
        min_win_rate=1.0,
    )

    assert len(plans) == 1
    assert plans[0].tier == "same_lineup"
    assert plans[0].battles == 3
    assert plans[0].recommended is True


def test_query_plans_excludes_other_defender_lineups():
    _create_account()
    replays = [
        _replay(battle_id="5001", win=True),
        _replay(
            battle_id="5002",
            win=True,
            defender=_team([21, 22, 23, 24, 25], 6003),
        ),
    ]
    parsed = parse_battle_get_response({"response": {"replays": replays, "users": {}}})
    import_battles("Alice", parsed)

    key = lineup_key_from_ids([11, 12, 13, 14, 15], 6002, 1)
    plans = query_plans(defender_lineup_key=key, defender_id="222")

    assert len(plans) == 1
    assert plans[0].battles == 1


def test_build_team_treats_zero_banner_and_pet_as_none():
    team = _team([1, 2, 3, 4, 5], 6001)
    no_pet = _team([1, 2, 3, 4, 5], 6001)
    no_pet.pop("6001")
    for hero in no_pet.values():
        hero.pop("petId", None)

    assert build_team(team, 0).lineup_key == build_team(team, None).lineup_key
    assert build_team(team, 0).banner_id is None

    zero_pet = dict(team)
    zero_pet["6001"] = _pet(0)
    assert build_team(zero_pet, 2).pet_id is None
    assert build_team(zero_pet, 2).lineup_key == build_team(no_pet, 2).lineup_key


def test_parse_replay_zero_banner_effect_becomes_none():
    replay = _replay()
    replay["effects"]["defendersBanner"] = {"id": 0, "slots": []}
    parsed = parse_battle_get_response({"response": {"replays": [replay]}})

    assert parsed[0].defender.banner_id is None


def test_battle_type_falls_back_to_requested_type():
    replay = _replay(battle_id="7001")
    replay.pop("type")

    parsed = parse_battle_get_response(
        {"response": {"replays": [replay]}}, battle_type="grand"
    )

    assert parsed[0].battle_type == "grand"


def test_parse_defenders_dict_and_multiple_teams(caplog):
    single = _replay(battle_id="7101")
    single["defenders"] = single["defenders"][0]
    parsed = parse_battle_get_response({"response": {"replays": [single]}})
    assert parsed[0].defender.heroes_summary == "11,12,13,14,15"

    multi = _replay(battle_id="7102")
    second = _team([21, 22, 23, 24, 25], 6003)
    multi["defenders"] = multi["defenders"] + [second]
    with caplog.at_level(logging.WARNING, logger=battle_log.__name__):
        parsed = parse_battle_get_response({"response": {"replays": [multi]}})

    assert parsed[0].defender.heroes_summary == "11,12,13,14,15"
    assert parsed[0].completeness == "partial"
    assert "7102" in caplog.text


def test_config_hash_normalizes_integral_floats():
    int_team = _team([1, 2, 3, 4, 5], 6001)
    float_team = _team([1, 2, 3, 4, 5], 6001)
    float_team["1"]["hp"] = 1000.0

    assert build_team(int_team, 2).config_hash == build_team(float_team, 2).config_hash

    changed = _team([1, 2, 3, 4, 5], 6001)
    changed["1"]["hp"] = 1001.0
    assert build_team(changed, 2).config_hash != build_team(int_team, 2).config_hash


def test_intra_batch_duplicate_replay_inserted_once():
    _create_account()
    replay = _replay(battle_id="7201")
    parsed = parse_battle_get_response({"response": {"replays": [replay, replay]}})

    stats = import_battles("Alice", parsed)

    assert stats.inserted == 1
    assert stats.duplicates == 1
    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 1


def test_integrity_error_does_not_block_next_battle(monkeypatch):
    _create_account()
    replays = [_replay(battle_id="7301"), _replay(battle_id="7302")]
    parsed = parse_battle_get_response({"response": {"replays": replays}})
    original = battle_log._upsert_team
    calls = {"n": 0}

    def flaky_upsert(db, team, cache):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT INTO team_configs", {}, Exception("conflict"))
        return original(db, team, cache)

    monkeypatch.setattr(battle_log, "_upsert_team", flaky_upsert)

    stats = import_battles("Alice", parsed)

    assert stats.inserted == 1
    assert stats.skipped == 1
    assert len(stats.errors) == 1
    with get_session_local()() as db:
        rows = db.query(BattleRecord).all()
        assert [row.source_record_id for row in rows] == ["7302"]
        assert db.query(TeamConfig).count() == 2


def test_viewer_role_and_cross_account_involvement():
    _create_account("Alice", "111")
    _create_account("Bob", "222")
    replay = _replay(battle_id="7401")  # userId 111 attacks typeId 222
    parsed = parse_battle_get_response({"response": {"replays": [replay]}})

    alice_stats = import_battles("Alice", parsed)
    bob_stats = import_battles("Bob", parsed)

    assert alice_stats.inserted == 1
    assert bob_stats.inserted == 0
    assert bob_stats.duplicates == 1

    with get_session_local()() as db:
        row = db.query(BattleRecord).one()
        assert row.viewer_role == "attacker"

    # Bob was the defender; involvement-based filtering finds Alice's row.
    bob_rows = query_matchups(account_alias="Bob")
    assert len(bob_rows) == 1
    assert bob_rows[0].defender_player_id == "222"
    assert len(query_matchups(account_alias="Alice")) == 1


def test_query_matchups_filters_by_game_version():
    _create_account()
    replays = [
        _replay(battle_id="7501", game_version=298),
        _replay(battle_id="7502", game_version=299),
    ]
    parsed = parse_battle_get_response({"response": {"replays": replays}})
    import_battles("Alice", parsed)

    assert len(query_matchups(account_alias="Alice", game_version="298")) == 1
    assert query_matchups(account_alias="Alice", game_version="999") == []

    key = lineup_key_from_ids([11, 12, 13, 14, 15], 6002, 1)
    assert query_plans(defender_lineup_key=key, game_version="999") == []
    assert len(query_plans(defender_lineup_key=key, game_version="299")) == 1


def test_win_none_excluded_from_stats():
    _create_account()
    unknown = _replay(battle_id="7601")
    unknown["result"].pop("win")
    replays = [_replay(battle_id="7602", win=True), unknown]
    parsed = parse_battle_get_response({"response": {"replays": replays}})
    import_battles("Alice", parsed)

    matchups = query_matchups(account_alias="Alice")
    assert sum(row.battles for row in matchups) == 1

    key = lineup_key_from_ids([11, 12, 13, 14, 15], 6002, 1)
    plans = query_plans(defender_lineup_key=key, defender_id="222", min_samples=1)
    assert sum(plan.battles for plan in plans) == 1


def test_query_matchups_by_matchup_omits_arbitrary_defender():
    _create_account()
    replays = [
        _replay(battle_id="7701", win=True, defender_id="222"),
        _replay(battle_id="7702", win=False, defender_id="333"),
    ]
    parsed = parse_battle_get_response({"response": {"replays": replays}})
    import_battles("Alice", parsed)

    matchup_rows = query_matchups(account_alias="Alice", by="matchup")
    assert len(matchup_rows) == 1
    assert matchup_rows[0].battles == 2
    assert matchup_rows[0].defender_player_id is None

    defender_rows = query_matchups(account_alias="Alice", by="defender")
    assert {row.defender_player_id for row in defender_rows} == {"222", "333"}


def test_viewer_role_attacker_defender_and_unknown():
    _create_account("Alice", "111")
    _create_account("Bob", "222")
    _create_account("Carol", "333")

    def _single(battle_id: str):
        replay = _replay(battle_id=battle_id)  # userId 111 attacks typeId 222
        return parse_battle_get_response({"response": {"replays": [replay]}})

    import_battles("Alice", _single("7801"))  # attacker
    import_battles("Bob", _single("7802"))  # defender
    import_battles("Carol", _single("7803"))  # neither side

    with get_session_local()() as db:
        roles = {
            row.source_record_id: row.viewer_role
            for row in db.query(BattleRecord).all()
        }
    assert roles == {"7801": "attacker", "7802": "defender", "7803": "unknown"}


def test_query_matchups_rejects_account_without_player_id():
    _create_account("Dave", "")

    with pytest.raises(BattleLogError, match="no player_id"):
        query_matchups(account_alias="Dave")


def test_light_migration_adds_viewer_role_and_index():
    """既存の battle_records（viewer_role なし）に列と索引が後付けされる。"""
    from sqlalchemy import text

    from hw_genie.core.database import _apply_light_migrations, get_engine

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE battle_records"))
        conn.execute(
            text(
                "CREATE TABLE battle_records ("
                "id INTEGER PRIMARY KEY, battle_type VARCHAR(16) NOT NULL, "
                "defender_lineup_key VARCHAR(64) NOT NULL)"
            )
        )

    _apply_light_migrations(eng)
    _apply_light_migrations(eng)  # 冪等

    with eng.connect() as conn:
        columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(battle_records)"))
        }
        indexes = {
            row[1] for row in conn.execute(text("PRAGMA index_list(battle_records)"))
        }
    assert "viewer_role" in columns
    assert "ix_battle_records_type_defender_lineup" in indexes


def test_prune_deletes_old_battles_and_comparisons():
    _create_account()
    old = _replay(battle_id="old-1")
    old["startTime"] = str(
        int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    )
    new = _replay(battle_id="new-1")
    new["startTime"] = str(
        int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
    )
    parsed = parse_battle_get_response(
        {"response": {"replays": [old, new], "users": {}}}
    )
    import_battles("Alice", parsed)

    with get_session_local()() as db:
        rows = {row.source_record_id: row for row in db.query(BattleRecord).all()}
        db.add(
            BattleComparison(
                account_id=rows["old-1"].account_id,
                predicted_battle_id=rows["old-1"].id,
                actual_battle_id=rows["new-1"].id,
                mismatch_type="match",
            )
        )
        db.commit()

    cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
    dry = prune_battles(cutoff, dry_run=True)
    assert dry.matched == 1
    assert dry.deleted == 0
    assert dry.comparisons == 1
    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 2
        assert db.query(BattleComparison).count() == 1

    real = prune_battles(cutoff)
    assert real.deleted == 1
    assert real.comparisons == 1
    with get_session_local()() as db:
        remaining = db.query(BattleRecord).all()
        assert [row.source_record_id for row in remaining] == ["new-1"]
        assert db.query(BattleComparison).count() == 0


def test_summarize_battle_reports_errors_and_account(capsys):
    from hw_genie.runner import summarize_battle

    stats = ImportStats(total=2, inserted=1, duplicates=1, errors=["boom"])

    failed = summarize_battle([("Alice", (stats, None))])

    assert failed == 1
    out = capsys.readouterr().out
    assert "Multi battle import summary" in out
    assert "Alice" in out


def test_summarize_battle_clean_result_returns_zero(capsys):
    from hw_genie.runner import summarize_battle

    failed = summarize_battle([("Bob", (ImportStats(total=1, inserted=1), None))])

    assert failed == 0
    assert "Bob" in capsys.readouterr().out
