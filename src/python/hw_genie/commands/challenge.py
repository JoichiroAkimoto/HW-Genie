"""Friendly challenge automation (chatSendChallenge / chatAcceptChallenge).

Live-verified flow (2026-11-15, serverVersion 299):

1. Send: ``chatSendChallenge`` with the **target (defending) team**. The
   observed game flow sends a single unit (``heroes=[4013]``); the saved
   ``teamGetAll.challenge_titan`` / ``challenge`` provides the default pick.
   The recipient gets a personal-chat message with ``messageType="challenge"``
   whose ``data.heroes`` is this target team.
2. messageId: ``chatGetAll`` (``chatType=personal``, ``userId=<peer>``)
   returns ``chat[]``. The challenge message's ``id`` is the ``messageId``
   needed by ``chatAcceptChallenge``. A later ``replay`` message whose
   ``data.challengeId`` equals that id means the challenge was already fought.
3. Battle: ``chatAcceptChallenge`` with the peer userId, the messageId and the
   full saved team as attackers. **Both participants can battle**: the
   recipient fights the target team, and the sender can also battle its own
   challenge (attackers = own full team, defenders = the challenge's target
   team; ``battle.userId`` / ``battle.typeId`` are both the sender because
   both teams belong to it).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from rich import box
from rich.console import Console
from rich.table import Table

from hw_genie.core.client import (
    Emojis,
    HWClient,
    HWResponse,
    ResponseStatus,
    _safe_int,
)
from hw_genie.core.utils import display_timezone_name, format_timestamp_for_display

CHALLENGE_TYPES = ("titan", "hero")
CHALLENGE_TEAM_FIELDS = {"titan": "challenge_titan", "hero": "challenge"}
CHALLENGE_DEFAULTS_KEY = "challenge_defaults"
DEFAULT_THREAD_COUNT = 200
DEFAULT_WAIT_SECONDS = 10.0
DEFAULT_POLL_INTERVAL = 1.0
MAX_TEAM_SIZE = 5
REPLAY_URL = "https://www.hero-wars.com/?replay_id={}"

SEND_FAILURE_HINTS = {
    "notFound": "target player was not found or cannot be challenged (check --user-id)",
}

_console = Console()


class ChallengeError(Exception):
    """Challenge send/accept failed (auth errors stay ``HWAuthError``)."""


@dataclass(frozen=True)
class ChallengeMessage:
    """One ``messageType=challenge`` entry in a personal-chat thread."""

    message_id: int | str
    sender_id: str
    challenge_type: str
    power: int
    ctime: int
    pending: bool

    @property
    def display_time(self) -> str:
        if not self.ctime:
            return "-"
        iso = datetime.fromtimestamp(self.ctime, tz=timezone.utc).isoformat()
        return format_timestamp_for_display(iso)[:16]


@dataclass(frozen=True)
class ChallengeSendResult:
    peer_id: str
    challenge_type: str
    heroes: tuple[int, ...]


@dataclass(frozen=True)
class ChallengeBattleResult:
    win: bool
    stars: int
    battle_id: str
    battle_type: str
    reward: list[Any]

    @property
    def replay_url(self) -> str | None:
        return REPLAY_URL.format(self.battle_id) if self.battle_id else None


@dataclass(frozen=True)
class ChallengeRunResult:
    """Per-account result used by ``multi challenge`` summaries."""

    peer_id: str
    challenge_type: str
    dry_run: bool
    battle: ChallengeBattleResult | None


def validate_user_id(value: Any) -> str:
    """Return a numeric peer userId as a string, or raise ``ChallengeError``."""
    text = str(value).strip() if value is not None else ""
    if not text or not (text.isascii() and text.isdigit()) or int(text) <= 0:
        raise ChallengeError(f"Invalid userId: {value!r} (numeric player id required).")
    return text


def validate_heroes(heroes: Any) -> tuple[int, ...]:
    """Validate a challenge lineup (1-5 unique positive ids; partial teams are OK)."""
    if not isinstance(heroes, (list, tuple)) or not heroes:
        raise ChallengeError("Challenge team is empty.")
    if len(heroes) > MAX_TEAM_SIZE:
        raise ChallengeError(
            f"Challenge team accepts at most {MAX_TEAM_SIZE} units (got {len(heroes)})."
        )
    normalized: list[int] = []
    for raw in heroes:
        if isinstance(raw, bool):
            raise ChallengeError(f"Invalid unit id: {raw!r}")
        try:
            unit_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ChallengeError(f"Invalid unit id: {raw!r}") from exc
        if unit_id <= 0:
            raise ChallengeError(f"Invalid unit id: {raw!r}")
        if unit_id in normalized:
            raise ChallengeError(f"Duplicate unit id in challenge team: {unit_id}")
        normalized.append(unit_id)
    return tuple(normalized)


def load_challenge_defaults(account: str) -> dict[str, Any]:
    """Read ``account_configs.challenge_defaults`` (``{user_id, type}``) for an account."""
    from hw_genie.core.session_manager import SessionManager

    data = SessionManager.load(account) or {}
    raw = data.get(CHALLENGE_DEFAULTS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def set_challenge_defaults(
    account: str,
    *,
    user_id: str | int | None = None,
    challenge_type: str | None = None,
) -> dict[str, Any]:
    """Merge a target user id / challenge type into ``challenge_defaults``."""
    from hw_genie.core.session_manager import SessionManager

    if user_id is None and challenge_type is None:
        raise ChallengeError("Nothing to set (pass --user-id and/or --type).")
    validated_user = validate_user_id(user_id) if user_id is not None else None
    if challenge_type is not None and challenge_type not in CHALLENGE_TYPES:
        raise ChallengeError(
            f"Unknown challenge type: {challenge_type!r} (choices: {', '.join(CHALLENGE_TYPES)})"
        )

    def _merge(existing: Any) -> dict[str, Any]:
        current = dict(existing) if isinstance(existing, dict) else {}
        if validated_user is not None:
            current["user_id"] = validated_user
        if challenge_type is not None:
            current["type"] = challenge_type
        return current

    return SessionManager.repo.update_config_merged(account, CHALLENGE_DEFAULTS_KEY, _merge)


def clear_challenge_defaults(account: str) -> dict[str, Any]:
    """Remove all stored challenge defaults for an account."""
    from hw_genie.core.session_manager import SessionManager

    return SessionManager.repo.update_config_merged(
        account, CHALLENGE_DEFAULTS_KEY, lambda _existing: {}
    )


def resolve_challenge_target(
    account: str,
    user_id: str | int | None = None,
    challenge_type: str | None = None,
) -> tuple[str, str]:
    """Resolve the target peer and type: CLI value > stored default > error / titan."""
    if challenge_type is not None and challenge_type not in CHALLENGE_TYPES:
        raise ChallengeError(
            f"Unknown challenge type: {challenge_type!r} (choices: {', '.join(CHALLENGE_TYPES)})"
        )
    if user_id is not None and str(user_id).strip() and challenge_type is not None:
        return validate_user_id(user_id), challenge_type
    defaults = load_challenge_defaults(account)
    raw = user_id if user_id is not None else defaults.get("user_id")
    if raw is None or str(raw).strip() == "":
        raise ChallengeError(
            "No challenge target: pass --user-id or set one with "
            f"`hw-genie challenge config -a {account} --user-id <player id>`."
        )
    target = validate_user_id(raw)
    resolved_type = challenge_type if challenge_type is not None else defaults.get("type")
    if resolved_type not in CHALLENGE_TYPES:
        resolved_type = "titan"
    return target, str(resolved_type)


def resolve_challenge_peer(account: str, user_id: str | int | None = None) -> str:
    """Resolve only the peer id (``--user-id`` > stored default > error)."""
    if user_id is not None:
        return validate_user_id(user_id)
    return resolve_challenge_target(account, None, None)[0]


def resolve_challenge_type(account: str, challenge_type: str | None = None) -> str:
    """Resolve only the challenge type (``--type`` > stored default > titan)."""
    if challenge_type is not None:
        if challenge_type not in CHALLENGE_TYPES:
            raise ChallengeError(
                f"Unknown challenge type: {challenge_type!r} (choices: {', '.join(CHALLENGE_TYPES)})"
            )
        return challenge_type
    stored = load_challenge_defaults(account).get("type")
    return str(stored) if stored in CHALLENGE_TYPES else "titan"


def _to_message_id(value: Any) -> int | str | None:
    """Normalize chat message ids / replay challengeIds (int when numeric)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # JSON numbers such as ``challengeId: 1.0`` must match message id 1.
        return int(value) if value.is_integer() else str(value)
    text = str(value).strip()
    if not text:
        return None
    return int(text) if text.isascii() and text.isdigit() else text


