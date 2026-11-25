"""Arena v1: status, ranking, team presets, and targeted attacks.

Scope: normal Arena only. Grand Arena can be described by the private
``_GRAND_MODE`` scaffold, but every runner rejects it until a later release.

Safety rules:
- Only players ranked *above* the current account can be attacked.
- A live attack is accepted only for players in the latest
  ``arenaFindEnemies`` candidate list (range-check ``true`` alone is not
  enough). The list rotates, so it is refreshed up to
  ``DEFAULT_CANDIDATE_RETRIES`` extra times while waiting for the target.
  There is no fixed "N ranks above" cutoff: candidates may include players
  farther than the roughly 13 ranks observed so far.
- ``--dry-run`` performs the same candidate preflight and
  ``arenaCheckTargetRange`` call, but never calls ``arenaAttack``.
- Experimental exception: ``--from-snapshot`` skips
  ``arenaFindEnemies`` and attacks a saved candidate directly. The upper-only
  rule is still always enforced from a fresh ``topGet`` (the target must be in
  the current top 50 so its current rank can be verified), and a false
  ``arenaCheckTargetRange`` stops the attack unless ``--force`` is given.
  ``--force`` is only valid together with ``--from-snapshot``.
- ``arenaCheckTargetRange`` accepts at most 3 IDs per request; callers are
  batched accordingly.
- ``arenaAttack`` is sent exactly once (``max_retries=0``): a timeout after the
  server accepted the request must not trigger a second attack.

The verified API shapes live in ``docs/api/ARENA_API.md``, together with the
empirical rank-window observations. That window is rank/league-dependent (not
a fixed ±21 rule); the ``*CheckTargetRange`` response is authoritative.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator

from hw_genie.core.client import (
    ApiAction,
    Emojis,
    ErrorName,
    HWClient,
    HWResponse,
    ResponseStatus,
)
from hw_genie.core.battle_log import PlanCandidate, lineup_key_from_ids, query_plans

# Arena attack attempts / cooldown live in ``userGetInfo.refillable``.
ARENA_ATTACK_REFILLABLE_ID = 6
ARENA_COOLDOWN_REFILLABLE_ID = 5
ARENA_MAX_ATTEMPTS = 5

# ``*CheckTargetRange`` rejects 4+ ids with ERROR TooMany.
MAX_RANGE_IDS = 3
# ``*Attack`` only accepts players in the latest ``*FindEnemies`` list. The
# list rotates, so refresh it this many extra times while waiting for the
# requested target to appear.
DEFAULT_CANDIDATE_RETRIES = 10

# Config key in account_configs: {"arena": {name: {heroes, pet?, favor, banners?}}}
ARENA_TEAM_PRESETS_KEY = "arena_team_presets"
# Config key in account_configs for the manual-verification snapshot.
ARENA_SNAPSHOT_KEY = "arena_snapshot"

_PRESET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")

# Kinds of ``*FindEnemies`` list wrappers seen/expected across modes.
_CANDIDATE_LIST_KEYS = ("enemies", "list", "candidates", "targets", "items")


class ArenaError(Exception):
    """User-facing Arena error (validation, target resolution, API rejection)."""


@dataclass(frozen=True)
class ArenaMode:
    """Method/field names for Arena or Grand Arena."""

    key: str
    get_all: ApiAction
    find_enemies: ApiAction
    check_range: ApiAction
    attack: ApiAction
    top_type: str
    place_field: str
    team_field: str


ARENA_MODE = ArenaMode(
    key="arena",
    get_all=ApiAction.ARENA_GET_ALL,
    find_enemies=ApiAction.ARENA_FIND_ENEMIES,
    check_range=ApiAction.ARENA_CHECK_TARGET_RANGE,
    attack=ApiAction.ARENA_ATTACK,
    top_type="arena",
    place_field="arenaPlace",
    team_field="arena",
)

_GRAND_MODE = ArenaMode(
    key="grand",
    get_all=ApiAction.ARENA_GET_ALL,
    find_enemies=ApiAction.GRAND_FIND_ENEMIES,
    check_range=ApiAction.GRAND_CHECK_TARGET_RANGE,
    attack=ApiAction.GRAND_ATTACK,
    top_type="grand",
    place_field="grandPlace",
    team_field="grand",
)


def _require_arena_mode(mode: ArenaMode) -> None:
    """Reject non-Arena modes until the 3-team GA shape is implemented."""
    if mode.key != ARENA_MODE.key:
        raise ArenaError("Grand Arena is not implemented yet.")


def _normalize_banner(value: Any) -> int | None:
    """Return a banner ID, treating the ``0`` sentinel (and gaps) as absent."""
    banner = _safe_int(value, None)
    if banner is None or banner <= 0:
        return None
    return banner


@dataclass
class ArenaTeam:
    """A normalized Arena team (heroes + optional pet/favor/banner)."""

    heroes: list[int] = field(default_factory=list)
    pet: int | None = None
    favor: dict[int, int] = field(default_factory=dict)
    banners: list[int] = field(default_factory=list)

    @property
    def banner(self) -> int | None:
        return next((b for b in self.banners if b), None)


@dataclass
class ArenaTarget:
    user_id: int
    place: int
    name: str | None = None
    power: int | None = None
    # Candidate entry captured while resolving the target, so the attack step
    # does not have to re-run the rotating-list retry loop.
    candidate: ArenaTopEntry | None = field(default=None, compare=False, repr=False)
    # topGet entry captured while resolving the target. Kept separate from
    # ``candidate`` so the attack path's semantics (candidate == live
    # *FindEnemies hit) stay unchanged.
    top_entry: ArenaTopEntry | None = field(default=None, compare=False, repr=False)


@dataclass
class ArenaTopEntry:
    place: int
    user_id: int
    name: str | None = None
    power: int | None = None
    heroes: list[int] = field(default_factory=list)
    pet: int | None = None
    banner: int | None = None


@dataclass
class ArenaSnapshot:
    """A saved ``*FindEnemies`` response for manual verification."""

    own_place: int
    candidates: list[ArenaTopEntry] = field(default_factory=list)
    saved_at: str = ""


@dataclass
class ArenaTop:
    entries: list[ArenaTopEntry] = field(default_factory=list)
    own_place: int | None = None
    raw: dict | None = None


@dataclass
class ArenaStatus:
    place: int
    grand_place: int
    user_id: int | None
    name: str | None
    attempts: int | None
    cooldown: int | None
    saved_team: ArenaTeam | None


@dataclass
class ArenaAttackResult:
    dry_run: bool
    accepted: bool
    win: bool | None = None
    old_place: int | None = None
    new_place: int | None = None
    stopped: str | None = None
    warning: str | None = None


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _safe_int(value: Any, default: int | None = 0) -> int | None:
    """int conversion that rejects bools and returns ``default`` on failure."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _human_int(value: int | None) -> str:
    return "?" if value is None else str(value)


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


def _response_map(detail: Any) -> dict[str, Any]:
    """Map ``ident`` -> response body for single- and multi-call responses."""
    if not isinstance(detail, dict):
        return {}
    if "response" in detail:
        return {"body": detail.get("response")}
    return {str(ident): _unwrap_item(item) for ident, item in detail.items()}


def _parse_top_entry(item: Any, users: dict[str, Any]) -> ArenaTopEntry | None:
    if not isinstance(item, dict):
        return None
    place = _safe_int(item.get("place"), None)
    user_id = _safe_int(item.get("userId"), None)
    if place is None or place <= 0 or user_id is None or user_id <= 0:
        return None

    user = item.get("user")
    if not isinstance(user, dict):
        user = users.get(str(user_id)) or users.get(user_id)
        if not isinstance(user, dict):
            user = {}

    heroes: list[int] = []
    pet: int | None = None
    raw_heroes = item.get("heroes")
    if isinstance(raw_heroes, list):
        for entry in raw_heroes:
            hero_id = _safe_int(entry.get("id") if isinstance(entry, dict) else entry, None)
            if hero_id is None or hero_id <= 0:
                continue
            if hero_id >= 6000:
                if pet is None:
                    pet = hero_id
            else:
                heroes.append(hero_id)

    banner: int | None = None
    raw_banners = item.get("banners")
    if isinstance(raw_banners, list):
        for entry in raw_banners:
            banner = _normalize_banner(entry.get("id") if isinstance(entry, dict) else entry)
            if banner is not None:
                break
    elif isinstance(raw_banners, dict):
        banner = _normalize_banner(raw_banners.get("id"))

    return ArenaTopEntry(
        place=place,
        user_id=user_id,
        name=user.get("name") if isinstance(user.get("name"), str) else None,
        power=_safe_int(item.get("power"), None),
        heroes=heroes,
        pet=pet,
        banner=banner,
    )


