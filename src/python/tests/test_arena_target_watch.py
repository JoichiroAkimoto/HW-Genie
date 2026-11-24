"""Tests for `arena target-watch` (pre-fetched targets + one gated attack)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hw_genie.commands import arena as arena_mod
from hw_genie.commands.arena import (
    ArenaAttackResult,
    ArenaError,
    ArenaSnapshot,
    ArenaStatus,
    ArenaTeam,
    ArenaTopEntry,
    run_arena_target_watch,
)
from hw_genie.core.client import ApiAction

TARGET_HEROES = [46, 62, 71, 35, 70]
ATTACK_TEAM = ArenaTeam(
    heroes=[29, 73, 35, 48, 52],
    pet=6008,
    favor={29: 6011, 35: 6006, 48: 6005, 52: 6001, 73: 6004},
    banners=[2],
)
DEFENSE = ArenaTeam(heroes=TARGET_HEROES, pet=6008, banners=[])


def _status(
    *,
    place: int = 100,
    attempts: int | None = 5,
    defense: ArenaTeam | None = DEFENSE,
) -> ArenaStatus:
    return ArenaStatus(
        place=place,
        grand_place=20,
        user_id=111,
        name="Alice",
        attempts=attempts,
        cooldown=0,
        saved_team=None,
        defense_team=defense,
    )


def _entry(
    place: int,
    user_id: int,
    *,
    heroes: list[int] | None = None,
    pet: int | None = 6008,
    banner: int | None = 2,
) -> ArenaTopEntry:
    return ArenaTopEntry(
        place=place,
        user_id=user_id,
        heroes=list(heroes if heroes is not None else TARGET_HEROES),
        pet=pet,
        banner=banner,
    )


def _snapshot(*entries: ArenaTopEntry) -> ArenaSnapshot:
    return ArenaSnapshot(own_place=100, candidates=list(entries), saved_at="now")


def _patch_watch(
    monkeypatch,
    *,
    statuses: list[ArenaStatus],
    candidates: list[ArenaTopEntry],
    snapshot: ArenaSnapshot | None = None,
    presets: dict[str, ArenaTeam] | None = None,
):
    monkeypatch.setattr(
        arena_mod,
        "load_arena_presets",
        lambda *_a, **_k: {"auto": ATTACK_TEAM} if presets is None else presets,
    )
    monkeypatch.setattr(
        arena_mod,
        "capture_arena_snapshot",
        lambda *_a, **_k: snapshot or _snapshot(*candidates[:3]),
    )
    monkeypatch.setattr(arena_mod, "save_arena_snapshot", lambda *_a, **_k: None)
    status_iter = iter(statuses)
    monkeypatch.setattr(
        arena_mod,
        "fetch_arena_status",
        lambda *_a, **_k: next(status_iter),
    )
    monkeypatch.setattr(
        arena_mod,
        "fetch_arena_candidates",
        lambda *_a, **_k: list(candidates),
    )
    monkeypatch.setattr(arena_mod.time, "sleep", lambda *_a, **_k: None)


def test_arena_set_heroes_is_not_an_available_action():
    """The tool must never be able to change the Arena defense team."""
    import ast
    import inspect

    assert "arenaSetHeroes" not in {action.value for action in ApiAction}

    tree = ast.parse(inspect.getsource(arena_mod))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "name"
                and isinstance(value, ast.Constant)
                and value.value == "arenaSetHeroes"
            ):
                pytest.fail("arenaSetHeroes RPC call found in arena module")


def test_target_watch_picks_highest_ranked_match(monkeypatch):
    _patch_watch(
        monkeypatch,
        statuses=[_status(place=100), _status(place=100)],
        candidates=[_entry(80, 333), _entry(70, 222)],
    )
    dry_calls = []

    def fake_attack(client, status, target, team, **kwargs):
        dry_calls.append(target)

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        once=True,
    )

    assert [target.user_id for target in dry_calls] == [222]
    assert history[0]["target_place"] == 70


def test_target_watch_honors_max_polls(monkeypatch, capsys):
    _patch_watch(
        monkeypatch,
        statuses=[
            _status(place=100),
            _status(place=100),
            _status(place=100),
        ],
        candidates=[_entry(90, 222)],
    )

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        max_polls=2,
    )

    assert history == []
    assert "Reached --max-polls 2" in capsys.readouterr().out


def test_target_watch_resumes_after_presend_error(monkeypatch, capsys):
    _patch_watch(
        monkeypatch,
        statuses=[
            _status(),
            _status(),
            _status(),
            _status(),
        ],
        candidates=[_entry(80, 222)],
    )
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append(target.user_id)
        if len(calls) == 1:
            raise ArenaError("target is outside the range window")
        return ArenaAttackResult(
            dry_run=False, accepted=True, win=True, old_place=100, new_place=80
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        execute=True,
        max_polls=3,
    )

    assert calls == [222, 222]
    assert history[0]["error"] == "target is outside the range window"
    assert history[1]["attacked"] is True
    assert "Resuming the watch (1/3)" in capsys.readouterr().out


def test_target_watch_defense_change_ignores_hero_order(monkeypatch, capsys):
    reordered = ArenaTeam(heroes=list(reversed(TARGET_HEROES)), pet=6008, banners=[])
    _patch_watch(
        monkeypatch,
        statuses=[
            _status(defense=DEFENSE),
            _status(defense=DEFENSE),
            _status(defense=reordered),
        ],
        candidates=[_entry(80, 222)],
    )
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *a, **k: ArenaAttackResult(
            dry_run=False, accepted=True, win=True, old_place=100, new_place=80
        ),
    )

    run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        execute=True,
        once=True,
    )

    assert "Defense team changed" not in capsys.readouterr().out


def test_target_watch_dry_run_matches_and_does_not_attack(monkeypatch, capsys):
    _patch_watch(
        monkeypatch,
        statuses=[_status(), _status()],
        candidates=[
            _entry(80, 222, heroes=[70, 35, 71, 62, 46], pet=6001, banner=9)
        ],
    )
    dry_calls = []

    def fake_attack(client, status, target, team, **kwargs):
        dry_calls.append((target, kwargs))

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        once=True,
    )

    assert len(dry_calls) == 1
    assert dry_calls[0][0].user_id == 222
    assert dry_calls[0][1]["dry_run"] is True
    assert dry_calls[0][1]["min_gap"] == 15
    assert history[0]["target_user_id"] == 222
    assert history[0]["gap"] == 20
    assert history[0]["attacked"] is False
    out = capsys.readouterr().out
    assert "Dry-run verified; rerun with --execute" in out
    assert "target is rank 80, i.e. 20 rank(s) above you (required >= 15)" in out


def test_target_watch_executes_once_and_verifies_defense(monkeypatch, capsys):
    _patch_watch(
        monkeypatch,
        statuses=[_status(), _status(), _status()],
        candidates=[_entry(80, 222)],
    )
    calls = []

    def fake_attack(client, status, target, team, **kwargs):
        calls.append((target, team, kwargs))
        return ArenaAttackResult(
            dry_run=False,
            accepted=True,
            win=True,
            old_place=100,
            new_place=80,
        )

    monkeypatch.setattr(arena_mod, "run_arena_attack", fake_attack)

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        execute=True,
        once=True,
    )

    assert len(calls) == 1
    target, team, kwargs = calls[0]
    assert target.user_id == 222
    assert team == ATTACK_TEAM
    assert kwargs["dry_run"] is False
    assert history[0]["attacked"] is True
    assert history[0]["win"] is True
    out = capsys.readouterr().out
    assert "Attack result: win=True" in out
    assert "Defense team changed" not in out


def test_target_watch_waits_when_gap_is_too_small(monkeypatch, capsys):
    _patch_watch(
        monkeypatch,
        statuses=[_status(place=100), _status(place=100)],
        candidates=[_entry(90, 222)],
    )
    attacked = []
    monkeypatch.setattr(
        arena_mod, "run_arena_attack", lambda *a, **k: attacked.append((a, k))
    )

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        once=True,
    )

    assert history == []
    assert attacked == []
    out = capsys.readouterr().out
    assert "10 rank(s) above you < 15" in out


def test_target_watch_never_targets_same_or_lower_rank(monkeypatch, capsys):
    _patch_watch(
        monkeypatch,
        statuses=[_status(place=100), _status(place=100)],
        candidates=[_entry(110, 222)],
    )
    monitored = []
    monkeypatch.setattr(
        arena_mod, "run_arena_attack", lambda *a, **k: monitored.append((a, k))
    )

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        min_gap=0,
        once=True,
    )

    assert history == []
    assert monitored == []
    assert "is not above your rank 100" in capsys.readouterr().out


def test_target_watch_requires_preset(monkeypatch):
    _patch_watch(
        monkeypatch,
        statuses=[_status()],
        candidates=[_entry(80, 222)],
        presets={},
    )
    with pytest.raises(ArenaError, match="preset 'auto' was not found"):
        run_arena_target_watch(
            MagicMock(),
            "Alice",
            team_name="auto",
            target_heroes=TARGET_HEROES,
            once=True,
        )


def test_target_watch_reuses_saved_snapshot(monkeypatch):
    snapshot = _snapshot(_entry(80, 222))
    _patch_watch(
        monkeypatch,
        statuses=[_status(), _status()],
        candidates=[_entry(80, 222)],
        snapshot=snapshot,
    )
    monkeypatch.setattr(
        arena_mod,
        "capture_arena_snapshot",
        lambda *_a, **_k: pytest.fail("should not capture a new snapshot"),
    )
    monkeypatch.setattr(
        arena_mod,
        "load_arena_snapshot",
        lambda *_a, **_k: snapshot,
    )
    attacked = []
    monkeypatch.setattr(
        arena_mod, "run_arena_attack", lambda *a, **k: attacked.append((a, k))
    )

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        from_snapshot=True,
        once=True,
    )

    assert history[0]["target_user_id"] == 222
    assert len(attacked) == 1
    assert attacked[0][1]["dry_run"] is True


def test_target_watch_warns_when_defense_changed(monkeypatch, capsys):
    changed = ArenaTeam(heroes=[1, 2, 3, 4, 5], pet=6001, banners=[])
    _patch_watch(
        monkeypatch,
        statuses=[
            _status(defense=DEFENSE),
            _status(defense=DEFENSE),
            _status(defense=changed),
        ],
        candidates=[_entry(80, 222)],
    )
    monkeypatch.setattr(
        arena_mod,
        "run_arena_attack",
        lambda *a, **k: ArenaAttackResult(
            dry_run=False, accepted=True, win=True, old_place=100, new_place=80
        ),
    )

    run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        execute=True,
        once=True,
    )

    out = capsys.readouterr().out
    assert "Defense team changed during the attack" in out
    assert "No arenaSetHeroes call was made" in out


def test_target_watch_stops_without_attempts(monkeypatch, capsys):
    _patch_watch(
        monkeypatch,
        statuses=[_status(attempts=0), _status(attempts=0)],
        candidates=[_entry(80, 222)],
    )

    history = run_arena_target_watch(
        MagicMock(),
        "Alice",
        team_name="auto",
        target_heroes=TARGET_HEROES,
        once=True,
    )

    assert history == []
    assert "No arena attack attempts left" in capsys.readouterr().out