def message_sort_key(message_id: int | str, ctime: int) -> tuple[int, int, str]:
    """Numeric-aware ordering key: ctime, then numeric id, then string form."""
    numeric_id = -1
    if isinstance(message_id, int):
        numeric_id = message_id
    else:
        text = str(message_id).strip()
        if text.isascii() and text.isdigit():
            numeric_id = int(text)
    return (ctime, numeric_id, str(message_id))


def _response_of(res: HWResponse) -> dict[str, Any] | None:
    detail = res.detail
    if not isinstance(detail, dict):
        return None
    response = detail.get("response")
    return response if isinstance(response, dict) else None


def parse_challenge_messages(response: Any) -> list[ChallengeMessage]:
    """Parse ``chatGetAll`` response into challenge messages, oldest first.

    A challenge is ``pending`` until a ``replay`` message references its id via
    ``data.challengeId``.
    """
    if not isinstance(response, dict):
        return []
    chat = response.get("chat")
    if not isinstance(chat, list):
        return []

    consumed: set[int | str] = set()
    for item in chat:
        if not isinstance(item, dict) or item.get("messageType") != "replay":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        challenge_id = _to_message_id(data.get("challengeId"))
        if challenge_id is not None:
            consumed.add(challenge_id)

    challenges: list[ChallengeMessage] = []
    for item in chat:
        if not isinstance(item, dict) or item.get("messageType") != "challenge":
            continue
        message_id = _to_message_id(item.get("id"))
        if message_id is None:
            continue
        sender_id = str(item.get("userId") or "").strip()
        if not sender_id:
            # Never surface a challenge without a sender (cannot pick a direction).
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        challenges.append(
            ChallengeMessage(
                message_id=message_id,
                sender_id=sender_id,
                challenge_type=str(data.get("type") or "titan"),
                power=_safe_int(data.get("power")),
                ctime=_safe_int(item.get("ctime")),
                pending=message_id not in consumed,
            )
        )
    challenges.sort(key=lambda m: message_sort_key(m.message_id, m.ctime))
    return challenges


