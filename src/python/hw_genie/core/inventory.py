"""inventoryGet の取得・パースと consumable 消費（consumableUse*）の共通ヘルパー。

``inventoryGet`` のレスポンスは ``response.<カテゴリ> = {libId: 個数}`` という
構造（カテゴリ: ``consumable`` / ``gear`` / ``scroll`` / ``fragmentScroll`` /
``fragmentGear`` 等）。消費系（``consumableUseLootBox`` 等）のレスポンスは
``response.<消費数> = {カテゴリ: {libId: 報酬量}}`` のため、カテゴリ別の
合計報酬量に集計して返す。

認証エラー（HWAuthError）以外の失敗は ``InventoryReadError`` で表し、
消費自体の失敗は呼び出し側で判定できるよう ``ConsumableUseResult`` に
status / error_name を載せる。
"""

from dataclasses import dataclass, field
from typing import Any

from hw_genie.core.client import ApiAction, HWAuthError, HWClient, ResponseStatus, _safe_int


class InventoryReadError(Exception):
    """inventoryGet の取得・パースが失敗したことを表す（認証エラーは HWAuthError のまま）。"""

    def __init__(self, message: str, error_name: str | None = None):
        super().__init__(message)
        self.error_name = error_name


@dataclass
class InventorySnapshot:
    """inventoryGet のパース結果。カテゴリ別に ``{libId: 個数}`` を保持する。"""

    categories: dict[str, dict[int, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def consumable(self) -> dict[int, Any]:
        """``response.consumable``（未取得・未存在なら空辞書）。"""
        return self.categories.get("consumable", {})


@dataclass
class ConsumableUseResult:
    """consumable 消費 1 件の実行結果。"""

    lib_id: int
    name: str | None = None
    stock: int = 0  # 直近ラウンドの在庫（dry-run では消費予定数）
    consumed: int = 0  # 実際に消費した数（dry-run では 0）
    rewards: dict[str, int] = field(default_factory=dict)  # カテゴリ → 合計報酬量
    status: ResponseStatus = ResponseStatus.ERROR
    error_name: str | None = None


def fetch_inventory(client: HWClient) -> InventorySnapshot:
    """inventoryGet を呼び、カテゴリ別の在庫を返す。

    Raises:
        HWAuthError: 認証エラー（握りつぶさず再送出）
        InventoryReadError: 通信・API エラー、または予期しないレスポンス形式
    """
    try:
        res = client.call(
            {"calls": [{"name": ApiAction.INVENTORY_GET, "args": {}, "ident": "inventory"}]}
        )
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InventoryReadError(f"inventoryGet failed: {exc}") from exc
    if not res.is_success:
        raise InventoryReadError(
            f"inventoryGet failed ({res.error_name or res.status.value})",
            error_name=res.error_name,
        )
    detail = res.detail if isinstance(res.detail, dict) else {}
    response = detail.get("response")
    if not isinstance(response, dict):
        raise InventoryReadError("inventoryGet returned unexpected response (missing 'response' dict)")
    categories: dict[str, dict[int, Any]] = {}
    for category, entries in response.items():
        if isinstance(entries, dict):
            categories[category] = {_safe_int(lib_id): qty for lib_id, qty in entries.items()}
    return InventorySnapshot(categories=categories, raw=response)


def parse_use_rewards(detail: Any, amount: int) -> dict[str, int]:
    """consumableUse* のレスポンスから報酬をカテゴリ別合計に集計する。

    形式: ``response[str(amount)] = {カテゴリ: {libId: 報酬量}}``。値が
    辞書でない（``"stamina": 120`` のようなスカラー）場合はその値をそのまま
    合計に加える。

    サーバーが消費数を amount 未満にキャップして応答する場合（``str(amount)``
    キーが存在しない）に備え、キー不一致時は応答内の数値キー（消費数キー）を
    全走査して報酬を合算する（偽の「報酬なし成功」を避ける）。
    """
    rewards: dict[str, int] = {}
    response = detail.get("response") if isinstance(detail, dict) else None
    if not isinstance(response, dict):
        return rewards

    payload = response.get(str(amount))
    if isinstance(payload, dict):
        _merge_rewards(rewards, payload)
        return rewards

    # キー不一致（キャップ応答）: 数値解釈できるキーの dict 値だけを対象に合算
    for key, value in response.items():
        if isinstance(value, dict) and _safe_int(key) > 0:
            _merge_rewards(rewards, value)
    return rewards


def _merge_rewards(rewards: dict[str, int], payload: dict[str, Any]) -> None:
    """response 内の 1 報酬ブロックをカテゴリ別合計へ足し込む。"""
    for category, value in payload.items():
        if isinstance(value, dict):
            total = sum(_safe_int(qty) for qty in value.values())
        else:
            total = _safe_int(value)
        if total > 0:
            rewards[category] = rewards.get(category, 0) + total


def _actual_consumed(detail: Any, requested: int) -> int:
    """レスポンスから実際の消費数を導出する。

    通常は ``response[str(requested)]`` が存在するため requested を返す。
    サーバーが消費数を amount 未満にキャップして応答した場合は、応答内の
    数値キー（実際の消費数）の合計を返す。判別不能な場合は requested に
    フォールバックする（報酬なし成功の偽装より過大計上の方が安全）。
    """
    response = detail.get("response") if isinstance(detail, dict) else None
    if not isinstance(response, dict):
        return requested
    if str(requested) in response:
        return requested
    keys = [key for key, value in response.items() if isinstance(value, dict) and _safe_int(key) > 0]
    if keys:
        return sum(_safe_int(key) for key in keys)
    return requested


def use_consumable(
    client: HWClient,
    lib_id: int,
    amount: int,
    method: str,
    player_reward_choice_index: int | None = None,
) -> ConsumableUseResult:
    """consumableUse* を 1 回呼び、その実行結果を返す。

    Args:
        client: 認証済み HWClient。
        lib_id: 消費対象の consumable libId。
        amount: 消費する数量（在庫全量を渡す想定）。
        method: 消費 RPC メソッド名（``consumableUseLootBox`` 等）。
        player_reward_choice_index: 選択式報酬ボックスの報酬選択インデックス。
            ``playerRewardChoiceIndex`` として args に含める（``None`` なら含めない）。

    Raises:
        HWAuthError: 認証エラー（握りつぶさず再送出）
    """
    args: dict[str, Any] = {"libId": lib_id, "amount": amount}
    if player_reward_choice_index is not None:
        args["playerRewardChoiceIndex"] = player_reward_choice_index
    try:
        res = client.call(
            {
                "calls": [
                    {
                        "name": method,
                        "args": args,
                        "context": {"actionTs": 0},
                        "ident": "consumable_use",
                    }
                ]
            }
        )
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        return ConsumableUseResult(
            lib_id=lib_id, stock=amount, status=ResponseStatus.UNEXPECTED, error_name=str(exc)
        )
    if not res.is_success:
        return ConsumableUseResult(
            lib_id=lib_id, stock=amount, status=ResponseStatus.ERROR, error_name=res.error_name
        )
    rewards = parse_use_rewards(res.detail, amount)
    return ConsumableUseResult(
        lib_id=lib_id,
        stock=amount,
        consumed=_actual_consumed(res.detail, amount),
        rewards=rewards,
        status=ResponseStatus.SUCCESS,
    )
