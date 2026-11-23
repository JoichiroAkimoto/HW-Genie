"""Asgard Guild Raid boss battles (Osh / Maestro).

Each account stores one team per ``slot`` (1-5) for each boss. ``run`` detects
the current week from ``clanRaid_getInfo``'s shop signature (the same Osh
(61-81) / Maestro (112-133) buffId ranges used by :mod:`asgard_shop`), then
executes the enabled teams in slot order:

1. ``clanRaid_startBossBattle`` with the stored heroes / pet / favor.
2. ``clanRaid_endBossBattle`` with an empty ``progress``. The server treats
   this as an invalid client simulation and falls back to its own auto-battle
   (``result.afterInvalid: true``), returning the damage / win / replay. This
   was live-verified on 2026-11-17 (serverVersion 300): start consumes one
   ``bossAttempts`` immediately, and without the end call the attempt stays
   unresolved (``userStats.damage`` does not change), so the end call is
   required.

The end response also exposes per-defender ``result.damage`` and
``userStats.damage`` / ``bossAttempts`` before/after, which are used for the
run summary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hw_genie.commands.asgard_shop import (
    MAESTRO_BUFF_IDS,
    OSH_BUFF_IDS,
    is_maestro_shop,
    is_osh_shop,
)
from hw_genie.core.client import (
    ApiAction,
    Emojis,
    HWAuthError,
    HWClient,
    ResponseStatus,
    _safe_int,
)
from hw_genie.core.utils import format_number_with_suffix

BOSS_OSH = "osh"
BOSS_MAESTRO = "maestro"
BOSS_LABELS = {BOSS_OSH: "Osh", BOSS_MAESTRO: "Maestro"}
BOSS_CHOICES = (BOSS_OSH, BOSS_MAESTRO)

TEAM_SLOTS = (1, 2, 3, 4, 5)
TEAM_SIZE = 5

REWARD_STANDING_CLAN = "standingClan"
REWARD_STANDING_USER = "standingUser"
REWARD_TYPES = (REWARD_STANDING_CLAN, REWARD_STANDING_USER)

logger = logging.getLogger(__name__)


class AsgardRaidError(Exception):
    """Invalid team data or a failed run (auth errors stay ``HWAuthError``)."""


class AsgardRaidReadError(AsgardRaidError):
    """``clanRaid_getInfo`` could not be fetched or parsed."""


@dataclass(frozen=True)
class AsgardTeam:
    """One stored boss-battle team."""

    boss: str
    slot: int
    heroes: tuple[int, ...]
    pet: int | None
    favor: dict[int, int] = field(default_factory=dict)
    enabled: bool = True
    memo: str | None = None


@dataclass
class AsgardRaidTeamResult:
    """Outcome of one team in a run."""

    slot: int
    memo: str | None
    enabled: bool
    status: ResponseStatus
    damage: int = 0
    win: bool = False
    stars: int = 0
    replay: str | None = None
    error: str | None = None
    #: ``result.afterInvalid`` from the end response. ``True`` means the
    #: server replaced the (empty) client simulation with its own auto-battle;
    #: ``False`` means the damage may not reflect a completed battle.
    after_invalid: bool = False


@dataclass
class AsgardRewardResult:
    """Weekly ranking reward claim outcome (second claim is skipped)."""

    reward_type: str
    status: ResponseStatus
    error: str | None = None


@dataclass(frozen=True)
class AsgardLoggedTeam:
    """One boss-battle team reconstructed from ``clanRaid_logBoss``."""

    slot: int
    heroes: tuple[int, ...]
    pet: int | None
    favor: dict[int, int]
    battle_id: str
    start_time: int | None
    damage: int


@dataclass
class AsgardRaidImportResult:
    """Outcome of ``asgard-raid team import-log``."""

    boss: str
    dry_run: bool
    teams: list[AsgardLoggedTeam] = field(default_factory=list)
    imported_slots: list[int] = field(default_factory=list)
    skipped_slots: list[int] = field(default_factory=list)


@dataclass
class AsgardRaidRunResult:
    """Per-account result used by the single command and ``multi`` summary."""

    boss: str | None
    dry_run: bool
    teams: list[AsgardRaidTeamResult] = field(default_factory=list)
    rewards: list[AsgardRewardResult] = field(default_factory=list)
    attempts_before: int | None = None
    attempts_after: int | None = None
    damage_before: int = 0
    damage_after: int = 0
    points_before: int = 0
    points_after: int = 0
    error: str | None = None

    @property
    def failed_count(self) -> int:
        return sum(1 for team in self.teams if team.status == ResponseStatus.ERROR)

    @property
    def total_damage(self) -> int:
        return sum(team.damage for team in self.teams)


# --- validation -----------------------------------------------------------


def validate_slot(slot: Any) -> int:
    try:
        value = int(slot)
    except (TypeError, ValueError) as exc:
        raise AsgardRaidError(f"Invalid slot {slot!r}: expected 1-5.") from exc
    if value not in TEAM_SLOTS:
        raise AsgardRaidError(f"Invalid slot {slot!r}: expected 1-5.")
    return value


def validate_boss(boss: Any) -> str:
    value = str(boss or "").strip().lower()
    if value not in BOSS_CHOICES:
        raise AsgardRaidError(
            f"Invalid boss {boss!r}: expected one of {', '.join(BOSS_CHOICES)}."
        )
    return value


def parse_heroes(raw: list[int] | None) -> tuple[int, ...]:
    if raw is None:
        raise AsgardRaidError("--heroes is required (5 hero IDs).")
    try:
        heroes = [int(hero) for hero in raw]
    except (TypeError, ValueError) as exc:
        raise AsgardRaidError(f"Invalid hero ID in {raw!r}.") from exc
    if len(heroes) != TEAM_SIZE:
        raise AsgardRaidError(f"Boss team requires exactly {TEAM_SIZE} heroes (got {len(heroes)}).")
    if any(hero <= 0 for hero in heroes):
        raise AsgardRaidError("Hero IDs must be positive.")
    if len(set(heroes)) != len(heroes):
        raise AsgardRaidError("Hero IDs must be unique.")
    return tuple(heroes)


def parse_favor_args(raw: list[str] | None) -> dict[int, int]:
    """Parse repeatable ``--favor HERO=PATRON`` values (empty when omitted)."""
    if raw is None:
        return {}
    favor: dict[int, int] = {}
    for value in raw:
        text = str(value).strip()
        parts = text.split("=")
        if len(parts) != 2:
            raise AsgardRaidError(f"Invalid --favor value {value!r}; expected HERO=PATRON.")
        try:
            hero_id = int(parts[0])
            patron_id = int(parts[1])
        except ValueError as exc:
            raise AsgardRaidError(
                f"Invalid --favor value {value!r}; expected HERO=PATRON."
            ) from exc
        if hero_id <= 0 or patron_id <= 0:
            raise AsgardRaidError(f"Invalid --favor value {value!r}; IDs must be positive.")
        if hero_id in favor:
            raise AsgardRaidError(f"Duplicate --favor for hero {hero_id}.")
        favor[hero_id] = patron_id
    return favor


def validate_pet(pet: Any) -> int | None:
    if pet is None:
        return None
    try:
        value = int(pet)
    except (TypeError, ValueError) as exc:
        raise AsgardRaidError(f"Invalid pet ID {pet!r}: expected a positive integer.") from exc
    if value <= 0:
        raise AsgardRaidError(f"Invalid pet ID {pet!r}: expected a positive integer.")
    return value


def validate_team(team: AsgardTeam) -> None:
    validate_boss(team.boss)
    validate_slot(team.slot)
    if len(team.heroes) != TEAM_SIZE or len(set(team.heroes)) != len(team.heroes):
        raise AsgardRaidError(f"Boss team requires {TEAM_SIZE} unique heroes.")
    if any(hero <= 0 for hero in team.heroes):
        raise AsgardRaidError("Hero IDs must be positive.")
    if team.pet is not None and team.pet <= 0:
        raise AsgardRaidError("Pet ID must be positive.")
    unknown = sorted(set(team.favor) - set(team.heroes))
    if unknown:
        raise AsgardRaidError(
            f"Favor heroes must be part of the team: {unknown}"
        )
    if any(patron <= 0 for patron in team.favor.values()):
        raise AsgardRaidError("Patron IDs must be positive.")


# --- storage --------------------------------------------------------------


def set_asgard_raid_team(account: str, team: AsgardTeam) -> AsgardTeam:
    from hw_genie.core.session_manager import SessionManager

    validate_team(team)
    SessionManager.repo.save_asgard_raid_team(
        account,
        boss=team.boss,
        slot=team.slot,
        heroes=list(team.heroes),
        pet=team.pet,
        favor=team.favor,
        enabled=team.enabled,
        memo=team.memo,
    )
    return team


def list_asgard_raid_teams(account: str, boss: str | None = None) -> list[AsgardTeam]:
    from hw_genie.core.session_manager import SessionManager

    if boss is not None:
        boss = validate_boss(boss)
    rows = SessionManager.repo.list_asgard_raid_teams(account, boss)
    return [_team_from_row(row) for row in rows]


def remove_asgard_raid_team(account: str, boss: str, slot: int) -> None:
    from hw_genie.core.session_manager import SessionManager

    boss = validate_boss(boss)
    slot = validate_slot(slot)
    if not SessionManager.repo.delete_asgard_raid_team(account, boss=boss, slot=slot):
        raise AsgardRaidError(
            f"No {BOSS_LABELS[boss]} team registered in slot {slot} for '{account}'."
        )


def _team_from_row(row: dict[str, Any]) -> AsgardTeam:
    return AsgardTeam(
        boss=str(row.get("boss")),
        slot=int(row.get("slot")),
        heroes=tuple(int(hero) for hero in row.get("heroes") or []),
        pet=int(row["pet"]) if row.get("pet") is not None else None,
        favor={int(hero): int(patron) for hero, patron in (row.get("favor") or {}).items()},
        enabled=bool(row.get("enabled", True)),
        memo=row.get("memo"),
    )


def format_team(team: AsgardTeam) -> str:
    return _format_team_parts(team.heroes, team.pet, team.favor)


def _format_team_parts(
    heroes: tuple[int, ...], pet: int | None, favor: dict[int, int]
) -> str:
    parts = [f"heroes={list(heroes)}"]
    if pet is not None:
        parts.append(f"pet={pet}")
    if favor:
        favor_text = " ".join(
            f"{hero}={patron}" for hero, patron in sorted(favor.items())
        )
        parts.append(f"favor={favor_text}")
    return " ".join(parts)


def format_logged_team(team: AsgardLoggedTeam) -> str:
    return _format_team_parts(team.heroes, team.pet, team.favor)


# --- read-only helpers ----------------------------------------------------


def fetch_clan_raid_info(client: HWClient) -> dict[str, Any]:
    """Return the ``response`` dict from ``clanRaid_getInfo``."""
    try:
        res = client.call(
            {"calls": [{"name": ApiAction.CLAN_RAID_GET_INFO, "args": {}, "ident": "body"}]}
        )
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AsgardRaidReadError(f"clanRaid_getInfo failed: {exc}") from exc
    if not res.is_success:
        raise AsgardRaidReadError(
            f"clanRaid_getInfo failed ({res.error_name or res.status.value})"
        )
    detail = res.detail if isinstance(res.detail, dict) else {}
    response = detail.get("response")
    if not isinstance(response, dict):
        raise AsgardRaidReadError(
            "clanRaid_getInfo returned unexpected response (missing 'response' dict)"
        )
    return response


def fetch_boss_log(client: HWClient) -> dict[str, Any]:
    """Return ``clanRaid_logBoss``'s ``response`` (userId → battleId → entry)."""
    try:
        res = client.call(
            {"calls": [{"name": ApiAction.CLAN_RAID_LOG_BOSS, "args": {}, "ident": "body"}]}
        )
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AsgardRaidReadError(f"clanRaid_logBoss failed: {exc}") from exc
    if not res.is_success:
        raise AsgardRaidReadError(
            f"clanRaid_logBoss failed ({res.error_name or res.status.value})"
        )
    detail = res.detail if isinstance(res.detail, dict) else {}
    response = detail.get("response")
    if not isinstance(response, dict):
        raise AsgardRaidReadError(
            "clanRaid_logBoss returned unexpected response (missing 'response' dict)"
        )
    return response


