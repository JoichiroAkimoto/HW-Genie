"""Tests for `battle simulate` (Phase 2-A oracle parity)."""
from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest

from hw_genie.battle.engine import (
    BattleEstimate,
    BridgeError,
    PersistentPlaywrightBattleEngine,
    config_getter_for,
)
from hw_genie.commands.battle import (
    run_battle_config_getters,
    run_battle_simulate,
)
from hw_genie.core.battle_log import (
    BattleLogError,
    build_battle_payload,
    compare_result,
    parse_battle_replays,
    query_matchups,
    query_plans,
)
from hw_genie.core.database import (
    Account,
    BattleComparison,
    BattleRecord,
    get_session_local,
)


def _hero(hero_id: int, *, hp: int = 1000) -> dict:
    return {
        "id": hero_id,
        "type": "hero",
        "level": 130,
        "star": 6,
        "power": 100,
        "hp": hp,
        "slots": [0, 0, 0, 0, 0, 0],
        "artifacts": [{"level": 130, "star": 6}],
        "petId": 6001,
    }


def _replay(*, battle_id: str = "9001", win: bool = True) -> dict:
    attackers = {str(i): _hero(i) for i in range(1, 6)}
    attackers["6001"] = {"id": 6001, "type": "pet", "level": 130, "star": 6}
    defenders = {str(i): _hero(i) for i in range(11, 16)}
    defenders["6002"] = {"id": 6002, "type": "pet", "level": 130, "star": 6}
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
        "result": {"win": win, "stars": 3 if win else 0, "serverVersion": 298},
    }


class FakeClient:
    def __init__(self, detail: dict):
        self.detail = detail
        self.calls: list[tuple] = []

    def battle_get_by_type(self, battle_type, *, limit=50, offset=0):
        self.calls.append((battle_type, limit, offset))
        return SimpleNamespace(is_success=True, detail=self.detail)

    def get_headers(self) -> dict[str, str]:
        return {"x-auth-token": "test"}


class FakeOracle:
    engine_name = "fake-oracle"

    def __init__(self, *, win: bool = True, stars: int = 3):
        self.win = win
        self.stars = stars
        self.calls: list[dict] = []
        self.closed = False

    def calc(self, battle: dict) -> BattleEstimate:
        self.calls.append(battle)
        return BattleEstimate(win=self.win, stars=self.stars, progress=[])

    def close(self) -> None:
        self.closed = True


def _create_account(alias: str = "Alice", player_id: str = "111") -> None:
    with get_session_local()() as db:
        db.add(Account(player_id=player_id, alias=alias, player_name=alias))
        db.commit()


def _detail(*replays: dict) -> dict:
    return {"response": {"replays": list(replays), "users": {}}}


def test_build_battle_payload_keeps_seed_and_effects():
    replay = _replay()
    pairs = parse_battle_replays(_detail(replay))
    assert len(pairs) == 1

    payload = build_battle_payload(pairs[0][0], "arena")

    assert payload["seed"] == 1234567890
    assert payload["startTime"] == 1793075321
    assert payload["type"] == "arena"
    assert payload["effects"] == replay["effects"]
    assert payload["progress"] == []


def test_simulate_requires_config_getter_for_non_toe():
    client = FakeClient(_detail(_replay()))
    with pytest.raises(BattleLogError):
        run_battle_simulate(
            client,
            "Alice",
            battle_type="arena",
            config_getter="get_titanPvpManual",
            oracle=None,
        )


def test_config_getter_for_battle_types():
    assert config_getter_for("arena") == "get_pvp"
    assert config_getter_for("grand") == "get_pvp"
    assert config_getter_for("pvp") == "get_pvp"
    assert config_getter_for("titan_arena") == "get_titanPvpManual"
    assert config_getter_for("pvp_manual") == "get_pvpManual"
    assert config_getter_for("challenge") == "get_pvpManual"
    assert config_getter_for(None) == "get_titanPvpManual"
    assert config_getter_for("unknown") == "get_titanPvpManual"


