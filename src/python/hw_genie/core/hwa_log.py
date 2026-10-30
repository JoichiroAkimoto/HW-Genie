"""HWAssistant (HWA) snapshot import.

HWA stores battles in a gzip-compressed JSON snapshot. Replays use the same
shape as ``battleGetByType`` except heroes are compact 36-element arrays under
``c``. Only the sections requested by the caller are streamed:

* ``arena_replays`` — one arena battle per entry.
* ``ga_replays`` — one Grand Arena Bo3 series per entry, with the battles under
  the keys ``"0"`` / ``"1"`` / ``"2"``.

``users`` / ``userTeams`` / war replays are ignored. Compact heroes are expanded
into the normal hero dicts so the existing ``build_team`` / hashing code applies
unchanged. HWA does not preserve every API field (e.g. ``currentSkin``), so
both ``battle_records.completeness`` and ``team_configs.completeness`` are
``partial`` and the derived ``config_hash`` only covers the partial config.
"""
from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Iterator

import ijson

from .battle_log import (
    BattleLogError,
    ParsedBattle,
    _as_int,
    _as_optional_str,
    _banner_id,
    build_team,
    bulk_import_battles,
    import_battles,
    matchup_key,
)
from .database import Account, get_session_local

logger = logging.getLogger(__name__)

HWA_SOURCE = "hwa_import"
HWA_ENGINE = "hwa"

# Indices of HWA's 36-element compact hero array. Unknown indices are ignored;
# this mapping was verified against API-exported heroes (e.g. hp/power/level).
HWA_HERO_FIELDS: dict[int, str] = {
    0: "agility",
    1: "armor",
    2: "armorPenetration",
    3: "artifacts",
    4: "ascensions",
    5: "color",
    6: "favorPetId",
    7: "favorPower",
    8: "hp",
    9: "intelligence",
    10: "level",
    11: "magicPower",
    12: "magicResist",
    13: "perks",
    14: "petId",
    15: "physicalAttack",
    16: "power",
    17: "runes",
    18: "skills",
    19: "skins",
    20: "slots",
    21: "star",
    22: "strength",
    23: "titanGiftLevel",
    25: "type",
    31: "scale",
    33: "physicalCritChance",
}

PET_ID_FLOOR = 6000


@dataclass
class HwaImportStats:
    total: int = 0
    inserted: int = 0
    duplicates: int = 0
    upgraded: int = 0
    team_configs_inserted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    by_account: dict[str, int] = field(default_factory=dict)


def _normalize_hero_type(hero: dict[str, Any]) -> str:
    """Return ``hero`` / ``pet`` for an HWA hero entry.

    HWA stores the marker with inconsistent casing (or omits it entirely), so
    infer the type from the id when the value is absent or unknown.
    """
    raw = hero.get("type")
    value = str(raw).strip().lower() if raw is not None else ""
    if value not in ("hero", "pet"):
        hero_id = _as_int(hero.get("id"), 0) or 0
        value = "pet" if hero_id >= PET_ID_FLOOR else "hero"
    return value


def expand_hwa_hero(entry: dict[str, Any]) -> dict[str, Any]:
    """Expand HWA's compact ``c`` array into a normal hero dict.

    Entries without ``c`` (already-expanded API shape) are returned as-is.
    """
    if not isinstance(entry, dict):
        return {}
    compact = entry.get("c")
    if not isinstance(compact, list):
        hero = dict(entry)
        hero["type"] = _normalize_hero_type(hero)
        return hero

    hero: dict[str, Any] = {"id": entry.get("id")}
    for index, name in HWA_HERO_FIELDS.items():
        if index < len(compact) and compact[index] is not None:
            hero[name] = compact[index]
    hero["type"] = _normalize_hero_type(hero)
    return hero


