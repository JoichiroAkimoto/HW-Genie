"""Tests for HWAssistant snapshot import and the battle watcher."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from hw_genie.commands.battle import (
    parse_battle_types,
    run_battle_watch,
)
from hw_genie.core.battle_log import (
    BattleLogError,
    import_battles,
    lineup_key_from_ids,
    parse_battle_get_response,
)
from hw_genie.core.database import (
    Account,
    BattleComparison,
    BattleRecord,
    TeamConfig,
    get_session_local,
)
from hw_genie.core.hwa_log import (
    expand_hwa_hero,
    import_hwa_file,
    iter_hwa_battles,
)


def _compact(
    hero_id: int,
    *,
    hero_type: str = "hero",
    hp: int = 1000,
    power: int = 100,
    level: int = 130,
    star: int = 6,
    pet_id: int | None = 6001,
) -> dict:
    c = [None] * 36
    c[0] = 100
    c[1] = 100
    c[2] = 100
    c[3] = [{"level": 130, "star": 6}]
    c[4] = {}
    c[5] = 18
    c[6] = pet_id
    c[7] = 0
    c[8] = hp if hero_type == "hero" else None
    c[9] = 100
    c[10] = level
    c[11] = 100
    c[12] = 100
    c[13] = []
    c[14] = pet_id
    c[15] = 100
    c[16] = power
    c[17] = [100]
    c[18] = {}
    c[19] = {}
    c[20] = [0, 0, 0, 0, 0, 0]
    c[21] = star
    c[22] = 100
    c[23] = 30
    c[25] = hero_type
    c[31] = 1
    return {"id": hero_id, "c": c, "ts": 1793075321}


def _hwa_battle(
    battle_id: str,
    *,
    battle_type: str = "arena",
    user: str = "111",
    defender: str = "222",
    win: bool = True,
    order: int = 0,
) -> dict:
    attackers = {str(i): _compact(i) for i in range(1, 6)}
    attackers["6001"] = _compact(6001, hero_type="pet", pet_id=None)
    defenders = {str(i): _compact(i) for i in range(11, 16)}
    defenders["6002"] = _compact(6002, hero_type="pet", pet_id=None)
    return {
        "id": battle_id,
        "type": battle_type,
        "userId": user,
        "typeId": defender,
        "seed": "1234567890",
        "startTime": "1793075321",
        "endTime": "1793075322",
        "attackers": attackers,
        "defenders": [defenders],
        "effects": {
            "attackersBanner": {"id": 2, "slots": [1, 2, 3]},
            "defendersBanner": {"id": 1, "slots": [4, 5, 6]},
        },
        "result": {
            "win": win,
            "stars": 3 if win else 0,
            "serverVersion": 298,
            "battleOrder": order,
        },
        "attackerTeamPower": 1000,
        "defenderTeamPower": 900,
        "platform": "wb",
        "dnow": 1793075321000,
    }


def _api_hero(hero_id: int) -> dict:
    return {
        "id": hero_id,
        "type": "hero",
        "level": 130,
        "star": 6,
        "power": 100,
        "hp": 1000,
        "petId": 6001,
    }


def _api_replay(battle_id: str = "api-1", *, win: bool = True) -> dict:
    attackers = {str(i): _api_hero(i) for i in range(1, 6)}
    attackers["6001"] = {"id": 6001, "type": "pet", "level": 130}
    defenders = {str(i): _api_hero(i) for i in range(11, 16)}
    defenders["6002"] = {"id": 6002, "type": "pet", "level": 130}
    return {
        "id": battle_id,
        "type": "arena",
        "userId": "111",
        "typeId": "222",
        "seed": "1234567890",
        "startTime": "1793075321",
        "endTime": "1793075322",
        "attackers": attackers,
        "defenders": [defenders],
        "effects": {
            "attackersBanner": {"id": 2, "slots": [1, 2, 3]},
            "defendersBanner": {"id": 1, "slots": [4, 5, 6]},
        },
        "result": {"win": win, "stars": 3, "serverVersion": 298},
    }


def _write_hwa(path, *, arena=(), ga=()) -> None:
    payload = {
        "arena_replays": {battle["id"]: battle for battle in arena},
        "ga_replays": {
            f"series-{index}": {"0": battle}
            for index, battle in enumerate(ga)
        },
        "users": {},
        "userTeams": {},
    }
    _write_payload(path, payload)


def _write_payload(path, payload: dict) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _create_account(alias: str, player_id: str) -> None:
    with get_session_local()() as db:
        db.add(Account(player_id=player_id, alias=alias, player_name=alias))
        db.commit()


def test_expand_compact_hero_maps_combat_stats():
    hero = expand_hwa_hero(_compact(1, hp=1234, power=5678, level=130, star=6))
    assert hero["id"] == 1
    assert hero["type"] == "hero"
    assert hero["hp"] == 1234
    assert hero["power"] == 5678
    assert hero["level"] == 130
    assert hero["star"] == 6

    pet = expand_hwa_hero(_compact(6001, hero_type="pet", pet_id=None))
    assert pet["type"] == "pet"


def test_expand_hero_type_is_case_insensitive_and_inferred():
    upper = _compact(1, hero_type="PET")
    assert expand_hwa_hero(upper)["type"] == "pet"

    missing = _compact(2)
    missing["c"][25] = None
    assert expand_hwa_hero(missing)["type"] == "hero"

    pet_missing = _compact(6001, pet_id=None)
    pet_missing["c"][25] = None
    assert expand_hwa_hero(pet_missing)["type"] == "pet"


def test_iter_hwa_battles_expands_ga_series(tmp_path):
    path = tmp_path / "hwa.json.gz"
    _write_hwa(
        path,
        arena=[_hwa_battle("a-1")],
        ga=[
            _hwa_battle("g-1", battle_type="grand"),
            _hwa_battle("g-2", battle_type="grand", order=1),
        ],
    )

    battles = list(iter_hwa_battles(path, types=("arena", "grand")))

    assert [battle.source_record_id for battle in battles] == ["a-1", "g-1", "g-2"]
    assert [battle.battle_type for battle in battles] == ["arena", "grand", "grand"]
    assert len(list(iter_hwa_battles(path, types=("arena",)))) == 1


def test_hwa_lineup_key_matches_manual_ids(tmp_path):
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("a-1")])

    battle = next(iter(iter_hwa_battles(path, types=("arena",))))

    assert battle.attacker.lineup_key == lineup_key_from_ids(
        [1, 2, 3, 4, 5], 6001, 2
    )
    assert battle.defender.lineup_key == lineup_key_from_ids(
        [11, 12, 13, 14, 15], 6002, 1
    )


def test_import_hwa_file_dry_run_then_real(tmp_path):
    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(
        path,
        arena=[_hwa_battle("a-1")],
        ga=[_hwa_battle("g-1", battle_type="grand")],
    )

    dry = import_hwa_file("Alice", path, dry_run=True)
    assert dry.total == 2
    assert dry.inserted == 2
    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 0

    real = import_hwa_file("Alice", path)
    assert real.inserted == 2
    assert real.duplicates == 0
    with get_session_local()() as db:
        rows = db.query(BattleRecord).all()
        assert len(rows) == 2
        assert {row.source for row in rows} == {"hwa_import"}
        assert all(row.authoritative is True for row in rows)
        assert all(row.viewer_role == "attacker" for row in rows)


def test_hwa_import_dedupes_observation_sources(tmp_path):
    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("shared-1")])

    battle = next(iter(iter_hwa_battles(path, types=("arena",))))
    first = import_battles(
        "Alice", [battle], source="api_import", engine="game-server"
    )
    assert first.inserted == 1

    second = import_hwa_file("Alice", path)
    assert second.inserted == 0
    assert second.duplicates == 1

    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 1


def test_hwa_import_attaches_to_involved_account(tmp_path):
    _create_account("Alice", "111")
    _create_account("Bob", "222")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(
        path,
        arena=[
            _hwa_battle("alice-attack", user="111", defender="999"),
            _hwa_battle("bob-defense", user="999", defender="222"),
        ],
    )

    stats = import_hwa_file("Alice", path)

    assert stats.by_account == {"Alice": 1, "Bob": 1}
    with get_session_local()() as db:
        bob = db.query(Account).filter(Account.alias == "Bob").one()
        row = (
            db.query(BattleRecord)
            .filter(BattleRecord.source_record_id == "bob-defense")
            .one()
        )
        assert row.account_id == bob.id
        assert row.viewer_role == "defender"


class FakeWatchClient:
    def __init__(self, detail: dict):
        self.detail = detail
        self.calls: list[tuple[str, int]] = []
        self.slept = 0

    def battle_get_by_type(self, battle_type, *, limit=50, offset=0):
        self.calls.append((battle_type, limit))
        return SimpleNamespace(is_success=True, detail=self.detail)

    def sleep(self) -> None:
        self.slept += 1


def test_run_battle_watch_once_imports_new_battles():
    _create_account("Alice", "111")
    detail = {"response": {"replays": [_api_replay("watch-1")], "users": {}}}
    client = FakeWatchClient(detail)

    passes = run_battle_watch(
        client, "Alice", types=("arena",), once=True, limit=10
    )

    assert len(passes) == 1
    assert passes[0]["results"]["arena"]["inserted"] == 1
    assert client.calls == [("arena", 10)]
    assert client.slept == 1
    with get_session_local()() as db:
        row = db.query(BattleRecord).one()
        assert row.source_record_id == "watch-1"
        assert row.source == "api_import"


def test_parse_battle_types():
    assert parse_battle_types("arena,ga") == ("arena", "grand")
    assert parse_battle_types("grand,ga") == ("grand",)
    with pytest.raises(BattleLogError):
        parse_battle_types("arena,bogus")


@pytest.mark.parametrize(
    ("drop_first", "drop_second"),
    [(False, True), (True, True)],
)
def test_ga_series_missing_ids_get_distinct_fallbacks(
    tmp_path, drop_first, drop_second
):
    _create_account("Alice", "111")
    first = _hwa_battle("g-1", battle_type="grand")
    second = _hwa_battle("g-2", battle_type="grand", order=1)
    if drop_first:
        first.pop("id")
    if drop_second:
        second.pop("id")
    path = tmp_path / "hwa.json.gz"
    _write_payload(
        path,
        {
            "arena_replays": {},
            "ga_replays": {"series-A": {"0": first, "1": second}},
        },
    )

    battles = list(iter_hwa_battles(path, types=("grand",)))
    ids = [battle.source_record_id for battle in battles]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    if drop_first:
        assert ids[0] == "series-A-0-1793075321-1234567890-0"
    else:
        assert ids[0] == "g-1"
    if drop_second:
        assert ids[1] == "series-A-1-1793075321-1234567890-1"
    else:
        assert ids[1] == "g-2"

    stats = import_hwa_file("Alice", path)
    assert stats.inserted == 2
    assert stats.duplicates == 0
    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 2


def test_list_shaped_sections_are_supported(tmp_path):
    path = tmp_path / "hwa.json.gz"
    _write_payload(
        path,
        {
            "arena_replays": [_hwa_battle("list-a")],
            "ga_replays": [
                {"0": _hwa_battle("list-g", battle_type="grand")}
            ],
        },
    )

    battles = list(iter_hwa_battles(path))

    assert [battle.source_record_id for battle in battles] == [
        "list-a",
        "list-g",
    ]
    assert [battle.battle_type for battle in battles] == ["arena", "grand"]


def test_duplicate_ids_in_one_file_are_skipped(tmp_path):
    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_payload(
        path,
        {
            "arena_replays": {},
            "ga_replays": {
                "series-dup": {
                    "0": _hwa_battle("dup-1", battle_type="grand"),
                    "1": _hwa_battle("dup-1", battle_type="grand", order=1),
                }
            },
        },
    )

    stats = import_hwa_file("Alice", path)

    assert stats.total == 2
    assert stats.inserted == 1
    assert stats.duplicates == 1
    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 1


def test_malformed_hwa_record_is_reported_not_fatal(tmp_path):
    path = tmp_path / "hwa.json.gz"
    _write_payload(
        path,
        {
            "arena_replays": {
                "bad": {"id": "bad", "attackers": {}, "defenders": []},
                "good": _hwa_battle("good-1"),
            },
            "ga_replays": {},
        },
    )
    errors: list[str] = []

    battles = list(
        iter_hwa_battles(path, types=("arena",), on_error=errors.append)
    )

    assert [battle.source_record_id for battle in battles] == ["good-1"]
    assert len(errors) == 1


def test_hwa_and_api_lineup_keys_match(tmp_path):
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("a-1")])

    hwa = next(iter(iter_hwa_battles(path, types=("arena",))))
    api = parse_battle_get_response(
        {"response": {"replays": [_api_replay("api-1")]}}, battle_type="arena"
    )[0]

    assert hwa.attacker.lineup_key == api.attacker.lineup_key
    assert hwa.defender.lineup_key == api.defender.lineup_key
    assert hwa.battle_type == api.battle_type == "arena"


def test_api_import_upgrades_hwa_partial_row(tmp_path):
    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("shared-upgrade")])

    assert import_hwa_file("Alice", path).inserted == 1
    hwa_battle = next(iter(iter_hwa_battles(path, types=("arena",))))
    api_battle = parse_battle_get_response(
        {"response": {"replays": [_api_replay("shared-upgrade")]}},
        battle_type="arena",
    )[0]

    stats = import_battles("Alice", [api_battle])

    assert stats.upgraded == 1
    assert stats.inserted == 0
    assert stats.duplicates == 0
    with get_session_local()() as db:
        row = db.query(BattleRecord).filter(
            BattleRecord.source_record_id == "shared-upgrade"
        ).one()
        assert row.source == "api_import"
        assert row.completeness == "full"
        attacker_config = db.query(TeamConfig).filter(
            TeamConfig.id == row.attacker_config_id
        ).one()
        assert attacker_config.config_hash == api_battle.attacker.config_hash
        assert attacker_config.config_hash != hwa_battle.attacker.config_hash
        assert attacker_config.completeness == "full"


class PartialFailureWatchClient:
    def __init__(self, detail: dict):
        self.detail = detail
        self.calls: list[tuple[str, int, int]] = []

    def battle_get_by_type(self, battle_type, *, limit=50, offset=0):
        self.calls.append((battle_type, limit, offset))
        if battle_type == "arena":
            raise RuntimeError("arena endpoint down")
        return SimpleNamespace(is_success=True, detail=self.detail)

    def sleep(self) -> None:
        pass


def test_run_battle_watch_once_reports_partial_failure():
    _create_account("Alice", "111")
    grand = _api_replay("watch-grand")
    grand["type"] = "grand"
    client = PartialFailureWatchClient(
        {"response": {"replays": [grand], "users": {}}}
    )

    passes = run_battle_watch(
        client,
        "Alice",
        types=("arena", "grand"),
        once=True,
        limit=5,
        offset=10,
    )

    results = passes[0]["results"]
    assert "error" in results["arena"]
    assert results["grand"]["inserted"] == 1
    assert client.calls == [("arena", 5, 10), ("grand", 5, 10)]


class _FakeSimClient:
    def __init__(self, detail: dict):
        self.detail = detail

    def battle_get_by_type(self, battle_type, *, limit=50, offset=0):
        return SimpleNamespace(is_success=True, detail=self.detail)

    def get_headers(self) -> dict[str, str]:
        return {}


class _FakeOracle:
    engine_name = "fake-oracle"

    def calc(self, battle: dict):
        from hw_genie.battle.engine import BattleEstimate

        return BattleEstimate(win=True, stars=3, progress=[])


def test_simulate_upgrades_hwa_partial_row_without_error(tmp_path):
    """An HWA partial row is upgraded, not treated as a failed import."""
    from hw_genie.commands.battle import run_battle_simulate

    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("shared-sim")])
    assert import_hwa_file("Alice", path).inserted == 1

    client = _FakeSimClient(
        {"response": {"replays": [_api_replay("shared-sim")]}}
    )
    comparisons = run_battle_simulate(
        client, "Alice", battle_type="arena", oracle=_FakeOracle()
    )

    assert [comparison.battle_id for comparison in comparisons] == ["shared-sim"]
    with get_session_local()() as db:
        observed = (
            db.query(BattleRecord).filter(BattleRecord.source != "sim").one()
        )
        assert observed.source == "api_import"
        assert observed.completeness == "full"
        sim = db.query(BattleRecord).filter(BattleRecord.source == "sim").one()
        assert sim.source_record_id == "shared-sim"
        assert db.query(BattleComparison).count() == 1


def _ids_only_api_replay(battle_id: str) -> dict:
    replay = _api_replay(battle_id)
    for team in replay["attackers"].values():
        team.pop("hp", None)
    for team in replay["defenders"][0].values():
        team.pop("hp", None)
    return replay


def test_ids_only_api_replay_does_not_replace_hwa_partial_row(tmp_path):
    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("keep-hwa")])
    assert import_hwa_file("Alice", path).inserted == 1

    ids_only = parse_battle_get_response(
        {"response": {"replays": [_ids_only_api_replay("keep-hwa")]}},
        battle_type="arena",
    )[0]
    assert ids_only.completeness == "ids_only"

    stats = import_battles("Alice", [ids_only])

    assert stats.duplicates == 1
    assert stats.upgraded == 0
    assert stats.inserted == 0
    with get_session_local()() as db:
        row = db.query(BattleRecord).one()
        assert row.source == "hwa_import"
        assert row.completeness == "partial"


def test_upgrade_keeps_original_owner_and_viewer_role(tmp_path):
    _create_account("Alice", "111")
    _create_account("Bob", "222")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("bob-partial", user="999", defender="222")])
    assert import_hwa_file("Alice", path).inserted == 1
    with get_session_local()() as db:
        bob_id = db.query(Account).filter(Account.alias == "Bob").one().id
        before = db.query(BattleRecord).one()
        assert before.account_id == bob_id
        assert before.viewer_role == "defender"

    api = parse_battle_get_response(
        {"response": {"replays": [_api_replay("bob-partial")]}},
        battle_type="arena",
    )[0]
    stats = import_battles("Alice", [api])

    assert stats.upgraded == 1
    with get_session_local()() as db:
        row = db.query(BattleRecord).one()
        assert row.source == "api_import"
        assert row.account_id == bob_id
        assert row.viewer_role == "defender"


def test_import_batch_handles_new_upgrade_and_duplicate_together(tmp_path):
    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("upgrade-me")])
    assert import_hwa_file("Alice", path).inserted == 1
    duplicate = parse_battle_get_response(
        {"response": {"replays": [_api_replay("already-api")]}},
        battle_type="arena",
    )[0]
    assert import_battles("Alice", [duplicate]).inserted == 1

    batch = parse_battle_get_response(
        {
            "response": {
                "replays": [
                    _api_replay("upgrade-me"),
                    _api_replay("already-api"),
                    _api_replay("brand-new"),
                ]
            }
        },
        battle_type="arena",
    )
    stats = import_battles("Alice", batch)

    assert stats.inserted == 1
    assert stats.upgraded == 1
    assert stats.duplicates == 1
    with get_session_local()() as db:
        sources = {
            row.source_record_id: row.source for row in db.query(BattleRecord)
        }
        assert sources == {
            "upgrade-me": "api_import",
            "already-api": "api_import",
            "brand-new": "api_import",
        }


def test_hwa_bulk_failure_falls_back_to_per_battle(monkeypatch, tmp_path):
    import hw_genie.core.hwa_log as hwa_log

    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[_hwa_battle("bulk-1"), _hwa_battle("bulk-2")])

    calls: list = []

    def _boom(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("bulk exploded")

    monkeypatch.setattr(hwa_log, "bulk_import_battles", _boom)
    stats = import_hwa_file("Alice", path)

    assert calls, "bulk path should have been attempted"
    assert stats.inserted == 2
    assert stats.duplicates == 0
    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 2


def test_ga_series_container_with_id_iterates_sub_battles(tmp_path):
    path = tmp_path / "hwa.json.gz"
    _write_payload(
        path,
        {
            "arena_replays": {},
            "ga_replays": {
                "series-1": {
                    "id": "series-1",
                    "0": _hwa_battle("g-inner", battle_type="grand"),
                }
            },
        },
    )

    battles = list(iter_hwa_battles(path, types=("grand",)))

    assert [battle.source_record_id for battle in battles] == ["g-inner"]


def test_ga_fallback_ids_depend_on_content(tmp_path):
    first = _hwa_battle("unused", battle_type="grand")
    first.pop("id")
    second = dict(first)
    second["startTime"] = "1793075399"
    second["seed"] = "999"

    ids: list[str] = []
    for name, record in (("a", first), ("b", second)):
        path = tmp_path / f"{name}.json.gz"
        _write_payload(
            path,
            {"arena_replays": {}, "ga_replays": {"series-A": {"0": record}}},
        )
        ids.append(
            next(iter(iter_hwa_battles(path, types=("grand",)))).source_record_id
        )

    assert ids[0] != ids[1]
    assert ids[0].startswith("series-A-0-1793075321-1234567890-")
    assert ids[1].startswith("series-A-0-1793075399-999-")


def test_malformed_epoch_does_not_drop_the_record(tmp_path):
    battle = _hwa_battle("epoch-bad")
    battle["startTime"] = str(10**20)
    battle["dnow"] = "1e400"
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[battle])

    parsed = next(iter(iter_hwa_battles(path, types=("arena",))))

    assert parsed.source_record_id == "epoch-bad"
    assert isinstance(parsed.occurred_at, datetime)


def test_empty_and_missing_sections_yield_nothing(tmp_path):
    empty = tmp_path / "empty.json.gz"
    _write_payload(empty, {"arena_replays": {}, "ga_replays": {}})
    assert list(iter_hwa_battles(empty)) == []

    missing = tmp_path / "missing.json.gz"
    _write_payload(missing, {"users": {}})
    assert list(iter_hwa_battles(missing)) == []


def _import_hwa_args(**overrides) -> SimpleNamespace:
    values = {
        "account": "Alice",
        "source": "snapshot.json.gz",
        "types": "arena",
        "limit": None,
        "batch_size": 500,
        "dry_run": False,
        "json": True,
        "no_sync": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_sync_replica_quiet_is_silent_on_success(monkeypatch, capsys):
    from hw_genie import main as cli
    import hw_genie.core.database as database

    monkeypatch.setenv("TURSO_SYNC_URL", "libsql://example.turso.io")

    class Raw:
        def __init__(self) -> None:
            self.synced = 0

        def sync(self) -> None:
            self.synced += 1

    raw = Raw()

    class Conn:
        connection = SimpleNamespace(dbapi_connection=raw)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Engine:
        def connect(self):
            return Conn()

    monkeypatch.setattr(database, "get_engine", lambda: Engine())

    assert cli._sync_replica(quiet=True) is True
    assert raw.synced == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    assert cli._sync_replica(quiet=False) is True
    assert "Local replica synced" in capsys.readouterr().out


def test_sync_replica_failure_is_stderr_only_when_not_quiet(monkeypatch, capsys):
    from hw_genie import main as cli
    import hw_genie.core.database as database

    monkeypatch.setenv("TURSO_SYNC_URL", "libsql://example.turso.io")

    class BrokenEngine:
        def connect(self):
            raise RuntimeError("remote down")

    monkeypatch.setattr(database, "get_engine", lambda: BrokenEngine())

    assert cli._sync_replica(quiet=True) is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    assert cli._sync_replica(quiet=False) is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Sync failed" in captured.err


def test_import_hwa_json_no_sync_keeps_stdout_clean(monkeypatch, capsys):
    from hw_genie import main as cli

    def _fake_run(alias, source, **kwargs):
        print(json.dumps({"inserted": 1}))
        return SimpleNamespace()

    monkeypatch.setattr(
        "hw_genie.commands.battle.run_battle_import_hwa", _fake_run
    )
    monkeypatch.setattr(cli, "resolve_account", lambda account: "Alice")
    sync_calls: list[bool] = []

    def _sync(quiet: bool = False) -> bool:
        sync_calls.append(quiet)
        return False

    monkeypatch.setattr(cli, "_sync_replica", _sync)
    monkeypatch.setenv("TURSO_SYNC_ON_CONNECT", "true")

    cli.cmd_battle_import_hwa(_import_hwa_args())

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"inserted": 1}
    assert sync_calls == [True]
    assert "replica sync failed" in captured.err


def test_import_hwa_sync_failure_does_not_mask_import_error(monkeypatch, capsys):
    from hw_genie import main as cli

    def _fail_run(alias, source, **kwargs):
        raise BattleLogError("import exploded")

    monkeypatch.setattr(
        "hw_genie.commands.battle.run_battle_import_hwa", _fail_run
    )
    monkeypatch.setattr(cli, "resolve_account", lambda account: "Alice")
    monkeypatch.setattr(cli, "_sync_replica", lambda quiet=False: False)
    monkeypatch.setenv("TURSO_SYNC_ON_CONNECT", "true")

    with pytest.raises(SystemExit) as excinfo:
        cli.cmd_battle_import_hwa(_import_hwa_args())

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "import exploded" in captured.err


class _ErrorWatchClient:
    def battle_get_by_type(self, *args, **kwargs):
        raise RuntimeError("endpoint down")

    def sleep(self) -> None:
        pass


def test_watch_once_all_errors_exits_nonzero(monkeypatch, capsys):
    from hw_genie import main as cli

    monkeypatch.setattr(cli, "resolve_account", lambda account: "Alice")
    monkeypatch.setattr(cli, "_ensure_session", lambda args: {})
    monkeypatch.setattr(cli, "HWClient", lambda headers: _ErrorWatchClient())

    args = SimpleNamespace(
        account="Alice",
        types="arena,grand",
        interval=1,
        limit=5,
        offset=0,
        once=True,
        json=True,
    )
    with pytest.raises(SystemExit) as excinfo:
        cli.cmd_battle_watch(args)

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["results"]) == {"arena", "grand"}
    assert all("error" in result for result in payload["results"].values())


def test_import_hwa_since_filters_old(tmp_path):
    _create_account("Alice", "111")
    old = _hwa_battle("old-1")
    old["startTime"] = str(
        int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    )
    new = _hwa_battle("new-1")
    new["startTime"] = str(
        int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
    )
    path = tmp_path / "hwa.json.gz"
    _write_hwa(path, arena=[old, new])

    stats = import_hwa_file(
        "Alice",
        path,
        since=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert stats.total == 2
    assert stats.filtered == 1
    assert stats.inserted == 1
    with get_session_local()() as db:
        ids = [
            row.source_record_id
            for row in db.query(BattleRecord).all()
        ]
    assert ids == ["new-1"]


def test_import_hwa_offset_limit_paginates(tmp_path):
    _create_account("Alice", "111")
    path = tmp_path / "hwa.json.gz"
    _write_hwa(
        path,
        arena=[
            _hwa_battle("page-1"),
            _hwa_battle("page-2"),
            _hwa_battle("page-3"),
        ],
    )

    stats = import_hwa_file("Alice", path, offset=1, limit=1)

    assert stats.inserted == 1
    with get_session_local()() as db:
        ids = [
            row.source_record_id
            for row in db.query(BattleRecord).all()
        ]
    assert ids == ["page-2"]