def resolve_player_id(client: HWClient, user_id: str | None = None) -> str:
    """Resolve the game player ID used as the ``clanRaid_logBoss`` key.

    ``--user-id`` wins. Otherwise ``userGetInfo`` is called: the stored
    ``player.id`` is not part of ``SessionManager.load`` (only the auth
    ``x-auth-user-id`` is available, which is a different identifier).
    """
    if user_id is not None:
        text = str(user_id).strip()
        if not text.isdigit() or int(text) <= 0:
            raise AsgardRaidError(
                f"Invalid --user-id {user_id!r}: a numeric player ID is required."
            )
        return text

    try:
        res = client.user_get_info()
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AsgardRaidReadError(f"userGetInfo failed: {exc}") from exc
    if not res.is_success:
        raise AsgardRaidReadError(
            f"userGetInfo failed ({res.error_name or res.status.value})"
        )
    detail = res.detail if isinstance(res.detail, dict) else {}
    response = detail.get("response")
    if not isinstance(response, dict):
        raise AsgardRaidReadError(
            "userGetInfo returned unexpected response (missing 'response' dict)"
        )
    resolved = str(response.get("id") or "").strip()
    if not resolved.isdigit() or int(resolved) <= 0:
        raise AsgardRaidError(
            "Could not resolve the game player ID; pass --user-id explicitly."
        )
    return resolved