def pending_challenges(
    challenges: list[ChallengeMessage], sender_id: str | None = None
) -> list[ChallengeMessage]:
    """Pending challenges, optionally only those sent by ``sender_id``."""
    expected = str(sender_id) if sender_id is not None else None
    return [
        c
        for c in challenges
        if c.pending and (expected is None or c.sender_id == expected)
    ]


def sent_pending_challenges(
    challenges: list[ChallengeMessage], peer_id: str | int
) -> list[ChallengeMessage]:
    """Pending challenges sent by this account (sender_id is not the peer)."""
    peer = str(peer_id)
    return [c for c in pending_challenges(challenges) if c.sender_id != peer]


def select_challenge_team(team_detail: Any, challenge_type: str) -> tuple[int, ...]:
    """Pick the saved challenge team for ``challenge_type`` from teamGetAll."""
    field = CHALLENGE_TEAM_FIELDS.get(challenge_type)
    if field is None:
        raise ChallengeError(
            f"Unknown challenge type: {challenge_type!r} (choices: {', '.join(CHALLENGE_TYPES)})"
        )
    if not isinstance(team_detail, dict):
        raise ChallengeError("Unexpected teamGetAll response format.")
    teams = team_detail.get("response", team_detail)
    if not isinstance(teams, dict):
        raise ChallengeError("Unexpected teamGetAll response format.")
    raw = teams.get(field)
    if raw is None:
        raise ChallengeError(
            f"teamGetAll has no '{field}' team for this account. "
            "Set the challenge team in-game or pass --heroes explicitly."
        )
    return validate_heroes(raw)


