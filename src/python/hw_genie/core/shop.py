"""shopGetAll の取得・在庫（slots）パースの共通ヘルパー。

``quests.py``（デイリー自動実行時の shopBuy reward/cost 動的解決）と
``hero_shopping.py``（デフォルト購入対象の抽出）はどちらも同じ契約
（``detail["response"][<shop_id>]["slots"]``）で shopGetAll を扱っていたため、
取得・パース・購入済み判定をここに一元化し、契約のドリフト（``.get("slots")``
と ``.get("slots", {})`` の違い、bought フィルタの有無等）を防ぐ。
"""

import logging
from typing import Any

from hw_genie.core.client import ApiAction, HWAuthError, HWClient

logger = logging.getLogger(__name__)


class ShopReadError(Exception):
    """shopGetAll の取得・パースが失敗したことを表す（認証エラーは HWAuthError のまま）。"""

    def __init__(self, message: str, error_name: str | None = None):
        super().__init__(message)
        self.error_name = error_name


class ShopNotFoundError(ShopReadError):
    """在庫の取得自体は成功したが、指定 shop が在庫に存在しない。"""


def fetch_shops(client: HWClient) -> dict[str, Any]:
    """shopGetAll を呼び、``{shop_id(str): shop}`` の辞書を返す。

    Raises:
        HWAuthError: 認証エラー（握りつぶさず再送出）
        ShopReadError: 通信・API エラー、または予期しないレスポンス形式
    """
    try:
        res = client.call({"calls": [{"name": ApiAction.SHOP_GET_ALL, "args": {}, "ident": "shopGetAll"}]})
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ShopReadError(f"shopGetAll failed: {exc}") from exc
    if not res.is_success:
        raise ShopReadError(
            f"shopGetAll failed ({res.error_name or res.status.value})",
            error_name=res.error_name,
        )
    detail = res.detail if isinstance(res.detail, dict) else {}
    shops = detail.get("response")
    if not isinstance(shops, dict):
        raise ShopReadError("shopGetAll returned unexpected response (missing 'response' dict)")
    return shops


def get_shop_slots(shops: dict[str, Any], shop_id: Any) -> dict[str, Any]:
    """指定 shop の ``slots`` 辞書を取り出す。

    ``slots`` キーの欠落（``.get("slots")`` と ``.get("slots", {})`` の
    混在でドリフトしていた部分）は空辞書として吸収する。

    Raises:
        ShopNotFoundError: 指定 shop が在庫に存在しない。
    """
    shop = shops.get(str(shop_id))
    if not isinstance(shop, dict):
        raise ShopNotFoundError(f"shop {shop_id} not found in shopGetAll response")
    slots = shop.get("slots")
    return slots if isinstance(slots, dict) else {}


def is_bought(item: Any) -> bool:
    """slot が購入済み（``bought``）かどうかを型安全に判定する。

    ``bought`` は bool（``true``/``false``）の他、int/str（``1``/``"1"``）が
    混在し得るため、hero_shopping と同じ正規化を共通化する。
    """
    return isinstance(item, dict) and item.get("bought") in (True, 1, "1")


class ShopInventory:
    """shopGetAll の実行結果をキャッシュするラッパー。

    同一実行（複数クエスト・複数ステップ）内で shopGetAll を最大 1 回に
    抑える。取得失敗（認証以外）もキャッシュし、以降は再試行せず
    フォールバックに任せる。
    """

    def __init__(self) -> None:
        self._shops: dict[str, Any] | None = None
        self._error: ShopReadError | None = None

    @property
    def error(self) -> ShopReadError | None:
        """直近の取得失敗理由（なければ None）。"""
        return self._error

    @property
    def has_failed(self) -> bool:
        """在庫取得に失敗済みかどうか。"""
        return self._error is not None

    def load(self, client: HWClient) -> dict[str, Any] | None:
        """在庫を取得して返す（初回のみ実際に取得）。

        Returns:
            ``{shop_id: shop}``。取得失敗時は ``None``（呼び出し側で
            既定値フォールバック）。認証エラー（HWAuthError）は再送出。
        """
        if self._shops is not None or self._error is not None:
            return self._shops
        try:
            self._shops = fetch_shops(client)
        except ShopReadError as exc:
            self._error = exc
            logger.warning("shopGetAll failed; falling back to defaults: %s", exc)
        return self._shops
