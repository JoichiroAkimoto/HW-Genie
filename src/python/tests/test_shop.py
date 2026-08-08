"""``core/shop.py``（shopGetAll の共通ヘルパー）のテスト。

- HWClient は実インスタンスを使い、``call`` のみモックする。
- ネットワークには依存しない。
"""

from unittest.mock import MagicMock

import pytest

from hw_genie.core.client import HWAuthError, HWClient, HWResponse, ResponseStatus
from hw_genie.core.shop import (
    ShopInventory,
    ShopNotFoundError,
    ShopReadError,
    fetch_shops,
    get_shop_slots,
    is_bought,
)


def _make_client(responses) -> HWClient:
    client = HWClient(headers={"x-auth-token": "test"})
    client.call = MagicMock(side_effect=responses)
    return client


def _ok_response(detail: dict) -> HWResponse:
    return HWResponse(status=ResponseStatus.SUCCESS, detail=detail)


# --- fetch_shops ---


def test_fetch_shops_success():
    """shopGetAll 成功時は ``{shop_id: shop}`` を返す。"""
    detail = {"response": {"13": {"slots": {"18": {"reward": {}, "cost": {}}}}}}
    client = _make_client([_ok_response(detail)])
    assert fetch_shops(client) == detail["response"]
    assert client.call.call_count == 1


def test_fetch_shops_error_response():
    """API エラー応答は error_name 付きの ShopReadError になる。"""
    res = HWResponse(status=ResponseStatus.ERROR, error_name="NotEnough", detail={})
    client = _make_client([res])
    with pytest.raises(ShopReadError) as exc_info:
        fetch_shops(client)
    assert exc_info.value.error_name == "NotEnough"


def test_fetch_shops_auth_error_reraises():
    """認証エラー（HWAuthError）は握りつぶさず re-raise される。"""
    client = _make_client([HWAuthError("auth failed")])
    with pytest.raises(HWAuthError):
        fetch_shops(client)


def test_fetch_shops_unexpected_response():
    """response キー欠落など予期しない形式は ShopReadError になる。"""
    client = _make_client([_ok_response({"response": None})])
    with pytest.raises(ShopReadError):
        fetch_shops(client)

    client2 = _make_client([_ok_response({})])
    with pytest.raises(ShopReadError):
        fetch_shops(client2)

    client3 = _make_client([_ok_response("not a dict")])
    with pytest.raises(ShopReadError):
        fetch_shops(client3)


def test_fetch_shops_call_exception():
    """call() の例外（通信エラー等）は ShopReadError になる（認証は例外）。"""
    client = _make_client([OSError("network down")])
    with pytest.raises(ShopReadError):
        fetch_shops(client)


# --- get_shop_slots ---


def test_get_shop_slots_found():
    """指定 shop の slots 辞書を取り出せる。"""
    shops = {"13": {"slots": {"1": {"reward": {"gold": 1}}}}}
    assert get_shop_slots(shops, 13) == {"1": {"reward": {"gold": 1}}}
    assert get_shop_slots(shops, "13") == {"1": {"reward": {"gold": 1}}}


def test_get_shop_slots_shop_missing():
    """shop が存在しない場合は ShopNotFoundError（不在と取得失敗を区別する）。"""
    shops = {"10": {"slots": {}}}
    with pytest.raises(ShopNotFoundError):
        get_shop_slots(shops, 13)
    with pytest.raises(ShopNotFoundError):
        get_shop_slots({}, 13)


def test_get_shop_slots_missing_slots_key():
    """slots キーが無い場合は空 dict として吸収する（.get("slots", {}) 相当）。"""
    assert get_shop_slots({"13": {}}, 13) == {}
    assert get_shop_slots({"13": {"slots": None}}, 13) == {}


# --- is_bought ---


@pytest.mark.parametrize("value", [True, 1, "1"])
def test_is_bought_true_variants(value):
    """購入済みの表現（bool / int / str）をすべて判定できる。"""
    assert is_bought({"bought": value, "reward": {}})


@pytest.mark.parametrize("value", [False, 0, "0", None])
def test_is_bought_false_variants(value):
    """未購入・不明の表現は買い物対象として判定されない。"""
    assert not is_bought({"bought": value, "reward": {}})


def test_is_bought_missing_key_or_non_dict():
    """bought キー欠落・非 dict の slot は未購入扱い。"""
    assert not is_bought({"reward": {}})
    assert not is_bought("weird")


# --- ShopInventory (キャッシュ) ---


def test_shop_inventory_load_caches_success():
    """取得成功は初回の shopGetAll をキャッシュし、2 回目は呼び出さない。"""
    detail = {"response": {"13": {"slots": {}}}}
    client = _make_client([_ok_response(detail)])
    cache = ShopInventory()

    assert cache.load(client) == detail["response"]
    assert cache.load(client) == detail["response"]
    assert client.call.call_count == 1
    assert cache.has_failed is False
    assert cache.error is None


def test_shop_inventory_load_caches_failure(caplog):
    """取得失敗（認証以外）は None でキャッシュされ、再試行しない。"""
    res = HWResponse(status=ResponseStatus.UNEXPECTED, error_name="network_or_parse_error", detail={})
    client = _make_client([res])
    cache = ShopInventory()

    assert cache.load(client) is None
    assert cache.load(client) is None
    assert client.call.call_count == 1
    assert cache.has_failed is True
    assert cache.error is not None
    assert "shopGetAll failed" in caplog.text


def test_shop_inventory_auth_error_reraises():
    """認証エラーはキャッシュせず再送出する。"""
    client = _make_client([HWAuthError("auth failed")])
    cache = ShopInventory()
    with pytest.raises(HWAuthError):
        cache.load(client)