def select_challenge_target_team(team_detail: Any, challenge_type: str) -> tuple[int, ...]:
    """Pick the challenge **target** lineup (the team the opponent fights).

    The observed game flow sends a single unit (``chatSendChallenge.heroes``
    becomes the ``defenders`` snapshot in the battle), while the accepting side
    brings its full saved team. Default to the first saved unit.
    """
    return select_challenge_team(team_detail, challenge_type)[:1]


def parse_send_result(detail: Any) -> None:
    """Raise ``ChallengeError`` unless ``chatSendChallenge`` returned result=true."""
    response = detail.get("response") if isinstance(detail, dict) else None
    if not isinstance(response, dict):
        raise ChallengeError("Unexpected chatSendChallenge response format.")
    if response.get("result") is True:
        return
    reason = str(response.get("reason") or "unknown")
    hint = SEND_FAILURE_HINTS.get(reason)
    message = f"chatSendChallenge was rejected: reason={reason}"
    if hint:
        message += f" ({hint})"
    raise ChallengeError(message)


def parse_accept_result(detail: Any) -> ChallengeBattleResult:
    """Extract the battle outcome from a ``chatAcceptChallenge`` response."""
    response = detail.get("response") if isinstance(detail, dict) else None
    if isinstance(response, dict) and response.get("result") is False:
        reason = str(response.get("reason") or "unknown")
        raise ChallengeError(f"chatAcceptChallenge was rejected: reason={reason}")
    battle = response.get("battle") if isinstance(response, dict) else None
    if not isinstance(battle, dict):
        raise ChallengeError("Unexpected chatAcceptChallenge response format (no battle).")
    result = battle.get("result") if isinstance(battle.get("result"), dict) else {}
    return ChallengeBattleResult(
        win=bool(result.get("win")),
        stars=_safe_int(result.get("stars")),
        battle_id=str(battle.get("id") or ""),
        battle_type=str(battle.get("type") or ""),
        reward=battle.get("reward") if isinstance(battle.get("reward"), list) else [],
    )


def fetch_thread(client: HWClient, peer_id: str, count: int = DEFAULT_THREAD_COUNT) -> dict[str, Any]:
    """Fetch one personal-chat thread (chatGetAll with chatType=personal)."""
    res = client.chat_get_all(chat_type="personal", count=count, user_id=peer_id)
    if res.status != ResponseStatus.SUCCESS:
        raise ChallengeError(
            f"Failed to fetch personal chat with {peer_id}: "
            f"{res.status.value} ({res.error_name or '-'})"
        )
    response = _response_of(res)
    if response is None or "chat" not in response:
        raise ChallengeError("Unexpected chatGetAll response format.")
    return response


def _fetch_team_detail(client: HWClient) -> dict[str, Any]:
    res = client.team_get_all()
    if res.status != ResponseStatus.SUCCESS:
        raise ChallengeError(
            f"Failed to fetch saved teams: {res.status.value} ({res.error_name or '-'})"
        )
    detail = res.detail if isinstance(res.detail, dict) else None
    if detail is None:
        raise ChallengeError("Unexpected teamGetAll response format.")
    return detail


def fetch_player_id(client: HWClient) -> str:
    """Return the account's player id via userGetInfo (not the x-auth-user-id header)."""
    res = client.user_get_info()
    if res.status != ResponseStatus.SUCCESS:
        raise ChallengeError(
            f"Failed to fetch player info: {res.status.value} ({res.error_name or '-'})"
        )
    response = _response_of(res)
    player_id = str((response or {}).get("id") or "").strip()
    if not player_id:
        raise ChallengeError("userGetInfo did not return a player id.")
    return player_id


