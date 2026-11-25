"""Power Tournament (Hero / Titan) monitoring and reward claiming.

The event alternates between titan and hero power. ``powerTournament_getState``
reports the active ``type`` plus the current day's reward milestones
(``currentDayRewards``), and ``powerTournament_getGroupInfo`` returns the group
ranking as ``points`` (userId -> points), ``users`` (userId -> profile incl.
``lastLoginTime``) and the place -> reward table.

Only reward IDs explicitly listed by the API are sent to
``powerTournament_dailyFarm``; the CLI never guesses an ID. A milestone is
considered claimable only when it is not farmed yet, its threshold is known and
today's points (``currentDailyPoints``) have reached that threshold; the
cumulative tournament points (``currentTournamentPoints``) are used only as a
fallback when today's points are unavailable.
Already-farmed / unavailable claims are reported as skipped, unknown errors as
failed, and auth errors propagate.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hw_genie.core.client import (
    AUTH_ERROR_NAMES,
    ApiAction,
    Emojis,
    ErrorName,
    HWAuthError,
    HWClient,
    Messages,
    ResponseStatus,
    _safe_int,
)
from hw_genie.core.utils import (
    display_width,
    format_number_with_suffix,
    pad,
    rank_color,
    style,
)

DEFAULT_TOP = 5
MAX_TOP = 50
CLAN_STATS_BATCH = 10
POWER_BATCH = 10
CLAN_INVITES_ARGS = {
    "minTeamLevel": 35,
    "minHeroesPower": 0,
    "minTitansPower": 0,
    "languageId": 2,
    "clanStatus": 0,
    "page": 0,
}

TYPE_LABELS = {
    "titans": "Titan",
    "titan": "Titan",
    "heroes": "Hero",
    "hero": "Hero",
}

POWER_FIELDS = {
    "titans": "titansPower",
    "titan": "titansPower",
    "heroes": "heroesPower",
    "hero": "heroesPower",
}


def _power_field(tournament_type: str) -> str | None:
    """Return the event-type power field, or ``None`` for an unknown type.

    An unknown / empty type must not silently fall back to Titan power, so the
    lookup is explicit instead of a substring test.
    """
    return POWER_FIELDS.get(str(tournament_type or "").strip().lower())


#: Claim failures that mean "nothing to do" rather than a broken run.
CLAIM_SKIP_ERRORS = frozenset(
    {
        ErrorName.NOT_AVAILABLE.value,
        ErrorName.LIMIT_REACHED.value,
        ErrorName.NOT_ENOUGH.value,
    }
)

#: ``getState`` failures that mean the event is not running.
NO_EVENT_ERRORS = frozenset({ErrorName.NOT_AVAILABLE.value})

FINAL_REWARD_PENDING = (
    "Final ranking reward: not claimed (the API does not expose a claim id; "
    "claim it in game after the event ends)."
)


class PowerTournamentError(Exception):
    """Invalid input or an unexpected response shape."""


class PowerTournamentReadError(PowerTournamentError):
    """``getState`` / ``getGroupInfo`` could not be fetched."""


@dataclass(frozen=True)
class TournamentReward:
    reward_id: int
    points: int | None
    claimable: bool
    is_farmed: bool


@dataclass(frozen=True)
class TournamentMemberStats:
    """Guild-member stats from ``clanGetInfo.membersStat`` (today / week)."""

    activity_today: int | None = None
    activity_week: int | None = None
    titanite_today: int | None = None
    titanite_week: int | None = None
    prestige_today: int | None = None
    prestige_week: int | None = None

    def _values(self) -> tuple[int | None, ...]:
        return (
            self.activity_today,
            self.activity_week,
            self.titanite_today,
            self.titanite_week,
            self.prestige_today,
            self.prestige_week,
        )

    def is_empty(self) -> bool:
        return all(value is None for value in self._values())

    def is_complete(self) -> bool:
        return all(value is not None for value in self._values())


def _merge_stats(
    primary: TournamentMemberStats | None,
    fallback: TournamentMemberStats | None,
) -> TournamentMemberStats | None:
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    return TournamentMemberStats(
        activity_today=primary.activity_today
        if primary.activity_today is not None
        else fallback.activity_today,
        activity_week=primary.activity_week
        if primary.activity_week is not None
        else fallback.activity_week,
        titanite_today=primary.titanite_today
        if primary.titanite_today is not None
        else fallback.titanite_today,
        titanite_week=primary.titanite_week
        if primary.titanite_week is not None
        else fallback.titanite_week,
        prestige_today=primary.prestige_today
        if primary.prestige_today is not None
        else fallback.prestige_today,
        prestige_week=primary.prestige_week
        if primary.prestige_week is not None
        else fallback.prestige_week,
    )


@dataclass(frozen=True)
class TournamentRankEntry:
    place: int
    user_id: str
    name: str
    points: int
    last_login_ts: int | None = None
    is_self: bool = False
    server_id: str | None = None
    clan_id: str | None = None
    power: int | None = None
    stats: TournamentMemberStats | None = None


@dataclass
class TournamentState:
    tournament_type: str
    type_label: str
    end_ts: int | None
    daily_points: int | None
    total_points: int | None
    rewards: list[TournamentReward] = field(default_factory=list)
    final_reward_farmed: bool = False
    ended: bool = False


@dataclass
class TournamentClaimResult:
    reward_id: int
    status: ResponseStatus
    error: str | None = None


@dataclass
class PowerTournamentResult:
    account: str
    dry_run: bool
    state: TournamentState | None = None
    ranking: list[TournamentRankEntry] = field(default_factory=list)
    claims: list[TournamentClaimResult] = field(default_factory=list)
    own_user_id: str | None = None
    own_place: int | None = None
    ranking_error: str | None = None
    enrichment_error: str | None = None
    error: str | None = None

    @property
    def failed_claims(self) -> int:
        return sum(1 for c in self.claims if c.status == ResponseStatus.ERROR)


def _unwrap_item(item: Any) -> Any:
    """Unwrap ``{"result": {"response": X}}`` / ``{"response": X}`` to X."""
    if not isinstance(item, dict):
        return item
    if "result" in item:
        result = item.get("result")
        if isinstance(result, dict) and "response" in result:
            return result.get("response")
        return result
    if "response" in item:
        return item.get("response")
    return item


def parse_state(response: Any, *, now: int | None = None) -> TournamentState:
    """Parse a ``powerTournament_getState`` response body.

    ``currentDayRewards`` may be absent (no rewards offered) or a list. Any
    other shape - a mapping, a string, or an entry that is not an object with a
    parseable ``id`` - raises instead of being silently skipped, so a broken
    payload cannot look like "nothing to claim".
    """
    if not isinstance(response, dict) or not any(
        key in response for key in ("type", "endTime", "currentDayRewards")
    ):
        shape = (
            f"keys={sorted(response)}"
            if isinstance(response, dict)
            else type(response).__name__
        )
        raise PowerTournamentReadError(
            f"unexpected getState response shape ({shape})"
        )
    raw_type = str(response.get("type") or "").strip().lower()
    end_ts = _safe_int(response.get("endTime"), None)
    if "currentDayRewards" not in response:
        raw_rewards: list[Any] = []
    else:
        raw_rewards = response["currentDayRewards"]
        if not isinstance(raw_rewards, list):
            raise PowerTournamentReadError(
                "unexpected getState currentDayRewards shape "
                f"({type(raw_rewards).__name__}, expected a list)"
            )

    daily_points = _safe_int(response.get("currentDailyPoints"), None)
    total_points = _safe_int(response.get("currentTournamentPoints"), None)
    eligible_points = daily_points if daily_points is not None else total_points

    rewards: list[TournamentReward] = []
    for index, item in enumerate(raw_rewards):
        if not isinstance(item, dict):
            raise PowerTournamentReadError(
                "unexpected getState currentDayRewards entry shape "
                f"(index {index}: {type(item).__name__}, expected an object)"
            )
        reward_id = _safe_int(item.get("id"), None)
        if reward_id is None:
            raise PowerTournamentReadError(
                "unexpected getState currentDayRewards entry shape "
                f"(index {index}: missing or unparseable id)"
            )
        threshold = _safe_int(item.get("points"), None)
        is_farmed = _safe_int(item.get("isFarmed"), 0) != 0
        claimable = (
            not is_farmed
            and threshold is not None
            and eligible_points is not None
            and eligible_points >= threshold
        )
        rewards.append(
            TournamentReward(
                reward_id=reward_id,
                points=threshold,
                claimable=claimable,
                is_farmed=is_farmed,
            )
        )

    current = int(now if now is not None else time.time())
    ended = end_ts is not None and current >= end_ts
    type_label = TYPE_LABELS.get(raw_type, raw_type.title() if raw_type else "Unknown")
    return TournamentState(
        tournament_type=raw_type,
        type_label=type_label,
        end_ts=end_ts,
        daily_points=daily_points,
        total_points=total_points,
        rewards=rewards,
        final_reward_farmed=_safe_int(response.get("finalRewardFarmed"), 0) != 0,
        ended=ended,
    )


def _user_sort_key(user_id: str) -> tuple[int, int, str]:
    if user_id.isdigit():
        return (0, int(user_id), "")
    return (1, 0, user_id)


def _first_int(mapping: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in mapping:
            value = _safe_int(mapping.get(key), None)
            if value is not None:
                return value
    return None


def _parse_stats(mapping: Any) -> TournamentMemberStats | None:
    if not isinstance(mapping, dict):
        return None
    stats = TournamentMemberStats(
        activity_today=_first_int(mapping, "todayActivity", "activityToday"),
        activity_week=_first_int(mapping, "activitySum", "activityWeek"),
        titanite_today=_first_int(
            mapping, "todayDungeonActivity", "todayTitanite", "titaniteToday"
        ),
        titanite_week=_first_int(
            mapping,
            "dungeonActivitySum",
            "titaniteSum",
            "weekTitanite",
            "titaniteWeek",
        ),
        prestige_today=_first_int(mapping, "todayPrestige", "prestigeToday"),
        prestige_week=_first_int(
            mapping, "prestigeSum", "weekPrestige", "prestigeWeek"
        ),
    )
    return None if stats.is_empty() else stats


def _stats_from_profile(profile: dict[str, Any]) -> TournamentMemberStats | None:
    for key in ("stat", "stats", "memberStat", "membersStat"):
        nested = profile.get(key)
        if isinstance(nested, dict):
            stats = _parse_stats(nested)
            if stats is not None:
                return stats
    return _parse_stats(profile)


def _optional_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def parse_group_info(
    response: Any,
    *,
    power_field: str | None,
    own_user_id: str | None = None,
) -> list[TournamentRankEntry]:
    """Build the full group ranking (points desc) from ``getGroupInfo``.

    A malformed success payload raises instead of being read as an empty
    ranking, so the CLI cannot exit 0 on a broken response. An empty
    ``points: {}`` object is still a legitimate empty ranking.

    ``power`` is read from ``power_field`` (``titansPower`` for Titan events,
    ``heroesPower`` for Hero events); ``None`` means the event type is unknown
    and no power is read. Member stats are read from the user profile when
    present; ``run_power_tournament`` fills the rest from
    ``clanInvites_search`` / ``clanGetInfo``.
    """
    points = response.get("points") if isinstance(response, dict) else None
    if not isinstance(points, dict):
        shape = (
            f"keys={sorted(response)}"
            if isinstance(response, dict)
            else type(response).__name__
        )
        raise PowerTournamentReadError(
            f"unexpected getGroupInfo response shape ({shape})"
        )
    users = response.get("users")
    if not isinstance(users, dict):
        users = {}

    rows: list[
        tuple[
            str,
            str,
            int,
            int | None,
            str | None,
            str | None,
            int | None,
            TournamentMemberStats | None,
        ]
    ] = []
    for user_id, raw_points in points.items():
        uid = str(user_id)
        profile = users.get(uid)
        name = ""
        last_login = None
        server_id = None
        clan_id = None
        power = None
        stats = None
        if isinstance(profile, dict):
            name = str(profile.get("name") or "").strip()
            last_login = _safe_int(profile.get("lastLoginTime"), None)
            server_id = _optional_id(profile.get("serverId"))
            clan_id = _optional_id(profile.get("clanId"))
            if power_field is not None:
                power = _first_int(profile, power_field)
            stats = _stats_from_profile(profile)
        rows.append(
            (
                uid,
                name or f"User {uid}",
                _safe_int(raw_points, 0),
                last_login,
                server_id,
                clan_id,
                power,
                stats,
            )
        )

    rows.sort(key=lambda row: (-row[2], _user_sort_key(row[0])))
    entries: list[TournamentRankEntry] = []
    previous_points: int | None = None
    previous_place = 0
    for index, (
        uid,
        name,
        pts,
        last_login,
        server_id,
        clan_id,
        power,
        stats,
    ) in enumerate(rows, start=1):
        # Competition ranking: ties share the earlier place (900, 900, 500 -> 1, 1, 3).
        place = previous_place if pts == previous_points else index
        entries.append(
            TournamentRankEntry(
                place=place,
                user_id=uid,
                name=name,
                points=pts,
                last_login_ts=last_login,
                is_self=own_user_id is not None and uid == str(own_user_id),
                server_id=server_id,
                clan_id=clan_id,
                power=power,
                stats=stats,
            )
        )
        previous_points = pts
        previous_place = place
    return entries


def _members_stat_map(response: Any) -> dict[str, TournamentMemberStats]:
    if not isinstance(response, dict):
        return {}
    raw = response.get("membersStat")
    if isinstance(raw, dict):
        # Object form: keyed by userId, values may omit ``userId`` entirely.
        items: list[tuple[Any, Any]] = list(raw.items())
    elif isinstance(raw, list):
        items = [(None, item) for item in raw]
    else:
        return {}
    stats_by_user: dict[str, TournamentMemberStats] = {}
    for key, item in items:
        if not isinstance(item, dict):
            continue
        user_id = None
        for candidate in (item.get("userId"), item.get("id"), key):
            user_id = _optional_id(candidate)
            if user_id is not None:
                break
        if user_id is None:
            continue
        stats = _parse_stats(item)
        if stats is not None:
            stats_by_user[user_id] = stats
    return stats_by_user


def _member_power(response: Any, power_field: str) -> dict[str, int]:
    """Map userId -> the type-specific power from a search response."""
    if not isinstance(response, dict):
        return {}
    items = response.get("items")
    if not isinstance(items, list):
        return {}
    powers: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        user = item.get("user")
        if not isinstance(user, dict):
            continue
        user_id = _optional_id(user.get("id"))
        if user_id is None:
            continue
        power = _first_int(user, power_field)
        if power is not None:
            powers[user_id] = power
    return powers


def _parse_power_response(
    response: Any, power_field: str
) -> tuple[dict[str, int], str | None]:
    """Parse one ``clanInvites_search`` body.

    An empty ``items`` list is legitimate (the user was not found); any other
    shape is reported as a warning instead of being silently dropped.
    """
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        return {}, "clanInvites_search returned an unexpected payload"
    return _member_power(response, power_field), None


def fetch_member_power(
    client: HWClient, entries: list[TournamentRankEntry], power_field: str
) -> tuple[dict[str, int], str | None]:
    """Fill the event-type power (``titansPower`` or ``heroesPower``).

    The RPC name is misleading: ``args.id`` is a **userId** and the response
    carries ``heroesPower`` / ``titansPower``. The game's Power Tournament group
    view uses the same batched calls. Failures are returned as a warning.
    """
    user_ids = sorted({entry.user_id for entry in entries if entry.power is None})
    if not user_ids:
        return {}, None

    powers: dict[str, int] = {}
    errors: list[str] = []
    for start in range(0, len(user_ids), POWER_BATCH):
        chunk = user_ids[start : start + POWER_BATCH]
        calls = [
            {
                "name": ApiAction.CLAN_INVITES_SEARCH,
                "args": {"id": user_id, **CLAN_INVITES_ARGS},
                "context": {"actionTs": 0},
                "ident": f"power_{user_id}",
            }
            for user_id in chunk
        ]
        res = client.call({"calls": calls})
        client.sleep()
        if not res.is_success:
            errors.append(
                f"clanInvites_search failed ({res.error_name or res.status.value})"
            )
            continue
        if len(chunk) == 1:
            chunk_powers, chunk_error = _parse_power_response(
                _unwrap_item(res.detail), power_field
            )
            powers.update(chunk_powers)
            if chunk_error:
                errors.append(chunk_error)
            continue
        if not isinstance(res.detail, dict):
            errors.append("clanInvites_search returned an unexpected payload")
            continue
        for index, user_id in enumerate(chunk):
            item = res.detail.get(f"power_{user_id}")
            if not isinstance(item, dict):
                item = res.detail.get(str(index))
            if not isinstance(item, dict):
                errors.append(f"power {user_id}: no result")
                continue
            if "error" in item:
                error = item.get("error")
                name = error.get("name") if isinstance(error, dict) else str(error)
                if name in AUTH_ERROR_NAMES:
                    raise HWAuthError(Messages.AUTH_ERROR)
                errors.append(f"power {user_id}: {name}")
                continue
            chunk_powers, chunk_error = _parse_power_response(
                _unwrap_item(item), power_field
            )
            powers.update(chunk_powers)
            if chunk_error:
                errors.append(f"power {user_id}: {chunk_error}")
    return powers, ("; ".join(errors) if errors else None)


def fetch_member_stats(
    client: HWClient, entries: list[TournamentRankEntry]
) -> tuple[dict[str, TournamentMemberStats], str | None]:
    """Fill activity/titanite/prestige from each player's clan.

    ``clanGetInfo`` accepts ``{"clanId": ...}`` for other clans. Requests are
    batched (up to ``CLAN_STATS_BATCH`` clans per RPC); failures are returned as
    a warning string so the ranking itself still succeeds.
    """
    missing = [
        entry
        for entry in entries
        if entry.clan_id and (entry.stats is None or not entry.stats.is_complete())
    ]
    clan_ids = sorted({entry.clan_id for entry in missing})
    if not clan_ids:
        return {}, None

    stats_by_user: dict[str, TournamentMemberStats] = {}
    errors: list[str] = []
    for start in range(0, len(clan_ids), CLAN_STATS_BATCH):
        chunk = clan_ids[start : start + CLAN_STATS_BATCH]
        calls = [
            {
                "name": ApiAction.CLAN_GET_INFO,
                "args": {"clanId": clan_id},
                "context": {"actionTs": 0},
                "ident": f"clan_{clan_id}",
            }
            for clan_id in chunk
        ]
        res = client.call({"calls": calls})
        client.sleep()
        if not res.is_success:
            errors.append(
                f"clanGetInfo failed ({res.error_name or res.status.value})"
            )
            continue
        if len(chunk) == 1:
            stats_by_user.update(_members_stat_map(_unwrap_item(res.detail)))
            continue
        if not isinstance(res.detail, dict):
            errors.append("clanGetInfo returned an unexpected payload")
            continue
        for index, clan_id in enumerate(chunk):
            item = res.detail.get(f"clan_{clan_id}")
            if not isinstance(item, dict):
                # HWClient falls back to string indices when idents are absent.
                item = res.detail.get(str(index))
            if not isinstance(item, dict):
                errors.append(f"clan {clan_id}: no result")
                continue
            if "error" in item:
                error = item.get("error")
                name = error.get("name") if isinstance(error, dict) else str(error)
                if name in AUTH_ERROR_NAMES:
                    raise HWAuthError(Messages.AUTH_ERROR)
                errors.append(f"clan {clan_id}: {name}")
                continue
            stats_by_user.update(_members_stat_map(_unwrap_item(item)))
    return stats_by_user, ("; ".join(errors) if errors else None)


def _single_call(client: HWClient, name: str, args: dict[str, Any] | None = None) -> Any:
    res = client.call(
        {
            "calls": [
                {
                    "name": name,
                    "args": args or {},
                    "context": {"actionTs": 0},
                    "ident": "body",
                }
            ]
        }
    )
    client.sleep()
    if not res.is_success:
        raise PowerTournamentReadError(
            f"{name} failed ({res.error_name or res.status.value})."
        )
    return _unwrap_item(res.detail)


def fetch_state(client: HWClient) -> TournamentState | None:
    """Fetch and parse the event state. ``None`` means no active event."""
    res = client.call(
        {
            "calls": [
                {
                    "name": ApiAction.POWER_TOURNAMENT_GET_STATE,
                    "args": {},
                    "context": {"actionTs": 0},
                    "ident": "body",
                }
            ]
        }
    )
    client.sleep()
    if res.is_success:
        return parse_state(_unwrap_item(res.detail))
    if res.error_name in NO_EVENT_ERRORS:
        return None
    raise PowerTournamentReadError(
        f"powerTournament_getState failed ({res.error_name or res.status.value})."
    )


def fetch_group(client: HWClient) -> Any:
    return _single_call(client, ApiAction.POWER_TOURNAMENT_GET_GROUP_INFO)


def fetch_own_user_id(client: HWClient) -> str | None:
    """Best-effort own player id for highlighting; ranking still works without it."""
    try:
        response = _single_call(client, ApiAction.USER_GET_INFO)
    except HWAuthError:
        raise
    except Exception:  # noqa: BLE001 - optional enrichment only
        return None
    if isinstance(response, dict) and response.get("id") is not None:
        return str(response.get("id"))
    return None


def claim_rewards(
    client: HWClient, rewards: list[TournamentReward]
) -> list[TournamentClaimResult]:
    """Claim every explicitly claimable reward. Never uses a guessed id."""
    results: list[TournamentClaimResult] = []
    for reward in rewards:
        if not reward.claimable:
            continue
        try:
            res = client.call(
                {
                    "calls": [
                        {
                            "name": ApiAction.POWER_TOURNAMENT_DAILY_FARM,
                            "args": {"id": reward.reward_id},
                            "context": {"actionTs": 0},
                            "ident": "body",
                        }
                    ]
                },
                max_retries=0,
            )
        except HWAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - one claim must not stop the rest
            results.append(
                TournamentClaimResult(
                    reward.reward_id, ResponseStatus.ERROR, str(exc)
                )
            )
        else:
            if res.is_success:
                results.append(
                    TournamentClaimResult(
                        reward.reward_id, ResponseStatus.SUCCESS
                    )
                )
            elif res.error_name in CLAIM_SKIP_ERRORS:
                results.append(
                    TournamentClaimResult(
                        reward.reward_id,
                        ResponseStatus.SKIPPED,
                        res.error_name,
                    )
                )
            else:
                results.append(
                    TournamentClaimResult(
                        reward.reward_id,
                        ResponseStatus.ERROR,
                        res.error_name or res.status.value,
                    )
                )
        # Space out every claim (including the last one) so the following
        # getGroupInfo is not fired immediately after dailyFarm.
        client.sleep()
    return results


def run_power_tournament(
    client: HWClient,
    *,
    account_alias: str,
    top: int = DEFAULT_TOP,
    dry_run: bool = False,
) -> PowerTournamentResult:
    """Read the event, claim available rewards, then read the group ranking."""
    if isinstance(top, bool) or not isinstance(top, int) or not 1 <= top <= MAX_TOP:
        raise ValueError(f"top must be an integer between 1 and {MAX_TOP} (got {top!r})")

    result = PowerTournamentResult(account=account_alias, dry_run=dry_run)
    try:
        result.state = fetch_state(client)
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported in the result
        result.error = str(exc)
        return result

    if result.state is None:
        return result

    power_field = _power_field(result.state.tournament_type)

    # Claims first: the end-of-event window is time sensitive.
    if not dry_run:
        result.claims = claim_rewards(client, result.state.rewards)

    try:
        group_response = fetch_group(client)
        result.own_user_id = fetch_own_user_id(client)
        entries = parse_group_info(
            group_response,
            own_user_id=result.own_user_id,
            power_field=power_field,
        )
        top_entries = entries[:top]
        own_entry = next((entry for entry in entries if entry.is_self), None)
        if own_entry is not None and all(
            entry.user_id != own_entry.user_id for entry in top_entries
        ):
            result.ranking = [*top_entries, own_entry]
        else:
            result.ranking = top_entries
        result.own_place = own_entry.place if own_entry is not None else None
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - state/claims already done
        result.ranking_error = str(exc)

    if result.ranking:
        enrichment_errors: list[str] = []
        if power_field is None:
            enrichment_errors.append(
                f"unknown tournament type '{result.state.tournament_type}'; "
                "power skipped"
            )
        else:
            try:
                power_by_user, power_error = fetch_member_power(
                    client, result.ranking, power_field
                )
                if power_by_user:
                    result.ranking = [
                        replace(
                            entry,
                            power=entry.power
                            if entry.power is not None
                            else power_by_user.get(entry.user_id),
                        )
                        for entry in result.ranking
                    ]
                if power_error:
                    enrichment_errors.append(power_error)
            except HWAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - optional enrichment only
                enrichment_errors.append(str(exc))

        try:
            stats_by_user, stats_error = fetch_member_stats(client, result.ranking)
            if stats_by_user:
                result.ranking = [
                    replace(
                        entry,
                        stats=_merge_stats(
                            entry.stats, stats_by_user.get(entry.user_id)
                        ),
                    )
                    for entry in result.ranking
                ]
            if stats_error:
                enrichment_errors.append(stats_error)
        except HWAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - optional enrichment only
            enrichment_errors.append(str(exc))

        result.enrichment_error = (
            "; ".join(enrichment_errors) if enrichment_errors else None
        )
    return result


def _display_timezone() -> timezone | ZoneInfo:
    tz_name = os.environ.get("HWGENIE_TZ")
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone.utc


def _format_epoch(ts: int | None) -> str:
    if ts is None:
        return "-"
    zone = _display_timezone()
    return datetime.fromtimestamp(ts, zone).strftime("%Y-%m-%d %H:%M")


def _age(ts: int | None, *, now: int | None = None) -> str:
    if ts is None:
        return "-"
    current = int(now if now is not None else time.time())
    delta = max(0, current - ts)
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"


def _age_color(ts: int | None, *, now: int | None = None) -> str | None:
    if ts is None:
        return None
    current = int(now if now is not None else time.time())
    delta = max(0, current - ts)
    if delta <= 300:
        return "green"
    if delta <= 3600:
        return "yellow"
    return None


def _clip(text: str, width: int) -> str:
    out: list[str] = []
    used = 0
    for ch in text:
        w = display_width(ch, ambiguous_wide=False)
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out)


_RANK_HEADERS = (
    "#",
    "Name",
    "Points",
    "Power",
    "Srv",
    "Act T/W",
    "Tit T/W",
    "Pre T/W",
    "Last login",
    "Seen",
)

#: Hard cap for the Name column; wider names are clipped before measuring.
_RANK_NAME_WIDTH = 18


def _number(value: int | None) -> str:
    return format_number_with_suffix(value) if value is not None else "-"


def _pair(today: int | None, week: int | None) -> str:
    return f"{_number(today)}/{_number(week)}"


def _stats_values(entry: TournamentRankEntry) -> tuple[str, str, str]:
    stats = entry.stats
    if stats is None:
        return "-/-", "-/-", "-/-"
    return (
        _pair(stats.activity_today, stats.activity_week),
        _pair(stats.titanite_today, stats.titanite_week),
        _pair(stats.prestige_today, stats.prestige_week),
    )


def _rank_cells(entry: TournamentRankEntry, *, now: int | None) -> tuple[str, ...]:
    """Unpadded cell strings for one ranking row (narrow-ambiguous policy)."""
    activity, titanite, prestige = _stats_values(entry)
    return (
        str(entry.place),
        _clip(entry.name, _RANK_NAME_WIDTH),
        _number(entry.points),
        _number(entry.power),
        entry.server_id or "-",
        activity,
        titanite,
        prestige,
        _format_epoch(entry.last_login_ts),
        _age(entry.last_login_ts, now=now),
    )


def _rank_widths(rows: list[tuple[str, ...]]) -> tuple[int, ...]:
    """Column widths: the header plus the widest cell, measured narrow.

    Data-driven widths keep the table compact while never overflowing: a
    ``25.0K/123.5K`` pair needs 12 columns, ``1000.0K`` needs 7, and so on.
    """
    widths: list[int] = []
    for index, header in enumerate(_RANK_HEADERS):
        width = display_width(header, ambiguous_wide=False)
        for row in rows:
            width = max(width, display_width(row[index], ambiguous_wide=False))
        widths.append(width)
    return tuple(widths)


def _render_rank_table(entries: list[TournamentRankEntry], *, now: int | None) -> None:
    # Player names are commonly Cyrillic/Greek, which terminals render one
    # column wide; the layout therefore counts East Asian Ambiguous chars as 1
    # (the reverse trade-off of the default CJK policy in core.utils).
    rows = [_rank_cells(entry, now=now) for entry in entries]
    widths = _rank_widths(rows)
    header = "  " + " | ".join(
        pad(title, width, ambiguous_wide=False)
        for title, width in zip(_RANK_HEADERS, widths)
    )
    print(style(header, bold=True, fg="cyan"))
    rule = "  " + "-" * (display_width(header, ambiguous_wide=False) - 2)
    print(style(rule, dim=True))
    for entry, cells in zip(entries, rows):
        padded = [
            pad(cell, width, ambiguous_wide=False)
            for cell, width in zip(cells[:-1], widths[:-1])
        ]
        # Rank color follows the shared arena convention (1 gold, 2-14 green);
        # the own row is cyan/bold so it stands out even when it is appended
        # below the top N.
        padded[0] = style(
            padded[0],
            fg=rank_color(entry.place),
            bold=entry.place == 1,
        )
        padded[1] = style(
            padded[1],
            fg="cyan" if entry.is_self else rank_color(entry.place),
            bold=entry.is_self or entry.place == 1,
        )
        # ``← you`` is appended after the padded Seen cell, so it never widens
        # the table (and is excluded from the measured column widths).
        seen = style(
            pad(cells[-1], widths[-1], ambiguous_wide=False),
            fg=_age_color(entry.last_login_ts, now=now),
        )
        marker = style(" ← you", bold=True, fg="cyan") if entry.is_self else ""
        print("  " + " | ".join(padded) + " | " + seen + marker)


def _reward_counts(
    result: PowerTournamentResult,
) -> tuple[int, int, int]:
    """Return ``(received, unreceived, claimable)`` for the current-day rewards."""
    state = result.state
    if state is None:
        return 0, 0, 0
    total = len(state.rewards)
    farmed = sum(1 for reward in state.rewards if reward.is_farmed)
    if result.dry_run:
        claimable = sum(1 for reward in state.rewards if reward.claimable)
        return farmed, total - farmed, claimable

    claimed = sum(1 for c in result.claims if c.status == ResponseStatus.SUCCESS)
    already_farmed = sum(
        1
        for c in result.claims
        if c.status == ResponseStatus.SKIPPED
        and c.error == ErrorName.NOT_AVAILABLE.value
    )
    received = min(total, farmed + claimed + already_farmed)
    return received, total - received, 0


def render_power_tournament(
    result: PowerTournamentResult, *, now: int | None = None
) -> None:
    """Print one account's state, top ranking and claim outcome."""
    prefix = f"[{result.account}] " if result.account else ""
    print(f"\n{Emojis.START}{prefix}Power Tournament")
    if result.error:
        print(f"  {Emojis.ERROR}{result.error}")
        return
    state = result.state
    if state is None:
        print(f"  {Emojis.INFO}Power Tournament is not active.")
        return

    ends = _format_epoch(state.end_ts)
    print(f"  {Emojis.INFO}Type: {state.type_label} | Ends: {ends}")
    own_place = f"#{result.own_place}" if result.own_place else "-"
    total = (
        format_number_with_suffix(state.total_points)
        if state.total_points is not None
        else "-"
    )
    today = (
        format_number_with_suffix(state.daily_points)
        if state.daily_points is not None
        else "-"
    )
    print(
        f"  {Emojis.INFO}{result.account or '?'}: "
        f"rank {own_place} | points {total} (today {today})"
    )

    if result.ranking:
        _render_rank_table(result.ranking, now=now)
        if result.own_user_id is None:
            print(
                f"  {Emojis.WARNING}Own rank unavailable (user id lookup failed)."
            )
    elif result.ranking_error:
        print(f"  {Emojis.WARNING}Ranking unavailable: {result.ranking_error}")
    else:
        print(f"  {Emojis.INFO}No group ranking entries returned.")

    if result.enrichment_error:
        print(
            f"  {Emojis.WARNING}Member info unavailable: "
            f"{result.enrichment_error}"
        )

    received, unreceived, claimable = _reward_counts(result)
    reward_line = (
        f"  {Emojis.INFO}Rewards: received {received} / unreceived {unreceived}"
    )
    if result.dry_run:
        reward_line += f" (claimable {claimable})"
    elif result.failed_claims:
        reward_line += f" (failed {result.failed_claims})"
    print(reward_line)
    if result.dry_run:
        print(f"  {Emojis.INFO}Dry-run: rewards were not claimed.")

    if state.ended:
        if state.final_reward_farmed:
            print(f"  {Emojis.INFO}Final ranking reward: already claimed.")
        else:
            print(f"  {Emojis.WARNING}{FINAL_REWARD_PENDING}")
