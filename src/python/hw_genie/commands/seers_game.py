"""Seer's Game の自動周回。

1 ゲーム = ``eventPicker_startGame`` → ``eventPicker_playRound`` ×4 →
``eventPicker_finishGame``。引いたカードの報酬はランダムで何が出ても
気にしないため、毎ラウンド ``pick``（デフォルト 2）を引き続ける。
``Seer's Coin`` がなくなり ``startGame`` がエラーを返したら正常終了する。

``stashClient`` は Spec で不要と明記のため送らない。``quests`` 同梱は
無視する（live evidence なし、docs-only）。

認証エラー（HWAuthError）は握りつぶさず再送出する（上位で共通処理）。
"""

import sys
from dataclasses import dataclass, field
from typing import Any

from hw_genie.core.client import ApiAction, Emojis, HWAuthError, HWClient, ResponseStatus

# 1 ゲーム開始に消費する Seer's Coin。
GAME_COST = 25
# 1 ゲームあたりのカード引き回数。
ROUNDS_PER_GAME = 4
# Seer's Coin の coin ID（inventoryGet の coin 枠）。
SEERS_COIN_ID: int = 2824001094
# 枯渇（正常終了）として扱う startGame 失敗の error_name 集合。
# live 検証済み (2026-10-09): コイン不足・無料分なしの開始失敗は NotEnough。
CLEAN_DEPLETION_ERRORS: frozenset = frozenset({"NotEnough"})


def is_clean_depletion(error_name: str | None) -> bool:
    """枯渇による正常停止かどうかを判定する（0 ゲームでも失敗にしない）。"""
    return error_name in CLEAN_DEPLETION_ERRORS


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


def validate_seers_args(
    pick: int = 2, max_games: int | None = None
) -> tuple[int, int | None]:
    """``--pick`` / ``--max-games`` を API 呼び出し前に検証する。

    不正値は ``stderr`` へのメッセージ + ``SystemExit(2)``（argparse の
    ``parser.error`` と同等）で落とす。単体 ``seers-game`` ハンドラと
    ``multi seers-game`` 分岐の両方から呼ぶ共有バリデータ。
    """
    if isinstance(pick, bool) or not isinstance(pick, int) or pick < 1:
        print(
            f"hw-genie seers-game: error: --pick must be >= 1 (got {pick!r})",
            file=sys.stderr,
        )
        sys.exit(2)
    if max_games is not None and (
        isinstance(max_games, bool) or not isinstance(max_games, int) or max_games < 1
    ):
        print(
            f"hw-genie seers-game: error: --max-games must be >= 1 (got {max_games!r})",
            file=sys.stderr,
        )
        sys.exit(2)
    return pick, max_games


def _merge_rewards(accum: dict[str, int], collected: Any) -> None:
    """``collected_rewards`` のスナップショット 1 件を ``accum`` に加算する。

    注意: ``collected_rewards`` はゲーム内の累積スナップショット（各
    ``playRound`` 応答がそれまでの合計を返す）なので、中間の全スナップ
    ショットを合算してはならない（例: 各 play が ``12:20``、finish が
    ``12:40`` の場合、合計は 40 が正しく 20+20+…+40 ではない）。
    呼び出し側は 1 ゲームにつき finish 時の最終スナップショット 1 件だけ
    をマージすること（失敗時は最後の成功スナップショット 1 件）。
    ``collected_rewards`` は ``{"consumable": {"12": 20}, ...}`` 形式、
    または空リスト（未収集時）のいずれかで現れる。
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


def _raise_if_unexpected(res: Any, action: str) -> None:
    """``UNEXPECTED``（通信・パース失敗等）はコイン枯渇と混同せず大声で失敗させる。"""
    if getattr(res, "status", None) == ResponseStatus.UNEXPECTED:
        error_name = getattr(res, "error_name", None) or "unexpected"
        raise SeersGameReadError(f"{action} failed ({error_name})")


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

    実装は :func:`_start_new_game` に委譲する（``run_seers_game`` の新規
    ゲームループと同一プロトコル実装を共有する）。
    ``pick`` は毎ラウンドの live ``event.size`` に対して検証する。
    いずれかの呼び出しが ERROR を返した場合は ``games_failed=1`` として
    早期リターンする（例外は投げない。HWAuthError を除く）。
    ただし ``UNEXPECTED``（通信・パース失敗等）はコイン枯渇とみなさず
    ``SeersGameReadError`` を送出し、``pick`` 範囲外は ``ValueError`` を送出する。
    HWAuthError は握りつぶさず再送出する。
    """
    result = SeersGameResult()
    _start_new_game(client, pick, result)
    return result


