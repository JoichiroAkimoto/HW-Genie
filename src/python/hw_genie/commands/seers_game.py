"""Seer's Game の自動周回。

1 ゲーム = ``eventPicker_startGame`` → ``eventPicker_playRound`` ×4 →
``eventPicker_finishGame``。引いたカードの報酬はランダムで何が出ても
気にしないため、毎ラウンド ``pick``（デフォルト 2）を引き続ける。
``Seer's Coin`` がなくなり ``startGame`` がエラーを返したら正常終了する。

``stashClient`` は Spec で不要と明記のため送らない。``quests`` 同梱は
無視する（live evidence なし、docs-only）。

認証エラー（HWAuthError）は握りつぶさず再送出する（上位で共通処理）。
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from hw_genie.core.client import ApiAction, Emojis, HWAuthError, HWClient

logger = logging.getLogger(__name__)

# 1 ゲーム開始に消費する Seer's Coin。
GAME_COST = 25
# 1 ゲームあたりのカード引き回数。
ROUNDS_PER_GAME = 4
# Seer's Coin の coin ID（inventoryGet の coin 枠）。
SEERS_COIN_ID: int = 2824001094


class SeersGameReadError(Exception):
    """eventPicker_getState の取得・パースが失敗したことを表す（認証エラーは HWAuthError のまま）。"""


@dataclass
class SeersGameResult:
    """Seer's Game の実行結果サマリ。"""

    games_played: int = 0
    games_failed: int = 0
    last_state: str | None = None
    last_error: str | None = None
    rewards: dict[str, int] = field(default_factory=dict)


def _safe_int(value: Any, default: int = 0) -> int:
    """int 安全変換（失敗時は default）。bool は数値化しない。"""
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _merge_rewards(accum: dict[str, int], collected: Any) -> None:
    """``collected_rewards`` を ``accum`` に加算する（flat key ``"{category}:{id}"``）。

    ``collected_rewards`` は ``{"consumable": {"12": 20}, "coin": {"24": 500}}``
    形式、または空リスト（未収集時）のいずれかで現れる。
    """
    if not isinstance(collected, dict):
        return
    for category, items in collected.items():
        if not isinstance(items, dict):
            continue
        for cid, amount in items.items():
            key = f"{category}:{cid}"
            accum[key] = accum.get(key, 0) + _safe_int(amount)


def _event_from_detail(detail: Any) -> dict[str, Any]:
    """単一コールの ``res.detail`` から ``event`` dict を取り出す。"""
    if not isinstance(detail, dict):
        raise SeersGameReadError("eventPicker returned unexpected response (detail is not a dict)")
    response = detail.get("response")
    if not isinstance(response, dict):
        raise SeersGameReadError("eventPicker returned unexpected response (missing 'response' dict)")
    event = response.get("event")
    if not isinstance(event, dict):
        raise SeersGameReadError("eventPicker returned unexpected response (missing 'event' dict)")
    return event


def _check_pick(pick: int, size: int) -> None:
    """``pick`` が live の ``event.size`` 範囲内か検証する（範囲外は ValueError）。"""
    if size and not 1 <= pick <= size:
        raise ValueError(f"pick must be between 1 and {size}, but got {pick}")


def fetch_seers_state(client: HWClient) -> dict[str, Any]:
    """``eventPicker_getState`` を呼び、現在の ``event`` を返す。

    Raises:
        HWAuthError: 認証エラー（握りつぶさず再送出）
        SeersGameReadError: 通信・API エラー、または予期しないレスポンス形式
    """
    try:
        res = client.call(
            {"calls": [{"name": ApiAction.EVENT_PICKER_GET_STATE, "args": {}, "ident": "body"}]}
        )
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SeersGameReadError(f"eventPicker_getState failed: {exc}") from exc
    if not res.is_success:
        status = getattr(res.status, "value", res.status)
        raise SeersGameReadError(
            f"eventPicker_getState failed ({res.error_name or status})"
        )
    return _event_from_detail(res.detail)