def test_simulate_dry_run_writes_nothing():
    _create_account()
    oracle = FakeOracle()
    client = FakeClient(_detail(_replay()))

    comparisons = run_battle_simulate(
        client, "Alice", battle_type="arena", dry_run=True, oracle=oracle
    )

    assert len(comparisons) == 1
    assert comparisons[0].matched is True
    assert comparisons[0].mismatch_type == "match"
    assert oracle.calls[0]["seed"] == 1234567890
    # An injected oracle is owned by the caller, so the runner does not close it.
    assert oracle.closed is False

    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 0
        assert db.query(BattleComparison).count() == 0


def test_simulate_records_sim_row_and_comparison():
    _create_account()
    oracle = FakeOracle(win=True, stars=3)
    client = FakeClient(_detail(_replay()))

    comparisons = run_battle_simulate(
        client, "Alice", battle_type="arena", dry_run=False, oracle=oracle
    )

    assert len(comparisons) == 1
    assert comparisons[0].matched is True

    with get_session_local()() as db:
        rows = db.query(BattleRecord).all()
        assert len(rows) == 2
        sources = {row.source for row in rows}
        assert sources == {"api_import", "sim"}
        sim = next(row for row in rows if row.source == "sim")
        assert sim.authoritative is False
        assert sim.engine == "fake-oracle"
        assert sim.win is True
        comparison = db.query(BattleComparison).one()
        assert comparison.mismatch_type == "match"
        assert comparison.delta_stars == 0


def test_simulate_records_mismatch():
    _create_account()
    oracle = FakeOracle(win=False, stars=0)
    client = FakeClient(_detail(_replay(win=True)))

    comparisons = run_battle_simulate(
        client, "Alice", battle_type="arena", dry_run=False, oracle=oracle
    )

    assert comparisons[0].matched is False
    assert comparisons[0].mismatch_type == "win_flip"

    with get_session_local()() as db:
        comparison = db.query(BattleComparison).one()
        assert comparison.mismatch_type == "win_flip"
        assert comparison.expected_win is False
        assert comparison.actual_win is True


def test_sim_rows_are_excluded_from_stats_and_plans():
    _create_account()
    client = FakeClient(_detail(_replay()))
    run_battle_simulate(
        client, "Alice", battle_type="arena", dry_run=False, oracle=FakeOracle()
    )

    stats = query_matchups(account_alias="Alice")
    assert len(stats) == 1
    assert stats[0].battles == 1

    from hw_genie.core.battle_log import lineup_key_from_ids

    plans = query_plans(
        defender_lineup_key=lineup_key_from_ids([11, 12, 13, 14, 15], 6002, 1),
        defender_id="222",
        min_samples=1,
        min_win_rate=0.0,
    )
    assert len(plans) == 1
    assert plans[0].battles == 1


def test_simulate_rerun_updates_single_sim_and_comparison_row():
    _create_account()
    client = FakeClient(_detail(_replay()))

    run_battle_simulate(
        client, "Alice", battle_type="arena", dry_run=False, oracle=FakeOracle()
    )
    run_battle_simulate(
        client, "Alice", battle_type="arena", dry_run=False, oracle=FakeOracle()
    )

    with get_session_local()() as db:
        sim_rows = db.query(BattleRecord).filter(BattleRecord.source == "sim").all()
        assert len(sim_rows) == 1
        assert db.query(BattleRecord).filter(
            BattleRecord.source == "api_import"
        ).count() == 1
        assert db.query(BattleComparison).count() == 1