def detect_boss(response: dict[str, Any]) -> str | None:
    """Detect the current boss from the shop (or active buffs) signature.

    ``shop`` is the primary source. When every slot has been bought (or the
    response omits the shop), the active ``buffs`` map still carries the
    week's buff IDs, so it is used as a fallback.
    """
    shop = response.get("shop")
    if isinstance(shop, dict) and shop:
        if is_osh_shop(shop):
            return BOSS_OSH
        if is_maestro_shop(shop):
            return BOSS_MAESTRO

    buffs = response.get("buffs")
    if isinstance(buffs, dict) and buffs:
        known = _source_buff_ids(buffs) & (OSH_BUFF_IDS | MAESTRO_BUFF_IDS)
        pseudo_shop = {
            str(index): {"buffId": buff_id}
            for index, buff_id in enumerate(sorted(known))
        }
        if is_osh_shop(pseudo_shop):
            return BOSS_OSH
        if is_maestro_shop(pseudo_shop):
            return BOSS_MAESTRO
    return None


def _source_buff_ids(source: dict[str, Any]) -> set[int]:
    """Collect buff IDs from a ``shop`` (``buffId``) or ``buffs`` (``id``) map."""
    ids: set[int] = set()
    for key, value in source.items():
        raw = value.get("buffId", value.get("id", key)) if isinstance(value, dict) else key
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _user_damage(response: dict[str, Any]) -> int:
    stats = response.get("userStats")
    if not isinstance(stats, dict):
        return 0
    return _safe_int(stats.get("damage"))