def run_single_game(client: HWClient, pick: int = 2) -> SeersGameResult:
    """1 ゲーム（start → play ×4 → finish）を実行する。

    ``pick`` は毎ラウンドの live ``event.size`` に対して検証する。
    いずれかの呼び出しが ERROR を返した場合は ``games_failed=1`` として
    早期リターンする（例外は投げない。HWAuthError を除く）。
    """
    result = SeersGameResult()

    res = client.call(
        {"calls": [{"name": ApiAction.EVENT_PICKER_START_GAME, "args": {}, "ident": "body"}]}
    )
    client.sleep()
    if not res.is_success:
        result.games_failed = 1
        result.last_error = res.error_name or "unknown"
        return result
    event = _event_from_detail(res.detail)
    size = _safe_int(event.get("size"))
    _check_pick(pick, size)
    result.last_state = event.get("state") if isinstance(event.get("state"), str) else None
    _merge_rewards(result.rewards, event.get("collected_rewards"))

    for _ in range(ROUNDS_PER_GAME):
        _check_pick(pick, size)
        res = client.call(
            {
                "calls": [
                    {"name": ApiAction.EVENT_PICKER_PLAY_ROUND, "args": {"num": pick}, "ident": "body"}
                ]
            }
        )
        client.sleep()
        if not res.is_success:
            result.games_failed = 1
            result.last_error = res.error_name or "unknown"
            return result
        event = _event_from_detail(res.detail)
        size = _safe_int(event.get("size"))
        result.last_state = event.get("state") if isinstance(event.get("state"), str) else None
        _merge_rewards(result.rewards, event.get("collected_rewards"))

    res = client.call(
        {"calls": [{"name": ApiAction.EVENT_PICKER_FINISH_GAME, "args": {}, "ident": "body"}]}
    )
    client.sleep()
    if not res.is_success:
        result.games_failed = 1
        result.last_error = res.error_name or "unknown"
        return result
    event = _event_from_detail(res.detail)
    result.last_state = event.get("state") if isinstance(event.get("state"), str) else None
    _merge_rewards(result.rewards, event.get("collected_rewards"))

    result.games_played = 1
    return result


def _play_remaining_and_finish(
    client: HWClient,
    pick: int,
    plays_remaining: int,
    size: int,
    result: SeersGameResult,
) -> bool:
    """中断ゲームの残りラウンド + finish を実行する。成功時 True。"""
    for _ in range(plays_remaining):
        _check_pick(pick, size)
        res = client.call(
            {
                "calls": [
                    {"name": ApiAction.EVENT_PICKER_PLAY_ROUND, "args": {"num": pick}, "ident": "body"}
                ]
            }
        )
        client.sleep()
        if not res.is_success:
            result.games_failed += 1
            result.last_error = res.error_name or "unknown"
            return False
        event = _event_from_detail(res.detail)
        size = _safe_int(event.get("size"))
        if isinstance(event.get("state"), str):
            result.last_state = event.get("state")
        _merge_rewards(result.rewards, event.get("collected_rewards"))

    res = client.call(
        {"calls": [{"name": ApiAction.EVENT_PICKER_FINISH_GAME, "args": {}, "ident": "body"}]}
    )
    client.sleep()
    if not res.is_success:
        result.games_failed += 1
        result.last_error = res.error_name or "unknown"
        return False
    event = _event_from_detail(res.detail)
    if isinstance(event.get("state"), str):
        result.last_state = event.get("state")
    _merge_rewards(result.rewards, event.get("collected_rewards"))
    result.games_played += 1
    return True


def _coin_balance_from_inventory(detail: Any) -> int | None:
    """``inventoryGet`` の detail から Seer's Coin 残高を取り出す（失敗時は None）。"""
    try:
        if not isinstance(detail, dict):
            return None
        response = detail.get("response")
        if not isinstance(response, dict):
            return None
        coins = response.get("coin")
        if isinstance(coins, dict):
            for key in (str(SEERS_COIN_ID), SEERS_COIN_ID):
                if key in coins:
                    return _safe_int(coins[key])
        inventory = response.get("inventory")
        if isinstance(inventory, dict):
            coins = inventory.get("coin", inventory)
            if isinstance(coins, dict):
                for key in (str(SEERS_COIN_ID), SEERS_COIN_ID):
                    if key in coins:
                        return _safe_int(coins[key])
    except Exception:  # noqa: BLE001
        return None
    return None


