"""Battle log corpus: canonical hashing, import, and matchup statistics.

Data source is the ``battleGetByType`` RPC. Each replay carries full
attacker/defender configs, banner effects, ``seed`` and ``result``, so the
corpus can answer "which lineup beat this defender?" without simulating.

Two keys are derived per team:

* ``lineup_key`` — heroes (in replay order) + pet + banner only. Power and
  stat changes are ignored so past battles stay comparable after upgrades.
* ``config_hash`` — the full combat-relevant config, stats included. Used for
  exact replay/cache lookups.

The hero order is taken from the replay dict insertion order. The game does not
expose an explicit position field, so this is the only stable ordering signal
available; treat it as an assumption when interpreting lineup keys.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from .database import (
    Account,
    BattleComparison,
    BattleRecord,
    TeamConfig,
    TeamConfigHero,
    _wal_io_lock,
    get_session_local,
    get_write_session_local,
    is_transient_db_error,
    retry_on_wal_contention,
)

logger = logging.getLogger(__name__)

LINEUP_PREFIX = b"hwlineup:v1:"
CONFIG_PREFIX = b"hwconfig:v1:"
MATCHUP_PREFIX = b"hwmatchup:v1:"

# Fields that do not affect combat and would only add noise to exact matches.
_VOLATILE_HERO_FIELDS = frozenset({"xp", "titanCoinsSpent", "name"})

# Pet ids in the game data start at 6000; heroes are below it. The replay marks
# pets with ``type: "pet"``, but that field is used as a fallback when absent.
PET_ID_FLOOR = 6000


class BattleLogError(Exception):
    """Raised when a battle log cannot be imported."""


@dataclass(frozen=True)
class TeamComponents:
    config: dict[str, Any]
    config_hash: str
    lineup_key: str
    format: str
    heroes_summary: str
    pet_id: int | None
    banner_id: int | None
    heroes: list[dict[str, Any]]
    completeness: str


@dataclass(frozen=True)
class ParsedBattle:
    source_record_id: str
    battle_type: str
    occurred_at: datetime
    ended_at: datetime | None
    game_version: str | None
    seed: int | None
    win: bool | None
    stars: int | None
    attacker_player_id: str | None
    defender_player_id: str | None
    attacker: TeamComponents
    defender: TeamComponents
    matchup_key: str
    completeness: str


@dataclass
class ImportStats:
    total: int = 0
    inserted: int = 0
    duplicates: int = 0
    upgraded: int = 0
    team_configs_inserted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchupStat:
    battle_type: str
    defender_player_id: str | None
    matchup_key: str
    attacker_heroes: str
    defender_heroes: str
    battles: int
    wins: int
    win_rate: float
    last_seen: datetime | None


@dataclass(frozen=True)
class PlanCandidate:
    """An attacker lineup ranked against one defender lineup."""

    attacker_lineup_key: str
    heroes_summary: str
    pet_id: int | None
    banner_id: int | None
    battles: int
    wins: int
    win_rate: float
    wilson_low: float
    last_seen: datetime | None
    tier: str
    recommended: bool
    # hero id -> patron pet id, taken from the most recent winning config for
    # this lineup. Used to reproduce the attacker's favor in arenaAttack.
    favor: dict[int, int] = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class SimulationComparison:
    """Parity result for one re-simulated replay."""

    battle_id: str
    actual_win: bool | None
    actual_stars: int | None
    simulated_win: bool | None
    simulated_stars: int | None
    matched: bool
    mismatch_type: str
    delta_stars: int
    elapsed_seconds: float = 0.0
    sweep_seeds: int = 0
    sweep_wins: int = 0
    sweep_win_rate: float = 0.0


@dataclass
class PruneStats:
    """How many old battle rows (and comparisons) a prune matched/deleted."""

    before: datetime
    matched: int = 0
    deleted: int = 0
    comparisons: int = 0


def _canonical_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(prefix: bytes, payload: str) -> str:
    return hashlib.sha256(prefix + payload.encode("utf-8")).hexdigest()


def _lineup_key(hero_ids: list[int], pet_id: int | None, banner_id: int | None) -> str:
    obj = {
        "v": 1,
        "format": f"team{len(hero_ids)}",
        "heroes": [int(h) for h in hero_ids],
        "pet": pet_id,
        "banner": banner_id,
    }
    return _sha256(LINEUP_PREFIX, _canonical_dumps(obj))


def lineup_key_from_ids(
    heroes: list[int], pet_id: int | None, banner_id: int | None
) -> str:
    """Compute the lineup key for a live candidate that has no stats.

    Mirrors :func:`build_team`: non-positive pet/banner ids are the game's
    "absent" sentinel and are normalized to ``None`` so live candidates match
    keys derived from stored replays.
    """
    if pet_id is not None and pet_id <= 0:
        pet_id = None
    if banner_id is not None and banner_id <= 0:
        banner_id = None
    return _lineup_key(heroes, pet_id, banner_id)


def wilson_lower_bound(wins: int, battles: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval for a win rate.

    Prevents small samples (e.g. 1-0) from outranking well-supported lineups.
    """
    if battles <= 0:
        return 0.0
    p = wins / battles
    denom = 1.0 + (z * z) / battles
    centre = p + (z * z) / (2 * battles)
    margin = z * math.sqrt((p * (1 - p) + (z * z) / (4 * battles)) / battles)
    return max(0.0, (centre - margin) / denom)


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_epoch(value: Any) -> datetime | None:
    ts = _as_int(value)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _normalize_value(value: Any) -> Any:
    """Normalize nested JSON values so equivalent builds hash identically.

    Dict keys become strings, lists are normalized recursively, and integral
    floats (``1000.0``) collapse to ints so numeric serialization differences
    between builds do not produce spurious ``config_hash`` changes.
    """
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _normalize_slots(value: Any) -> Any:
    """Normalize ``slots`` across builds (list vs dict serialization)."""
    if isinstance(value, list):
        return {str(i): _normalize_value(v) for i, v in enumerate(value)}
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in value.items()}
    return value


def _normalize_hero(hero: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in hero.items():
        if key in _VOLATILE_HERO_FIELDS:
            continue
        out[key] = _normalize_slots(value) if key == "slots" else _normalize_value(value)
    return out


def _banner_id(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("id")
    banner_id = _as_int(value)
    # The API reports "no banner" as 0 in some builds; normalize it to NULL so
    # it matches replays that omit the banner entirely.
    if banner_id is None or banner_id <= 0:
        return None
    return banner_id


def _split_team(team: dict[str, Any]) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, Any] | None]:
    heroes: list[tuple[int, dict[str, Any]]] = []
    pet: dict[str, Any] | None = None
    for key, value in team.items():
        if not isinstance(value, dict):
            continue
        hero_id = _as_int(value.get("id"), _as_int(key))
        is_pet = value.get("type") == "pet" or (
            value.get("type") is None and hero_id is not None and hero_id >= PET_ID_FLOOR
        )
        if is_pet:
            pet = value
            continue
        if hero_id is None:
            continue
        heroes.append((hero_id, value))
    return heroes, pet