def _user_points(response: dict[str, Any]) -> int:
    stats = response.get("userStats")
    if not isinstance(stats, dict):
        return 0
    return _safe_int(stats.get("points"))


def parse_end_detail(detail: Any) -> tuple[int, bool, int, str | None, bool]:
    """Parse ``clanRaid_endBossBattle`` into ``(damage, win, stars, replay, after_invalid)``.

    ``after_invalid`` mirrors ``result.afterInvalid``: the server only marks the
    battle as auto-simulated when it rejected the (empty) client ``progress``.
    A missing / false value means the reported damage may be unreliable.
    """
    response = detail.get("response") if isinstance(detail, dict) else {}
    if not isinstance(response, dict):
        response = {}
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    damage_map = result.get("damage")
    if not isinstance(damage_map, dict):
        damage_map = response.get("damage")
    damage = (
        sum(_safe_int(value) for value in damage_map.values())
        if isinstance(damage_map, dict)
        else 0
    )
    replay_raw = response.get("replay")
    replay = str(replay_raw) if replay_raw is not None else None
    return (
        damage,
        bool(result.get("win")),
        _safe_int(result.get("stars")),
        replay,
        bool(result.get("afterInvalid")),
    )


# --- run ------------------------------------------------------------------


def parse_logged_teams(
    log: dict[str, Any], user_id: str, *, limit: int | None = None, latest: bool = False
) -> list[AsgardLoggedTeam]:
    """Reconstruct teams from ``clanRaid_logBoss`` in attack order (slots 1..N).

    Each battle's ``attackers`` map contains ``type="hero"`` units and one
    ``type="pet"`` unit. A hero's ``favorPetId`` is the patron used for favor
    (``petId`` is used when ``favorPetId`` is missing/0). Battles with anything
    other than exactly five unique heroes are skipped with a warning. More than
    ``limit`` battles is an error (slot mapping would be ambiguous) unless
    ``latest`` is set, in which case the most recent ``limit`` battles are used.
    """
    if limit is None:
        limit = len(TEAM_SLOTS)
    entries = log.get(str(user_id))
    if not isinstance(entries, dict) or not entries:
        raise AsgardRaidError(f"No boss battle logs found for player {user_id}.")

    parsed: list[tuple[tuple[int, int], AsgardLoggedTeam]] = []
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        attackers = entry.get("attackers")
        if not isinstance(attackers, dict):
            continue

        heroes: list[int] = []
        favor: dict[int, int] = {}
        pet: int | None = None
        for attacker_key, unit in attackers.items():
            if not isinstance(unit, dict):
                continue
            unit_id = _optional_int(unit.get("id", attacker_key))
            if unit_id is None or unit_id <= 0:
                continue
            unit_type = str(unit.get("type") or "").lower()
            if unit_type == "pet":
                pet = unit_id
                continue
            if unit_type != "hero":
                continue
            heroes.append(unit_id)
            patron = _optional_int(unit.get("favorPetId"))
            if patron is None or patron <= 0:
                patron = _optional_int(unit.get("petId"))
            if patron is not None and patron > 0:
                favor[unit_id] = patron

        if len(heroes) != TEAM_SIZE or len(set(heroes)) != len(heroes):
            logger.warning(
                "Skipping battle %s for player %s: expected %d unique heroes, got %d",
                key,
                user_id,
                TEAM_SIZE,
                len(heroes),
            )
            continue

        result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
        damage_map = result.get("damage") if isinstance(result.get("damage"), dict) else {}
        damage = sum(_safe_int(value) for value in damage_map.values())
        start_time = _optional_int(entry.get("startTime"))
        battle_id = str(entry.get("id") or key)
        sort_key = (start_time or 0, _optional_int(battle_id) or 0)
        parsed.append(
            (
                sort_key,
                AsgardLoggedTeam(
                    slot=0,
                    heroes=tuple(heroes),
                    pet=pet,
                    favor=favor,
                    battle_id=battle_id,
                    start_time=start_time,
                    damage=damage,
                ),
            )
        )

    if not parsed:
        raise AsgardRaidError(
            f"No complete {TEAM_SIZE}-hero boss battles found for player {user_id}."
        )
    parsed.sort(key=lambda item: item[0])
    if len(parsed) > limit:
        if not latest:
            raise AsgardRaidError(
                f"Found {len(parsed)} boss battles for player {user_id}; "
                f"cannot map more than {limit} battles to slots 1-{limit}. "
                "Refusing to guess (use --latest to take the most recent 5)."
            )
        parsed = parsed[-limit:]

    teams: list[AsgardLoggedTeam] = []
    for slot, (_, team) in enumerate(parsed, 1):
        teams.append(
            AsgardLoggedTeam(
                slot=slot,
                heroes=team.heroes,
                pet=team.pet,
                favor=team.favor,
                battle_id=team.battle_id,
                start_time=team.start_time,
                damage=team.damage,
            )
        )
    return teams