def parse_top_response(response: Any) -> ArenaTop:
    """Parse a ``topGet`` response body (``{top, place, users}``)."""
    if not isinstance(response, dict):
        return ArenaTop()
    users = response.get("users")
    if not isinstance(users, dict):
        users = {}
    raw_top = response.get("top")
    entries: list[ArenaTopEntry] = []
    if isinstance(raw_top, list):
        entries = [
            entry
            for entry in (_parse_top_entry(item, users) for item in raw_top)
            if entry is not None
        ]
    return ArenaTop(
        entries=entries,
        own_place=_safe_int(response.get("place"), None),
        raw=response,
    )


def _candidate_items(response: Any) -> list[Any]:
    if isinstance(response, list):
        return response
    if not isinstance(response, dict):
        return []
    for key in _CANDIDATE_LIST_KEYS:
        value = response.get(key)
        if isinstance(value, list):
            return value
    # Fallback: first list of dict-like entries carrying userId/place.
    for value in response.values():
        if (
            isinstance(value, list)
            and value
            and all(isinstance(v, dict) for v in value)
            and any("userId" in v or "place" in v for v in value)
        ):
            return value
    return []


def parse_candidates_response(response: Any) -> list[ArenaTopEntry]:
    """Parse an ``arenaFindEnemies`` / ``grandFindEnemies`` response body."""
    return [
        entry
        for entry in (_parse_top_entry(item, {}) for item in _candidate_items(response))
        if entry is not None
    ]


def parse_range_response(detail: Any) -> dict[int, bool]:
    """Parse ``*CheckTargetRange`` into ``{userId: bool}``."""
    if not isinstance(detail, dict):
        return {}
    payload = detail.get("response", detail)
    if not isinstance(payload, dict):
        return {}
    parsed: dict[int, bool] = {}
    for key, value in payload.items():
        user_id = _safe_int(key, None)
        if user_id is None:
            continue
        if isinstance(value, bool):
            parsed[user_id] = value
        elif isinstance(value, (int, float)):
            parsed[user_id] = bool(value)
        elif isinstance(value, str) and value.strip().lower() in ("true", "false"):
            parsed[user_id] = value.strip().lower() == "true"
    return parsed


# ---------------------------------------------------------------------------
# Validation / team resolution
# ---------------------------------------------------------------------------


def _validate_hero_ids(heroes: list[int]) -> None:
    if len(heroes) != 5:
        raise ArenaError(f"Arena team must have exactly 5 heroes (got {len(heroes)}).")
    for hero_id in heroes:
        if isinstance(hero_id, bool) or not isinstance(hero_id, int) or not (1 <= hero_id < 6000):
            raise ArenaError(f"Invalid hero ID {hero_id!r}: heroes must be integers below 6000.")
    if len(set(heroes)) != len(heroes):
        raise ArenaError("Arena team heroes must be unique.")


def _validate_pet_id(pet: int) -> None:
    if isinstance(pet, bool) or not isinstance(pet, int) or pet < 6000:
        raise ArenaError(f"Invalid pet ID {pet!r}: pets must be integers >= 6000.")


def _validate_favor_map(favor: dict[int, int], heroes: list[int] | None) -> None:
    for hero_id, patron_id in favor.items():
        if heroes is not None and hero_id not in heroes:
            raise ArenaError(
                f"Favor hero {hero_id} is not part of the selected team ({heroes})."
            )
        if isinstance(patron_id, bool) or not isinstance(patron_id, int) or patron_id <= 0:
            raise ArenaError(f"Invalid patron pet ID {patron_id!r} for hero {hero_id}.")


def _validate_banners(banners: list[int]) -> None:
    selected = [banner for banner in banners if banner != 0]
    if len(selected) > 1:
        raise ArenaError(f"Arena team accepts at most 1 banner (got {len(selected)}).")
    for banner in selected:
        if isinstance(banner, bool) or not isinstance(banner, int) or banner < 1:
            raise ArenaError(f"Invalid banner ID {banner!r}: banners must be positive.")


def validate_team(team: ArenaTeam) -> None:
    """Validate an Arena team before any attack-related API call."""
    if not isinstance(team, ArenaTeam):
        raise ArenaError("Arena team is invalid.")
    heroes = list(team.heroes or [])
    _validate_hero_ids(heroes)
    if team.pet is not None:
        _validate_pet_id(team.pet)
    _validate_favor_map(team.favor or {}, heroes)
    _validate_banners(team.banners or [])


def validate_partial_team(
    *,
    heroes: list[int] | None = None,
    pet: int | None = None,
    banner: int | None = None,
    favor: dict[int, int] | None = None,
) -> None:
    """Validate only the fields supplied on the command line, before any API call.

    Favor hero membership needs the resolved heroes, so it is skipped when
    ``heroes`` is None and re-checked by :func:`validate_team` after the team
    is resolved. Patron IDs are always checked.
    """
    hero_ids = list(heroes) if heroes is not None else None
    if hero_ids is not None:
        _validate_hero_ids(hero_ids)
    if pet is not None:
        _validate_pet_id(pet)
    if favor is not None:
        _validate_favor_map(favor, hero_ids)
    if banner is not None:
        _validate_banners([banner])


def parse_favor_args(raw: list[str] | None) -> dict[int, int] | None:
    """Parse repeatable ``--favor HERO=PATRON`` values (None when omitted)."""
    if raw is None:
        return None
    favor: dict[int, int] = {}
    for value in raw:
        text = str(value).strip()
        parts = text.split("=")
        if len(parts) != 2:
            raise ArenaError(f"Invalid --favor value {value!r}; expected HERO=PATRON.")
        try:
            hero_id = int(parts[0])
            patron_id = int(parts[1])
        except ValueError as exc:
            raise ArenaError(f"Invalid --favor value {value!r}; expected HERO=PATRON.") from exc
        if hero_id <= 0 or patron_id <= 0:
            raise ArenaError(f"Invalid --favor value {value!r}; IDs must be positive.")
        if hero_id in favor:
            raise ArenaError(f"Duplicate --favor for hero {hero_id}.")
        favor[hero_id] = patron_id
    return favor


def resolve_team(
    *,
    heroes: list[int] | None = None,
    pet: int | None = None,
    banner: int | None = None,
    favor: dict[int, int] | None = None,
    preset: ArenaTeam | None = None,
    saved: ArenaTeam | None = None,
) -> tuple[ArenaTeam, list[int]]:
    """Resolve a team with direct flags > preset > saved team, field by field.

    Each field falls back independently: a preset that only defines heroes
    still inherits the saved team's pet/banner. Favor precedence is explicit
    ``--favor`` > preset favor > saved favor for the selected heroes. Missing
    entries are returned as hero IDs for a warning.
    """
    if heroes is not None:
        selected_heroes = [int(h) for h in heroes]
    elif preset is not None and preset.heroes:
        selected_heroes = list(preset.heroes)
    elif saved is not None:
        selected_heroes = list(saved.heroes)
    else:
        selected_heroes = []
    if not selected_heroes:
        raise ArenaError(
            "No Arena team selected and no saved Arena team was found. "
            "Pass --heroes (and optionally --pet/--banner/--favor) or use --team."
        )

    if pet is not None:
        selected_pet = pet
    elif preset is not None and preset.pet is not None:
        selected_pet = preset.pet
    else:
        selected_pet = saved.pet if saved is not None else None

    if banner is not None:
        # 0 is the game's "no banner" sentinel: an explicit 0 suppresses the
        # preset/saved banner instead of falling back to it.
        selected_banner = None if banner == 0 else banner
    elif preset is not None and preset.banner is not None:
        selected_banner = preset.banner
    else:
        selected_banner = saved.banner if saved is not None else None

    merged_favor: dict[int, int] = {}
    if favor is not None:
        merged_favor = dict(favor)
    else:
        if preset is not None:
            merged_favor.update(
                {h: p for h, p in preset.favor.items() if h in selected_heroes}
            )
        if saved is not None:
            for hero_id in selected_heroes:
                if hero_id not in merged_favor and hero_id in saved.favor:
                    merged_favor[hero_id] = saved.favor[hero_id]

    team = ArenaTeam(
        heroes=selected_heroes,
        pet=selected_pet,
        favor=merged_favor,
        banners=[selected_banner] if selected_banner is not None else [],
    )
    validate_team(team)
    missing = [h for h in selected_heroes if h not in team.favor]
    return team, missing