def _play_remaining_and_finish(
    client: HWClient,
    pick: int,
    plays_remaining: int,
    size: int,
    result: SeersGameResult,
    pending: Any = None,
) -> bool:
    """中断ゲームの残りラウンド + finish を実行する。成功時 True。

    報酬は累積スナップショットなので中間マージせず、成功時は finish 時の
    最終スナップショット 1 件だけをマージする。ERROR による失敗時は
    最後の成功スナップショット（``pending`` / 各 play の最新 1 件）を
    1 件だけマージして部分報酬とする。

    ERROR は ``games_failed`` 加算 + ``last_error`` 記録して False を返す。
    ``UNEXPECTED`` は ``SeersGameReadError`` を送出する（正常停止扱いしない）。
    ``ValueError``（pick 範囲外）・HWAuthError はそのまま送出する。
    """
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
        _raise_if_unexpected(res, "eventPicker_playRound")
        if not res.is_success:
            result.games_failed += 1
            result.last_error = res.error_name or "unknown"
            if pending is not None:
                _merge_rewards(result.rewards, pending)
            return False
        event = _event_from_detail(res.detail)
        size = _safe_int(event.get("size"))
        if isinstance(event.get("state"), str):
            result.last_state = event.get("state")
        pending = event.get("collected_rewards")

    res = client.call(
        {"calls": [{"name": ApiAction.EVENT_PICKER_FINISH_GAME, "args": {}, "ident": "body"}]}
    )
    client.sleep()
    _raise_if_unexpected(res, "eventPicker_finishGame")
    if not res.is_success:
        result.games_failed += 1
        result.last_error = res.error_name or "unknown"
        if pending is not None:
            _merge_rewards(result.rewards, pending)
        return False
    event = _event_from_detail(res.detail)
    if isinstance(event.get("state"), str):
        result.last_state = event.get("state")
    _merge_rewards(result.rewards, event.get("collected_rewards"))
    result.games_played += 1
    return True


def _start_new_game(
    client: HWClient,
    pick: int,
    result: SeersGameResult,
    count_start_error: bool = True,
) -> bool:
    """``startGame`` → 残り 4 play + finish の新規 1 ゲームを実行する。

    ``run_single_game`` と ``run_seers_game`` の新規ゲームループで共有する
    単一プロトコル実装。``startGame`` の ERROR は ``count_start_error`` が
    True（``run_single_game``）なら ``games_failed`` 加算 + ``last_error``
    記録で False を返す。False（``run_seers_game`` の周回ループ）の場合は
    コイン枯渇等の正常停止として ``last_error`` のみ記録し ``games_failed``
    は加算せず False を返す（呼び出し側が break する）。成功時は
    ``_play_remaining_and_finish`` に委譲し、その戻り値を返す。
    ``UNEXPECTED`` / ``ValueError`` / HWAuthError はそのまま送出する。
    """
    res = client.call(
        {"calls": [{"name": ApiAction.EVENT_PICKER_START_GAME, "args": {}, "ident": "body"}]}
    )
    client.sleep()
    _raise_if_unexpected(res, "eventPicker_startGame")
    if not res.is_success:
        if count_start_error:
            result.games_failed += 1
        result.last_error = res.error_name or "unknown"
        return False
    event = _event_from_detail(res.detail)
    size = _safe_int(event.get("size"))
    _check_pick(pick, size)
    if isinstance(event.get("state"), str):
        result.last_state = event.get("state")
    pending = event.get("collected_rewards")
    return _play_remaining_and_finish(client, pick, ROUNDS_PER_GAME, size, result, pending)