def import_teams_from_log(
    client: HWClient,
    *,
    account_alias: str,
    boss: str,
    user_id: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    latest: bool = False,
) -> AsgardRaidImportResult:
    """Register teams reconstructed from ``clanRaid_logBoss``.

    Existing slots are left untouched unless ``overwrite`` is set. When a slot
    is overwritten its ``enabled`` / ``memo`` values are preserved because the
    battle log carries no such metadata. With more than five battles in the
    log, ``latest`` selects the five most recent ones instead of erroring.
    """
    if not account_alias:
        raise AsgardRaidError("account alias is required")
    boss = validate_boss(boss)
    player_id = resolve_player_id(client, user_id)
    log = fetch_boss_log(client)
    logged = parse_logged_teams(log, player_id, latest=latest)
    existing = {
        team.slot: team for team in list_asgard_raid_teams(account_alias, boss)
    }
    result = AsgardRaidImportResult(boss=boss, dry_run=dry_run, teams=logged)

    print(
        f"\n{Emojis.STEP}Asgard raid import-log ({BOSS_LABELS[boss]}) for "
        f"'{account_alias}' (player {player_id})"
    )
    for team in logged:
        current = existing.get(team.slot)
        if current is None:
            action = "import"
        elif overwrite:
            action = "overwrite"
        else:
            action = "skip (already registered)"
        damage = (
            f" damage={format_number_with_suffix(team.damage)}" if team.damage else ""
        )
        print(f"  slot {team.slot}: {action} {format_logged_team(team)}{damage}")

    if dry_run:
        print(f"{Emojis.INFO}Dry-run: no teams were written.")
        return result

    for team in logged:
        current = existing.get(team.slot)
        if current is not None and not overwrite:
            result.skipped_slots.append(team.slot)
            continue
        set_asgard_raid_team(
            account_alias,
            AsgardTeam(
                boss=boss,
                slot=team.slot,
                heroes=team.heroes,
                pet=team.pet,
                favor=team.favor,
                enabled=current.enabled if current is not None else True,
                memo=current.memo if current is not None else None,
            ),
        )
        result.imported_slots.append(team.slot)

    print(
        f"{Emojis.SUCCESS}Imported slots: {result.imported_slots or '-'}; "
        f"skipped existing: {result.skipped_slots or '-'}"
    )
    return result