def build_attack_args(user_id: int, team: ArenaTeam) -> dict[str, Any]:
    """Build the ``arenaAttack.args`` payload (omit absent optional fields)."""
    args: dict[str, Any] = {
        "userId": int(user_id),
        "heroes": [int(h) for h in team.heroes],
    }
    if team.pet is not None:
        args["pet"] = int(team.pet)
    if team.favor:
        args["favor"] = {str(h): int(p) for h, p in team.favor.items()}
    banners = [int(b) for b in team.banners[:1] if b]
    if banners:
        args["banners"] = banners
    return args


def parse_attack_result(detail: Any) -> ArenaAttackResult | None:
    """Parse ``response.battles[0].result``; None when the shape is unknown."""
    if not isinstance(detail, dict):
        return None
    response = detail.get("response", detail)
    if not isinstance(response, dict):
        return None
    battles = response.get("battles")
    if not isinstance(battles, list) or not battles:
        return None
    first = battles[0]
    if not isinstance(first, dict):
        return None
    result = first.get("result")
    if not isinstance(result, dict) or not result:
        return None
    win = result.get("win")
    if not isinstance(win, bool):
        return None
    return ArenaAttackResult(
        dry_run=False,
        accepted=True,
        win=win,
        old_place=_safe_int(result.get("oldPlace"), None),
        new_place=_safe_int(result.get("newPlace"), None),
    )


# ---------------------------------------------------------------------------
# Read APIs
# ---------------------------------------------------------------------------


def _refillable_amount(user: dict, refillable_id: int) -> int | None:
    raw = user.get("refillable")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and _safe_int(item.get("id"), None) == refillable_id:
                return _safe_int(item.get("amount"), None)
        return None
    if isinstance(raw, dict):
        for key in (str(refillable_id), refillable_id):
            if key in raw:
                return _safe_int(raw[key], None)
    return None


def fetch_saved_team(
    client: HWClient, mode: ArenaMode = ARENA_MODE
) -> ArenaTeam | None:
    """Fetch the saved team/favor/banner for the mode (None when unavailable)."""
    _require_arena_mode(mode)
    res = client.call(
        {
            "calls": [
                {"name": ApiAction.TEAM_GET_ALL, "args": {}, "ident": "team"},
                {"name": ApiAction.TEAM_GET_FAVOR, "args": {}, "ident": "favor"},
                {"name": ApiAction.TEAM_GET_BANNERS, "args": {}, "ident": "banner"},
            ]
        }
    )
    client.sleep()
    if not res.is_success:
        return None

    responses = _response_map(res.detail)
    team_body = responses.get("team")
    saved = team_body.get(mode.team_field) if isinstance(team_body, dict) else None
    if not isinstance(saved, list) or len(saved) < 5:
        return None

    heroes: list[int] = []
    for value in saved[:5]:
        hero_id = _safe_int(value, None)
        if hero_id is None or hero_id <= 0:
            return None
        heroes.append(hero_id)

    pet: int | None = None
    if len(saved) >= 6:
        pet = _safe_int(saved[5], None)
        if pet is not None and pet < 6000:
            pet = None

    favor: dict[int, int] = {}
    favor_body = responses.get("favor")
    raw_favor = favor_body.get(mode.team_field) if isinstance(favor_body, dict) else None
    if isinstance(raw_favor, dict):
        for hero_key, patron in raw_favor.items():
            hero_id = _safe_int(hero_key, None)
            patron_id = _safe_int(patron, None)
            # Ignore stale favor entries for heroes outside the saved team.
            if hero_id in heroes and patron_id is not None:
                favor[hero_id] = patron_id

    banners: list[int] = []
    banner_body = responses.get("banner")
    raw_banner = banner_body.get(mode.team_field) if isinstance(banner_body, dict) else None
    if isinstance(raw_banner, list):
        raw_banner = raw_banner[0] if raw_banner else None
    banner = _normalize_banner(raw_banner)
    if banner is not None:
        banners.append(banner)

    team = ArenaTeam(heroes=heroes, pet=pet, favor=favor, banners=banners)
    try:
        validate_team(team)
    except ArenaError:
        return None
    return team


def fetch_arena_status(
    client: HWClient, mode: ArenaMode = ARENA_MODE
) -> ArenaStatus:
    """Resolve own rank, attempts/cooldown, and the saved team."""
    _require_arena_mode(mode)
    res = client.call(
        {
            "calls": [
                {"name": ApiAction.USER_GET_INFO, "args": {}, "ident": "user"},
                {"name": mode.get_all, "args": {}, "ident": "arena"},
            ]
        }
    )
    client.sleep()
    if not res.is_success:
        raise ArenaError(
            f"Could not read Arena status ({res.error_name or res.status.value})."
        )

    responses = _response_map(res.detail)
    user = responses.get("user") or {}
    arena = responses.get("arena") or {}
    if not isinstance(user, dict):
        user = {}
    if not isinstance(arena, dict):
        arena = {}

    place = _safe_int(arena.get(mode.place_field), None)
    if place is None or place <= 0:
        raise ArenaError(
            f"Could not read your {mode.key} rank from {mode.get_all.value}; "
            "refusing to continue."
        )
    grand_place = _safe_int(arena.get("grandPlace"), 0) or 0

    user_id = _safe_int(user.get("id"), None)
    name = user.get("name") if isinstance(user.get("name"), str) else None
    saved_team = fetch_saved_team(client, mode)

    return ArenaStatus(
        place=place,
        grand_place=grand_place,
        user_id=user_id,
        name=name,
        attempts=_refillable_amount(user, ARENA_ATTACK_REFILLABLE_ID),
        cooldown=_refillable_amount(user, ARENA_COOLDOWN_REFILLABLE_ID),
        saved_team=saved_team,
    )


def fetch_own_place(client: HWClient, mode: ArenaMode = ARENA_MODE) -> int:
    """Read only ``arenaGetAll`` and return the mode's own rank.

    Used before an attack (so a rank change between the earlier status fetch
    and the attack cannot widen the upper-only boundary) and by the read-only
    ``arena snapshot`` view as the ``topGet`` fallback.
    """
    _require_arena_mode(mode)
    res = client.call(
        {"calls": [{"name": mode.get_all, "args": {}, "ident": "arena"}]}
    )
    client.sleep()
    if not res.is_success:
        raise ArenaError(
            f"Could not read your {mode.key} rank from {mode.get_all.value} "
            f"({res.error_name or res.status.value}); refusing to attack."
        )
    responses = _unwrap_item(res.detail)
    place = (
        _safe_int(responses.get(mode.place_field), None)
        if isinstance(responses, dict)
        else None
    )
    if place is None or place <= 0:
        raise ArenaError(
            f"Could not read your {mode.key} rank from {mode.get_all.value}; "
            "refusing to attack."
        )
    return place


def _resolve_own_place(
    top_place: int | None, fallback: Callable[[], int | None]
) -> int:
    """Return a positive own rank, preferring the fresher ``topGet`` value.

    ``topGet`` has been observed to report ``place: 0`` when the own rank is
    unavailable. ``None`` and non-positive values are therefore treated as
    missing so the upper-only rule never compares against an invalid rank.
    ``fallback`` is only called when ``top_place`` is unusable.
    """
    if top_place is not None and top_place > 0:
        return top_place
    place = fallback()
    if place is None or place <= 0:
        raise ArenaError(
            "Could not determine your current arena rank; refusing to resolve "
            "or attack a target. Refresh the ranking and try again."
        )
    return place


def fetch_arena_top(client: HWClient, mode: ArenaMode = ARENA_MODE) -> ArenaTop:
    _require_arena_mode(mode)
    res = client.call(
        {
            "calls": [
                {
                    "name": ApiAction.TOP_GET,
                    "args": {"type": mode.top_type, "extraId": 0},
                    "ident": "body",
                }
            ]
        }
    )
    client.sleep()
    if not res.is_success:
        raise ArenaError(f"topGet failed ({res.error_name or res.status.value}).")
    response = _unwrap_item(res.detail)
    return parse_top_response(response)