def test_simulate_continues_after_one_oracle_failure(capsys):
    _create_account()
    client = FakeClient(_detail(_replay(battle_id="9001"), _replay(battle_id="9002")))

    class FlakyOracle:
        engine_name = "flaky-oracle"

        def __init__(self) -> None:
            self.calls = 0

        def calc(self, battle: dict) -> BattleEstimate:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return BattleEstimate(win=True, stars=3, progress=[])

    comparisons = run_battle_simulate(
        client,
        "Alice",
        battle_type="arena",
        dry_run=False,
        as_json=True,
        oracle=FlakyOracle(),
    )

    assert [c.battle_id for c in comparisons] == ["9002"]
    report = json.loads(capsys.readouterr().out)
    assert report["processed"] == 2
    assert report["succeeded"] == 1
    assert report["failures"] == [{"battle_id": "9001", "error": "boom"}]

    with get_session_local()() as db:
        sim = db.query(BattleRecord).filter(BattleRecord.source == "sim").one()
        assert sim.source_record_id == "9002"
        assert db.query(BattleComparison).count() == 1


def test_simulate_raises_when_all_battles_fail():
    _create_account()
    client = FakeClient(_detail(_replay(battle_id="9001"), _replay(battle_id="9002")))

    class BrokenOracle:
        engine_name = "broken-oracle"

        def calc(self, battle: dict) -> BattleEstimate:
            raise RuntimeError("engine down")

    with pytest.raises(BattleLogError, match="all simulations failed"):
        run_battle_simulate(
            client, "Alice", battle_type="arena", oracle=BrokenOracle()
        )


def test_simulate_owned_oracle_is_closed(monkeypatch):
    _create_account()
    created: list = []

    class FakePersistentEngine:
        engine_name = "fake-persistent"

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.closed = False
            created.append(self)

        def calc(self, battle: dict) -> BattleEstimate:
            return BattleEstimate(win=True, stars=3, progress=[])

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "hw_genie.battle.engine.PersistentPlaywrightBattleEngine",
        FakePersistentEngine,
    )

    run_battle_simulate(
        FakeClient(_detail(_replay())),
        "Alice",
        battle_type="titan_arena",
        as_json=True,
    )

    assert len(created) == 1
    assert created[0].closed is True


def test_simulate_json_with_zero_replays(capsys):
    client = FakeClient({"response": {"replays": [], "users": {}}})

    result = run_battle_simulate(
        client, "Alice", battle_type="titan_arena", as_json=True
    )

    assert result == []
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == 0
    assert report["results"] == []
    assert report["failures"] == []


def test_persistent_engine_recycles_after_two_attempts(monkeypatch):
    class FakePage:
        def evaluate(self, script, arg=None):
            return json.dumps({"result": {"win": True, "stars": 3}, "progress": []})

    engine = PersistentPlaywrightBattleEngine(recycle_every=2)
    monkeypatch.setattr(engine, "_ensure_page", lambda: FakePage())
    closed: list[int] = []
    monkeypatch.setattr(engine, "close", lambda: closed.append(1))

    engine.calc({"id": 1})
    assert closed == []
    engine.calc({"id": 2})
    assert closed == [1]


def test_persistent_engine_closes_and_raises_on_null(monkeypatch):
    class NullPage:
        def evaluate(self, script, arg=None):
            return None

    engine = PersistentPlaywrightBattleEngine()
    monkeypatch.setattr(engine, "_ensure_page", lambda: NullPage())
    closed: list[int] = []
    monkeypatch.setattr(engine, "close", lambda: closed.append(1))

    with pytest.raises(BridgeError, match="returned null"):
        engine.calc({"id": 1})
    assert closed == [1]


def test_persistent_engine_list_config_getters_parses_and_wraps(monkeypatch):
    class FakePage:
        def __init__(self, value):
            self.value = value

        def evaluate(self, script):
            if isinstance(self.value, BaseException):
                raise self.value
            return self.value

    engine = PersistentPlaywrightBattleEngine()
    monkeypatch.setattr(
        engine,
        "_ensure_page",
        lambda: FakePage('{"staticKeys": ["a"], "properties": ["b=c"]}'),
    )
    assert engine.list_config_getters() == {
        "staticKeys": ["a"],
        "properties": ["b=c"],
    }

    monkeypatch.setattr(
        engine, "_ensure_page", lambda: FakePage(RuntimeError("page crashed"))
    )
    with pytest.raises(BridgeError, match="page crashed"):
        engine.list_config_getters()