def claim_rating_rewards(client: HWClient) -> list[AsgardRewardResult]:
    """Claim the weekly guild (standingClan) and personal (standingUser) rewards.

    The reward can be claimed once per week. Any non-auth failure (already
    received, not available, transport error) is recorded as ``SKIPPED`` so the
    raid run itself still succeeds; auth errors propagate.
    """
    results: list[AsgardRewardResult] = []
    for reward_type in REWARD_TYPES:
        # Space the clan / user calls (and the preceding end call) apart.
        client.sleep()
        payload = {
            "calls": [
                {
                    "name": ApiAction.CLAN_RAID_FARM_RATING_REWARD,
                    "args": {"type": reward_type},
                    "context": {"actionTs": 0},
                    "ident": "body",
                }
            ]
        }
        try:
            res = client.call(payload, max_retries=0)
        except HWAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("clanRaid_farmRatingReward(%s) failed: %s", reward_type, exc)
            results.append(
                AsgardRewardResult(reward_type, ResponseStatus.SKIPPED, str(exc))
            )
            continue
        if res.is_success:
            results.append(AsgardRewardResult(reward_type, ResponseStatus.SUCCESS))
            continue
        error = res.error_name or res.status.value
        logger.info("clanRaid_farmRatingReward(%s) skipped: %s", reward_type, error)
        results.append(AsgardRewardResult(reward_type, ResponseStatus.SKIPPED, error))
    return results


def _start_payload(team: AsgardTeam) -> dict[str, Any]:
    args: dict[str, Any] = {
        "heroes": list(team.heroes),
        "favor": {str(hero): patron for hero, patron in team.favor.items()},
    }
    if team.pet is not None:
        args["pet"] = team.pet
    return {
        "calls": [
            {
                "name": ApiAction.CLAN_RAID_START_BOSS_BATTLE,
                "args": args,
                "context": {"actionTs": 0},
                "ident": "body",
            }
        ]
    }


def _end_payload() -> dict[str, Any]:
    return {
        "calls": [
            {
                "name": ApiAction.CLAN_RAID_END_BOSS_BATTLE,
                "args": {"result": {"win": False, "stars": 0}, "progress": []},
                "context": {"actionTs": 0},
                "ident": "body",
            }
        ]
    }


def _team_result(
    team: AsgardTeam,
    status: ResponseStatus,
    *,
    damage: int = 0,
    win: bool = False,
    stars: int = 0,
    replay: str | None = None,
    error: str | None = None,
    after_invalid: bool = False,
) -> AsgardRaidTeamResult:
    return AsgardRaidTeamResult(
        slot=team.slot,
        memo=team.memo,
        enabled=team.enabled,
        status=status,
        damage=damage,
        win=win,
        stars=stars,
        replay=replay,
        error=error,
        after_invalid=after_invalid,
    )


def _print_plan(
    account: str, boss: str, teams: list[AsgardTeam], attempts: int | None
) -> None:
    label = BOSS_LABELS[boss]
    attempts_text = "-" if attempts is None else str(attempts)
    print(f"\n{Emojis.STEP}Asgard raid plan ({label}) for '{account}' — boss attempts: {attempts_text}")
    for team in teams:
        state = "enabled" if team.enabled else "disabled"
        memo = f" memo={team.memo}" if team.memo else ""
        print(f"  slot {team.slot} [{state}] {format_team(team)}{memo}")
    print(f"  weekly rewards: {', '.join(REWARD_TYPES)} (claimed at the end of a real run)")


