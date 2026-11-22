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


@dataclass
class AsgardRewardResult:
    """Weekly ranking reward claim outcome (second claim is skipped)."""

    reward_type: str
    status: ResponseStatus
    error: str | None = None


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
    parts = [f"heroes={list(team.heroes)}"]
    if team.pet is not None:
        parts.append(f"pet={team.pet}")
    if team.favor:
        favor = " ".join(f"{hero}={patron}" for hero, patron in sorted(team.favor.items()))
        parts.append(f"favor={favor}")
    return " ".join(parts)


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


def parse_end_detail(detail: Any) -> tuple[int, bool, int, str | None]:
    """Parse ``clanRaid_endBossBattle`` into ``(damage, win, stars, replay)``."""
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
    return damage, bool(result.get("win")), _safe_int(result.get("stars")), replay


# --- run ------------------------------------------------------------------


def claim_rating_rewards(client: HWClient) -> list[AsgardRewardResult]:
    """Claim the weekly guild (standingClan) and personal (standingUser) rewards.

    The reward can be claimed once per week. Any non-auth failure (already
    received, not available, transport error) is recorded as ``SKIPPED`` so the
    raid run itself still succeeds; auth errors propagate.
    """
    results: list[AsgardRewardResult] = []
    for reward_type in REWARD_TYPES:
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
            print(
                f"  slot {team.slot}: damage={format_number_with_suffix(team.damage)} "
                f"win={team.win} stars={team.stars}{replay}{memo}"
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
    logger.info(
        "asgard-raid score (%s): damage %d -> %d, points %d -> %d, "
        "bossAttempts %s -> %s, rewards %s",
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
    )


def _refresh_after(
    client: HWClient, result: AsgardRaidRunResult, executed: int
) -> None:
    """Refresh the post-run score / attempts (best effort)."""
    try:
        after = fetch_clan_raid_info(client)
    except HWAuthError:
        raise
    except AsgardRaidError as exc:
        print(f"{Emojis.WARNING}Could not refresh raid info: {exc}")
        after = {}
    if after:
        result.attempts_after = _optional_int(after.get("bossAttempts"))
        result.damage_after = _user_damage(after)
        result.points_after = _user_points(after)
    else:
        result.attempts_after = (
            None
            if result.attempts_before is None
            else result.attempts_before - executed
        )
        result.damage_after = result.damage_before + result.total_damage
        result.points_after = result.points_before


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

            damage, win, stars, replay = parse_end_detail(end_res.detail)
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
                )
            )

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
    "AsgardRaidReadError",
    "AsgardRaidRunResult",
    "AsgardRaidTeamResult",
    "AsgardRewardResult",
    "AsgardTeam",
    "TEAM_SIZE",
    "TEAM_SLOTS",
    "claim_rating_rewards",
    "detect_boss",
    "fetch_clan_raid_info",
    "format_team",
    "list_asgard_raid_teams",
    "parse_end_detail",
    "parse_favor_args",
    "parse_heroes",
    "remove_asgard_raid_team",
    "run_asgard_raid",
    "set_asgard_raid_team",
    "validate_boss",
    "validate_pet",
    "validate_slot",
    "validate_team",
]
