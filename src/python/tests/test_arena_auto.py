"""Tests for `arena auto` (guild exclusion, highest target, gated attack)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hw_genie.commands import arena as arena_mod
from hw_genie.commands.arena import (
    ArenaAttackResult,
    ArenaAttackUncertain,
    ArenaError,
    ArenaStatus,
    ArenaTeam,
    ArenaTopEntry,
    fetch_clan_member_ids,
    run_arena_auto,
    select_auto_targets,
)
from hw_genie.core.battle_log import PlanCandidate, lineup_key_from_ids

PLAN_HEROES = [1, 2, 3, 4, 5]
PLAN_PET = 6001
PLAN_BANNER = 2


def _entry(
    place: int,
    user_id: int,
    *,
    heroes: list[int] | None = None,
    pet: int | None = PLAN_PET,
    banner: int | None = PLAN_BANNER,
) -> ArenaTopEntry:
    return ArenaTopEntry(
        place=place,
        user_id=user_id,
        heroes=heroes if heroes is not None else list(PLAN_HEROES),
        pet=pet,
        banner=banner,
    )


def _plan(
    *,
    recommended: bool = True,
    heroes: list[int] | None = None,
    pet: int | None = PLAN_PET,
    banner: int | None = PLAN_BANNER,
    key: str | None = None,
    favor: dict[int, int] | None = None,
) -> PlanCandidate:
    lineup = list(heroes if heroes is not None else PLAN_HEROES)
    return PlanCandidate(
        attacker_lineup_key=(
            key if key is not None else lineup_key_from_ids(lineup, pet, banner)
        ),
        heroes_summary=",".join(str(hero) for hero in lineup),
        pet_id=pet,
        banner_id=banner,
        battles=3,
        wins=3,
        win_rate=1.0,
        wilson_low=0.4,
        last_seen=None,
        tier="same_defender",
        recommended=recommended,
        favor=dict(favor or {}),
    )


def _status(saved_team: ArenaTeam | None = None) -> ArenaStatus:
    return ArenaStatus(
        place=10,
        grand_place=20,
        user_id=111,
        name="Alice",
        attempts=5,
        cooldown=0,
        saved_team=saved_team,
    )


def _patch_auto(
    monkeypatch,
    *,
    candidates: list[ArenaTopEntry],
    saved_team: ArenaTeam | None = None,
    clan_member_ids: set[int] | None = None,
    plans=None,
) -> list[dict]:
    """Patch the read-only helpers and return the captured query_plans kwargs."""
    monkeypatch.setattr(
        arena_mod, "fetch_arena_status", lambda *a, **k: _status(saved_team)
    )
    monkeypatch.setattr(
        arena_mod,
        "fetch_clan_member_ids",
        lambda *a, **k: set(clan_member_ids or ()),
    )
    monkeypatch.setattr(
        arena_mod, "fetch_arena_candidates", lambda *a, **k: list(candidates)
    )
    plan_calls: list[dict] = []

    def fake_query_plans(**kwargs):
        plan_calls.append(kwargs)
        if plans is None:
            return [_plan()]
        return plans(kwargs["defender_id"])

    monkeypatch.setattr(arena_mod, "query_plans", fake_query_plans)
    return plan_calls


class FakeClanClient:
    def __init__(self, detail: dict, *, ok: bool = True, error: str | None = None):
        self.detail = detail
        self.ok = ok
        self.error = error

    def clan_get_info(self):
        return SimpleNamespace(
            is_success=self.ok,
            detail=self.detail,
            error_name=self.error,
            status=SimpleNamespace(value="ERROR"),
        )

    def sleep(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Clan parsing
# ---------------------------------------------------------------------------


def test_fetch_clan_member_ids_parses_members():
    client = FakeClanClient(
        {
            "response": {
                "clan": {
                    "members": {
                        "111": {"id": "111"},
                        "222": {"id": "222"},
                    }
                }
            }
        }
    )
    assert fetch_clan_member_ids(client) == {111, 222}


def test_fetch_clan_member_ids_empty_clan_is_empty():
    assert fetch_clan_member_ids(FakeClanClient({"response": {"clan": {}}})) == set()


def test_fetch_clan_member_ids_empty_members_aborts():
    client = FakeClanClient({"response": {"clan": {"id": "1", "members": {}}}})
    with pytest.raises(ArenaError, match="without members"):
        fetch_clan_member_ids(client)


def test_fetch_clan_member_ids_mixed_unparseable_entry_aborts():
    client = FakeClanClient(
        {
            "response": {
                "clan": {
                    "id": "1",
                    "members": {"111": {"id": "111"}, "bad-member": {}},
                }
            }
        }
    )
    with pytest.raises(ArenaError, match="without a valid player id"):
        fetch_clan_member_ids(client)


def test_fetch_clan_member_ids_missing_clan_aborts():
    with pytest.raises(ArenaError, match="missing 'clan'"):
        fetch_clan_member_ids(FakeClanClient({"response": {}}))


def test_fetch_clan_member_ids_malformed_members_aborts():
    client = FakeClanClient({"response": {"clan": {"members": ["111"]}}})
    with pytest.raises(ArenaError, match="members"):
        fetch_clan_member_ids(client)


def test_fetch_clan_member_ids_unparseable_members_aborts():
    client = FakeClanClient(
        {"response": {"clan": {"members": {"not-an-id": {"name": "Bob"}}}}}
    )
    with pytest.raises(ArenaError):
        fetch_clan_member_ids(client)


@pytest.mark.parametrize("error_name", ["NotFound", "NotAvailable"])
def test_fetch_clan_member_ids_no_clan_error_is_empty(error_name):
    client = FakeClanClient({}, ok=False, error=error_name)
    assert fetch_clan_member_ids(client) == set()


def test_fetch_clan_member_ids_real_error_aborts():
    client = FakeClanClient({}, ok=False, error="NetworkError")
    with pytest.raises(ArenaError):
        fetch_clan_member_ids(client)


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------


def test_select_auto_targets_excludes_guild_below_and_incomplete():
    candidates = [
        _entry(5, 100),
        _entry(3, 200),  # guild
        _entry(4, 300),
        _entry(1, 400, heroes=[1, 2, 3]),  # incomplete lineup
        _entry(20, 500),  # below own rank
    ]
    selected = select_auto_targets(candidates, own_place=10, clan_member_ids={200})

    assert [entry.user_id for entry in selected] == [300, 100]


def test_select_auto_targets_excludes_own_and_attempted_ids():
    candidates = [_entry(3, 100), _entry(4, 200), _entry(5, 300)]
    selected = select_auto_targets(
        candidates,
        own_place=10,
        clan_member_ids=set(),
        own_user_id=100,
        exclude_user_ids={200},
    )

    assert [entry.user_id for entry in selected] == [300]


# ---------------------------------------------------------------------------
# run_arena_auto
# ---------------------------------------------------------------------------


def test_run_arena_auto_dry_run_chooses_highest_non_guild(monkeypatch, capsys):
    _patch_auto(
        monkeypatch,
        candidates=[_entry(4, 200), _entry(5, 222)],
        clan_member_ids={200},
    )
    attacked = []
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *a, **k: attacked.append((a, k)),
    )

    history = run_arena_auto(MagicMock(), execute=False)

    assert attacked == []
    assert len(history) == 1
    assert history[0]["target_user_id"] == 222
    assert history[0]["attacked"] is False
    out = capsys.readouterr().out
    assert "Dry-run: no arenaAttack sent" in out


def test_run_arena_auto_team_matches_plan_and_uses_plan_favor(
    monkeypatch, capsys
):
    saved = ArenaTeam(
        heroes=list(PLAN_HEROES),
        pet=6009,
        favor={1: 6002, 9: 6003},
        banners=[7],
    )
    plan_favor = {1: 6011}
    _patch_auto(
        monkeypatch,
        candidates=[_entry(5, 222)],
        saved_team=saved,
        plans=lambda _defender_id: [_plan(favor=plan_favor)],
    )
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append((target, team, kwargs))
        return ArenaAttackResult(
            dry_run=False,
            accepted=True,
            win=True,
            old_place=10,
            new_place=5,
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_auto(MagicMock(), execute=True, max_attacks=1)

    assert len(calls) == 1
    target, team, _kwargs = calls[0]
    assert target.user_id == 222
    assert team.pet == PLAN_PET
    assert team.banner == PLAN_BANNER
    banner = team.banners[0] if team.banners else None
    assert lineup_key_from_ids(team.heroes, team.pet, banner) == (
        lineup_key_from_ids(PLAN_HEROES, PLAN_PET, PLAN_BANNER)
    )
    # The observed plan's favor wins; saved favor is not inherited.
    assert team.favor == plan_favor
    assert history[0]["attacked"] is True
    assert history[0]["favor"] == plan_favor
    assert history[0]["pet"] == PLAN_PET
    assert history[0]["banner"] == PLAN_BANNER
    out = capsys.readouterr().out
    assert "The observed plan has no favor for hero(es) [2, 3, 4, 5]" in out
    assert "Favor: 1=6011" in out


def test_run_arena_auto_does_not_fall_back_to_saved_favor(monkeypatch, capsys):
    saved = ArenaTeam(
        heroes=list(PLAN_HEROES),
        pet=6009,
        favor={1: 6002},
        banners=[7],
    )
    _patch_auto(
        monkeypatch,
        candidates=[_entry(5, 222)],
        saved_team=saved,
        plans=lambda _defender_id: [_plan(favor={})],
    )
    attacked = []
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *a, **k: attacked.append((a, k)),
    )

    history = run_arena_auto(MagicMock(), execute=False)

    assert attacked == []
    assert history[0]["favor"] == {}
    out = capsys.readouterr().out
    assert "The observed plan has no favor for hero(es) [1, 2, 3, 4, 5]" in out
    assert "Favor: -" in out


def test_run_arena_auto_executes_once(monkeypatch, capsys):
    _patch_auto(monkeypatch, candidates=[_entry(5, 222)])
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append((target, team, kwargs))
        return ArenaAttackResult(
            dry_run=False,
            accepted=True,
            win=True,
            old_place=10,
            new_place=5,
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_auto(MagicMock(), execute=True, max_attacks=1)

    assert len(calls) == 1
    target, team, kwargs = calls[0]
    assert target.user_id == 222
    assert team.heroes == PLAN_HEROES
    assert kwargs["dry_run"] is False
    assert history[0]["attacked"] is True
    assert history[0]["win"] is True
    assert "Attack result: win=True" in capsys.readouterr().out


def test_run_arena_auto_skips_team_that_does_not_match_plan(
    monkeypatch, capsys
):
    plan_calls = _patch_auto(
        monkeypatch,
        candidates=[_entry(4, 333), _entry(5, 222)],
        plans=lambda defender_id: [
            _plan(key="not-the-resolved-lineup")
            if defender_id == "333"
            else _plan()
        ],
    )
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append((target, team, kwargs))
        return ArenaAttackResult(
            dry_run=False,
            accepted=True,
            win=True,
            old_place=10,
            new_place=5,
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_auto(MagicMock(), execute=True, max_attacks=1)

    assert [call[0].user_id for call in calls] == [222]
    assert [call["defender_id"] for call in plan_calls] == ["333", "222"]
    assert history[0]["target_user_id"] == 222
    assert "does not match the plan lineup" in capsys.readouterr().out


def test_run_arena_auto_does_not_attack_without_gate(monkeypatch, capsys):
    _patch_auto(
        monkeypatch,
        candidates=[_entry(5, 222)],
        plans=lambda _defender_id: [_plan(recommended=False)],
    )
    attacked = []
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *a, **k: attacked.append((a, k)),
    )

    history = run_arena_auto(MagicMock(), execute=True)

    assert history == []
    assert attacked == []
    assert "No target has a plan that meets the gate" in capsys.readouterr().out


def test_run_arena_auto_uses_next_recommended_plan(monkeypatch, capsys):
    _patch_auto(
        monkeypatch,
        candidates=[_entry(5, 222)],
        plans=lambda _defender_id: [
            _plan(key="not-the-resolved-lineup"),
            _plan(),
        ],
    )
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append((target, team, kwargs))
        return ArenaAttackResult(
            dry_run=False,
            accepted=True,
            win=True,
            old_place=10,
            new_place=5,
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_auto(MagicMock(), execute=True, max_attacks=1)

    assert len(calls) == 1
    target, team, _kwargs = calls[0]
    assert target.user_id == 222
    assert team.heroes == PLAN_HEROES
    assert team.pet == PLAN_PET
    assert team.banner == PLAN_BANNER
    assert history[0]["attacker_heroes"] == PLAN_HEROES
    assert "trying the next recommended plan" in capsys.readouterr().out


def test_run_arena_auto_falls_back_to_next_target_without_plan(
    monkeypatch, capsys
):
    plan_calls = _patch_auto(
        monkeypatch,
        candidates=[_entry(4, 333), _entry(5, 222)],
        plans=lambda defender_id: (
            [_plan(recommended=False)] if defender_id == "333" else [_plan()]
        ),
    )
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append((target, team, kwargs))
        return ArenaAttackResult(
            dry_run=False,
            accepted=True,
            win=True,
            old_place=10,
            new_place=5,
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    run_arena_auto(MagicMock(), execute=True, max_attacks=2)

    assert [call[0].user_id for call in calls] == [222]
    # 333 (no plan) and 222 (attacked) are both excluded from later loops.
    assert [call["defender_id"] for call in plan_calls] == ["333", "222"]
    assert plan_calls[0]["defender_lineup_key"] == lineup_key_from_ids(
        PLAN_HEROES, PLAN_PET, PLAN_BANNER
    )
    assert "No eligible target" in capsys.readouterr().out


def test_run_arena_auto_forwards_gate_kwargs(monkeypatch):
    plan_calls = _patch_auto(
        monkeypatch, candidates=[_entry(5, 222)], saved_team=None
    )
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *a, **k: ArenaAttackResult(
            dry_run=False, accepted=True, win=True, old_place=10, new_place=5
        ),
    )

    run_arena_auto(
        MagicMock(),
        execute=False,
        min_samples=4,
        min_win_rate=0.75,
        game_version="1.2.3",
    )

    assert len(plan_calls) == 1
    kwargs = plan_calls[0]
    assert kwargs["min_samples"] == 4
    assert kwargs["min_win_rate"] == 0.75
    assert kwargs["game_version"] == "1.2.3"
    assert kwargs["battle_type"] == "arena"
    assert kwargs["defender_id"] == "222"


def test_run_arena_auto_no_stop_on_loss_skips_attempted_target(
    monkeypatch, capsys
):
    _patch_auto(monkeypatch, candidates=[_entry(4, 333), _entry(5, 222)])
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append(target.user_id)
        return ArenaAttackResult(
            dry_run=False,
            accepted=True,
            win=target.user_id == 222,
            old_place=10,
            new_place=4,
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_auto(
        MagicMock(), execute=True, max_attacks=2, stop_on_loss=False
    )

    assert calls == [333, 222]
    assert [record["target_user_id"] for record in history] == [333, 222]
    assert history[0]["win"] is False
    assert history[1]["win"] is True
    assert "Stopping after a loss" not in capsys.readouterr().out


def test_run_arena_auto_not_enough_does_not_count_as_attack(
    monkeypatch, capsys
):
    _patch_auto(monkeypatch, candidates=[_entry(4, 333), _entry(5, 222)])
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append(target.user_id)
        return ArenaAttackResult(
            dry_run=False,
            accepted=False,
            stopped="NotEnough",
            warning="Arena attack not sent: not enough attack attempts (NotEnough).",
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_auto(MagicMock(), execute=True, max_attacks=2)

    # Break after the unsent attack: no second target and no budget consumed.
    assert calls == [333]
    assert len(history) == 1
    assert history[0]["attacked"] is False
    assert history[0]["win"] is None
    assert history[0]["warning"].startswith("Arena attack not sent")
    out = capsys.readouterr().out
    assert "Attack result" not in out
    assert "loss" not in out.lower()


def test_run_arena_auto_propagates_uncertain_attack(monkeypatch):
    _patch_auto(monkeypatch, candidates=[_entry(5, 222)])

    def fake_attack(*_args, **_kwargs):
        raise ArenaAttackUncertain("outcome is unknown")

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    with pytest.raises(ArenaAttackUncertain):
        run_arena_auto(MagicMock(), execute=True)


def test_run_arena_auto_presend_error_falls_through_to_next_target(
    monkeypatch, capsys
):
    _patch_auto(monkeypatch, candidates=[_entry(4, 333), _entry(5, 222)])
    calls: list[int] = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append(target.user_id)
        if target.user_id == 333:
            raise ArenaError("target did not appear in the candidate list")
        return ArenaAttackResult(
            dry_run=False, accepted=True, win=True, old_place=10, new_place=5
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_auto(MagicMock(), execute=True, max_attacks=1)

    assert calls == [333, 222]
    assert [record["target_user_id"] for record in history] == [333, 222]
    assert history[0]["attacked"] is False
    assert "target did not appear" in history[0]["error"]
    assert history[1]["attacked"] is True
    assert "skip rank 4" in capsys.readouterr().out