def build_team(team: dict[str, Any], banner_id: int | None) -> TeamComponents:
    """Build hashes and slot rows for one attacker/defender team."""
    heroes, pet = _split_team(team)
    hero_ids = [hero_id for hero_id, _ in heroes]
    pet_id = _as_int(pet.get("id")) if pet else None
    if pet_id is None:
        for _, hero in heroes:
            pet_id = _as_int(hero.get("petId"))
            if pet_id:
                break
    # "No pet" / "no banner" is reported as 0 by some API builds; treat it as
    # absent so the lineup key matches builds that omit the field.
    if pet_id is not None and pet_id <= 0:
        pet_id = None
    if banner_id is not None and banner_id <= 0:
        banner_id = None
    fmt = f"team{len(hero_ids)}"
    heroes_summary = ",".join(str(h) for h in hero_ids)

    config_obj = {
        "v": 1,
        "format": fmt,
        "heroes": [_normalize_hero(hero) for _, hero in heroes],
        "pet": _normalize_hero(pet) if pet else None,
        "banner": banner_id,
    }

    complete = bool(heroes) and all("hp" in hero for _, hero in heroes)
    slot_rows = [
        {
            "slot": slot,
            "hero_id": hero_id,
            "level": _as_int(hero.get("level")),
            "star": _as_int(hero.get("star")),
            "power": _as_int(hero.get("power")),
        }
        for slot, (hero_id, hero) in enumerate(heroes)
    ]

    return TeamComponents(
        config=config_obj,
        config_hash=_sha256(CONFIG_PREFIX, _canonical_dumps(config_obj)),
        lineup_key=_lineup_key(hero_ids, pet_id, banner_id),
        format=fmt,
        heroes_summary=heroes_summary,
        pet_id=pet_id,
        banner_id=banner_id,
        heroes=slot_rows,
        completeness="full" if complete else "ids_only",
    )


def matchup_key(battle_type: str, attacker: TeamComponents, defender: TeamComponents) -> str:
    payload = {
        "v": 1,
        "type": battle_type,
        "attacker": attacker.lineup_key,
        "defender": defender.lineup_key,
    }
    return _sha256(MATCHUP_PREFIX, _canonical_dumps(payload))


def _parse_replay(replay: Any, battle_type: str | None = None) -> ParsedBattle | None:
    if not isinstance(replay, dict):
        return None
    attackers = replay.get("attackers")
    defenders = replay.get("defenders")
    if not isinstance(attackers, dict):
        return None
    multi_defender = False
    defender_team_count = 1
    if isinstance(defenders, list):
        teams = [entry for entry in defenders if isinstance(entry, dict) and entry]
        multi_defender = len(teams) > 1
        defender_team_count = len(teams)
        defender_team = teams[0] if teams else {}
    elif isinstance(defenders, dict):
        defender_team = defenders
    else:
        defender_team = {}
    if not defender_team:
        return None

    effects = replay.get("effects") if isinstance(replay.get("effects"), dict) else {}
    attacker = build_team(attackers, _banner_id(effects.get("attackersBanner")))
    defender = build_team(defender_team, _banner_id(effects.get("defendersBanner")))

    result = replay.get("result") if isinstance(replay.get("result"), dict) else {}
    # The replay usually carries its own ``type``; fall back to the requested
    # type (e.g. ``grand``) instead of assuming arena.
    battle_type = str(replay.get("type") or battle_type or "arena")
    happened = _parse_epoch(replay.get("startTime")) or datetime.now(timezone.utc).replace(tzinfo=None)
    ended = _parse_epoch(replay.get("endTime"))
    seed = _as_int(replay.get("seed"))
    record_id = _as_optional_str(replay.get("id"))
    if record_id is None:
        record_id = (
            f"{_as_optional_str(replay.get('userId')) or ''}-"
            f"{_as_optional_str(replay.get('typeId')) or ''}-"
            f"{replay.get('startTime') or ''}-{seed if seed is not None else ''}"
        )
    game_version = _as_optional_str(result.get("serverVersion"))
    if multi_defender:
        logger.warning(
            "Replay %s has %d defender teams; only the first is stored.",
            record_id,
            defender_team_count,
        )
    completeness = (
        "full"
        if attacker.completeness == "full" and defender.completeness == "full"
        else "ids_only"
    )
    if multi_defender:
        completeness = "partial"

    return ParsedBattle(
        source_record_id=record_id,
        battle_type=battle_type,
        occurred_at=happened,
        ended_at=ended,
        game_version=game_version,
        seed=seed,
        win=bool(result["win"]) if "win" in result else None,
        stars=_as_int(result.get("stars")),
        attacker_player_id=_as_optional_str(replay.get("userId")),
        defender_player_id=_as_optional_str(replay.get("typeId")),
        attacker=attacker,
        defender=defender,
        matchup_key=matchup_key(battle_type, attacker, defender),
        completeness=completeness,
    )


def parse_battle_get_response(detail: Any, battle_type: str | None = None) -> list[ParsedBattle]:
    """Parse a ``battleGetByType`` response detail into battle records.

    ``detail`` is the ``HWResponse.detail`` (the ``result`` object). Both the
    wrapped ``{"response": {...}}`` and bare response shapes are accepted.
    ``battle_type`` is used when a replay omits its own ``type`` field.
    """
    return [parsed for _, parsed in parse_battle_replays(detail, battle_type)]


def parse_battle_replays(
    detail: Any, battle_type: str | None = None
) -> list[tuple[dict[str, Any], ParsedBattle]]:
    """Return ``(raw replay, ParsedBattle)`` pairs.

    The raw replay is kept so the oracle can re-simulate with the original
    payload (including ``effects`` and ``seed``) without storing it in the DB.
    """
    if not isinstance(detail, dict):
        return []
    response = detail.get("response", detail)
    if not isinstance(response, dict):
        return []
    replays = response.get("replays")
    if not isinstance(replays, list):
        return []
    pairs: list[tuple[dict[str, Any], ParsedBattle]] = []
    for raw in replays:
        if not isinstance(raw, dict):
            continue
        parsed = _parse_replay(raw, battle_type)
        if parsed is not None:
            pairs.append((raw, parsed))
    return pairs