def run_challenge_send(
    client: HWClient,
    account_alias: str,
    user_id: str | int | None = None,
    challenge_type: str | None = None,
    heroes: list[int] | None = None,
    *,
    dry_run: bool = False,
) -> ChallengeSendResult:
    """Send a friendly challenge to ``user_id`` with the target (defending) team.

    ``user_id`` / ``challenge_type`` fall back to the account's
    ``challenge_defaults`` (see ``challenge config``). ``heroes`` (or the saved
    challenge team's first unit) becomes the team the opponent fights; the
    accepting side uses its full saved team.
    """
    peer_id, resolved_type = resolve_challenge_target(account_alias, user_id, challenge_type)
    team = validate_heroes(heroes) if heroes is not None else select_challenge_target_team(
        _fetch_team_detail(client), resolved_type
    )
    print(
        f"{Emojis.START}[{account_alias}] Sending {resolved_type} challenge to "
        f"{peer_id} with target team ({len(team)} unit(s)): {', '.join(map(str, team))}"
    )
    if dry_run:
        print(f"{Emojis.INFO} Dry run: chatSendChallenge was not called.")
        return ChallengeSendResult(peer_id=peer_id, challenge_type=resolved_type, heroes=team)

    res = client.chat_send_challenge(
        user_id=peer_id,
        heroes=list(team),
        challenge_type=resolved_type,
    )
    if res.status != ResponseStatus.SUCCESS:
        raise ChallengeError(
            f"chatSendChallenge failed: {res.status.value} ({res.error_name or '-'})"
        )
    parse_send_result(res.detail)
    print(f"{Emojis.SUCCESS}Challenge sent to {peer_id}.")
    return ChallengeSendResult(peer_id=peer_id, challenge_type=resolved_type, heroes=team)


def run_challenge_accept(
    client: HWClient,
    account_alias: str,
    user_id: str | int | None = None,
    *,
    message_id: int | str | None = None,
    heroes: list[int] | None = None,
    count: int = DEFAULT_THREAD_COUNT,
    sent: bool = False,
    dry_run: bool = False,
) -> ChallengeBattleResult | None:
    """Battle a pending challenge (``chatAcceptChallenge``).

    By default the newest pending challenge **received from** ``user_id`` is
    used (recipient flow). With ``sent=True`` the newest pending challenge
    **sent by this account to** ``user_id`` is used (sender-side flow: the
    sender fights its own challenge with the full team, while the challenge's
    1-unit target team is the defender).

    The challenge message is discovered in the personal thread: its ``id`` is
    the ``messageId`` required by ``chatAcceptChallenge``. ``userId`` is always
    the peer (the other participant in the personal thread). When ``user_id``
    is omitted, the account's ``challenge_defaults.user_id`` is used.
    """
    peer_id = resolve_challenge_peer(account_alias, user_id)
    effective_count = max(1, min(DEFAULT_THREAD_COUNT, count))
    challenges = parse_challenge_messages(fetch_thread(client, peer_id, effective_count))

    if message_id is not None:
        wanted = _to_message_id(message_id)
        target = next((c for c in challenges if c.message_id == wanted), None)
        if target is None:
            raise ChallengeError(
                f"Challenge messageId {message_id!r} was not found in the last "
                f"{effective_count} messages with {peer_id} (the API returns at most "
                "200 and a personal thread may expose only unread messages). "
                "Check `challenge list`."
            )
        if target.pending is False:
            print(
                f"{Emojis.WARNING}Challenge #{target.message_id}: a replay already "
                "exists for this messageId; continuing because --message-id was explicit."
            )
    else:
        if sent:
            pending = sent_pending_challenges(challenges, peer_id)
            if not pending:
                raise ChallengeError(
                    f"No pending challenge sent to {peer_id}. Send one with "
                    "`hw-genie challenge send --user-id <peer>` first."
                )
        else:
            pending = pending_challenges(challenges, peer_id)
            if not pending:
                raise ChallengeError(
                    f"No pending challenge from {peer_id}. Ask them to send one, or "
                    "use --sent to battle a challenge you sent."
                )
        target = pending[-1]

    if sent and target.sender_id == peer_id:
        raise ChallengeError(
            f"--sent was given but messageId {target.message_id} was received from "
            f"{peer_id}; omit --sent for a received challenge."
        )

    direction = "sent by this account" if target.sender_id != peer_id else f"from {peer_id}"
    challenge_type = target.challenge_type or "titan"
    team = validate_heroes(heroes) if heroes is not None else select_challenge_team(
        _fetch_team_detail(client), challenge_type
    )
    print(
        f"{Emojis.STEP}[{account_alias}] Battling {challenge_type} challenge "
        f"#{target.message_id} ({direction}) with {len(team)} unit(s): "
        f"{', '.join(map(str, team))}"
    )
    if dry_run:
        print(f"{Emojis.INFO} Dry run: chatAcceptChallenge was not called.")
        return None

    res = client.chat_accept_challenge(
        user_id=peer_id,
        message_id=target.message_id,
        heroes=list(team),
    )
    if res.status != ResponseStatus.SUCCESS:
        raise ChallengeError(
            f"chatAcceptChallenge failed: {res.status.value} ({res.error_name or '-'})"
        )
    result = parse_accept_result(res.detail)
    marker = Emojis.VICTORY if result.win else Emojis.ERROR
    outcome = "WIN" if result.win else "LOSS"
    print(f"{marker}Result: {outcome} ({result.stars} star(s))")
    if result.replay_url:
        print(f"  replay: {result.replay_url}")
    return result


