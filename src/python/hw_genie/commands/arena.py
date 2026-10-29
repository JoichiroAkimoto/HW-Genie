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
- ``arenaCheckTargetRange`` accepts at most 3 IDs per request; callers are
  batched accordingly.
- ``arenaAttack`` is sent exactly once (``max_retries=0``): a timeout after the
  server accepted the request must not trigger a second attack.

The verified API shapes and the empirical ±21 rank window live in
``docs/api/ARENA_API.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from hw_genie.core.client import (
    ApiAction,
    Emojis,
    ErrorName,
    HWClient,
    HWResponse,
    ResponseStatus,
)

# Arena attack attempts / cooldown live in ``userGetInfo.refillable``.
ARENA_ATTACK_REFILLABLE_ID = 6
ARENA_COOLDOWN_REFILLABLE_ID = 5
ARENA_MAX_ATTEMPTS = 5

# Empirical window observed at ranks 22/34: own_place ± 21. This is only used
# for user-facing messages; the authoritative per-rank window is whatever
# ``*CheckTargetRange`` returns and may be wider at lower ranks.
ARENA_RANGE = 21
# ``*CheckTargetRange`` rejects 4+ ids with ERROR TooMany.
MAX_RANGE_IDS = 3
# ``*Attack`` only accepts players in the latest ``*FindEnemies`` list. The
# list rotates, so refresh it this many extra times while waiting for the
# requested target to appear.
DEFAULT_CANDIDATE_RETRIES = 10

# Config key in account_configs: {"arena": {name: {heroes, pet?, favor, banners?}}}
ARENA_TEAM_PRESETS_KEY = "arena_team_presets"

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


@dataclass
class ArenaTopEntry:
    place: int
    user_id: int
    name: str | None = None
    power: int | None = None
    heroes: list[int] = field(default_factory=list)
    pet: int | None = None


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

    return ArenaTopEntry(
        place=place,
        user_id=user_id,
        name=user.get("name") if isinstance(user.get("name"), str) else None,
        power=_safe_int(item.get("power"), None),
        heroes=heroes,
        pet=pet,
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


def attackable_window(own_place: int) -> tuple[int, int]:
    """Return the *above-only* rank window as ``(lower, upper)`` for messages.

    The ±21 span is empirical from ranks 22/34; the real per-rank window comes
    from ``*CheckTargetRange`` and may be wider at lower ranks. This feature
    never targets the same rank or below, so the upper bound is
    ``own_place - 1``.
    """
    lower = max(1, own_place - ARENA_RANGE)
    upper = max(0, own_place - 1)
    return lower, upper


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

    Used immediately before an attack so a rank change between the earlier
    status fetch and the attack cannot widen the upper-only boundary.
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
) -> ArenaTopEntry | None:
    """Return the target's current ``*FindEnemies`` entry.

    ``*Attack`` only accepts players present in the most recent candidate list,
    and the list rotates between calls. ``retries`` counts refreshes *after*
    the first call, so up to ``retries + 1`` requests are made. Identify the
    target by ``user_id`` (stable) or ``place`` (for ``--rank`` resolution).

    There is deliberately no fixed "N ranks above" limit here: the candidate
    window observed so far is roughly 1-13 ranks above, but a higher-ranked
    player can appear as a candidate. Membership in this list is the only
    extra requirement beyond the ±21 range check.
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
        if attempt < retries:
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
) -> ArenaTarget:
    """Resolve ``--rank`` / ``--user-id`` to a target ranked above the player.

    The own rank from ``topGet`` (``ArenaTop.own_place``) is preferred over the
    earlier ``status.place`` because it is fresher; the earlier value is only a
    fallback when ``topGet`` omits ``place``. Targets outside the top 50 are
    resolved from the rotating candidate list, retrying up to
    ``candidate_retries`` extra times. A candidate-resolved target keeps its
    entry in ``ArenaTarget.candidate`` so the attack step reuses it.
    """
    _require_arena_mode(mode)
    if (rank is None) == (user_id is None):
        raise ArenaError("Specify exactly one of --rank or --user-id.")
    _validate_candidate_retries(candidate_retries)

    if rank is not None:
        if isinstance(rank, bool) or rank < 1:
            raise ArenaError(f"--rank must be a positive integer (got {rank!r}).")
        top = fetch_arena_top(client, mode)
        own_place = top.own_place if top.own_place is not None else status.place
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
        )

    target_id = int(user_id)  # type: ignore[arg-type]
    if status.user_id is not None and target_id == status.user_id:
        raise ArenaError("Refusing to attack yourself.")

    top = fetch_arena_top(client, mode)
    own_place = top.own_place if top.own_place is not None else status.place
    entry = _find_entry(top.entries, user_id=target_id)
    candidate = None
    if entry is None:
        candidate = fetch_candidate(
            client,
            user_id=target_id,
            retries=candidate_retries,
            mode=mode,
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
            "is rank/league-dependent (empirical ±21 at ranks 22/34, wider at lower "
            "ranks); no attack was sent."
        )

    print(f"{Emojis.INFO}Target: {_format_target(target)}")
    print(f"{Emojis.INFO}Team:   {_format_team(team)}")
    print(f"{Emojis.SUCCESS}Range check: allowed")

    if dry_run:
        print(f"{Emojis.INFO}Dry-run: arenaAttack was not sent.")
        return ArenaAttackResult(dry_run=True, accepted=False)

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
        return ArenaAttackResult(
            dry_run=False, accepted=True, warning=warning
        )

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
    print(f"{Emojis.INFO}Your rank: {_human_int(top.own_place)}; topGet returns at most 50 entries{suffix}")
    return top


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
    "ArenaStatus",
    "ArenaTarget",
    "ArenaTeam",
    "ARENA_TEAM_PRESETS_KEY",
    "DEFAULT_CANDIDATE_RETRIES",
    "attackable_window",
    "build_attack_args",
    "check_target_range",
    "deserialize_team",
    "fetch_arena_candidates",
    "fetch_arena_status",
    "fetch_arena_top",
    "fetch_candidate",
    "fetch_own_place",
    "fetch_saved_team",
    "load_arena_presets",
    "parse_attack_result",
    "parse_candidates_response",
    "parse_favor_args",
    "parse_range_response",
    "parse_top_response",
    "remove_arena_preset",
    "resolve_target",
    "resolve_team",
    "run_arena_attack",
    "run_arena_status",
    "run_arena_team_list",
    "run_arena_team_remove",
    "run_arena_team_set",
    "run_arena_top",
    "save_arena_preset",
    "serialize_team",
    "validate_partial_team",
    "validate_preset_name",
    "validate_team",
]