def run_seers_game(
    client: HWClient,
    pick: int = 2,
    max_games: int | None = None,
    dry_run: bool = False,
    account_alias: str | None = None,
) -> SeersGameResult:
    """Seer's Game を ``max_games`` または Coin 枯渇まで周回する。

    - ``dry_run``: ``getState`` + ``inventoryGet``（読み取りのみ）を送り、
      表示だけして何も進行させない。
    - 通常時: 初回 ``getState`` で ``state == "active"`` の中断ゲームがあれば
      新規開始せず ``playRound`` から再開して ``finishGame`` で閉じる。
      以後は ``startGame`` の ERROR（コイン不足・回数上限など全種）を
      正常終了として ``last_error`` に記録し break する。
      無料/有料の区別はしない（枯渇まで回す）。
    """
    prefix = f"[{account_alias}] " if account_alias else ""
    result = SeersGameResult()

    if dry_run:
        print(f"\n{Emojis.STEP}{prefix}--- Seer's Game state (dry-run) ---", flush=True)
        try:
            event = fetch_seers_state(client)
        except SeersGameReadError as exc:
            print(f"{Emojis.ERROR}{prefix}Error: Failed to fetch Seer's Game state. {exc}", flush=True)
            result.last_error = str(exc)
            return result
        client.sleep()
        print(
            f"{Emojis.INFO}{prefix}State: {event.get('state')} "
            f"(round {event.get('round')}, size {event.get('size')}).",
            flush=True,
        )
        result.last_state = event.get("state") if isinstance(event.get("state"), str) else None
        try:
            inv_res = client.call(
                {"calls": [{"name": ApiAction.INVENTORY_GET, "args": {}, "ident": "body"}]}
            )
        except HWAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"{Emojis.WARNING}{prefix}Failed to fetch inventory: {exc}", flush=True)
        else:
            client.sleep()
            if inv_res.is_success:
                balance = _coin_balance_from_inventory(inv_res.detail)
                if balance is not None:
                    print(
                        f"{Emojis.INFO}{prefix}Seer's Coin balance: {balance} "
                        f"(~{balance // GAME_COST} game(s)).",
                        flush=True,
                    )
        print(f"{Emojis.INFO}{prefix}Dry-run: no game started.", flush=True)
        return result

    print(f"\n{Emojis.STEP}{prefix}--- Fetching Seer's Game state ---", flush=True)
    try:
        event = fetch_seers_state(client)
    except SeersGameReadError as exc:
        print(f"{Emojis.ERROR}{prefix}Error: Failed to fetch Seer's Game state. {exc}", flush=True)
        result.last_error = str(exc)
        result.games_failed += 1
        return result
    client.sleep()

    if event.get("state") == "active":
        # 中断ゲームを新規開始せず再開する。round=1 開始で 4 play のため、
        # 残り = (ROUNDS_PER_GAME + 1) - round（round=1→4、round=2→3、…）。
        round_no = _safe_int(event.get("round"), 1)
        remaining = max(1, (ROUNDS_PER_GAME + 1) - round_no)
        size = _safe_int(event.get("size"))
        print(
            f"{Emojis.RECOVERY}{prefix}Resuming interrupted game "
            f"(round {round_no}, {remaining} round(s) left)...",
            flush=True,
        )
        if isinstance(event.get("state"), str):
            result.last_state = event.get("state")
        _merge_rewards(result.rewards, event.get("collected_rewards"))
        if not _play_remaining_and_finish(client, pick, remaining, size, result):
            return result

    while max_games is None or result.games_played < max_games:
        print(f"{Emojis.STEP}{prefix}Starting game #{result.games_played + 1}...", flush=True)
        res = client.call(
            {"calls": [{"name": ApiAction.EVENT_PICKER_START_GAME, "args": {}, "ident": "body"}]}
        )
        client.sleep()
        if not res.is_success:
            error_name = res.error_name or "unknown"
            print(f"  Result: {Emojis.ERROR}Cannot start ({error_name}) - stopping.", flush=True)
            result.last_error = error_name
            break
        event = _event_from_detail(res.detail)
        size = _safe_int(event.get("size"))
        _check_pick(pick, size)
        if isinstance(event.get("state"), str):
            result.last_state = event.get("state")
        _merge_rewards(result.rewards, event.get("collected_rewards"))
        if not _play_remaining_and_finish(client, pick, ROUNDS_PER_GAME, size, result):
            break
        print(f"  Result: {Emojis.SUCCESS}Game #{result.games_played} finished.", flush=True)

    print(f"\n{Emojis.FINISH}{prefix}--- Seer's Game Results Summary ---", flush=True)
    print(f"  {Emojis.SUCCESS}Played: {result.games_played} game(s)", flush=True)
    if result.last_error:
        print(f"  {Emojis.INFO}Stopped: {result.last_error}", flush=True)
    return result