def print_challenge_table(
    challenges: list[ChallengeMessage], account_alias: str, peer_id: str
) -> None:
    """Display challenge messages (newest first) with their messageIds."""
    table = Table(
        title=(
            f"Challenges with {peer_id} - {account_alias}  {len(challenges)} found "
            f"[{display_timezone_name()}]"
        ),
        box=box.ROUNDED,
        show_lines=False,
        header_style="bold cyan",
    )
    table.add_column("Message ID", style="bold", no_wrap=True)
    table.add_column("Direction", no_wrap=True)
    table.add_column("Type", no_wrap=True)
    table.add_column("Power", justify="right", no_wrap=True)
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    for challenge in sorted(
        challenges,
        key=lambda c: message_sort_key(c.message_id, c.ctime),
        reverse=True,
    ):
        direction = "Received" if challenge.sender_id == peer_id else "Sent"
        status = "pending" if challenge.pending else "fought"
        table.add_row(
            str(challenge.message_id),
            direction,
            challenge.challenge_type,
            str(challenge.power),
            challenge.display_time,
            status,
        )
    _console.print(table)


def run_challenge_list(
    client: HWClient,
    account_alias: str,
    user_id: str | int | None = None,
    *,
    count: int = DEFAULT_THREAD_COUNT,
    json_output: bool = False,
) -> list[ChallengeMessage]:
    """List challenge messages and their messageIds for one personal thread."""
    peer_id = resolve_challenge_peer(account_alias, user_id)
    challenges = parse_challenge_messages(fetch_thread(client, peer_id, count))
    if json_output:
        print(json.dumps([asdict(c) for c in challenges], ensure_ascii=False, indent=1))
        return challenges
    if not challenges:
        print(f"{Emojis.INFO}[{account_alias}] No challenge messages found with {peer_id}.")
        return challenges
    print_challenge_table(challenges, account_alias, peer_id)
    received = pending_challenges(challenges, peer_id)
    sent_pending = sent_pending_challenges(challenges, peer_id)
    if received:
        print(
            f"{Emojis.INFO}Newest pending messageId from {peer_id}: "
            f"{received[-1].message_id} (`challenge accept` picks this automatically)."
        )
    elif sent_pending:
        print(
            f"{Emojis.INFO}Newest pending challenge you sent: "
            f"{sent_pending[-1].message_id} "
            "(`challenge accept --sent` battles it as the sender)."
        )
    else:
        print(f"{Emojis.INFO}No pending challenge with {peer_id}.")
    return challenges