def _print_summary(account: str, result: AsgardRaidRunResult) -> None:
    label = BOSS_LABELS.get(result.boss or "", result.boss or "-")
    print(f"\n{Emojis.FINISH}Asgard raid ({label}) for '{account}'")
    for team in result.teams:
        if team.status == ResponseStatus.SKIPPED:
            reason = "disabled" if not team.enabled else "no attempts left"
            print(f"  slot {team.slot}: skipped ({reason})")
            continue
        memo = f" memo={team.memo}" if team.memo else ""
        if team.status == ResponseStatus.SUCCESS:
            replay = f" replay={team.replay}" if team.replay else ""
            auto = "" if team.after_invalid else " auto=False"
            print(
                f"  slot {team.slot}: damage={format_number_with_suffix(team.damage)} "
                f"win={team.win} stars={team.stars}{replay}{auto}{memo}"
            )
        else:
            print(f"  slot {team.slot}: ERROR {team.error}{memo}")
    for reward in result.rewards:
        if reward.status == ResponseStatus.SUCCESS:
            print(f"  reward {reward.reward_type}: claimed")
        else:
            print(f"  reward {reward.reward_type}: skipped ({reward.error})")
    print(f"  Total battle damage: {format_number_with_suffix(result.total_damage)}")
    if result.attempts_before is not None or result.attempts_after is not None:
        before = "-" if result.attempts_before is None else result.attempts_before
        after = "-" if result.attempts_after is None else result.attempts_after
        print(f"  Boss attempts: {before} -> {after}")
    print(
        "  Score (account damage): "
        f"{format_number_with_suffix(result.damage_before)} -> "
        f"{format_number_with_suffix(result.damage_after)}"
    )
    print(f"  Score (points): {result.points_before} -> {result.points_after}")
    not_auto = sum(
        1
        for team in result.teams
        if team.status == ResponseStatus.SUCCESS and not team.after_invalid
    )
    logger.info(
        "asgard-raid score (%s): damage %d -> %d, points %d -> %d, "
        "bossAttempts %s -> %s, rewards %s, end-not-auto %d",
        account,
        result.damage_before,
        result.damage_after,
        result.points_before,
        result.points_after,
        result.attempts_before,
        result.attempts_after,
        ", ".join(
            f"{reward.reward_type}={reward.status.value}" for reward in result.rewards
        )
        or "none",
        not_auto,
    )


def _refresh_after(
    client: HWClient, result: AsgardRaidRunResult, executed: int
) -> None:
    """Refresh the post-run score / attempts (best effort).

    Fields missing from the refreshed response fall back to local estimates
    (``before - executed`` / ``before + total_damage``) instead of 0, so a
    partial ``clanRaid_getInfo`` never wipes the displayed score.
    """
    try:
        after = fetch_clan_raid_info(client)
    except HWAuthError:
        raise
    except AsgardRaidError as exc:
        print(f"{Emojis.WARNING}Could not refresh raid info: {exc}")
        after = {}
    attempts_after = _optional_int(after.get("bossAttempts")) if after else None
    if attempts_after is None:
        attempts_after = (
            None
            if result.attempts_before is None
            else result.attempts_before - executed
        )
    result.attempts_after = attempts_after
    stats = after.get("userStats") if isinstance(after.get("userStats"), dict) else {}
    damage_after = _optional_int(stats.get("damage")) if stats else None
    points_after = _optional_int(stats.get("points")) if stats else None
    result.damage_after = (
        damage_after
        if damage_after is not None
        else result.damage_before + result.total_damage
    )
    result.points_after = (
        points_after if points_after is not None else result.points_before
    )


