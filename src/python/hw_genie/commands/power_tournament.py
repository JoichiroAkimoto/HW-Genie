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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hw_genie.core.client import (
    ApiAction,
    Emojis,
    ErrorName,
    HWAuthError,
    HWClient,
    ResponseStatus,
    _safe_int,
)
from hw_genie.core.utils import display_width, format_number_with_suffix, pad, style

DEFAULT_TOP = 5
MAX_TOP = 50

TYPE_LABELS = {
    "titans": "Titan",
    "titan": "Titan",
    "heroes": "Hero",
    "hero": "Hero",
}

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
    label: str
    points: int | None
    claimable: bool


@dataclass(frozen=True)
class TournamentRankEntry:
    place: int
    user_id: str
    name: str
    points: int
    last_login_ts: int | None = None
    is_self: bool = False


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
    label: str
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


def _reward_label(reward: Any) -> str:
    """Compact one-line summary of a ``currentDayRewards[].reward`` payload."""
    if not isinstance(reward, dict):
        return ""
    parts: list[str] = []
    for category, payload in reward.items():
        if isinstance(payload, dict):
            for lib_id, amount in list(payload.items())[:3]:
                parts.append(f"{category}:{lib_id}×{amount}")
        elif payload is not None:
            parts.append(f"{category}×{payload}")
        if len(parts) >= 3:
            break
    return ", ".join(parts)


def _reward_ref(reward_id: int, label: str) -> str:
    """Format a reward id, appending its computed label when available."""
    return f"id {reward_id} ({label})" if label else f"id {reward_id}"


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
                label=_reward_label(item.get("reward")),
                points=threshold,
                claimable=claimable,
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


def parse_group_info(
    response: Any, *, own_user_id: str | None = None
) -> list[TournamentRankEntry]:
    """Build the full group ranking (points desc) from ``getGroupInfo``.

    A malformed success payload raises instead of being read as an empty
    ranking, so the CLI cannot exit 0 on a broken response. An empty
    ``points: {}`` object is still a legitimate empty ranking.
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

    rows: list[tuple[str, str, int, int | None]] = []
    for user_id, raw_points in points.items():
        uid = str(user_id)
        profile = users.get(uid)
        name = ""
        last_login = None
        if isinstance(profile, dict):
            name = str(profile.get("name") or "").strip()
            last_login = _safe_int(profile.get("lastLoginTime"), None)
        rows.append((uid, name or f"User {uid}", _safe_int(raw_points, 0), last_login))

    rows.sort(key=lambda row: (-row[2], _user_sort_key(row[0])))
    entries: list[TournamentRankEntry] = []
    previous_points: int | None = None
    previous_place = 0
    for index, (uid, name, pts, last_login) in enumerate(rows, start=1):
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
            )
        )
        previous_points = pts
        previous_place = place
    return entries


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
                    reward.reward_id, reward.label, ResponseStatus.ERROR, str(exc)
                )
            )
        else:
            if res.is_success:
                results.append(
                    TournamentClaimResult(
                        reward.reward_id, reward.label, ResponseStatus.SUCCESS
                    )
                )
            elif res.error_name in CLAIM_SKIP_ERRORS:
                results.append(
                    TournamentClaimResult(
                        reward.reward_id,
                        reward.label,
                        ResponseStatus.SKIPPED,
                        res.error_name,
                    )
                )
            else:
                results.append(
                    TournamentClaimResult(
                        reward.reward_id,
                        reward.label,
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

    # Claims first: the end-of-event window is time sensitive.
    if not dry_run:
        result.claims = claim_rewards(client, result.state.rewards)

    try:
        group_response = fetch_group(client)
        result.own_user_id = fetch_own_user_id(client)
        entries = parse_group_info(group_response, own_user_id=result.own_user_id)
        result.ranking = entries[:top]
        result.own_place = next(
            (entry.place for entry in entries if entry.is_self), None
        )
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - state/claims already done
        result.ranking_error = str(exc)
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
        w = display_width(ch)
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out)


_RANK_COLUMNS = (
    ("#", 4),
    ("Name", 22),
    ("UserID", 11),
    ("Points", 11),
    ("Last login", 16),
    ("Seen", 6),
)


def _render_rank_table(entries: list[TournamentRankEntry], *, now: int | None) -> None:
    header = "  " + " | ".join(pad(title, width) for title, width in _RANK_COLUMNS)
    print(style(header, bold=True, fg="cyan"))
    print(style("  " + "-" * (len(header) - 2), dim=True))
    for entry in entries:
        name = _clip(entry.name, _RANK_COLUMNS[1][1])
        cells = [
            pad(str(entry.place), _RANK_COLUMNS[0][1]),
            pad(name, _RANK_COLUMNS[1][1]),
            pad(entry.user_id, _RANK_COLUMNS[2][1]),
            pad(format_number_with_suffix(entry.points), _RANK_COLUMNS[3][1]),
            pad(_format_epoch(entry.last_login_ts), _RANK_COLUMNS[4][1]),
        ]
        seen = pad(_age(entry.last_login_ts, now=now), _RANK_COLUMNS[5][1])
        seen = style(seen, fg=_age_color(entry.last_login_ts, now=now))
        marker = style(" ← you", bold=True) if entry.is_self else ""
        print("  " + " | ".join(cells) + " | " + seen + marker)


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
    print(f"  {Emojis.INFO}You: rank {own_place} | points {total} (today {today})")

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

    if result.dry_run:
        available = [reward for reward in state.rewards if reward.claimable]
        if available:
            labels = ", ".join(
                _reward_ref(reward.reward_id, reward.label) for reward in available
            )
            print(f"  {Emojis.INFO}Claimable rewards: {labels}")
        else:
            print(f"  {Emojis.INFO}No claimable rewards.")
        print(f"  {Emojis.INFO}Dry-run: rewards were not claimed.")
    else:
        claimed = sum(1 for c in result.claims if c.status == ResponseStatus.SUCCESS)
        skipped = sum(1 for c in result.claims if c.status == ResponseStatus.SKIPPED)
        failed = result.failed_claims
        print(
            f"  {Emojis.INFO}Rewards: claimed {claimed}, "
            f"skipped {skipped}, failed {failed}"
        )
        for claim in result.claims:
            if claim.status == ResponseStatus.SUCCESS:
                mark = Emojis.SUCCESS
            elif claim.status == ResponseStatus.SKIPPED:
                mark = "⏭ "
            else:
                mark = Emojis.ERROR
            detail = f" ({claim.error})" if claim.error else ""
            print(f"    {mark}{_reward_ref(claim.reward_id, claim.label)}{detail}")

    if state.ended:
        if state.final_reward_farmed:
            print(f"  {Emojis.INFO}Final ranking reward: already claimed.")
        else:
            print(f"  {Emojis.WARNING}{FINAL_REWARD_PENDING}")