def test_compare_result_none_stars_is_not_star_delta():
    parsed = parse_battle_replays(_detail(_replay(win=True)))[0][1]

    comparison = compare_result(parsed, True, None)
    assert comparison.mismatch_type == "match"
    assert comparison.matched is True
    assert comparison.delta_stars == 0

    comparison = compare_result(parsed, True, 1)
    assert comparison.mismatch_type == "star_delta"
    assert comparison.delta_stars == -2


def test_build_battle_payload_keeps_unknown_fields():
    replay = dict(_replay())
    replay["futureField"] = {"nested": 1}
    replay["progress"] = [{"step": 1}]
    parsed = parse_battle_replays(_detail(replay))[0][1]

    payload = build_battle_payload(replay, "arena")

    assert payload["futureField"] == {"nested": 1}
    assert payload["progress"] == [{"step": 1}]
    assert isinstance(payload["seed"], int)
    assert isinstance(payload["startTime"], int)
    assert parsed.source_record_id == "9001"


def test_simulate_seed_sweep_records_extra_rows():
    _create_account()
    oracle = FakeOracle(win=True, stars=3)
    client = FakeClient(_detail(_replay()))

    comparisons = run_battle_simulate(
        client,
        "Alice",
        battle_type="arena",
        dry_run=False,
        oracle=oracle,
        seeds=3,
    )

    rng = random.Random(1234567890)
    expected_seeds = [1234567890] + [rng.getrandbits(32) for _ in range(2)]
    assert [call["seed"] for call in oracle.calls] == expected_seeds
    assert len(set(expected_seeds)) == 3
    assert comparisons[0].sweep_seeds == 3
    assert comparisons[0].sweep_wins == 3
    assert comparisons[0].sweep_win_rate == 1.0
    with get_session_local()() as db:
        rows = db.query(BattleRecord).all()
        sim_rows = [row for row in rows if row.source == "sim"]
        assert len(sim_rows) == 3  # observed + 2 extra seeds
        assert len([row for row in rows if row.source == "api_import"]) == 1
        assert db.query(BattleComparison).count() == 1
        observed = next(
            row for row in sim_rows if row.source_record_id == "9001"
        )
        assert observed.seed == 1234567890
        extra = [row for row in sim_rows if "#seed=" in row.source_record_id]
        assert len(extra) == 2
        assert sorted(row.seed for row in extra) == sorted(expected_seeds[1:])
        for row in extra:
            assert row.battle_type == "arena"
            assert row.matchup_key == observed.matchup_key
            assert row.viewer_role == "attacker"
            assert row.completeness == observed.completeness


def test_simulate_seed_sweep_is_deterministic():
    _create_account()
    client = FakeClient(_detail(_replay()))
    first = FakeOracle(win=True)
    second = FakeOracle(win=True)

    run_battle_simulate(
        client, "Alice", battle_type="arena", dry_run=True, oracle=first, seeds=4
    )
    run_battle_simulate(
        client, "Alice", battle_type="arena", dry_run=True, oracle=second, seeds=4
    )

    assert [call["seed"] for call in first.calls] == [
        call["seed"] for call in second.calls
    ]


def test_simulate_seed_sweep_mixed_wins():
    _create_account()
    rng = random.Random(1234567890)
    seeds = [1234567890] + [rng.getrandbits(32) for _ in range(3)]
    expected_wins = sum(1 for seed in seeds if seed % 2 == 0)

    class SeedParityOracle:
        engine_name = "seed-parity"

        def calc(self, battle):
            return BattleEstimate(
                win=battle["seed"] % 2 == 0, stars=3, progress=[]
            )

    comparisons = run_battle_simulate(
        FakeClient(_detail(_replay())),
        "Alice",
        battle_type="arena",
        dry_run=False,
        oracle=SeedParityOracle(),
        seeds=4,
    )

    assert comparisons[0].sweep_seeds == 4
    assert comparisons[0].sweep_wins == expected_wins
    assert comparisons[0].sweep_win_rate == expected_wins / 4
    with get_session_local()() as db:
        rows = db.query(BattleRecord).all()
        observed = next(
            row for row in rows if row.source_record_id == "9001"
        )
        assert observed.seed == 1234567890
        assert observed.win == (1234567890 % 2 == 0)
        extra = [row for row in rows if "#seed=" in row.source_record_id]
        assert len(extra) == 3
        for row in extra:
            assert row.seed in seeds[1:]
            assert row.win == (row.seed % 2 == 0)