def fetch_arena_candidates(
    client: HWClient, mode: ArenaMode = ARENA_MODE
) -> list[ArenaTopEntry]:
    _require_arena_mode(mode)
    res = client.call(
        {"calls": [{"name": mode.find_enemies, "args": {}, "ident": "body"}]}
    )
    client.sleep()
    if not res.is_success:
        raise ArenaError(
            f"{mode.find_enemies.value} failed ({res.error_name or res.status.value})."
        )
    return parse_candidates_response(_unwrap_item(res.detail))


def _validate_candidate_retries(retries: int) -> int:
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ArenaError(
            f"candidate_retries must be a non-negative integer (got {retries!r})."
        )
    return retries


def fetch_candidate(
    client: HWClient,
    *,
    user_id: int | None = None,
    place: int | None = None,
    retries: int = DEFAULT_CANDIDATE_RETRIES,
    mode: ArenaMode = ARENA_MODE,
    quiet: bool = False,
) -> ArenaTopEntry | None:
    """Return the target's current ``*FindEnemies`` entry.

    ``*Attack`` only accepts players present in the most recent candidate list,
    and the list rotates between calls. ``retries`` counts refreshes *after*
    the first call, so up to ``retries + 1`` requests are made. Identify the
    target by ``user_id`` (stable) or ``place`` (for ``--rank`` resolution).
    ``quiet`` suppresses the per-refresh status line (used by ``arena plan``
    JSON output so stdout stays machine-parseable).

    There is deliberately no fixed "N ranks above" limit here: the candidate
    window observed so far is roughly 1-13 ranks above, but a higher-ranked
    player can appear as a candidate. Membership in this list is the only
    extra requirement beyond a ``*CheckTargetRange`` result of ``true``.
    """
    _require_arena_mode(mode)
    if (user_id is None) == (place is None):
        raise ArenaError("fetch_candidate requires exactly one of user_id or place.")
    retries = _validate_candidate_retries(retries)
    for attempt in range(retries + 1):
        candidates = fetch_arena_candidates(client, mode)
        for entry in candidates:
            if user_id is not None and entry.user_id == user_id:
                return entry
            if place is not None and entry.place == place:
                return entry
        if attempt < retries and not quiet:
            label = f"userId {user_id}" if user_id is not None else f"rank {place}"
            print(
                f"{Emojis.INFO}Target ({label}) is not in the current candidates; "
                f"refreshing ({attempt + 1}/{retries})."
            )
    return None