def expand_hwa_team(team: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(team, dict):
        return {}
    expanded: dict[str, dict[str, Any]] = {}
    for key, entry in team.items():
        if not isinstance(entry, dict):
            continue
        hero = expand_hwa_hero(entry)
        hero_id = _as_int(hero.get("id"), _as_int(key))
        if hero_id is None:
            continue
        hero["id"] = hero_id
        # Re-evaluate with the id resolved (it may only exist in the team key).
        hero["type"] = _normalize_hero_type(hero)
        expanded[str(hero_id)] = hero
    return expanded


def _parse_epoch(value: Any) -> datetime | None:
    ts = _as_int(value)
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError, OSError):
        return None


def _parse_epoch_ms(value: Any) -> datetime | None:
    ts = _as_int(value)
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).replace(
            tzinfo=None
        )
    except (ValueError, OverflowError, OSError):
        return None


def parse_hwa_battle(
    record: dict[str, Any],
    section_type: str = "arena",
    fallback_id: str | None = None,
) -> ParsedBattle | None:
    """Convert one HWA replay/battle object into a :class:`ParsedBattle`.

    ``section_type`` is authoritative: HWA records can carry a stale ``type``
    (or none), so the section they were found in determines ``battle_type``.
    ``fallback_id`` identifies a sub-battle (e.g. a GA Bo3 game that has no
    replay id of its own).
    """
    if not isinstance(record, dict):
        return None

    attackers = expand_hwa_team(record.get("attackers"))
    raw_defenders = record.get("defenders")
    if isinstance(raw_defenders, list):
        defender_team = (
            raw_defenders[0]
            if raw_defenders and isinstance(raw_defenders[0], dict)
            else {}
        )
    elif isinstance(raw_defenders, dict):
        defender_team = raw_defenders
    else:
        defender_team = {}
    defenders = expand_hwa_team(defender_team)
    if not attackers or not defenders:
        return None

    effects = record.get("effects") if isinstance(record.get("effects"), dict) else {}
    attacker = replace(
        build_team(attackers, _banner_id(effects.get("attackersBanner"))),
        completeness="partial",
    )
    defender = replace(
        build_team(defenders, _banner_id(effects.get("defendersBanner"))),
        completeness="partial",
    )

    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    battle_type = str(section_type or "arena")
    occurred_at = (
        _parse_epoch(record.get("startTime"))
        or _parse_epoch_ms(record.get("dnow"))
        or datetime.now(timezone.utc).replace(tzinfo=None)
    )
    ended_at = _parse_epoch(record.get("endTime"))
    record_id = _as_optional_str(record.get("id")) or _as_optional_str(fallback_id)
    if record_id is None:
        order = _as_int(result.get("battleOrder"), 0) or 0
        record_id = (
            f"hwa-{battle_type}-{record.get('startTime') or ''}-"
            f"{_as_int(record.get('seed'), 0) or 0}-{order}"
        )

    return ParsedBattle(
        source_record_id=record_id,
        battle_type=battle_type,
        occurred_at=occurred_at,
        ended_at=ended_at,
        game_version=_as_optional_str(result.get("serverVersion")),
        seed=_as_int(record.get("seed")),
        win=bool(result["win"]) if "win" in result else None,
        stars=_as_int(result.get("stars")),
        attacker_player_id=_as_optional_str(record.get("userId")),
        defender_player_id=_as_optional_str(record.get("typeId")),
        attacker=attacker,
        defender=defender,
        matchup_key=matchup_key(battle_type, attacker, defender),
        completeness="partial",
    )


def _series_battles(series: Any) -> Iterator[dict[str, Any]]:
    """Yield the battles in one ``ga_replays`` entry.

    A series is normally a dict keyed ``"0"`` / ``"1"`` / ``"2"``, but some
    exports store a bare battle object. In the latter case yield it directly
    instead of iterating its fields as if they were battles. A bare battle is
    recognized by its ``attackers`` / ``defenders`` teams: a container may also
    carry an ``id`` (e.g. ``{"id": ..., "0": {...}}``) and must still be
    iterated.
    """
    if isinstance(series, dict):
        if "attackers" in series or "defenders" in series:
            yield series
            return
        for key in sorted(series.keys(), key=lambda item: str(item)):
            value = series[key]
            if isinstance(value, dict):
                yield value
    elif isinstance(series, list):
        for value in series:
            if isinstance(value, dict):
                yield value