def _find_new_pending(
    client: HWClient,
    sender_id: str,
    baseline: set[int | str],
    count: int,
) -> ChallengeMessage | None:
    challenges = parse_challenge_messages(fetch_thread(client, sender_id, count))
    fresh = [
        c
        for c in pending_challenges(challenges, sender_id)
        if c.message_id not in baseline
    ]
    return fresh[-1] if fresh else None


def run_challenge_battle(
    sender_client: HWClient,
    sender_alias: str,
    accept_client: HWClient,
    accept_alias: str,
    *,
    challenge_type: str | None = None,
    heroes: list[int] | None = None,
    accept_heroes: list[int] | None = None,
    dry_run: bool = False,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    count: int = DEFAULT_THREAD_COUNT,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ChallengeBattleResult | None:
    """Send a challenge from one registered account and accept it on another.

    Two-account mode: the accepter is the challenge recipient and uses its full
    saved team against the sender's target team. Use
    :func:`run_challenge_self_battle` for the sender-side flow (same account).
    ``challenge_type=None`` falls back to the sender's stored default.
    """
    resolved_type = resolve_challenge_type(sender_alias, challenge_type)
    if sender_alias.strip().lower() == accept_alias.strip().lower():
        raise ChallengeError(
            "Sender and accept account must differ; use the sender-side battle "
            "(--user-id without --accept-account) to battle your own challenge."
        )
    sender_id = fetch_player_id(sender_client)
    accept_id = fetch_player_id(accept_client)
    if sender_id == accept_id:
        raise ChallengeError("Sender and accept account resolve to the same player id.")
    # Resolve/validate both teams before sending: a bad --accept-heroes or a
    # missing saved team must not leave a sent-but-unbattled challenge behind.
    sender_team = validate_heroes(heroes) if heroes is not None else select_challenge_target_team(
        _fetch_team_detail(sender_client), resolved_type
    )
    accept_team = validate_heroes(accept_heroes) if accept_heroes is not None else select_challenge_team(
        _fetch_team_detail(accept_client), resolved_type
    )
    if dry_run:
        print(
            f"{Emojis.INFO}Dry run: {sender_alias} ({sender_id}) would send a "
            f"{resolved_type} challenge to {accept_alias} ({accept_id}) "
            f"with target team {list(sender_team)}, and {accept_alias} would battle it "
            f"with {list(accept_team)}."
        )
        return None

    baseline = {
        c.message_id
        for c in parse_challenge_messages(fetch_thread(accept_client, sender_id, count))
    }
    run_challenge_send(
        sender_client,
        sender_alias,
        accept_id,
        resolved_type,
        list(sender_team),
        dry_run=False,
    )

    attempts = max(1, int(wait_seconds / max(poll_interval, 0.1)) + 1)
    target: ChallengeMessage | None = None
    for attempt in range(attempts):
        target = _find_new_pending(accept_client, sender_id, baseline, count)
        if target is not None:
            break
        if attempt < attempts - 1:
            sleep_fn(poll_interval)
    if target is None:
        raise ChallengeError(
            f"Challenge was sent but no new pending challenge from {sender_id} appeared "
            f"in {accept_alias}'s thread within {wait_seconds:.0f}s. Retry with "
            f"`hw-genie challenge accept -a {accept_alias} --user-id {sender_id}`."
        )
    print(f"{Emojis.STEP}Found messageId {target.message_id} in {accept_alias}'s thread.")
    return run_challenge_accept(
        accept_client,
        accept_alias,
        sender_id,
        message_id=target.message_id,
        heroes=list(accept_team),
        count=count,
        dry_run=False,
    )


def run_challenge_self_battle(
    client: HWClient,
    account_alias: str,
    user_id: str | int | None = None,
    *,
    challenge_type: str | None = None,
    heroes: list[int] | None = None,
    accept_heroes: list[int] | None = None,
    dry_run: bool = False,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    count: int = DEFAULT_THREAD_COUNT,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ChallengeBattleResult | None:
    """Doc-style flow: send a 1-unit challenge and battle it on the same account.

    Live-verified: the sender calls ``chatAcceptChallenge`` with the same peer
    ``userId``, its full saved team as ``attackers``, and the challenge's
    target team as ``defenders``. ``battle.userId`` / ``battle.typeId`` are
    both the sender because both teams belong to the sender. ``user_id`` /
    ``challenge_type`` fall back to ``challenge_defaults``.
    """
    peer_id, resolved_type = resolve_challenge_target(account_alias, user_id, challenge_type)
    # Resolve/validate both teams before sending so an invalid override or a
    # missing saved team cannot leave a sent-but-unbattled challenge behind.
    team_detail = None
    if heroes is None or accept_heroes is None:
        team_detail = _fetch_team_detail(client)
    target_team = validate_heroes(heroes) if heroes is not None else select_challenge_target_team(
        team_detail, resolved_type
    )
    accept_team = validate_heroes(accept_heroes) if accept_heroes is not None else select_challenge_team(
        team_detail, resolved_type
    )
    if dry_run:
        print(
            f"{Emojis.INFO}Dry run: {account_alias} would send a {resolved_type} "
            f"challenge to {peer_id} with target team {list(target_team)} and battle "
            f"it on the same account with {list(accept_team)}."
        )
        return None

    baseline = {
        c.message_id
        for c in parse_challenge_messages(fetch_thread(client, peer_id, count))
    }
    run_challenge_send(
        client,
        account_alias,
        peer_id,
        resolved_type,
        list(target_team),
        dry_run=False,
    )

    attempts = max(1, int(wait_seconds / max(poll_interval, 0.1)) + 1)
    target: ChallengeMessage | None = None
    for attempt in range(attempts):
        challenges = parse_challenge_messages(fetch_thread(client, peer_id, count))
        fresh = [
            c
            for c in sent_pending_challenges(challenges, peer_id)
            if c.message_id not in baseline
        ]
        if fresh:
            target = fresh[-1]
            break
        if attempt < attempts - 1:
            sleep_fn(poll_interval)
    if target is None:
        raise ChallengeError(
            f"Challenge was sent but no new pending challenge appeared in "
            f"{account_alias}'s own thread with {peer_id} within {wait_seconds:.0f}s. "
            f"Retry with `hw-genie challenge accept -a {account_alias} "
            f"--user-id {peer_id} --sent`."
        )
    print(f"{Emojis.STEP}Found messageId {target.message_id} in {account_alias}'s own thread.")
    return run_challenge_accept(
        client,
        account_alias,
        peer_id,
        message_id=target.message_id,
        heroes=list(accept_team),
        count=count,
        sent=True,
        dry_run=False,
    )


__all__ = [
    "CHALLENGE_DEFAULTS_KEY",
    "CHALLENGE_TEAM_FIELDS",
    "CHALLENGE_TYPES",
    "ChallengeBattleResult",
    "ChallengeError",
    "ChallengeMessage",
    "ChallengeRunResult",
    "ChallengeSendResult",
    "clear_challenge_defaults",
    "fetch_player_id",
    "load_challenge_defaults",
    "parse_accept_result",
    "parse_challenge_messages",
    "parse_send_result",
    "pending_challenges",
    "resolve_challenge_peer",
    "resolve_challenge_target",
    "resolve_challenge_type",
    "run_challenge_accept",
    "run_challenge_battle",
    "run_challenge_list",
    "run_challenge_self_battle",
    "run_challenge_send",
    "select_challenge_target_team",
    "select_challenge_team",
    "sent_pending_challenges",
    "set_challenge_defaults",
    "validate_heroes",
    "validate_user_id",
]