def _coin_balance_from_inventory(detail: Any) -> int | None:
    """``inventoryGet`` の detail から Seer's Coin 残高を取り出す（失敗時は None）。

    ``inventoryGet`` の応答形状は2通りある: トップレベル
    ``response.coin``（他コマンドの live evidence にある形状）と、
    ネストした ``response.inventory.coin``（旧形状）。どちらも試す。
    ``str`` / ``int`` の coin ID キー両対応（JSON はキーが文字列化される）。
    残高0のアイテムは API がキーを省略するため、``coin`` 枠自体が存在して
    ID キーが無い場合は 0 とみなす（live 確認: 枯渇後はキーが消滅する）。
    ``coin`` 枠自体が無い場合のみ None（不明）を返す。
    """
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
            return 0
        inventory = response.get("inventory")
        if isinstance(inventory, dict):
            coins = inventory.get("coin")
            if isinstance(coins, dict):
                for key in (str(SEERS_COIN_ID), SEERS_COIN_ID):
                    if key in coins:
                        return _safe_int(coins[key])
                return 0
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
      以後は ``startGame`` の ERROR を ``last_error`` に記録し break する。
      ``NotEnough`` のみ正常停止とし、それ以外は ``games_failed`` 加算の
      失敗扱いとする。
      無料/有料の区別はしない（枯渇まで回す）。

    Raises:
        SeersGameReadError: ``UNEXPECTED``（通信・パース失敗等。コイン枯渇とは
            区別し正常停止扱いにしない）。
        ValueError: ``pick`` が live ``event.size`` の範囲外。
        HWAuthError: 認証エラー（握りつぶさず再送出）。
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
        # 残り = (ROUNDS_PER_GAME + 1) - round（round=1→4、round=2→3、…、
        # round=5（全 4 play 済み）→0 で finish のみ）。
        # getState の collected_rewards は累積スナップショットなのでここでは
        # マージせず、失敗時の部分報酬フォールバックとして渡すだけ。
        round_no = _safe_int(event.get("round"), 1)
        remaining = max(0, (ROUNDS_PER_GAME + 1) - round_no)
        size = _safe_int(event.get("size"))
        print(
            f"{Emojis.RECOVERY}{prefix}Resuming interrupted game "
            f"(round {round_no}, {remaining} round(s) left)...",
            flush=True,
        )
        if isinstance(event.get("state"), str):
            result.last_state = event.get("state")
        pending = event.get("collected_rewards")
        if not _play_remaining_and_finish(client, pick, remaining, size, result, pending):
            return result

    while max_games is None or result.games_played < max_games:
        print(f"{Emojis.STEP}{prefix}Starting game #{result.games_played + 1}...", flush=True)
        failed_before = result.games_failed
        ok = _start_new_game(client, pick, result, count_start_error=False)
        if not ok:
            if result.games_failed == failed_before:
                # startGame の ERROR: NotEnough のみ正常停止、それ以外は失敗計上。
                if not is_clean_depletion(result.last_error):
                    result.games_failed += 1
                print(
                    f"  Result: {Emojis.ERROR}Cannot start ({result.last_error}) - stopping.",
                    flush=True,
                )
            break
        print(f"  Result: {Emojis.SUCCESS}Game #{result.games_played} finished.", flush=True)

    print(f"\n{Emojis.FINISH}{prefix}--- Seer's Game Results Summary ---", flush=True)
    print(f"  {Emojis.SUCCESS}Played: {result.games_played} game(s)", flush=True)
    if result.games_failed:
        print(f"  {Emojis.ERROR}Failed: {result.games_failed} game(s)", flush=True)
    if result.rewards:
        rewards_str = ", ".join(
            f"{key} x{amount}" for key, amount in sorted(result.rewards.items())
        )
        print(f"  {Emojis.INFO}Rewards: {rewards_str}", flush=True)
    if result.last_error:
        print(f"  {Emojis.INFO}Stopped: {result.last_error}", flush=True)
    return result