def _chunked(items: list[int], size: int) -> Iterator[list[int]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def check_target_range(
    client: HWClient, ids: list[int], mode: ArenaMode = ARENA_MODE
) -> dict[int, bool]:
    """Check attackability, splitting IDs into API-sized chunks (max 3)."""
    _require_arena_mode(mode)
    wanted = [int(i) for i in ids]
    result: dict[int, bool] = {}
    for chunk in _chunked(wanted, MAX_RANGE_IDS):
        res = client.call(
            {
                "calls": [
                    {
                        "name": mode.check_range,
                        "args": {"ids": chunk},
                        "ident": "body",
                    }
                ]
            }
        )
        client.sleep()
        if not res.is_success:
            error = res.error_name or res.status.value
            if error == ErrorName.TOO_MANY.value:
                raise ArenaError(
                    "TooMany: target range check accepts at most "
                    f"{MAX_RANGE_IDS} ids per request."
                )
            raise ArenaError(f"{mode.check_range.value} failed ({error}).")
        result.update(parse_range_response(res.detail))
    return result


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def _find_entry(
    entries: list[ArenaTopEntry], *, rank: int | None = None, user_id: int | None = None
) -> ArenaTopEntry | None:
    for entry in entries:
        if rank is not None and entry.place == rank:
            return entry
        if user_id is not None and entry.user_id == user_id:
            return entry
    return None


def resolve_target(
    client: HWClient,
    status: ArenaStatus,
    *,
    rank: int | None = None,
    user_id: int | None = None,
    candidate_retries: int = DEFAULT_CANDIDATE_RETRIES,
    mode: ArenaMode = ARENA_MODE,
    quiet: bool = False,
) -> ArenaTarget:
    """Resolve ``--rank`` / ``--user-id`` to a target ranked above the player.

    The own rank from ``topGet`` (``ArenaTop.own_place``) is preferred over the
    earlier ``status.place`` because it is fresher; the earlier value is only a
    fallback when ``topGet`` omits ``place`` or reports 0/negative. If neither
    yields a positive rank, resolution stops with a clear error. Targets outside
    the top 50 are resolved from the rotating candidate list, retrying up to
    ``candidate_retries`` extra times. A candidate-resolved target keeps its
    entry in ``ArenaTarget.candidate`` so the attack step reuses it; a topGet
    hit keeps its entry in ``ArenaTarget.top_entry`` instead. ``quiet``
    suppresses candidate-refresh status lines (used by ``arena plan`` so stdout
    stays machine-parseable).
    """
    _require_arena_mode(mode)
    if (rank is None) == (user_id is None):
        raise ArenaError("Specify exactly one of --rank or --user-id.")
    _validate_candidate_retries(candidate_retries)

    if rank is not None:
        if isinstance(rank, bool) or rank < 1:
            raise ArenaError(f"--rank must be a positive integer (got {rank!r}).")
        top = fetch_arena_top(client, mode)
        own_place = _resolve_own_place(top.own_place, lambda: status.place)
        if rank >= own_place:
            raise ArenaError(
                f"Target rank {rank} is not above your current rank {own_place}; "
                "only players ranked above you can be attacked."
            )
        entry = _find_entry(top.entries, rank=rank)
        candidate = None
        if entry is None:
            candidate = fetch_candidate(
                client,
                place=rank,
                retries=candidate_retries,
                mode=mode,
                quiet=quiet,
            )
            entry = candidate
        if entry is None:
            raise ArenaError(
                f"Rank {rank} was not found in the top 50 or the current candidate "
                f"list after {candidate_retries + 1} attempt(s). The ranking API "
                "only returns the top 50; other players must appear in "
                "arenaFindEnemies. Retry later or raise --candidate-retries."
            )
        return ArenaTarget(
            user_id=entry.user_id,
            place=entry.place,
            name=entry.name,
            power=entry.power,
            candidate=candidate,
            top_entry=entry if candidate is None else None,
        )

    target_id = int(user_id)  # type: ignore[arg-type]
    if status.user_id is not None and target_id == status.user_id:
        raise ArenaError("Refusing to attack yourself.")

    top = fetch_arena_top(client, mode)
    own_place = _resolve_own_place(top.own_place, lambda: status.place)
    entry = _find_entry(top.entries, user_id=target_id)
    candidate = None
    if entry is None:
        candidate = fetch_candidate(
            client,
            user_id=target_id,
            retries=candidate_retries,
            mode=mode,
            quiet=quiet,
        )
        entry = candidate
    if entry is None:
        raise ArenaError(
            f"Could not resolve place for userId {target_id} after "
            f"{candidate_retries + 1} candidate attempt(s). It is not in the top 50 "
            "or the current candidate list; use --rank for a known rank."
        )
    if entry.place >= own_place:
        raise ArenaError(
            f"userId {target_id} is ranked {entry.place}, which is not above your "
            f"current rank {own_place}; only players ranked above you can be attacked."
        )
    return ArenaTarget(
        user_id=entry.user_id,
        place=entry.place,
        name=entry.name,
        power=entry.power,
        candidate=candidate,
        top_entry=entry if candidate is None else None,
    )


# ---------------------------------------------------------------------------
# Attack
# ---------------------------------------------------------------------------


def _format_team(team: ArenaTeam | None) -> str:
    if team is None:
        return "not available"
    parts = [f"heroes {team.heroes}"]
    if team.pet is not None:
        parts.append(f"pet {team.pet}")
    if team.banner is not None:
        parts.append(f"banner {team.banner}")
    if team.favor:
        favor = ", ".join(f"{h}->{p}" for h, p in sorted(team.favor.items()))
        parts.append(f"favor {favor}")
    else:
        parts.append("favor none")
    return ", ".join(parts)


def _format_target(target: ArenaTarget) -> str:
    name = f", {target.name}" if target.name else ""
    return f"rank {target.place} (userId {target.user_id}{name})"


def _error_text(res: HWResponse) -> str:
    """Flatten an ``HWResponse`` error payload into searchable text."""
    parts: list[str] = []
    detail = res.detail
    if isinstance(detail, dict):
        for key in ("name", "description", "message"):
            value = detail.get(key)
            if isinstance(value, str):
                parts.append(value)
    elif isinstance(detail, str):
        parts.append(detail)
    if res.error_name:
        parts.append(str(res.error_name))
    return " ".join(parts)


def _classify_attack_error(res: HWResponse) -> str | None:
    """Return ``ErrorName.NOT_ENOUGH`` / ``ErrorName.REFILLABLE`` or None.

    The server reports cooldown/attempt exhaustion as either
    ``error_name == "Refillable"`` or ``NotEnough`` with a description like
    ``"Refillable 5"`` (cooldown) / ``"Refillable 6"`` (attack attempts).
    """
    name = res.error_name or ""
    text = _error_text(res).lower()
    match = re.search(r"refillable\D{0,12}(\d+)", text)
    refillable_id = int(match.group(1)) if match else None

    if name == ErrorName.REFILLABLE.value:
        return (
            ErrorName.NOT_ENOUGH.value
            if refillable_id == ARENA_ATTACK_REFILLABLE_ID
            else ErrorName.REFILLABLE.value
        )
    if name == ErrorName.NOT_ENOUGH.value:
        if "refillable" in text and refillable_id == ARENA_COOLDOWN_REFILLABLE_ID:
            return ErrorName.REFILLABLE.value
        return ErrorName.NOT_ENOUGH.value
    return None


def _cooldown_suffix(status: ArenaStatus) -> str:
    if status.cooldown is None:
        return ""
    return f" Cooldown: {status.cooldown}s."


def run_arena_attack(
    client: HWClient,
    status: ArenaStatus,
    target: ArenaTarget,
    team: ArenaTeam,
    *,
    dry_run: bool = False,
    candidate_retries: int = DEFAULT_CANDIDATE_RETRIES,
    mode: ArenaMode = ARENA_MODE,
) -> ArenaAttackResult:
    """Verify the target and attack (unless ``dry_run``).

    A live attack is only accepted for players present in the latest
    ``*FindEnemies`` response, so the candidate list is refreshed up to
    ``candidate_retries`` extra times until the target appears. Both dry-run
    and live attacks require candidate membership, keeping the dry-run
    prediction accurate. When ``resolve_target`` already captured the entry in
    ``target.candidate``, it is reused instead of re-running the retry loop.
    """
    _require_arena_mode(mode)
    candidate_retries = _validate_candidate_retries(candidate_retries)
    validate_team(team)

    # Re-read the own rank immediately before the checks: a rank change since
    # ``status`` was fetched must not let a same/lower-ranked target through
    # (``*CheckTargetRange`` is also true for below-ranked opponents).
    fresh_place = fetch_own_place(client, mode)
    if target.place >= fresh_place:
        raise ArenaError(
            f"Target {_format_target(target)} is not above your current rank "
            f"{fresh_place}; only players ranked above you can be attacked "
            "(upper-only). Re-run after checking the ranking."
        )

    candidate = target.candidate
    if candidate is None:
        candidate = fetch_candidate(
            client, user_id=target.user_id, retries=candidate_retries, mode=mode
        )
        if candidate is None:
            raise ArenaError(
                f"Target {_format_target(target)} did not appear in the candidate list "
                f"after {candidate_retries + 1} attempt(s). arenaAttack only accepts "
                "players currently returned by arenaFindEnemies (the observed window is "
                "roughly 1-13 ranks above, but higher ranks can appear); no attack was "
                "sent. Retry later or raise --candidate-retries."
            )
        # The retry loop can span many requests, so re-read the own rank before
        # the upper-only check and the range-window message.
        fresh_place = fetch_own_place(client, mode)

    # The candidate entry carries the target's current place, which may have
    # shifted since ``resolve_target``. Re-apply the upper-only rule to it.
    target = ArenaTarget(
        user_id=target.user_id,
        place=candidate.place,
        name=candidate.name or target.name,
        power=candidate.power if candidate.power is not None else target.power,
        candidate=candidate,
    )
    if target.place >= fresh_place:
        raise ArenaError(
            f"Target {_format_target(target)} is not above your current rank "
            f"{fresh_place}; only players ranked above you can be attacked "
            "(upper-only). Re-run after checking the ranking."
        )

    allowed = check_target_range(client, [target.user_id], mode)
    if allowed.get(target.user_id) is not True:
        raise ArenaError(
            f"Target {_format_target(target)} is outside the attack range reported "
            f"by {mode.check_range.value} for your rank {fresh_place}. The window "
            "is rank/league-dependent and that check result is authoritative; "
            "no attack was sent."
        )

    print(f"{Emojis.INFO}Target: {_format_target(target)}")
    print(f"{Emojis.INFO}Team:   {_format_team(team)}")
    print(f"{Emojis.SUCCESS}Range check: allowed")

    if dry_run:
        print(f"{Emojis.INFO}Dry-run: arenaAttack was not sent.")
        return ArenaAttackResult(dry_run=True, accepted=False)

    return _send_arena_attack(client, status, target, team, mode)


def _send_arena_attack(
    client: HWClient,
    status: ArenaStatus,
    target: ArenaTarget,
    team: ArenaTeam,
    mode: ArenaMode = ARENA_MODE,
) -> ArenaAttackResult:
    """Send exactly one attack and classify the response."""
    args = build_attack_args(target.user_id, team)
    # Never retry an attack: a timeout after the server accepted the request
    # would double-attack and consume another attempt.
    res = client.call(
        {
            "calls": [
                {
                    "name": mode.attack,
                    "args": args,
                    "context": {"actionTs": 0},
                    "ident": "body",
                }
            ]
        },
        max_retries=0,
    )
    client.sleep()

    if not res.is_success:
        error = res.error_name or res.status.value
        if res.status == ResponseStatus.UNEXPECTED or error == "network_or_parse_error":
            raise ArenaError(
                "The arena attack request was sent but its outcome is unknown "
                f"({error}). Check the game client before retrying; the attack may "
                "have consumed an attempt."
            )
        stopped = _classify_attack_error(res)
        if stopped == ErrorName.NOT_ENOUGH.value:
            warning = "Arena attack not sent: not enough attack attempts (NotEnough)."
            print(f"{Emojis.WARNING}{warning}")
            return ArenaAttackResult(
                dry_run=False, accepted=False, stopped=stopped, warning=warning
            )
        if stopped == ErrorName.REFILLABLE.value:
            warning = (
                "Arena attack not sent: Arena is on cooldown (Refillable)."
                + _cooldown_suffix(status)
            )
            print(f"{Emojis.WARNING}{warning}")
            return ArenaAttackResult(
                dry_run=False, accepted=False, stopped=stopped, warning=warning
            )
        raise ArenaError(f"{mode.attack.value} failed ({error}).")

    parsed = parse_attack_result(res.detail)
    if parsed is None:
        warning = (
            "Attack was accepted but the battle result could not be parsed; "
            "check the game client before retrying (a retry may consume another attempt)."
        )
        print(f"{Emojis.WARNING}{warning}")
        return ArenaAttackResult(dry_run=False, accepted=True, warning=warning)

    outcome = "WIN" if parsed.win else "LOSS"
    print(
        f"{Emojis.VICTORY if parsed.win else Emojis.INFO}Result: {outcome} "
        f"({_human_int(parsed.old_place)} -> {_human_int(parsed.new_place)})"
    )
    return parsed


# ---------------------------------------------------------------------------
# Presets (account_configs.arena_team_presets)
# ---------------------------------------------------------------------------


def validate_preset_name(name: str) -> str:
    value = str(name or "").strip()
    if not _PRESET_NAME_RE.fullmatch(value):
        raise ArenaError(
            f"Invalid preset name {name!r}: use 1-40 characters "
            "([A-Za-z0-9._-], starting with a letter or digit)."
        )
    return value


def serialize_team(team: ArenaTeam) -> dict[str, Any]:
    data: dict[str, Any] = {
        "heroes": [int(h) for h in team.heroes],
        "favor": {str(h): int(p) for h, p in team.favor.items()},
    }
    if team.pet is not None:
        data["pet"] = int(team.pet)
    if team.banners and team.banners[0] > 0:
        data["banners"] = [int(team.banners[0])]
    return data


def deserialize_team(data: Any) -> ArenaTeam:
    if not isinstance(data, dict):
        raise ArenaError("Stored Arena team is not an object.")
    raw_heroes = data.get("heroes")
    if not isinstance(raw_heroes, list):
        raise ArenaError("Stored Arena team is missing heroes.")
    heroes = [int(h) for h in raw_heroes]
    pet = _safe_int(data.get("pet"), None)
    if pet == 0:
        pet = None
    raw_favor = data.get("favor")
    favor: dict[int, int] = {}
    if isinstance(raw_favor, dict):
        for hero_key, patron in raw_favor.items():
            hero_id = _safe_int(hero_key, None)
            patron_id = _safe_int(patron, None)
            if hero_id is not None and patron_id is not None:
                favor[hero_id] = patron_id
    raw_banners = data.get("banners")
    banners: list[int] = []
    if isinstance(raw_banners, list):
        banners = [int(b) for b in raw_banners if _normalize_banner(b) is not None]
    elif raw_banners is not None:
        banner = _normalize_banner(raw_banners)
        if banner is not None:
            banners = [banner]

    team = ArenaTeam(heroes=heroes, pet=pet, favor=favor, banners=banners)
    validate_team(team)
    return team


def load_arena_presets(account: str, mode: ArenaMode = ARENA_MODE) -> dict[str, ArenaTeam]:
    from hw_genie.core.session_manager import SessionManager

    data = SessionManager.load(account) or {}
    root = data.get(ARENA_TEAM_PRESETS_KEY)
    if not isinstance(root, dict):
        return {}
    section = root.get(mode.key)
    if not isinstance(section, dict):
        return {}

    presets: dict[str, ArenaTeam] = {}
    for name, raw in section.items():
        try:
            safe_name = validate_preset_name(name)
            presets[safe_name] = deserialize_team(raw)
        except (ArenaError, TypeError, ValueError):
            continue
    return presets


def save_arena_preset(
    account: str, name: str, team: ArenaTeam, mode: ArenaMode = ARENA_MODE
) -> ArenaTeam:
    from hw_genie.core.session_manager import SessionManager

    safe_name = validate_preset_name(name)
    validate_team(team)

    def _merge(existing: Any) -> dict[str, Any]:
        root = existing if isinstance(existing, dict) else {}
        section = root.get(mode.key)
        if not isinstance(section, dict):
            section = {}
        section[safe_name] = serialize_team(team)
        root[mode.key] = section
        return root

    SessionManager.repo.update_config_merged(account, ARENA_TEAM_PRESETS_KEY, _merge)
    return team


def remove_arena_preset(account: str, name: str, mode: ArenaMode = ARENA_MODE) -> None:
    from hw_genie.core.session_manager import SessionManager

    safe_name = validate_preset_name(name)

    def _merge(existing: Any) -> dict[str, Any]:
        root = existing if isinstance(existing, dict) else {}
        section = root.get(mode.key)
        if not isinstance(section, dict) or safe_name not in section:
            raise ArenaError(f"Arena team preset '{safe_name}' not found.")
        del section[safe_name]
        root[mode.key] = section
        return root

    SessionManager.repo.update_config_merged(account, ARENA_TEAM_PRESETS_KEY, _merge)


# ---------------------------------------------------------------------------
# Snapshots (account_configs.arena_snapshot)
# ---------------------------------------------------------------------------


def serialize_snapshot(snapshot: ArenaSnapshot) -> dict[str, Any]:
    return {
        "saved_at": snapshot.saved_at,
        "own_place": int(snapshot.own_place),
        "candidates": [
            {
                "user_id": int(entry.user_id),
                "place": int(entry.place),
                "name": entry.name,
                "power": entry.power,
            }
            for entry in snapshot.candidates
        ],
    }


def deserialize_snapshot(data: Any) -> ArenaSnapshot | None:
    if not isinstance(data, dict):
        return None
    own_place = _safe_int(data.get("own_place"), None)
    if own_place is None or own_place <= 0:
        return None
    raw_candidates = data.get("candidates")
    candidates: list[ArenaTopEntry] = []
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            user_id = _safe_int(item.get("user_id"), None)
            place = _safe_int(item.get("place"), None)
            if user_id is None or user_id <= 0 or place is None or place <= 0:
                continue
            name = item.get("name")
            candidates.append(
                ArenaTopEntry(
                    place=place,
                    user_id=user_id,
                    name=name if isinstance(name, str) else None,
                    power=_safe_int(item.get("power"), None),
                )
            )
    saved_at = data.get("saved_at")
    return ArenaSnapshot(
        own_place=own_place,
        candidates=candidates,
        saved_at=saved_at if isinstance(saved_at, str) else "",
    )


def save_arena_snapshot(
    account: str, snapshot: ArenaSnapshot, mode: ArenaMode = ARENA_MODE
) -> ArenaSnapshot:
    from hw_genie.core.session_manager import SessionManager

    _require_arena_mode(mode)

    def _merge(existing: Any) -> dict[str, Any]:
        root = existing if isinstance(existing, dict) else {}
        root[mode.key] = serialize_snapshot(snapshot)
        return root

    SessionManager.repo.update_config_merged(account, ARENA_SNAPSHOT_KEY, _merge)
    return snapshot


def load_arena_snapshot(
    account: str, mode: ArenaMode = ARENA_MODE
) -> ArenaSnapshot | None:
    from hw_genie.core.session_manager import SessionManager

    _require_arena_mode(mode)
    data = SessionManager.load(account) or {}
    root = data.get(ARENA_SNAPSHOT_KEY)
    section = root.get(mode.key) if isinstance(root, dict) else None
    return deserialize_snapshot(section)


def capture_arena_snapshot(
    client: HWClient, mode: ArenaMode = ARENA_MODE
) -> ArenaSnapshot:
    _require_arena_mode(mode)
    own_place = fetch_own_place(client, mode)
    candidates = fetch_arena_candidates(client, mode)
    return ArenaSnapshot(
        own_place=own_place,
        candidates=candidates,
        saved_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def resolve_snapshot_target(
    snapshot: ArenaSnapshot,
    *,
    rank: int | None = None,
    user_id: int | None = None,
) -> ArenaTarget:
    if (rank is None) == (user_id is None):
        raise ArenaError("Specify exactly one of --rank or --user-id.")
    entry = _find_entry(snapshot.candidates, rank=rank, user_id=user_id)
    if entry is None:
        label = f"rank {rank}" if rank is not None else f"userId {user_id}"
        raise ArenaError(
            f"{label} is not in the saved Arena snapshot. "
            "Run `arena find-enemies` to take a new snapshot."
        )
    return ArenaTarget(
        user_id=entry.user_id,
        place=entry.place,
        name=entry.name,
        power=entry.power,
    )


def _snapshot_age(saved_at: str) -> str:
    try:
        saved = datetime.fromisoformat(saved_at)
    except (TypeError, ValueError):
        return "unknown"
    now = datetime.now(saved.tzinfo) if saved.tzinfo else datetime.now()
    seconds = max(0, int((now - saved).total_seconds()))
    if seconds < 120:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes}m ago"
    return f"{minutes // 60}h ago"


def run_arena_find_enemies(
    client: HWClient,
    account: str,
    *,
    raw: bool = False,
    mode: ArenaMode = ARENA_MODE,
) -> ArenaSnapshot:
    snapshot = capture_arena_snapshot(client, mode)
    save_arena_snapshot(account, snapshot, mode)
    if raw:
        print(
            json.dumps(
                {mode.key: serialize_snapshot(snapshot)},
                indent=2,
                ensure_ascii=False,
            )
        )
        return snapshot

    print(f"{Emojis.SUCCESS}Saved {mode.key} snapshot at {snapshot.saved_at}")
    print(f"  {Emojis.INFO}Own rank: {snapshot.own_place}")
    if not snapshot.candidates:
        print(f"  {Emojis.INFO}No candidates returned by {mode.find_enemies.value}.")
    for entry in snapshot.candidates:
        name = f" {entry.name}" if entry.name else ""
        print(
            f"  {Emojis.INFO}rank {entry.place} "
            f"(userId {entry.user_id}{name}, delta {entry.place - snapshot.own_place:+d})"
        )
    return snapshot


def run_arena_snapshot(
    client: HWClient, account: str, mode: ArenaMode = ARENA_MODE
) -> ArenaSnapshot:
    snapshot = load_arena_snapshot(account, mode)
    if snapshot is None:
        raise ArenaError("No saved Arena snapshot. Run `arena find-enemies` first.")

    top = fetch_arena_top(client, mode)
    current_own = _resolve_own_place(
        top.own_place, lambda: fetch_own_place(client, mode)
    )
    current_by_uid = {entry.user_id: entry for entry in top.entries}
    ids = [entry.user_id for entry in snapshot.candidates]
    checks = check_target_range(client, ids, mode) if ids else {}

    print(f"\n{Emojis.STEP}--- Arena snapshot ({account}) ---")
    print(
        f"  {Emojis.INFO}Saved at: {snapshot.saved_at or 'unknown'} "
        f"({_snapshot_age(snapshot.saved_at)})"
    )
    print(f"  {Emojis.INFO}Own rank: {snapshot.own_place} -> {current_own}")
    if not snapshot.candidates:
        print(f"  {Emojis.INFO}No candidates in the snapshot.")
        return snapshot

    header = (
        f"{'UserID':>12} {'Name':<16} "
        f"{'Snapshot':>8} {'Current':>7} {'Delta':>6} {'>13':>4} {'Range':>5}"
    )
    print(header)
    print("-" * len(header))
    for entry in snapshot.candidates:
        current = current_by_uid.get(entry.user_id)
        current_place = current.place if current is not None else None
        delta = current_place - current_own if current_place is not None else None
        over13 = "-" if delta is None else ("yes" if delta < -13 else "no")
        range_value = checks.get(entry.user_id)
        range_text = "?" if range_value is None else ("true" if range_value else "false")
        print(
            f"{entry.user_id:>12} {(entry.name or '?')[:16]:<16} "
            f"{entry.place:>8} {_human_int(current_place):>7} "
            f"{_human_int(delta):>6} {over13:>4} {range_text:>5}"
        )
    print(
        f"{Emojis.INFO}topGet covers the top 50 only; the snapshot itself is not refreshed."
    )
    return snapshot


def run_arena_attack_from_snapshot(
    client: HWClient,
    status: ArenaStatus,
    target: ArenaTarget,
    team: ArenaTeam,
    *,
    dry_run: bool = False,
    force: bool = False,
    mode: ArenaMode = ARENA_MODE,
) -> ArenaAttackResult:
    """Attack a saved-snapshot target without calling ``*FindEnemies``.

    Experimental path for verifying whether the server still accepts a
    snapshot candidate after ranks have drifted. The target must be in the
    current top 50 so its current rank can be verified; the upper-only rule is
    always enforced from that fresh rank, and a false ``*CheckTargetRange``
    stops unless ``force`` is set.
    """
    _require_arena_mode(mode)
    validate_team(team)

    if status.user_id is not None and target.user_id == status.user_id:
        raise ArenaError("Refusing to attack yourself.")

    # The snapshot place is stale, so it is never used for the upper-only
    # check: ``topGet`` (current rank) is authoritative. Fall back to
    # ``arenaGetAll`` only when ``topGet`` omits the own rank or reports 0.
    top = fetch_arena_top(client, mode)
    fresh_place = _resolve_own_place(
        top.own_place, lambda: fetch_own_place(client, mode)
    )
    current = _find_entry(top.entries, user_id=target.user_id)
    if current is None:
        raise ArenaError(
            f"Target {_format_target(target)} is not in the current top 50; "
            "its current rank cannot be verified, so the upper-only rule cannot "
            "be enforced. No attack was sent."
        )
    target = ArenaTarget(
        user_id=target.user_id,
        place=current.place,
        name=current.name or target.name,
        power=current.power if current.power is not None else target.power,
    )
    if target.place >= fresh_place:
        raise ArenaError(
            f"Target {_format_target(target)} is not above your current rank "
            f"{fresh_place}; only players ranked above you can be attacked "
            "(upper-only). Re-run after checking the ranking."
        )

    allowed = check_target_range(client, [target.user_id], mode)
    range_ok = allowed.get(target.user_id) is True
    print(f"{Emojis.INFO}Target: {_format_target(target)}")
    print(f"{Emojis.INFO}Team:   {_format_team(team)}")
    print(
        f"{Emojis.INFO}Range check: "
        f"{'allowed' if range_ok else 'not allowed'}"
    )

    if not range_ok and not force:
        raise ArenaError(
            "CheckTargetRange returned false (or no result). The snapshot attack "
            "stopped without consuming an attempt; use --force to send it anyway "
            "(upper-only is still enforced)."
        )
    if dry_run:
        print(f"{Emojis.INFO}Dry-run: arenaAttack was not sent.")
        return ArenaAttackResult(dry_run=True, accepted=False)

    return _send_arena_attack(client, status, target, team, mode)


# ---------------------------------------------------------------------------
# CLI-facing runners
# ---------------------------------------------------------------------------


def run_arena_status(
    client: HWClient, account_alias: str | None = None, mode: ArenaMode = ARENA_MODE
) -> ArenaStatus:
    prefix = f"[{account_alias}] " if account_alias else ""
    status = fetch_arena_status(client, mode)

    print(f"\n{Emojis.STEP}{prefix}--- Arena status ---")
    print(f"  {Emojis.INFO}Rank: {status.place} (Grand Arena: {status.grand_place})")
    if status.attempts is not None:
        print(f"  {Emojis.INFO}Attack attempts: {status.attempts} / {ARENA_MAX_ATTEMPTS}")
    else:
        print(f"  {Emojis.WARNING}Attack attempts: unavailable")
    if status.cooldown is not None:
        cooldown = f"{status.cooldown}s" if status.cooldown else "none"
        print(f"  {Emojis.INFO}Cooldown: {cooldown}")
    print(f"  {Emojis.INFO}Saved team: {_format_team(status.saved_team)}")
    print(
        f"  {Emojis.INFO}Attack window: rank/league-dependent; "
        "use `arena attack --dry-run` to check a target"
    )
    return status


def run_arena_top(
    client: HWClient,
    *,
    limit: int | None = None,
    raw: bool = False,
    mode: ArenaMode = ARENA_MODE,
) -> ArenaTop:
    top = fetch_arena_top(client, mode)
    if raw:
        print(json.dumps(top.raw or {}, indent=2, ensure_ascii=False))
        return top
    if not top.entries:
        print(f"{Emojis.INFO}No ranking entries returned by topGet.")
        return top

    entries = top.entries
    if limit is not None:
        entries = entries[: max(1, int(limit))]

    header = f"{'Place':>5}  {'Name':<20} {'UserID':>12} {'Power':>10}  Heroes"
    print("\n" + header)
    print("-" * len(header))
    for entry in entries:
        name = (entry.name or "?")[:20]
        heroes = ",".join(str(h) for h in entry.heroes)
        if entry.pet is not None:
            heroes = f"{heroes} + pet {entry.pet}" if heroes else f"pet {entry.pet}"
        print(
            f"{entry.place:>5}  {name:<20} {entry.user_id:>12} "
            f"{_human_int(entry.power):>10}  {heroes}"
        )
    shown = len(entries)
    total = len(top.entries)
    suffix = f" (showing {shown}/{total})" if shown != total else ""
    own_place = (
        top.own_place if top.own_place is not None and top.own_place > 0 else None
    )
    print(f"{Emojis.INFO}Your rank: {_human_int(own_place)}; topGet returns at most 50 entries{suffix}")
    return top


def run_arena_plan(
    client: HWClient,
    *,
    rank: int | None = None,
    user_id: int | None = None,
    min_samples: int = 3,
    min_win_rate: float = 1.0,
    limit: int = 10,
    candidate_retries: int | None = None,
    game_version: str | None = None,
    as_json: bool = False,
    mode: ArenaMode = ARENA_MODE,
) -> list[PlanCandidate]:
    """Recommend attacker lineups against the target's current defense.

    Read-only: this never calls ``*Attack``. Recommendations come from the
    battle corpus (``hw-genie battle import``), aggregated across all accounts.
    Same-defender history is preferred when it has enough samples; otherwise
    same-lineup history from other defenders/accounts is used.
    ``game_version`` (None = all versions) restricts the corpus to one version.
    """
    _require_arena_mode(mode)
    if not 0.0 <= min_win_rate <= 1.0:
        raise ArenaError("--min-win-rate must be between 0.0 and 1.0.")
    retries = (
        DEFAULT_CANDIDATE_RETRIES
        if candidate_retries is None
        else _validate_candidate_retries(candidate_retries)
    )

    status = fetch_arena_status(client, mode)
    target = resolve_target(
        client,
        status,
        rank=rank,
        user_id=user_id,
        candidate_retries=retries,
        mode=mode,
        quiet=True,
    )
    # ``resolve_target`` keeps the topGet hit in ``top_entry`` and a rotating
    # *FindEnemies hit in ``candidate``. Planning needs only the current lineup
    # and must not re-fetch topGet (that would duplicate the call and could
    # disagree with the rank we just resolved).
    entry = target.top_entry or target.candidate
    if entry is None:
        # Outside both sources: fall back to the rotating candidate list. Keep
        # it quiet so `--json` output is not polluted by refresh status lines.
        entry = fetch_candidate(
            client, user_id=target.user_id, retries=retries, mode=mode, quiet=True
        )
    if entry is None or len(entry.heroes) != 5:
        raise ArenaError(
            "Could not read the target's current lineup from "
            f"{mode.find_enemies.value} or topGet; cannot plan."
        )

    defender_key = lineup_key_from_ids(entry.heroes, entry.pet, entry.banner)
    plans = query_plans(
        defender_lineup_key=defender_key,
        defender_id=str(target.user_id),
        battle_type=mode.key,
        game_version=game_version,
        min_samples=min_samples,
        min_win_rate=min_win_rate,
        limit=limit,
    )

    if as_json:
        print(
            json.dumps(
                {
                    "target": {
                        "user_id": target.user_id,
                        "place": entry.place,
                        "name": target.name,
                        "heroes": entry.heroes,
                        "pet": entry.pet,
                        "banner": entry.banner,
                    },
                    "min_samples": min_samples,
                    "min_win_rate": min_win_rate,
                    "plans": [
                        {
                            "heroes": plan.heroes_summary,
                            "pet": plan.pet_id,
                            "banner": plan.banner_id,
                            "battles": plan.battles,
                            "wins": plan.wins,
                            "win_rate": round(plan.win_rate, 4),
                            "wilson_low": round(plan.wilson_low, 4),
                            "tier": plan.tier,
                            "recommended": plan.recommended,
                            "last_seen": plan.last_seen.isoformat()
                            if plan.last_seen
                            else None,
                        }
                        for plan in plans
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return plans

    name = f" ({target.name})" if target.name else ""
    print(f"{Emojis.INFO}Target: rank {entry.place} userId {target.user_id}{name}")
    pet_text = entry.pet if entry.pet is not None else "-"
    banner_text = entry.banner if entry.banner is not None else "-"
    print(
        f"{Emojis.INFO}Defender lineup: {','.join(str(h) for h in entry.heroes)} "
        f"pet {pet_text} banner {banner_text}"
    )
    if entry.banner is None:
        print(
            f"{Emojis.WARNING}The live candidate did not return a banner; the corpus "
            "stores defender banners, so exact lineup matching may miss."
        )
    print(f"{Emojis.INFO}Gate: n >= {min_samples} and win rate >= {min_win_rate:.0%}")

    if not plans:
        print(
            f"{Emojis.WARNING}No matching battles in the corpus for this defender lineup."
        )
        print(
            "  Import more logs with `hw-genie battle import -a <account>`, "
            "or wait for the oracle (Phase 2)."
        )
        return plans

    header = (
        f"  {'#':>2}  {'heroes':<24} {'pet':>5} {'bnr':>4} "
        f"{'n':>3} {'w':>3} {'rate':>5} {'wilson':>6}  {'tier':<14} last seen"
    )
    print(header)
    for index, plan in enumerate(plans, 1):
        mark = "REC" if plan.recommended else "   "
        last_seen = plan.last_seen.strftime("%Y-%m-%d") if plan.last_seen else "-"
        pet = str(plan.pet_id) if plan.pet_id is not None else "-"
        banner = str(plan.banner_id) if plan.banner_id is not None else "-"
        print(
            f"{mark} {index:>2}  {plan.heroes_summary:<24} {pet:>5} {banner:>4} "
            f"{plan.battles:>3} {plan.wins:>3} {plan.win_rate * 100:>4.0f}% "
            f"{plan.wilson_low * 100:>5.0f}%  {plan.tier:<14} {last_seen}"
        )

    best = next((plan for plan in plans if plan.recommended), None)
    if best is None:
        print(
            f"{Emojis.WARNING}No plan meets the gate. Lower --min-win-rate / "
            "--min-samples, or collect more logs."
        )
        return plans

    heroes = " ".join(best.heroes_summary.split(","))
    parts = [
        f"uv run hw-genie arena attack -a <account> --user-id {target.user_id}",
        f"--heroes {heroes}",
    ]
    if best.pet_id is not None:
        parts.append(f"--pet {best.pet_id}")
    if best.banner_id is not None:
        parts.append(f"--banner {best.banner_id}")
    print(f"{Emojis.SUCCESS}Recommended: {' '.join(parts)}")
    return plans


def run_arena_team_set(
    account: str,
    name: str,
    *,
    client: HWClient | None = None,
    from_saved: bool = False,
    heroes: list[int] | None = None,
    pet: int | None = None,
    banner: int | None = None,
    favor: dict[int, int] | None = None,
    mode: ArenaMode = ARENA_MODE,
) -> ArenaTeam:
    safe_name = validate_preset_name(name)
    saved = None
    if from_saved:
        if client is None:
            raise ArenaError("--from-saved requires an authenticated API client.")
        saved = fetch_saved_team(client, mode)
        if saved is None:
            raise ArenaError(
                "Could not read the saved Arena team; check the session or pass "
                "--heroes explicitly."
            )
    team, missing = resolve_team(
        heroes=heroes,
        pet=pet,
        banner=banner,
        favor=favor,
        saved=saved,
    )
    save_arena_preset(account, safe_name, team, mode)
    print(f"{Emojis.SUCCESS}Saved Arena team preset '{safe_name}': {_format_team(team)}")
    if missing:
        print(f"{Emojis.WARNING}No favor entry for heroes: {', '.join(map(str, missing))}")
    return team


def run_arena_team_list(
    account: str, mode: ArenaMode = ARENA_MODE
) -> dict[str, ArenaTeam]:
    presets = load_arena_presets(account, mode)
    if not presets:
        print(f"{Emojis.INFO}No Arena team presets for '{account}'.")
        return presets
    print(f"\n{Emojis.STEP}--- Arena team presets ({account}) ---")
    for name in sorted(presets):
        print(f"  {name}: {_format_team(presets[name])}")
    return presets


def run_arena_team_remove(
    account: str, name: str, mode: ArenaMode = ARENA_MODE
) -> None:
    safe_name = validate_preset_name(name)
    remove_arena_preset(account, safe_name, mode)
    print(f"{Emojis.SUCCESS}Removed Arena team preset '{safe_name}'.")


__all__ = [
    "ARENA_MODE",
    "ArenaError",
    "ArenaSnapshot",
    "ArenaStatus",
    "ArenaTarget",
    "ArenaTeam",
    "ARENA_SNAPSHOT_KEY",
    "ARENA_TEAM_PRESETS_KEY",
    "DEFAULT_CANDIDATE_RETRIES",
    "build_attack_args",
    "capture_arena_snapshot",
    "check_target_range",
    "deserialize_snapshot",
    "deserialize_team",
    "fetch_arena_candidates",
    "fetch_arena_status",
    "fetch_arena_top",
    "fetch_candidate",
    "fetch_own_place",
    "fetch_saved_team",
    "load_arena_snapshot",
    "load_arena_presets",
    "parse_attack_result",
    "parse_candidates_response",
    "parse_favor_args",
    "parse_range_response",
    "parse_top_response",
    "remove_arena_preset",
    "resolve_snapshot_target",
    "resolve_target",
    "resolve_team",
    "run_arena_attack",
    "run_arena_attack_from_snapshot",
    "run_arena_find_enemies",
    "run_arena_snapshot",
    "run_arena_status",
    "run_arena_team_list",
    "run_arena_team_remove",
    "run_arena_team_set",
    "run_arena_top",
    "save_arena_preset",
    "save_arena_snapshot",
    "serialize_snapshot",
    "serialize_team",
    "validate_partial_team",
    "validate_preset_name",
    "validate_team",
]