def _section_shape(path: str | Path, section: str) -> str | None:
    """Return ``"map"`` / ``"array"`` / ``"missing"`` for a section.

    The scan stops at the section's own ``start_map`` / ``start_array`` event,
    so it is cheap even for a multi-hundred-MB snapshot. ``"missing"`` means
    the scan reached EOF without finding the section. Returns ``None`` when
    the file cannot be inspected; callers then keep the legacy item-stream
    fallback.
    """
    try:
        with gzip.open(path, "rb") as handle:
            for prefix, event, _value in ijson.parse(handle, use_float=True):
                if event == "start_map" and prefix == section:
                    return "map"
                if event == "start_array" and prefix == section:
                    return "array"
    except Exception:
        logger.debug(
            "Could not determine the shape of section %s", section, exc_info=True
        )
        return None
    return "missing"


def _iter_section(
    path: str | Path, section: str
) -> Iterator[tuple[Any, Any]]:
    """Yield ``(key, value)`` pairs from one section, list-shape tolerant.

    Prefer ``ijson.kvitems`` (object-shaped sections). An object-shaped section
    that exists but is empty already consumed its single pass, so the shape
    scan (which stops at the section's first event) is only used to decide
    whether a positional ``ijson.items`` fallback is needed. A list-shaped
    section is read up to three times: the failed ``kvitems`` attempt, the
    shape scan, and the item stream. List positions get a ``list-`` prefix so
    their synthetic keys are stable and cannot collide with an object key.
    """
    yielded = False
    try:
        with gzip.open(path, "rb") as handle:
            for key, value in ijson.kvitems(handle, section, use_float=True):
                yielded = True
                yield key, value
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        if yielded:
            # The stream failed mid-way; re-reading would duplicate rows.
            raise
    if yielded:
        return
    shape = _section_shape(path, section)
    if shape in ("map", "missing"):
        # The section exists and is an empty object (or is absent); the kvitems
        # pass above already covered it, so a second decompression adds nothing.
        return
    logger.debug("Falling back to the item stream for section %s", section)
    with gzip.open(path, "rb") as handle:
        for position, value in enumerate(
            ijson.items(handle, f"{section}.item", use_float=True)
        ):
            # A synthetic positional key keeps GA fallback ids unique when the
            # section is list-shaped.
            yield f"list-{position}", value


def _ga_fallback_id(section_key: Any, index: int, record: dict[str, Any]) -> str:
    """Build a content-dependent id for a GA sub-battle without a replay id.

    A series key plus index alone can repeat across files (or series), so the
    record's ``startTime`` / ``seed`` / ``battleOrder`` are included: two
    different sub-battles can never share the id. List-shaped sections report
    ``list-<position>`` keys, keeping their prefix stable.
    """
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    order = _as_int(result.get("battleOrder"))
    return (
        f"{section_key}-{index}-"
        f"{_as_optional_str(record.get('startTime')) or ''}-"
        f"{_as_optional_str(record.get('seed')) or ''}-"
        f"{order if order is not None else ''}"
    )