def test_simulate_seed_sweep_removes_stale_extra_rows():
    _create_account()
    client = FakeClient(_detail(_replay()))

    run_battle_simulate(
        client,
        "Alice",
        battle_type="arena",
        dry_run=False,
        oracle=FakeOracle(),
        seeds=3,
    )
    with get_session_local()() as db:
        assert len(
            [
                row
                for row in db.query(BattleRecord).all()
                if row.source == "sim"
            ]
        ) == 3

    run_battle_simulate(
        client,
        "Alice",
        battle_type="arena",
        dry_run=False,
        oracle=FakeOracle(),
        seeds=1,
    )
    with get_session_local()() as db:
        sim_rows = [
            row
            for row in db.query(BattleRecord).all()
            if row.source == "sim"
        ]
        assert len(sim_rows) == 1
        assert sim_rows[0].source_record_id == "9001"
        assert sim_rows[0].seed == 1234567890
        assert db.query(BattleComparison).count() == 1


def test_simulate_seed_sweep_dry_run_writes_nothing():
    _create_account()
    oracle = FakeOracle(win=True, stars=3)
    client = FakeClient(_detail(_replay()))

    comparisons = run_battle_simulate(
        client,
        "Alice",
        battle_type="arena",
        dry_run=True,
        oracle=oracle,
        seeds=3,
    )

    assert len(oracle.calls) == 3
    assert comparisons[0].sweep_seeds == 3
    with get_session_local()() as db:
        assert db.query(BattleRecord).count() == 0
        assert db.query(BattleComparison).count() == 0


def test_simulate_rejects_zero_seeds():
    with pytest.raises(BattleLogError):
        run_battle_simulate(
            FakeClient(_detail(_replay())),
            "Alice",
            battle_type="arena",
            oracle=FakeOracle(),
            seeds=0,
        )


def test_simulate_passes_headless_and_timeout_to_engine(monkeypatch):
    _create_account()
    import hw_genie.battle.engine as engine_mod

    captured = {}

    class FakeEngine:
        engine_name = "fake-engine"

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def calc(self, battle):
            return BattleEstimate(win=True, stars=3, progress=[])

        def close(self):
            pass

    monkeypatch.setattr(
        engine_mod, "PersistentPlaywrightBattleEngine", FakeEngine
    )
    run_battle_simulate(
        FakeClient(_detail(_replay())),
        "Alice",
        battle_type="arena",
        oracle=None,
        headless=False,
        timeout=45,
    )

    assert captured["headless"] is False
    assert captured["timeout"] == 45
    assert captured["config_getter"] is None


def test_config_getters_passes_headless_and_timeout(monkeypatch):
    import hw_genie.battle.engine as engine_mod

    captured = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def list_config_getters(self):
            return {"staticKeys": [], "properties": []}

        def close(self):
            pass

    monkeypatch.setattr(
        engine_mod, "PersistentPlaywrightBattleEngine", FakeEngine
    )
    run_battle_config_getters(
        FakeClient(_detail(_replay())), headless=False, timeout=45
    )

    assert captured["headless"] is False
    assert captured["timeout"] == 45


def test_simulate_rejects_bad_timeout():
    with pytest.raises(BattleLogError):
        run_battle_simulate(
            FakeClient(_detail(_replay())),
            "Alice",
            battle_type="arena",
            oracle=FakeOracle(),
            timeout=0,
        )