def run_asgard_raid(
    client: HWClient,
    *,
    account_alias: str | None = None,
    boss: str | None = None,
    dry_run: bool = False,
) -> AsgardRaidRunResult:
    """Execute the enabled teams for the detected (or forced) boss."""
    if not account_alias:
        raise AsgardRaidError("account alias is required")
    explicit_boss = validate_boss(boss) if boss is not None else None

    info = fetch_clan_raid_info(client)
    detected = detect_boss(info)
    selected = explicit_boss or detected
    result = AsgardRaidRunResult(
        boss=selected,
        dry_run=dry_run,
        attempts_before=_optional_int(info.get("bossAttempts")),
        damage_before=_user_damage(info),
        points_before=_user_points(info),
    )

    if selected is None:
        print(
            f"{Emojis.INFO}Asgard week not detected (unknown shop lineup); skipping battles. "
            "Use --boss osh|maestro to force a lineup."
        )
        if dry_run:
            print(f"{Emojis.INFO}Dry-run: weekly rewards are not claimed.")
            return result
        # The weekly ranking reward is independent of the current lineup
        # detection; try it anyway (already-received / unavailable → skipped).
        result.rewards = claim_rating_rewards(client)
        _refresh_after(client, result, executed=0)
        _print_summary(account_alias, result)
        return result
    if explicit_boss and detected and explicit_boss != detected:
        print(
            f"{Emojis.WARNING}Requested boss '{explicit_boss}' does not match the "
            f"detected week '{detected}'; using '{explicit_boss}'."
        )

    teams = list_asgard_raid_teams(account_alias, selected)
    if dry_run:
        _print_plan(account_alias, selected, teams, result.attempts_before)
        print(f"{Emojis.INFO}Dry-run: weekly rewards are not claimed.")
        result.teams = [_team_result(team, ResponseStatus.SKIPPED) for team in teams]
        return result

    executed = 0
    if not teams:
        print(
            f"{Emojis.INFO}No {BOSS_LABELS[selected]} teams registered for "
            f"'{account_alias}'. Register them with "
            f"`hw-genie asgard-raid team set <1-5> --boss {selected} ...`."
        )
    elif result.attempts_before is not None and result.attempts_before <= 0:
        print(
            f"{Emojis.INFO}No boss attempts left; skipping battles "
            "(weekly rewards are still claimed)."
        )
        result.teams = [_team_result(team, ResponseStatus.SKIPPED) for team in teams]
    else:
        for team in teams:
            if not team.enabled:
                print(f"{Emojis.INFO}slot {team.slot}: disabled; skipping.")
                result.teams.append(_team_result(team, ResponseStatus.SKIPPED))
                continue
            if (
                result.attempts_before is not None
                and result.attempts_before - executed <= 0
            ):
                print(f"{Emojis.INFO}slot {team.slot}: no boss attempts left; skipping.")
                result.teams.append(_team_result(team, ResponseStatus.SKIPPED))
                continue

            # Cooperative Ctrl+C: stop between battles (the attempt for the
            # current slot has not been spent yet, so this is not an error).
            from hw_genie.runner import is_cancelled

            if is_cancelled():
                print(f"{Emojis.INFO}cancelled; stopping before slot {team.slot}.")
                break

            print(
                f"{Emojis.STEP}slot {team.slot}: starting boss battle "
                f"(heroes={list(team.heroes)} pet={team.pet})..."
            )
            start_res = client.call(_start_payload(team), max_retries=0)
            if not start_res.is_success:
                error = start_res.error_name or start_res.status.value
                print(f"{Emojis.ERROR}slot {team.slot}: start failed ({error}).")
                result.teams.append(
                    _team_result(team, ResponseStatus.ERROR, error=f"start failed: {error}")
                )
                result.error = f"start failed for slot {team.slot}: {error}"
                break
            executed += 1

            end_res = client.call(_end_payload(), max_retries=0)
            if not end_res.is_success:
                error = end_res.error_name or end_res.status.value
                print(
                    f"{Emojis.WARNING}slot {team.slot}: start accepted but end failed "
                    f"({error}); stopping."
                )
                result.teams.append(
                    _team_result(team, ResponseStatus.ERROR, error=f"end failed: {error}")
                )
                result.error = f"end failed for slot {team.slot}: {error}"
                break

            damage, win, stars, replay, after_invalid = parse_end_detail(end_res.detail)
            if not after_invalid:
                print(
                    f"{Emojis.WARNING}slot {team.slot}: end was not auto-simulated "
                    "(afterInvalid missing/false); damage may be unreliable."
                )
                logger.warning(
                    "asgard-raid slot %s: end response has afterInvalid=false "
                    "(damage may be unreliable)",
                    team.slot,
                )
            print(
                f"{Emojis.SUCCESS}slot {team.slot}: damage="
                f"{format_number_with_suffix(damage)} win={win} stars={stars}"
                + (f" replay={replay}" if replay else "")
            )
            result.teams.append(
                _team_result(
                    team,
                    ResponseStatus.SUCCESS,
                    damage=damage,
                    win=win,
                    stars=stars,
                    replay=replay,
                    after_invalid=after_invalid,
                )
            )
            # Space the next start (if any) away from this end call.
            client.sleep()

    client.sleep()
    result.rewards = claim_rating_rewards(client)
    _refresh_after(client, result, executed)
    _print_summary(account_alias, result)
    return result


__all__ = [
    "BOSS_CHOICES",
    "BOSS_LABELS",
    "BOSS_MAESTRO",
    "BOSS_OSH",
    "REWARD_STANDING_CLAN",
    "REWARD_STANDING_USER",
    "REWARD_TYPES",
    "AsgardRaidError",
    "AsgardRaidImportResult",
    "AsgardRaidReadError",
    "AsgardRaidRunResult",
    "AsgardRaidTeamResult",
    "AsgardRewardResult",
    "AsgardLoggedTeam",
    "AsgardTeam",
    "TEAM_SIZE",
    "TEAM_SLOTS",
    "claim_rating_rewards",
    "detect_boss",
    "fetch_boss_log",
    "fetch_clan_raid_info",
    "format_logged_team",
    "format_team",
    "import_teams_from_log",
    "list_asgard_raid_teams",
    "parse_end_detail",
    "parse_favor_args",
    "parse_heroes",
    "parse_logged_teams",
    "remove_asgard_raid_team",
    "resolve_player_id",
    "run_asgard_raid",
    "set_asgard_raid_team",
    "validate_boss",
    "validate_pet",
    "validate_slot",
    "validate_team",
]