def build_battle_payload(
    replay: dict[str, Any], battle_type: str | None = None
) -> dict[str, Any]:
    """Build the payload the in-page oracle expects from a raw replay.

    Start from the raw replay so fields this module does not know about are
    forwarded unchanged; only the fields the oracle requires are normalized.
    """
    payload = dict(replay)
    payload["seed"] = _as_int(replay.get("seed"), 0) or 0
    payload["startTime"] = _as_int(replay.get("startTime"), 0) or 0
    if not payload.get("type"):
        payload["type"] = battle_type or "arena"
    payload.setdefault("progress", [])
    payload.setdefault("reward", [])
    return payload


def _resolve_account(db, alias: str) -> Account:
    account = db.query(Account).filter(Account.alias == alias).order_by(Account.id).first()
    if account is None:
        target = alias.strip().lower()
        account = next(
            (
                row
                for row in db.query(Account).all()
                if (row.alias or "").strip().lower() == target
            ),
            None,
        )
    if account is None:
        raise BattleLogError(f"Account not found: {alias!r}")
    return account


def _existing_source_ids(db, source: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    # Observation sources (api_import / hwa_import / attack) dedupe globally by
    # replay id so importing the same battle from HWA and the API does not
    # double-count. Simulations are a separate stream keyed by their own source.
    found: set[str] = set()
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        query = db.query(BattleRecord.source_record_id).filter(
            BattleRecord.source_record_id.in_(chunk)
        )
        if source == "sim":
            query = query.filter(BattleRecord.source == "sim")
        else:
            query = query.filter(BattleRecord.source != "sim")
        found.update(row[0] for row in query.all())
    return found


def _existing_observation_rows(db, ids: list[str]) -> dict[str, BattleRecord]:
    """Existing non-sim rows for ``ids`` keyed by replay id."""
    if not ids:
        return {}
    found: dict[str, BattleRecord] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        rows = (
            db.query(BattleRecord)
            .filter(
                BattleRecord.source != "sim",
                BattleRecord.source_record_id.in_(chunk),
            )
            .all()
        )
        for row in rows:
            found[row.source_record_id] = row
    return found


def _apply_battle_row(
    row: BattleRecord,
    battle: ParsedBattle,
    *,
    account_id: int,
    source: str,
    player_id: str | None,
    authoritative: bool,
    engine: str,
    attacker_config_id: int | None,
    defender_config_id: int | None,
) -> None:
    """Copy all replay-derived fields onto a battle row (insert or upgrade)."""
    row.account_id = account_id
    row.battle_type = battle.battle_type
    row.source = source
    row.source_record_id = battle.source_record_id
    row.battle_id = battle.source_record_id
    row.occurred_at = battle.occurred_at
    row.ended_at = battle.ended_at
    row.game_version = battle.game_version
    row.attacker_config_id = attacker_config_id
    row.defender_config_id = defender_config_id
    row.attacker_lineup_key = battle.attacker.lineup_key
    row.defender_lineup_key = battle.defender.lineup_key
    row.matchup_key = battle.matchup_key
    row.attacker_player_id = battle.attacker_player_id
    row.defender_player_id = battle.defender_player_id
    row.viewer_role = _viewer_role(battle, player_id)
    row.seed = battle.seed
    row.win = battle.win
    row.stars = battle.stars
    row.deterministic = None
    row.authoritative = authoritative
    row.engine = engine
    row.completeness = battle.completeness


def _upsert_team(db, team: TeamComponents, cache: dict[str, TeamConfig]) -> tuple[TeamConfig, bool]:
    cached = cache.get(team.config_hash)
    if cached is not None:
        return cached, False
    row = db.query(TeamConfig).filter(TeamConfig.config_hash == team.config_hash).first()
    created = False
    if row is None:
        row = TeamConfig(
            config_hash=team.config_hash,
            lineup_key=team.lineup_key,
            format=team.format,
            heroes_summary=team.heroes_summary,
            pet_id=team.pet_id,
            banner_id=team.banner_id,
            config_json=team.config,
            completeness=team.completeness,
        )
        db.add(row)
        db.flush()
        created = True
        for hero in team.heroes:
            db.add(
                TeamConfigHero(
                    team_config_id=row.id,
                    slot=hero["slot"],
                    hero_id=hero["hero_id"],
                    level=hero["level"],
                    star=hero["star"],
                    power=hero["power"],
                )
            )
    cache[team.config_hash] = row
    return row, created


def _viewer_role(battle: ParsedBattle, player_id: str | None) -> str:
    """Which side of the replay the importing account was on."""
    if player_id is None:
        return "unknown"
    if battle.attacker_player_id == player_id:
        return "attacker"
    if battle.defender_player_id == player_id:
        return "defender"
    return "unknown"


def import_battles(
    account_alias: str,
    battles: Iterable[ParsedBattle],
    *,
    source: str = "api_import",
    authoritative: bool = True,
    engine: str = "game-server",
    dry_run: bool = False,
) -> ImportStats:
    """Insert battle rows and their team configs idempotently.

    Duplicates are detected by ``(source, source_record_id)``. The unique key
    is global, so the first import wins and an identical replay imported under
    a second account is reported as a duplicate (its ``team_configs`` are still
    usable from the first row). An HWA row that only has a partial config is
    upgraded in place (source becomes ``api_import``) when the *full* API replay
    with the same id arrives; an ``ids_only`` replay is treated as a duplicate
    so it cannot replace HWA data with less information. The upgrade keeps the
    original ``account_id`` / ``viewer_role``. ``dry_run`` performs all reads
    but rolls back, reporting what would be inserted.

    The batch runs in one outer transaction. Each battle is inserted inside its
    own SAVEPOINT so a non-transient failure (e.g. ``IntegrityError``) rolls
    back only that battle and the rest of the batch keeps importing. Transient
    DB errors (WAL contention, dropped streams) are re-raised so
    ``retry_on_wal_contention`` retries the whole batch with a fresh session.
    """
    items = list(battles)

    def _attempt() -> ImportStats:
        stats = ImportStats(total=len(items))
        with _wal_io_lock:
            with get_write_session_local()() as db:
                if not db.in_transaction():
                    db.begin()
                # pysqlite (legacy isolation mode) defers the real BEGIN until
                # the first DML, so a SAVEPOINT issued before any write would be
                # the outermost one and its RELEASE would commit. Force the
                # transaction to start at the driver level when needed so
                # per-battle savepoints and the final dry-run rollback behave.
                connection = db.connection()
                dbapi_connection = connection.connection.dbapi_connection
                if getattr(dbapi_connection, "in_transaction", True) is False:
                    connection.exec_driver_sql("BEGIN")
                account = _resolve_account(db, account_alias)
                account_id = int(account.id)
                player_id = _as_optional_str(account.player_id)
                ids = [b.source_record_id for b in items]
                # Observation sources share one id space; keep the row objects
                # so an HWA partial row can be upgraded in place by a later API
                # import. Simulations are a separate stream.
                existing_rows = (
                    _existing_observation_rows(db, ids) if source != "sim" else {}
                )
                existing = (
                    set(existing_rows)
                    if source != "sim"
                    else _existing_source_ids(db, source, ids)
                )
                seen: set[str] = set()
                cache: dict[str, TeamConfig] = {}
                for battle in items:
                    record_id = battle.source_record_id
                    if record_id in seen:
                        stats.duplicates += 1
                        continue

                    existing_row = existing_rows.get(record_id)
                    if existing_row is not None:
                        # An HWA row only has the compact/partial config. When
                        # the full API replay arrives, replace it in place so
                        # stats/config FKs point at the complete team configs.
                        # Only a *full* API replay may replace HWA data: an
                        # ids_only replay would discard the HWA hero stats that
                        # are already stored.
                        upgrade = (
                            source == "api_import"
                            and existing_row.source == "hwa_import"
                            and (existing_row.completeness or "") != "full"
                            and battle.completeness == "full"
                        )
                        if not upgrade:
                            stats.duplicates += 1
                            continue
                        seen.add(record_id)
                        cached_before = set(cache)
                        created = 0
                        try:
                            with db.begin_nested():
                                attacker_row, attacker_created = _upsert_team(
                                    db, battle.attacker, cache
                                )
                                defender_row, defender_created = _upsert_team(
                                    db, battle.defender, cache
                                )
                                created = int(attacker_created) + int(
                                    defender_created
                                )
                                # Keep the original importer/ownership: stats
                                # are involvement-based (not account_id-based),
                                # and battle_comparisons FKs point at this row,
                                # so flipping ownership would only misattribute
                                # the battle without changing any result.
                                original_account_id = existing_row.account_id
                                original_viewer_role = existing_row.viewer_role
                                _apply_battle_row(
                                    existing_row,
                                    battle,
                                    account_id=account_id,
                                    source=source,
                                    player_id=player_id,
                                    authoritative=authoritative,
                                    engine=engine,
                                    attacker_config_id=attacker_row.id,
                                    defender_config_id=defender_row.id,
                                )
                                existing_row.account_id = original_account_id
                                existing_row.viewer_role = original_viewer_role
                                db.flush()
                            stats.upgraded += 1
                            stats.team_configs_inserted += created
                            existing_rows[record_id] = existing_row
                        except Exception as exc:  # keep importing the rest
                            if is_transient_db_error(exc):
                                raise
                            logger.exception(
                                "Failed to upgrade battle %s", record_id
                            )
                            for key in set(cache) - cached_before:
                                cache.pop(key, None)
                            stats.skipped += 1
                            detail = (
                                exc.orig
                                if isinstance(exc, IntegrityError) and exc.orig
                                else exc
                            )
                            stats.errors.append(f"{record_id}: {detail}")
                        continue

                    if record_id in existing:
                        stats.duplicates += 1
                        continue
                    seen.add(record_id)
                    cached_before = set(cache)
                    created = 0
                    try:
                        with db.begin_nested():
                            attacker_row, attacker_created = _upsert_team(
                                db, battle.attacker, cache
                            )
                            defender_row, defender_created = _upsert_team(
                                db, battle.defender, cache
                            )
                            created = int(attacker_created) + int(defender_created)
                            row = BattleRecord()
                            _apply_battle_row(
                                row,
                                battle,
                                account_id=account_id,
                                source=source,
                                player_id=player_id,
                                authoritative=authoritative,
                                engine=engine,
                                attacker_config_id=attacker_row.id,
                                defender_config_id=defender_row.id,
                            )
                            db.add(row)
                            db.flush()
                        stats.inserted += 1
                        stats.team_configs_inserted += created
                    except Exception as exc:  # keep importing the rest
                        if is_transient_db_error(exc):
                            raise
                        # Unexpected implementation errors should leave a trace,
                        # but a single bad battle must not abort the import.
                        logger.exception("Failed to import battle %s", record_id)
                        # The savepoint rolled back only this battle; drop any
                        # team-config instances it cached so later battles do
                        # not reference rows that no longer exist.
                        for key in set(cache) - cached_before:
                            cache.pop(key, None)
                        stats.skipped += 1
                        detail = (
                            exc.orig
                            if isinstance(exc, IntegrityError) and exc.orig
                            else exc
                        )
                        stats.errors.append(f"{record_id}: {detail}")
                if dry_run:
                    db.rollback()
                else:
                    db.commit()
                return stats

    return retry_on_wal_contention(_attempt, logger=logger)


def _existing_config_ids(db, hashes: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for start in range(0, len(hashes), 500):
        chunk = hashes[start : start + 500]
        rows = (
            db.query(TeamConfig.config_hash, TeamConfig.id)
            .filter(TeamConfig.config_hash.in_(chunk))
            .all()
        )
        for row in rows:
            found[row.config_hash] = row.id
    return found


def bulk_import_battles(
    account_alias: str,
    battles: Iterable[ParsedBattle],
    *,
    source: str,
    engine: str,
    authoritative: bool = True,
    dry_run: bool = False,
) -> ImportStats:
    """Bulk insert one batch with a single commit (no per-battle savepoints).

    Intended for large one-shot imports (HWA snapshots). A unique conflict
    aborts the batch; ``retry_on_wal_contention`` retries it, and the retry
    dedupes rows committed by a previous partial attempt.
    """
    items = list(battles)

    def _attempt() -> ImportStats:
        stats = ImportStats(total=len(items))
        if not items:
            return stats
        with _wal_io_lock:
            with get_write_session_local()() as db:
                if not db.in_transaction():
                    db.begin()
                connection = db.connection()
                dbapi_connection = connection.connection.dbapi_connection
                if getattr(dbapi_connection, "in_transaction", True) is False:
                    connection.exec_driver_sql("BEGIN")

                account = _resolve_account(db, account_alias)
                player_id = _as_optional_str(account.player_id)
                existing = _existing_source_ids(
                    db, source, [battle.source_record_id for battle in items]
                )
                # A batch can repeat an id (e.g. a malformed snapshot); keep
                # only the first occurrence so rows are not inserted twice.
                seen: set[str] = set()
                new_battles: list[ParsedBattle] = []
                duplicates = 0
                for battle in items:
                    record_id = battle.source_record_id
                    if record_id in existing or record_id in seen:
                        duplicates += 1
                        continue
                    seen.add(record_id)
                    new_battles.append(battle)
                stats.duplicates = duplicates
                if not new_battles:
                    db.rollback()
                    return stats

                configs: dict[str, TeamComponents] = {}
                for battle in new_battles:
                    configs.setdefault(battle.attacker.config_hash, battle.attacker)
                    configs.setdefault(battle.defender.config_hash, battle.defender)
                config_ids = _existing_config_ids(db, list(configs))
                new_configs = {
                    config_hash: team
                    for config_hash, team in configs.items()
                    if config_hash not in config_ids
                }
                stats.team_configs_inserted = len(new_configs)

                if dry_run:
                    db.rollback()
                    stats.inserted = len(new_battles)
                    return stats

                config_rows = [
                    {
                        "config_hash": config_hash,
                        "lineup_key": team.lineup_key,
                        "format": team.format,
                        "heroes_summary": team.heroes_summary,
                        "pet_id": team.pet_id,
                        "banner_id": team.banner_id,
                        "config_json": team.config,
                        "completeness": team.completeness,
                    }
                    for config_hash, team in new_configs.items()
                ]
                if config_rows:
                    db.execute(TeamConfig.__table__.insert(), config_rows)
                    config_ids.update(_existing_config_ids(db, list(new_configs)))

                hero_rows = []
                for config_hash, team in new_configs.items():
                    team_config_id = config_ids[config_hash]
                    for hero in team.heroes:
                        hero_rows.append(
                            {
                                "team_config_id": team_config_id,
                                "slot": hero["slot"],
                                "hero_id": hero["hero_id"],
                                "level": hero["level"],
                                "star": hero["star"],
                                "power": hero["power"],
                            }
                        )
                if hero_rows:
                    db.execute(TeamConfigHero.__table__.insert(), hero_rows)

                battle_rows = [
                    {
                        "account_id": int(account.id),
                        "battle_type": battle.battle_type,
                        "source": source,
                        "source_record_id": battle.source_record_id,
                        "battle_id": battle.source_record_id,
                        "occurred_at": battle.occurred_at,
                        "ended_at": battle.ended_at,
                        "game_version": battle.game_version,
                        "attacker_config_id": config_ids[
                            battle.attacker.config_hash
                        ],
                        "defender_config_id": config_ids[
                            battle.defender.config_hash
                        ],
                        "attacker_lineup_key": battle.attacker.lineup_key,
                        "defender_lineup_key": battle.defender.lineup_key,
                        "matchup_key": battle.matchup_key,
                        "attacker_player_id": battle.attacker_player_id,
                        "defender_player_id": battle.defender_player_id,
                        "viewer_role": _viewer_role(battle, player_id),
                        "seed": battle.seed,
                        "win": battle.win,
                        "stars": battle.stars,
                        "deterministic": None,
                        "authoritative": authoritative,
                        "engine": engine,
                        "completeness": battle.completeness,
                    }
                    for battle in new_battles
                ]
                if battle_rows:
                    db.execute(BattleRecord.__table__.insert(), battle_rows)
                db.commit()
                stats.inserted = len(new_battles)
                return stats

    return retry_on_wal_contention(_attempt, logger=logger)


def _chunks(values: list[int], size: int = 500):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def prune_battles(before: datetime, *, dry_run: bool = False) -> PruneStats:
    """Delete battle rows (and their comparisons) older than ``before``.

    ``team_configs`` are left in place: they may still be referenced by newer
    battles. They are small and can be reclaimed by a future vacuum/GC pass.
    """
    if before.tzinfo is not None:
        before = before.astimezone(timezone.utc).replace(tzinfo=None)

    def _attempt() -> PruneStats:
        with _wal_io_lock:
            with get_write_session_local()() as db:
                if not db.in_transaction():
                    db.begin()
                ids = [
                    row[0]
                    for row in db.query(BattleRecord.id)
                    .filter(BattleRecord.occurred_at < before)
                    .all()
                ]
                stats = PruneStats(before=before, matched=len(ids))
                if not ids:
                    db.rollback()
                    return stats

                comparison_ids: set[int] = set()
                comparison_count = 0
                for chunk in _chunks(ids):
                    comparison_filter = or_(
                        BattleComparison.predicted_battle_id.in_(chunk),
                        BattleComparison.actual_battle_id.in_(chunk),
                    )
                    if dry_run:
                        comparison_ids.update(
                            row[0]
                            for row in db.query(BattleComparison.id)
                            .filter(comparison_filter)
                            .all()
                        )
                    else:
                        comparison_count += (
                            db.query(BattleComparison)
                            .filter(comparison_filter)
                            .delete(synchronize_session=False)
                        )
                stats.comparisons = (
                    len(comparison_ids) if dry_run else comparison_count
                )
                if dry_run:
                    db.rollback()
                    return stats

                deleted = 0
                for chunk in _chunks(ids):
                    deleted += (
                        db.query(BattleRecord)
                        .filter(BattleRecord.id.in_(chunk))
                        .delete(synchronize_session=False)
                    )
                db.commit()
                stats.deleted = deleted
                return stats

    return retry_on_wal_contention(_attempt, logger=logger)


def _classify_result(
    actual_win: bool | None,
    actual_stars: int | None,
    simulated_win: bool | None,
    simulated_stars: int | None,
) -> tuple[str, int]:
    """Classify a sim-vs-actual result; returns ``(mismatch_type, delta_stars)``.

    A missing star count on either side is "unknown parity" for stars, not a
    delta: only two known, differing star counts count as ``star_delta``.
    """
    if actual_win is None or simulated_win is None:
        mismatch = "unknown"
    elif simulated_win != actual_win:
        mismatch = "win_flip"
    elif (
        actual_stars is not None
        and simulated_stars is not None
        and simulated_stars != actual_stars
    ):
        mismatch = "star_delta"
    else:
        mismatch = "match"
    if actual_stars is not None and simulated_stars is not None:
        delta = int(simulated_stars) - int(actual_stars)
    else:
        delta = 0
    return mismatch, delta


def compare_result(
    battle: ParsedBattle,
    simulated_win: bool | None,
    simulated_stars: int | None,
    *,
    elapsed_seconds: float = 0.0,
) -> SimulationComparison:
    """Classify a simulated result against the replay's recorded result."""
    mismatch, delta = _classify_result(
        battle.win, battle.stars, simulated_win, simulated_stars
    )
    return SimulationComparison(
        battle_id=battle.source_record_id,
        actual_win=battle.win,
        actual_stars=battle.stars,
        simulated_win=simulated_win,
        simulated_stars=simulated_stars,
        matched=mismatch == "match",
        mismatch_type=mismatch,
        delta_stars=delta,
        elapsed_seconds=elapsed_seconds,
    )


def upsert_simulation(
    account_alias: str,
    battle: ParsedBattle,
    *,
    engine: str,
) -> int:
    """Insert or refresh the ``source='sim'`` row for one replay.

    Simulations live in ``battle_records`` for comparison/audit, but they are
    excluded from historical stats and planning queries (``source != 'sim'``).
    Re-running the same battle refreshes the row instead of duplicating it.
    """

    def _attempt() -> int:
        with _wal_io_lock:
            with get_write_session_local()() as db:
                if not db.in_transaction():
                    db.begin()
                # pysqlite (legacy isolation mode) defers BEGIN until the first
                # DML, so a SAVEPOINT issued first would commit on RELEASE.
                # Force the driver-level transaction before savepoints, exactly
                # like import_battles.
                connection = db.connection()
                dbapi_connection = connection.connection.dbapi_connection
                if getattr(dbapi_connection, "in_transaction", True) is False:
                    connection.exec_driver_sql("BEGIN")
                account = _resolve_account(db, account_alias)
                player_id = _as_optional_str(account.player_id)
                cache: dict[str, TeamConfig] = {}
                try:
                    with db.begin_nested():
                        attacker_row, _ = _upsert_team(db, battle.attacker, cache)
                        defender_row, _ = _upsert_team(db, battle.defender, cache)
                        row = (
                            db.query(BattleRecord)
                            .filter(
                                BattleRecord.source == "sim",
                                BattleRecord.source_record_id
                                == battle.source_record_id,
                                BattleRecord.battle_type == battle.battle_type,
                            )
                            .order_by(BattleRecord.id.desc())
                            .first()
                        )
                        if row is None:
                            # (source, source_record_id) is unique, so a replay
                            # re-simulated under a different type must update
                            # the existing row rather than insert a duplicate.
                            row = (
                                db.query(BattleRecord)
                                .filter(
                                    BattleRecord.source == "sim",
                                    BattleRecord.source_record_id
                                    == battle.source_record_id,
                                )
                                .order_by(BattleRecord.id.desc())
                                .first()
                            )
                        if row is None:
                            base_battle_id = battle.source_record_id.split(
                                "#seed=", 1
                            )[0]
                            row = BattleRecord(
                                account_id=int(account.id),
                                battle_type=battle.battle_type,
                                source="sim",
                                source_record_id=battle.source_record_id,
                                battle_id=base_battle_id,
                                occurred_at=battle.occurred_at,
                                ended_at=battle.ended_at,
                                game_version=battle.game_version,
                                attacker_config_id=attacker_row.id,
                                defender_config_id=defender_row.id,
                                attacker_lineup_key=battle.attacker.lineup_key,
                                defender_lineup_key=battle.defender.lineup_key,
                                matchup_key=battle.matchup_key,
                                attacker_player_id=battle.attacker_player_id,
                                defender_player_id=battle.defender_player_id,
                                viewer_role=_viewer_role(battle, player_id),
                                seed=battle.seed,
                                win=battle.win,
                                stars=battle.stars,
                                deterministic=None,
                                authoritative=False,
                                engine=engine,
                                completeness=battle.completeness,
                            )
                            db.add(row)
                        else:
                            # Keep the row addressable by record_comparison:
                            # a type change also changes battle_type, the
                            # lineup keys and the matchup key.
                            row.battle_type = battle.battle_type
                            row.battle_id = battle.source_record_id.split(
                                "#seed=", 1
                            )[0]
                            row.matchup_key = battle.matchup_key
                            row.attacker_lineup_key = battle.attacker.lineup_key
                            row.defender_lineup_key = battle.defender.lineup_key
                            row.seed = battle.seed
                            row.win = battle.win
                            row.stars = battle.stars
                            row.engine = engine
                            row.authoritative = False
                            row.attacker_config_id = attacker_row.id
                            row.defender_config_id = defender_row.id
                        db.flush()
                        sim_id = int(row.id)
                    db.commit()
                    return sim_id
                except Exception:
                    db.rollback()
                    raise

    return retry_on_wal_contention(_attempt, logger=logger)


def delete_stale_sim_seeds(battle_id: str, keep_ids: set[str]) -> int:
    """Delete ``<battle_id>#seed=...`` sim rows not in ``keep_ids``."""
    prefix = f"{battle_id}#seed="

    def _attempt() -> int:
        with _wal_io_lock:
            with get_write_session_local()() as db:
                if not db.in_transaction():
                    db.begin()
                query = db.query(BattleRecord).filter(
                    BattleRecord.source == "sim",
                    BattleRecord.source_record_id.like(f"{prefix}%"),
                )
                if keep_ids:
                    query = query.filter(
                        ~BattleRecord.source_record_id.in_(keep_ids)
                    )
                deleted = query.delete(synchronize_session=False)
                db.commit()
                return deleted

    return retry_on_wal_contention(_attempt, logger=logger)


def record_comparison(
    source_record_id: str,
) -> SimulationComparison | None:
    """Upsert a ``battle_comparisons`` row linking a sim row to its replay.

    Replay ids are globally unique, so the lookup deliberately ignores
    ``battle_type`` (a re-simulation may correct a previously mis-typed row).
    """

    def _attempt() -> SimulationComparison | None:
        with _wal_io_lock:
            with get_write_session_local()() as db:
                if not db.in_transaction():
                    db.begin()
                actual = (
                    db.query(BattleRecord)
                    .filter(
                        BattleRecord.source != "sim",
                        BattleRecord.source_record_id == source_record_id,
                    )
                    .order_by(BattleRecord.id.desc())
                    .first()
                )
                sim = (
                    db.query(BattleRecord)
                    .filter(
                        BattleRecord.source == "sim",
                        BattleRecord.source_record_id == source_record_id,
                    )
                    .order_by(BattleRecord.id.desc())
                    .first()
                )
                if actual is None or sim is None:
                    return None
                mismatch, delta = _classify_result(
                    actual.win, actual.stars, sim.win, sim.stars
                )
                row = (
                    db.query(BattleComparison)
                    .filter(
                        BattleComparison.predicted_battle_id == sim.id,
                        BattleComparison.actual_battle_id == actual.id,
                    )
                    .first()
                )
                if row is None:
                    db.add(
                        BattleComparison(
                            account_id=actual.account_id,
                            predicted_battle_id=sim.id,
                            actual_battle_id=actual.id,
                            expected_win=sim.win,
                            actual_win=actual.win,
                            expected_stars=sim.stars,
                            actual_stars=actual.stars,
                            mismatch_type=mismatch,
                            delta_stars=delta,
                        )
                    )
                else:
                    row.expected_win = sim.win
                    row.actual_win = actual.win
                    row.expected_stars = sim.stars
                    row.actual_stars = actual.stars
                    row.mismatch_type = mismatch
                    row.delta_stars = delta
                    row.evaluated_at = datetime.now(timezone.utc).replace(
                        tzinfo=None
                    )
                db.commit()
                return SimulationComparison(
                    battle_id=source_record_id,
                    actual_win=actual.win,
                    actual_stars=actual.stars,
                    simulated_win=sim.win,
                    simulated_stars=sim.stars,
                    matched=mismatch == "match",
                    mismatch_type=mismatch,
                    delta_stars=delta,
                )

    return retry_on_wal_contention(_attempt, logger=logger)


def _account_player_filter(db, account_alias: str):
    """Filter battles where the account's player was on either side.

    The corpus deduplicates replays globally (first import wins), so the
    importing ``account_id`` is not a reliable ownership filter — another
    account may have imported a battle this account took part in.
    """
    player_id = _as_optional_str(_resolve_account(db, account_alias).player_id)
    if player_id is None:
        raise BattleLogError(
            f"Account {account_alias!r} has no player_id stored; cannot filter "
            "battles by involvement. Refresh the account (e.g. via `hw-genie auth`) "
            "and retry."
        )
    return or_(
        BattleRecord.attacker_player_id == player_id,
        BattleRecord.defender_player_id == player_id,
    )


def query_matchups(
    *,
    account_alias: str | None = None,
    battle_type: str | None = None,
    defender_id: str | None = None,
    game_version: str | None = None,
    by: str = "defender",
    min_samples: int = 1,
    limit: int = 25,
) -> list[MatchupStat]:
    """Aggregate win rates by defender (default) or by lineup matchup.

    ``account_alias`` filters by involvement: battles where that account's
    player id appears as attacker or defender, regardless of who imported them.
    ``game_version`` (None = all versions) restricts to one server version.
    """
    if by not in ("defender", "matchup"):
        raise ValueError(f"unsupported grouping: {by!r}")

    atk = aliased(TeamConfig)
    dfn = aliased(TeamConfig)
    battle_count = func.count(BattleRecord.id)
    wins = func.sum(case((BattleRecord.win.is_(True), 1), else_=0))

    session = get_session_local()()
    try:
        query = (
            session.query(
                BattleRecord.battle_type,
                BattleRecord.defender_player_id,
                BattleRecord.matchup_key,
                atk.heroes_summary.label("attacker_heroes"),
                dfn.heroes_summary.label("defender_heroes"),
                battle_count.label("battles"),
                wins.label("wins"),
                func.max(BattleRecord.occurred_at).label("last_seen"),
            )
            .join(atk, atk.id == BattleRecord.attacker_config_id)
            .join(dfn, dfn.id == BattleRecord.defender_config_id)
            .filter(BattleRecord.win.isnot(None))
            .filter(BattleRecord.source != "sim")
        )
        if battle_type:
            query = query.filter(BattleRecord.battle_type == battle_type)
        if defender_id:
            query = query.filter(BattleRecord.defender_player_id == str(defender_id))
        if game_version:
            query = query.filter(BattleRecord.game_version == str(game_version))
        if account_alias:
            query = query.filter(_account_player_filter(session, account_alias))

        group_cols = [
            BattleRecord.battle_type,
            BattleRecord.matchup_key,
            atk.heroes_summary,
            dfn.heroes_summary,
        ]
        if by == "defender":
            group_cols.append(BattleRecord.defender_player_id)
        query = query.group_by(*group_cols).having(battle_count >= min_samples)
        query = query.order_by((wins * 1.0 / battle_count).desc(), battle_count.desc())

        rows = query.limit(limit).all()
    finally:
        session.close()

    result: list[MatchupStat] = []
    for row in rows:
        battles = int(row.battles or 0)
        win_count = int(row.wins or 0)
        result.append(
            MatchupStat(
                battle_type=row.battle_type,
                # ``defender_player_id`` is not grouped for ``by="matchup"``, so
                # the selected value would be an arbitrary defender. Leave it
                # unset to avoid presenting it as authoritative.
                defender_player_id=(
                    row.defender_player_id if by == "defender" else None
                ),
                matchup_key=row.matchup_key,
                attacker_heroes=row.attacker_heroes or "",
                defender_heroes=row.defender_heroes or "",
                battles=battles,
                wins=win_count,
                win_rate=(win_count / battles) if battles else 0.0,
                last_seen=row.last_seen,
            )
        )
    return result


def _query_plan_rows(
    session,
    *,
    defender_lineup_key: str,
    defender_id: str | None,
    battle_type: str,
    account_alias: str | None,
    game_version: str | None = None,
):
    atk = aliased(TeamConfig)
    battle_count = func.count(BattleRecord.id)
    wins = func.sum(case((BattleRecord.win.is_(True), 1), else_=0))
    query = (
        session.query(
            BattleRecord.attacker_lineup_key,
            atk.heroes_summary,
            atk.pet_id,
            atk.banner_id,
            battle_count.label("battles"),
            wins.label("wins"),
            func.max(BattleRecord.occurred_at).label("last_seen"),
        )
        .join(atk, atk.id == BattleRecord.attacker_config_id)
        .filter(
            BattleRecord.battle_type == battle_type,
            BattleRecord.defender_lineup_key == defender_lineup_key,
            BattleRecord.win.isnot(None),
            BattleRecord.source != "sim",
        )
    )
    if defender_id is not None:
        query = query.filter(BattleRecord.defender_player_id == str(defender_id))
    if game_version:
        query = query.filter(BattleRecord.game_version == str(game_version))
    if account_alias:
        query = query.filter(_account_player_filter(session, account_alias))
    return query.group_by(
        BattleRecord.attacker_lineup_key,
        atk.heroes_summary,
        atk.pet_id,
        atk.banner_id,
    ).all()


def _favor_from_config(config_json: Any) -> dict[int, int]:
    """Extract ``hero id -> patron pet id`` from a stored team config."""
    if isinstance(config_json, str):
        try:
            config = json.loads(config_json)
        except (TypeError, ValueError):
            return {}
    elif isinstance(config_json, dict):
        config = config_json
    else:
        return {}
    heroes = config.get("heroes")
    if not isinstance(heroes, list):
        return {}
    favor: dict[int, int] = {}
    for hero in heroes:
        if not isinstance(hero, dict):
            continue
        hero_id = _as_int(hero.get("id"))
        patron_id = _as_int(hero.get("favorPetId"))
        if hero_id is not None and hero_id > 0 and patron_id is not None and patron_id > 0:
            favor[hero_id] = patron_id
    return favor


def _latest_favor_by_lineup(
    session,
    *,
    defender_lineup_key: str,
    defender_id: str | None,
    battle_type: str,
    account_alias: str | None,
    game_version: str | None = None,
) -> dict[str, dict[int, int]]:
    """Favor of the most recent winning config per attacker lineup."""
    rank = func.row_number().over(
        partition_by=BattleRecord.attacker_lineup_key,
        order_by=(BattleRecord.occurred_at.desc(), BattleRecord.id.desc()),
    )
    ranked = session.query(
        BattleRecord.attacker_lineup_key.label("lineup_key"),
        BattleRecord.attacker_config_id.label("config_id"),
        rank.label("rn"),
    ).filter(
        BattleRecord.battle_type == battle_type,
        BattleRecord.defender_lineup_key == defender_lineup_key,
        BattleRecord.win.is_(True),
        BattleRecord.source != "sim",
        BattleRecord.attacker_config_id.isnot(None),
    )
    if defender_id is not None:
        ranked = ranked.filter(BattleRecord.defender_player_id == str(defender_id))
    if game_version:
        ranked = ranked.filter(BattleRecord.game_version == str(game_version))
    if account_alias:
        ranked = ranked.filter(_account_player_filter(session, account_alias))
    sub = ranked.subquery()
    rows = (
        session.query(sub.c.lineup_key, TeamConfig.config_json)
        .join(TeamConfig, TeamConfig.id == sub.c.config_id)
        .filter(sub.c.rn == 1)
        .all()
    )
    return {str(row[0]): _favor_from_config(row[1]) for row in rows}


def _plan_candidate(
    row,
    tier: str,
    *,
    min_samples: int,
    min_win_rate: float,
    favor: dict[int, int] | None = None,
) -> PlanCandidate:
    battles = int(row.battles or 0)
    wins = int(row.wins or 0)
    rate = (wins / battles) if battles else 0.0
    return PlanCandidate(
        attacker_lineup_key=row.attacker_lineup_key,
        heroes_summary=row.heroes_summary or "",
        pet_id=row.pet_id,
        banner_id=row.banner_id,
        battles=battles,
        wins=wins,
        win_rate=rate,
        wilson_low=wilson_lower_bound(wins, battles),
        last_seen=row.last_seen,
        tier=tier,
        recommended=battles >= min_samples and rate >= min_win_rate,
        favor=dict(favor or {}),
    )


def query_plans(
    *,
    defender_lineup_key: str,
    defender_id: str | None = None,
    battle_type: str = "arena",
    account_alias: str | None = None,
    game_version: str | None = None,
    min_samples: int = 3,
    min_win_rate: float = 1.0,
    limit: int = 10,
    tiers: tuple[str, ...] | None = None,
) -> list[PlanCandidate]:
    """Rank attacker lineups against a defender lineup from observed battles.

    Same-defender history is preferred when it has enough samples; otherwise
    the broader same-lineup history (other accounts/defenders) is used. Results
    are ordered by the Wilson lower bound so small perfect samples do not
    automatically outrank well-supported lineups. ``game_version`` (None = all
    versions) restricts the corpus to one server version. ``tiers`` filters
    before sorting/slicing, so a lower-ranked same-defender plan is not lost
    behind many same-lineup plans.
    """
    if not 0.0 <= min_win_rate <= 1.0:
        raise ValueError("min_win_rate must be between 0.0 and 1.0")

    session = get_session_local()()
    try:
        same_rows = (
            _query_plan_rows(
                session,
                defender_lineup_key=defender_lineup_key,
                defender_id=defender_id,
                battle_type=battle_type,
                account_alias=account_alias,
                game_version=game_version,
            )
            if defender_id is not None
            else []
        )
        broad_rows = _query_plan_rows(
            session,
            defender_lineup_key=defender_lineup_key,
            defender_id=None,
            battle_type=battle_type,
            account_alias=account_alias,
            game_version=game_version,
        )
        same_favor = (
            _latest_favor_by_lineup(
                session,
                defender_lineup_key=defender_lineup_key,
                defender_id=defender_id,
                battle_type=battle_type,
                account_alias=account_alias,
                game_version=game_version,
            )
            if defender_id is not None
            else {}
        )
        broad_favor = _latest_favor_by_lineup(
            session,
            defender_lineup_key=defender_lineup_key,
            defender_id=None,
            battle_type=battle_type,
            account_alias=account_alias,
            game_version=game_version,
        )
    finally:
        session.close()

    same = {row.attacker_lineup_key: row for row in same_rows}
    broad = {row.attacker_lineup_key: row for row in broad_rows}
    candidates: list[PlanCandidate] = []

    for key, row in same.items():
        if int(row.battles or 0) >= min_samples:
            candidates.append(
                _plan_candidate(
                    row,
                    "same_defender",
                    min_samples=min_samples,
                    min_win_rate=min_win_rate,
                    favor=same_favor.get(key),
                )
            )
            continue
        alt = broad.get(key)
        if alt is not None and int(alt.battles or 0) > int(row.battles or 0):
            candidates.append(
                _plan_candidate(
                    alt,
                    "same_lineup",
                    min_samples=min_samples,
                    min_win_rate=min_win_rate,
                    favor=broad_favor.get(key),
                )
            )
        else:
            candidates.append(
                _plan_candidate(
                    row,
                    "same_defender",
                    min_samples=min_samples,
                    min_win_rate=min_win_rate,
                    favor=same_favor.get(key),
                )
            )

    for key, row in broad.items():
        if key not in same:
            candidates.append(
                _plan_candidate(
                    row,
                    "same_lineup",
                    min_samples=min_samples,
                    min_win_rate=min_win_rate,
                    favor=broad_favor.get(key),
                )
            )

    if tiers is not None:
        candidates = [candidate for candidate in candidates if candidate.tier in tiers]

    candidates.sort(
        key=lambda candidate: (
            candidate.recommended,
            candidate.wilson_low,
            candidate.battles,
        ),
        reverse=True,
    )
    return candidates[:limit]