def iter_hwa_battles(
    path: str | Path,
    *,
    types: tuple[str, ...] = ("arena", "grand"),
    on_error: Callable[[str], None] | None = None,
) -> Iterator[ParsedBattle]:
    """Stream HWA battles from a ``*.json.gz`` snapshot.

    The file is read once per requested section (plus a short shape scan and,
    for list-shaped sections, one item-stream pass) to keep memory bounded. A
    record that cannot be parsed is reported via ``on_error`` and skipped so one
    bad entry does not abort the whole snapshot.
    """
    wanted = {"grand" if item == "ga" else item for item in types}

    def _report(message: str) -> None:
        if on_error is not None:
            on_error(message)

    try:
        if "arena" in wanted:
            for _key, record in _iter_section(path, "arena_replays"):
                try:
                    parsed = parse_hwa_battle(record, "arena")
                except Exception as exc:
                    _report(f"arena_replays: {exc}")
                    continue
                if parsed is None:
                    _report("arena_replays: record has no attacker/defender team")
                    continue
                yield parsed

        if "grand" in wanted:
            for series_key, series in _iter_section(path, "ga_replays"):
                section_key = series_key if series_key is not None else "ga"
                for index, record in enumerate(_series_battles(series)):
                    try:
                        parsed = parse_hwa_battle(
                            record,
                            "grand",
                            fallback_id=_ga_fallback_id(section_key, index, record),
                        )
                    except Exception as exc:
                        _report(f"ga_replays[{section_key}][{index}]: {exc}")
                        continue
                    if parsed is None:
                        _report(
                            f"ga_replays[{section_key}][{index}]: record has no "
                            "attacker/defender team"
                        )
                        continue
                    yield parsed
    except (KeyboardInterrupt, BattleLogError):
        raise
    except Exception as exc:
        raise BattleLogError(
            f"failed to parse HWA snapshot {path}: {exc}"
        ) from exc


def _account_aliases() -> dict[str, str]:
    session = get_session_local()()
    try:
        rows = session.query(Account).all()
    finally:
        session.close()
    return {
        str(row.player_id): row.alias
        for row in rows
        if row.player_id and row.alias
    }


def import_hwa_file(
    default_account: str,
    source: str | Path,
    *,
    types: tuple[str, ...] = ("arena", "grand"),
    limit: int | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
) -> HwaImportStats:
    """Import an HWA snapshot, attaching battles to the involved account.

    A battle is attached to the registered account whose ``player_id`` matches
    the attacker or defender, falling back to ``default_account``. Observation
    sources dedupe globally by replay id, so re-importing HWA after the API (or
    vice versa) does not double-count.
    """
    aliases = _account_aliases()
    stats = HwaImportStats()
    buffers: dict[str, list[ParsedBattle]] = {}

    def _merge(result) -> None:
        stats.inserted += result.inserted
        stats.duplicates += result.duplicates
        stats.upgraded += result.upgraded
        stats.team_configs_inserted += result.team_configs_inserted
        stats.skipped += result.skipped
        stats.errors.extend(result.errors)

    def flush(alias: str) -> None:
        batch = buffers.pop(alias, [])
        if not batch:
            return
        try:
            result = bulk_import_battles(
                alias,
                batch,
                source=HWA_SOURCE,
                engine=HWA_ENGINE,
                dry_run=dry_run,
            )
        except Exception:
            # One bad row (e.g. an id collision the intra-file dedupe missed)
            # aborts the single bulk transaction. Retry battle-by-battle so the
            # rest of the snapshot still lands; import_battles isolates each
            # row in its own savepoint.
            logger.warning(
                "Bulk HWA import failed for %s; retrying per battle",
                alias,
                exc_info=True,
            )
            for battle in batch:
                result = import_battles(
                    alias,
                    [battle],
                    source=HWA_SOURCE,
                    engine=HWA_ENGINE,
                    dry_run=dry_run,
                )
                _merge(result)
            return
        _merge(result)

    seen: set[str] = set()

    def _on_error(message: str) -> None:
        stats.skipped += 1
        stats.errors.append(message)

    battles = iter_hwa_battles(source, types=types, on_error=_on_error)
    if limit is not None:
        # Do not parse one extra record just to detect the limit.
        battles = islice(battles, limit)

    for battle in battles:
        alias = (
            aliases.get(battle.attacker_player_id or "")
            or aliases.get(battle.defender_player_id or "")
            or default_account
        )
        # ``total`` / ``by_account`` count every parsed battle, including the
        # duplicates and skips reported below.
        stats.total += 1
        stats.by_account[alias] = stats.by_account.get(alias, 0) + 1
        if battle.source_record_id in seen:
            stats.duplicates += 1
            continue
        seen.add(battle.source_record_id)
        buffers.setdefault(alias, []).append(battle)
        if len(buffers[alias]) >= batch_size:
            flush(alias)

    for alias in list(buffers):
        flush(alias)
    return stats
