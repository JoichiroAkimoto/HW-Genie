"""エレメントギフト一括 MAX 化 / リセット (commands/element_gift, runner gift routines)."""

import json
from unittest.mock import MagicMock

import pytest

from hw_genie.commands.element_gift import (
    DEFAULT_MAX_POWER,
    GIFT_FULL_COST,
    GIFT_MAX_LEVEL,
    GiftReadError,
    GiftRunResult,
    fetch_state,
    gift_drop_payload,
    gift_up_payload,
    parse_coin_balance,
    parse_heroes,
    plan_reset,
    plan_up,
    print_status,
    run_gift_reset,
    run_gift_up,
)
from hw_genie.core.client import ApiAction, HWAuthError, ResponseStatus


def _hero(hero_id, power, gift_level, spent=None, level=130, star=6):
    data = {
        "id": hero_id,
        "power": power,
        "level": level,
        "star": star,
        "titanGiftLevel": gift_level,
        "type": "hero",
    }
    if spent is not None:
        data["titanCoinsSpent"] = {"consumable": {"24": spent}}
    else:
        data["titanCoinsSpent"] = {}
    return str(hero_id), data


def _heroes_payload(*specs):
    """`heroGetAll` の `response` 相当（{heroId: hero} 辞書）を組み立てる。"""
    return dict(_hero(*spec) for spec in specs)


def _res(detail, status=ResponseStatus.SUCCESS, error_name=None):
    res = MagicMock()
    res.detail = detail
    res.status = status
    res.error_name = error_name
    res.is_success = status == ResponseStatus.SUCCESS
    return res


def _state_detail(heroes, balance):
    """`fetch_state` の 1 リクエスト（heroes + inventory）応答を模す。"""
    return {
        "heroes": {"result": {"response": heroes}},
        "inventory": {"result": {"response": {"consumable": {"24": balance}}}},
    }


def _up_success(levels):
    """レベルアップ バッチの全 ident 成功応答。"""
    return {
        "body" if i == 0 else f"body_{i}": {"result": {"response": None, "quests": []}}
        for i in range(levels)
    }


def _up_partial(levels, succeeded, error_name="NotEnough"):
    detail = {}
    for i in range(levels):
        ident = "body" if i == 0 else f"body_{i}"
        if i < succeeded:
            detail[ident] = {"result": {"response": None, "quests": []}}
        else:
            detail[ident] = {"error": {"name": error_name}}
    return detail


def _drop_detail(refund):
    """単一コールの drop 応答（`HWClient.call` は `results[0].result` を detail にする）。"""
    return {"response": {"consumable": {"24": refund}}, "quests": []}


def _write_calls(mock_call, action):
    """実際に送られた操作 payload（読み取りを除く）を返す。"""
    payloads = [call[0][0] for call in mock_call.call_args_list]
    return [p for p in payloads if p["calls"] and p["calls"][0]["name"] == action]


# --- parse ---


def test_parse_heroes_keeps_only_heroes_and_sorts_by_power():
    payload = _heroes_payload((1, 100, 0), (2, 500, 30))
    payload["6008"] = {"id": 6008, "power": 99999, "type": "pet"}
    payload["bad"] = "not a dict"

    heroes = parse_heroes(payload)

    assert [h.hero_id for h in heroes] == [2, 1]
    assert heroes[0].is_max and not heroes[1].is_max


def test_parse_heroes_tolerates_missing_spend_and_junk():
    heroes = parse_heroes(_heroes_payload((7, 10, 0)))
    assert heroes[0].coins_spent == 0
    assert heroes[0].remaining_cost == GIFT_FULL_COST
    assert parse_heroes(None) == []


def test_parse_heroes_treats_non_dict_consumable_as_zero():
    """`titanCoinsSpent: {"consumable": null}` 等の形でも落ちない（消費済み 0 扱い）。"""
    payload = _heroes_payload((7, 10, 0))
    payload["7"]["titanCoinsSpent"] = {"consumable": None}
    heroes = parse_heroes(payload)
    assert heroes[0].coins_spent == 0
    assert heroes[0].remaining_cost == GIFT_FULL_COST

    # 辞書でない他の形（list / 文字列）も同じく 0
    for junk in ({"consumable": []}, {"consumable": "24"}, [], "null", 5):
        payload["7"]["titanCoinsSpent"] = junk
        assert parse_heroes(payload)[0].coins_spent == 0


def test_parse_coin_balance():
    assert parse_coin_balance({"consumable": {"24": 3705543}}) == 3705543
    assert parse_coin_balance({"consumable": {}}) == 0
    assert parse_coin_balance(None) == 0


# --- plan_up ---


def test_plan_up_strongest_first_and_stops_when_coins_run_out():
    heroes = parse_heroes(
        _heroes_payload(
            (1, 200000, 0),  # 65,150
            (2, 150000, 0),  # 65,150
            (3, 120000, 30),  # MAX: 除外
            (4, 90000, 0),  # 65,150
        )
    )

    actions, blocked, beyond = plan_up(heroes, balance=140000)

    assert [a.hero.hero_id for a in actions] == [1, 2]
    assert blocked == 1  # hero 4 は残コイン不足で打ち切り以降まとめて対象外
    assert beyond == 0


def test_plan_up_uses_remaining_cost_for_partial_heroes():
    heroes = parse_heroes(_heroes_payload((1, 100000, 12, GIFT_FULL_COST - 1000)))

    actions, blocked, _ = plan_up(heroes, balance=1000)

    assert [(a.hero.hero_id, a.levels, a.cost) for a in actions] == [(1, 18, 1000)]
    assert blocked == 0


def test_plan_up_respects_limit_and_reports_remaining():
    heroes = parse_heroes(_heroes_payload((1, 300, 0), (2, 200, 0), (3, 100, 0)))

    actions, blocked, beyond = plan_up(heroes, balance=10_000_000, limit=1)

    assert [a.hero.hero_id for a in actions] == [1]
    assert (blocked, beyond) == (0, 2)


def test_plan_up_empty_when_everything_maxed_or_broke():
    heroes = parse_heroes(_heroes_payload((1, 100, GIFT_MAX_LEVEL, GIFT_FULL_COST)))
    assert plan_up(heroes, balance=999999) == ([], 0, 0)
    unmaxed = parse_heroes(_heroes_payload((2, 100, 0)))
    assert plan_up(unmaxed, balance=GIFT_FULL_COST - 1) == ([], 1, 0)


# --- plan_reset ---


def test_plan_reset_targets_gifted_low_power_heroes_only():
    heroes = parse_heroes(
        _heroes_payload(
            (1, 150000, 30, GIFT_FULL_COST),  # power 超過: 対象外
            (2, DEFAULT_MAX_POWER, 30, GIFT_FULL_COST),  # 境界は「以下」で含む
            (3, 50000, 7, 9000),  # gift あり・低 power: 対象
            (4, 50000, 0),  # gift なし: 対象外
        )
    )

    targets = plan_reset(heroes)

    assert [h.hero_id for h in targets] == [2, 3]
    assert sum(h.refundable for h in targets) == GIFT_FULL_COST + 9000


def test_plan_reset_honours_max_power_and_limit():
    heroes = parse_heroes(_heroes_payload((1, 1000, 30, 500), (2, 900, 30, 400)))

    assert [h.hero_id for h in plan_reset(heroes, max_power=950)] == [2]
    assert [h.hero_id for h in plan_reset(heroes, limit=1)] == [1]


# --- payloads ---


def test_gift_up_payload_batches_levels_with_client_ident_format():
    payload = gift_up_payload(61, 3)

    assert len(payload["calls"]) == 3
    assert [c["ident"] for c in payload["calls"]] == ["body", "body_1", "body_2"]
    assert all(c["name"] == ApiAction.HERO_TITAN_GIFT_LEVEL_UP for c in payload["calls"])
    assert all(c["args"] == {"heroId": 61} for c in payload["calls"])


def test_gift_drop_payload_single_call():
    payload = gift_drop_payload(61)

    assert len(payload["calls"]) == 1
    assert payload["calls"][0]["name"] == ApiAction.HERO_TITAN_GIFT_DROP
    assert payload["calls"][0]["args"] == {"heroId": 61}


# --- fetch_state ---


def test_fetch_state_uses_one_batched_request(mock_client):
    client, mock_call = mock_client
    mock_call.return_value = _res(
        _state_detail(_heroes_payload((1, 100, 0)), 3_700_000)
    )

    heroes, balance = fetch_state(client)

    assert balance == 3_700_000
    assert [h.hero_id for h in heroes] == [1]
    assert mock_call.call_count == 1
    names = [c["name"] for c in mock_call.call_args[0][0]["calls"]]
    assert names == [ApiAction.HERO_GET_ALL, ApiAction.INVENTORY_GET]


def test_fetch_state_raises_on_empty_or_api_error(mock_client):
    client, mock_call = mock_client
    mock_call.return_value = _res(_state_detail({}, 0))
    with pytest.raises(GiftReadError):
        fetch_state(client)

    mock_call.return_value = _res({}, status=ResponseStatus.ERROR, error_name="InvalidSession")
    with pytest.raises(GiftReadError):
        fetch_state(client)


def test_fetch_state_propagates_auth_error(mock_client):
    client, mock_call = mock_client
    mock_call.side_effect = HWAuthError("expired")
    with pytest.raises(HWAuthError):
        fetch_state(client)


# --- run_gift_up ---


def _up_client(mock_call, states, up_details):
    """状態読取 → 各体のレベルアップ → 最終状態読取 の順で応答を返す。

    states: fetch_state 用の (heroes, balance) タプル列（先頭=実行前、末尾=実行後）。
    up_details: レベルアップ 1 体分の応答 detail 列。
    """
    state_responses = [_res(_state_detail(*s)) for s in states]
    responses = [_res(d) for d in up_details]
    queue = [state_responses[0], *responses, *state_responses[1:]]
    mock_call.side_effect = lambda payload: queue.pop(0)


def test_run_gift_up_maxes_heroes_strongest_first_and_reports_measured_spend(
    mock_client, mock_sleep
):
    client, mock_call = mock_client
    heroes = _heroes_payload((1, 200000, 0), (2, 150000, 0))
    # 初回: 残高 140,000（2 体分は 130,300 → 2 体とも MAX 可能）
    # 最終: 実残高 9,700 → 実消費 130,300
    _up_client(
        mock_call,
        states=[(heroes, 140000), (heroes, 9700)],
        up_details=[_up_success(GIFT_MAX_LEVEL), _up_success(GIFT_MAX_LEVEL)],
    )

    result = run_gift_up(client, account_alias="Alice", confirm=True)

    assert result.mode == "up"
    assert result.changed == 2
    assert result.levels == GIFT_MAX_LEVEL * 2
    assert result.coins == 140000 - 9700  # 見積でなく実測の消費額
    assert result.balance == 9700
    assert not result.is_failure
    sent = _write_calls(mock_call, ApiAction.HERO_TITAN_GIFT_LEVEL_UP)
    assert [len(p["calls"]) for p in sent] == [GIFT_MAX_LEVEL, GIFT_MAX_LEVEL]
    assert [p["calls"][0]["args"]["heroId"] for p in sent] == [1, 2]


def test_run_gift_up_stops_on_partial_batch(mock_client, mock_sleep):
    client, mock_call = mock_client
    heroes = _heroes_payload((1, 200000, 0), (2, 150000, 0))
    _up_client(
        mock_call,
        states=[(heroes, 100000), (_heroes_payload((1, 205555, 12, 20000)), 80000)],
        up_details=[_up_partial(GIFT_MAX_LEVEL, succeeded=12)],
    )

    result = run_gift_up(client, account_alias="Alice", confirm=True)

    assert result.changed == 0
    assert result.failed == 1
    assert result.levels == 12
    assert result.is_failure
    assert "NotEnough" in (result.error or "")
    # hero 2 へは送らない（同じ残高では失敗が連鎖するため）
    assert len(_write_calls(mock_call, ApiAction.HERO_TITAN_GIFT_LEVEL_UP)) == 1


def test_run_gift_up_dry_run_sends_no_gift_operation(mock_client):
    client, mock_call = mock_client
    mock_call.return_value = _res(_state_detail(_heroes_payload((1, 200000, 0)), 500000))

    result = run_gift_up(client, account_alias="Alice", dry_run=True)

    assert result.dry_run is True
    assert result.changed == 0
    assert _write_calls(mock_call, ApiAction.HERO_TITAN_GIFT_LEVEL_UP) == []


def test_run_gift_up_without_confirm_does_not_write(mock_client):
    client, mock_call = mock_client
    mock_call.return_value = _res(_state_detail(_heroes_payload((1, 200000, 0)), 500000))

    result = run_gift_up(client, account_alias="Alice", confirm=False)

    assert result.error == "skipped by user"
    assert result.is_failure
    assert _write_calls(mock_call, ApiAction.HERO_TITAN_GIFT_LEVEL_UP) == []


def test_run_gift_up_nothing_to_run_is_not_a_failure(mock_client):
    client, mock_call = mock_client
    maxed = _heroes_payload((1, 200000, GIFT_MAX_LEVEL, GIFT_FULL_COST))
    mock_call.return_value = _res(_state_detail(maxed, 500000))

    result = run_gift_up(client, account_alias="Alice", confirm=True)

    assert not result.is_failure
    assert result.changed == 0
    assert mock_call.call_count == 1  # 状態読取のみ（実行も最終再読もしない）


def test_run_gift_up_blocked_by_coins_reports_without_failure(mock_client):
    client, mock_call = mock_client
    heroes = _heroes_payload((1, 200000, 0), (2, 150000, 0))
    mock_call.return_value = _res(_state_detail(heroes, GIFT_FULL_COST - 1))

    result = run_gift_up(client, account_alias="Alice", confirm=True)

    assert result.blocked == 2
    assert result.changed == 0
    assert not result.is_failure
    assert mock_call.call_count == 1


def test_run_gift_up_state_read_failure_is_reported(mock_client):
    client, mock_call = mock_client
    mock_call.return_value = _res({}, status=ResponseStatus.ERROR, error_name="boom")

    result = run_gift_up(client, account_alias="Alice", confirm=True)

    assert result.is_failure
    assert "gift state read failed" in (result.error or "")


# --- run_gift_reset ---


def test_run_gift_reset_drops_targets_and_sums_refunds(mock_client, mock_sleep):
    client, mock_call = mock_client
    heroes = _heroes_payload(
        (1, 150000, GIFT_MAX_LEVEL, GIFT_FULL_COST),
        (2, 60000, GIFT_MAX_LEVEL, GIFT_FULL_COST),
        (3, 40000, 7, 9000),
    )
    calls = []

    def _side_effect(payload):
        names = [c["name"] for c in payload["calls"]]
        if names[0] == ApiAction.HERO_GET_ALL:
            calls.append("state")
            # 実行後は hero 3 だけ降ろされた状態（hero 2 は drop 失敗でgift のまま）
            if len(calls) == 1:
                return _res(_state_detail(heroes, 1000))
            after = _heroes_payload(
                (1, 150000, GIFT_MAX_LEVEL, GIFT_FULL_COST),
                (2, 60000, GIFT_MAX_LEVEL, GIFT_FULL_COST),
                (3, 40000, 0, 0),
            )
            return _res(_state_detail(after, 1000 + 9000))
        hero_id = payload["calls"][0]["args"]["heroId"]
        calls.append(hero_id)
        refund = GIFT_FULL_COST if hero_id in (1, 2) else 9000
        # hero 2 の drop は失敗させる
        if hero_id == 2:
            return _res({}, status=ResponseStatus.ERROR, error_name="notAvailable")
        return _res(_drop_detail(refund))

    mock_call.side_effect = _side_effect

    result = run_gift_reset(client, account_alias="Alice", confirm=True)

    # hero 1 (power 150,000) は閾値超過で対象外、2 → 3 の順（power 降順）
    assert calls[1:3] == [2, 3]
    assert result.mode == "reset"
    assert result.changed == 1  # hero 2 の drop は失敗
    assert result.failed == 1
    assert result.coins == 9000  # 失敗分の返却は計上しない
    assert result.balance == 10000  # 再読した実残高（hero 3 の返却分だけ増えている）
    assert result.is_failure


def test_run_gift_reset_dry_run_and_empty_target_list(mock_client):
    client, mock_call = mock_client
    heroes = _heroes_payload((1, 150000, GIFT_MAX_LEVEL, GIFT_FULL_COST))
    mock_call.return_value = _res(_state_detail(heroes, 1000))

    planned = run_gift_reset(client, account_alias="Alice", dry_run=True)
    assert planned.changed == 0
    assert _write_calls(mock_call, ApiAction.HERO_TITAN_GIFT_DROP) == []

    # 全員 MAX 超過 power → drop 対象ゼロは失敗ではない
    mock_call.reset_mock()
    result = run_gift_reset(client, account_alias="Alice", confirm=True)
    assert result.changed == 0
    assert not result.is_failure
    assert mock_call.call_count == 1


def test_run_gift_reset_honours_max_power_and_limit(mock_client, mock_sleep):
    client, mock_call = mock_client
    heroes = _heroes_payload(
        (1, 90000, 30, 65150), (2, 80000, 30, 65150), (3, 70000, 30, 65150)
    )
    dropped = []

    def _side_effect(payload):
        names = [c["name"] for c in payload["calls"]]
        if names[0] == ApiAction.HERO_GET_ALL:
            return _res(_state_detail(heroes, 0))
        dropped.append(payload["calls"][0]["args"]["heroId"])
        return _res(_drop_detail(65150))

    mock_call.side_effect = _side_effect

    run_gift_reset(client, account_alias="Alice", confirm=True, max_power=85000, limit=1)

    assert dropped == [2]


# --- runner routines / summary ---


def test_gift_routines_run_non_interactively(mock_client, mock_sleep):
    from hw_genie.runner import gift_reset_routine, gift_up_routine

    client, mock_call = mock_client
    heroes = _heroes_payload((1, 50000, 0))
    _up_client(
        mock_call,
        states=[
            (heroes, GIFT_FULL_COST),
            (_heroes_payload((1, 60000, GIFT_MAX_LEVEL, GIFT_FULL_COST)), 0),
        ],
        up_details=[_up_success(GIFT_MAX_LEVEL)],
    )

    up_result = gift_up_routine(limit=1)(client, "Alice")

    assert isinstance(up_result, GiftRunResult)
    assert up_result.mode == "up"
    assert up_result.changed == 1
    assert up_result.coins == GIFT_FULL_COST  # 実残高 65,150 -> 0
    assert not up_result.is_failure

    gifted = _heroes_payload((1, 50000, GIFT_MAX_LEVEL, GIFT_FULL_COST))
    queue = [
        _res(_state_detail(gifted, 0)),
        _res(_drop_detail(GIFT_FULL_COST)),
        _res(_state_detail(_heroes_payload((1, 40000, 0, 0)), GIFT_FULL_COST)),
    ]
    mock_call.side_effect = lambda payload: queue.pop(0)

    reset_result = gift_reset_routine()(client, "Alice")

    assert reset_result.mode == "reset"
    assert reset_result.changed == 1
    assert reset_result.coins == GIFT_FULL_COST
    assert reset_result.balance == GIFT_FULL_COST
    assert not reset_result.is_failure


def test_summarize_gift_counts_failures_and_blocks(capsys):
    from hw_genie.runner import summarize_gift

    results = {
        "Alice": (GiftRunResult(account="Alice", mode="up", changed=3, coins=195450, balance=1000, blocked=2), None),
        "Bob": (GiftRunResult(account="Bob", mode="up", changed=0, error="hero 5: NotEnough"), None),
        "Carol": (None, HWAuthError("expired")),
    }

    failed = summarize_gift(results.items(), dry_run=False, mode="up")

    out = capsys.readouterr().out
    assert failed == 2
    assert "Alice" in out and "Bob" in out and "Carol" in out
    assert "gift up summary" in out


def test_summarize_gift_reset_uses_reset_mode_label(capsys):
    from hw_genie.runner import summarize_gift

    results = {
        "Dave": (GiftRunResult(account="Dave", mode="reset", changed=4, coins=260600, balance=300000), None),
    }

    assert summarize_gift(results.items(), dry_run=True, mode="reset") == 0
    out = capsys.readouterr().out
    assert "gift reset summary" in out
    assert "planned" in out


def test_multi_gift_modes_build_runnable_routines(monkeypatch):
    """multi の routine は (client, account) で呼べる形でなければならない。

    factory をそのまま functools.partial で縛ると client が limit の位置に
    入り込んで TypeError になる（過去に実走で発生）。その退行を捕まえる。
    """
    from hw_genie import main
    from hw_genie.core.client import HWClient

    captured = {}

    def fake_run_all(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        captured["routine"] = routine
        captured["max_parallel"] = max_parallel
        return {acc: (routine(HWClient({"x-auth-token": "t"}), acc), None) for acc in accounts}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run_all)
    monkeypatch.setattr("hw_genie.main._preflight_multi_accounts", lambda accounts: (list(accounts), {}))
    monkeypatch.setattr("hw_genie.runner.list_account_aliases", lambda: ["Alice", "Bob"])
    monkeypatch.setattr("hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"})
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)
    monkeypatch.setattr(
        "hw_genie.commands.element_gift.fetch_state",
        lambda client: ([], 0),
    )

    for mode_name in ("gift-up", "gift-reset"):
        args = type(
            "A",
            (),
            {
                "mode": mode_name,
                "accounts": [],
                "parallel": 1,
                "debug": False,
                "dry_run": True,
                "limit": None,
                "max_power": None,
                "yes": False,
            },
        )()
        main.cmd_multi(args)
        assert callable(captured["routine"])


# --- 読み取りの厳格化（形変更を「残高 0」に化かさない） ---


def test_unwrap_requires_a_response_object():
    from hw_genie.commands.element_gift import _unwrap

    # 単一コール（drop 等）で response ラッパが無い形 → wrapper を返さず失敗させる
    with pytest.raises(GiftReadError):
        _unwrap({"quests": []}, None)
    with pytest.raises(GiftReadError):
        _unwrap("not a dict", None)
    with pytest.raises(GiftReadError):
        _unwrap({"heroes": {"result": "not a dict"}}, "heroes")
    # 正常形は両方平坦化して返す
    assert _unwrap({"response": {"consumable": {"24": 5}}}, None) == {"consumable": {"24": 5}}
    assert _unwrap({"heroes": {"result": {"response": {"1": {"id": 1}}}}}, "heroes") == {
        "1": {"id": 1}
    }


def test_fetch_state_raises_when_inventory_has_no_response_wrapper(mock_client):
    """`inventoryGet` の result に `response` が無い形は「残高 0」でなく読み取り失敗。"""
    client, mock_call = mock_client
    mock_call.return_value = _res(
        {
            "heroes": {"result": {"response": _heroes_payload((1, 100, 0))}},
            # response ラッパが無い（API 形変更を模す）
            "inventory": {"result": {"consumable": {"24": 5000}}},
        }
    )

    with pytest.raises(GiftReadError, match="inventory"):
        fetch_state(client)


def test_run_gift_up_malformed_state_read_is_not_coin_shortage(mock_client, capsys):
    """形変更を「残高 0 → コイン不足 → exit 0」に握りつぶさない（失敗として返す）。"""
    client, mock_call = mock_client
    mock_call.return_value = _res(
        {
            "heroes": {"result": {"response": _heroes_payload((1, 200000, 0))}},
            "inventory": {"result": {"consumable": {"24": 5000}}},
        }
    )

    result = run_gift_up(client, account_alias="Alice", confirm=True)

    assert result.is_failure
    assert "no response object" in (result.error or "")
    assert mock_call.call_count == 1
    out = capsys.readouterr().out
    assert "Nothing to run" not in out


def test_drop_refund_survives_malformed_drop_response(mock_client, mock_sleep, capsys):
    """strict な `_unwrap` でも drop 側は壊れない（読めなければ返却 0 で続行）。"""
    client, mock_call = mock_client
    gifted = _heroes_payload((1, 60000, GIFT_MAX_LEVEL, GIFT_FULL_COST))
    responses = [
        _res(_state_detail(gifted, 0)),
        _res({"quests": []}),  # response の無い drop 応答
        _res(_state_detail(_heroes_payload((1, 50000, 0, 0)), 65150)),
    ]
    mock_call.side_effect = lambda payload: responses.pop(0)

    result = run_gift_reset(client, account_alias="Alice", confirm=True)

    assert result.changed == 1
    assert result.coins == 0
    assert result.balance == 65150
    assert not result.is_failure
    assert "no refund reported" in capsys.readouterr().out


# --- 応答の「最初のエラー」は送信順（辞書順ではない） ---


def test_call_results_reports_the_earliest_error_in_sent_order():
    from hw_genie.commands.element_gift import _call_results

    detail = {}
    for i in range(12):
        ident = "body" if i == 0 else f"body_{i}"
        if i == 2:
            detail[ident] = {"error": {"name": "NotEnough"}}
        elif i == 10:
            detail[ident] = {"error": {"name": "ServerError"}}
        else:
            detail[ident] = {"result": {"response": None, "quests": []}}

    succeeded, first_error = _call_results(_res(detail), 12)

    # sorted(detail) だと body_10 が body_2 より先に来、遅いエラーを拾ってしまう
    assert succeeded == 10
    assert first_error == "NotEnough"


def test_call_results_still_counts_ident_shapes_missing_from_the_payload():
    from hw_genie.commands.element_gift import _call_results

    detail = {"0": {"result": {"response": None}}, "1": {"error": {"name": "Boom"}}}

    succeeded, first_error = _call_results(_res(detail), 2)

    assert succeeded == 1
    assert first_error == "Boom"


# --- print_status ---


def test_print_status_reports_counts(mock_client, capsys):
    client, mock_call = mock_client
    heroes = _heroes_payload((1, 200000, 0), (2, 60000, GIFT_MAX_LEVEL, GIFT_FULL_COST))
    mock_call.return_value = _res(_state_detail(heroes, GIFT_FULL_COST))

    print_status(client, "Alice")

    out = capsys.readouterr().out
    assert "Element gift status: Alice" in out
    assert f"coins (libId 24): {GIFT_FULL_COST:,}" in out
    assert "MAX 1 / not MAX 1" in out
    assert "reset : 1 hero(es)" in out


def test_print_status_raw_json_returns_none(mock_client, capsys):
    client, mock_call = mock_client
    mock_call.return_value = _res(_state_detail(_heroes_payload((1, 200000, 0)), 123))

    assert print_status(client, "Alice", raw=True) is None

    payload = json.loads(capsys.readouterr().out)
    assert payload["coin_balance"] == 123
    assert payload["heroes"][0]["heroId"] == 1


# --- 認証切れは部分レポートを出してから上げる ---


def test_run_gift_up_reports_partial_summary_before_auth_error(mock_client, mock_sleep, capsys):
    client, mock_call = mock_client
    heroes = _heroes_payload((1, 200000, 0), (2, 150000, 0))
    mock_call.side_effect = [
        _res(_state_detail(heroes, 200000)),
        _res(_up_success(GIFT_MAX_LEVEL)),
        HWAuthError("expired"),  # hero 2 への書き込みで認証切れ
        HWAuthError("expired"),  # 最終再読も失敗 → 見積で報告する
    ]

    with pytest.raises(HWAuthError):
        run_gift_up(client, account_alias="Alice", confirm=True)

    out = capsys.readouterr().out
    assert "1 hero(es) maxed" in out
    assert f"{GIFT_FULL_COST:,} coins spent" in out
    assert "balance 134,850" in out
    assert "auth error" in out


def test_run_gift_up_uses_measured_spend_when_final_read_survives(mock_client, mock_sleep, capsys):
    client, mock_call = mock_client
    heroes = _heroes_payload((1, 200000, 0), (2, 150000, 0))
    mock_call.side_effect = [
        _res(_state_detail(heroes, 200000)),
        _res(_up_success(GIFT_MAX_LEVEL)),
        HWAuthError("expired"),
        _res(_state_detail(_heroes_payload((1, 205555, GIFT_MAX_LEVEL, GIFT_FULL_COST), (2, 150000, 0)), 130000)),
    ]

    with pytest.raises(HWAuthError):
        run_gift_up(client, account_alias="Alice", confirm=True)

    out = capsys.readouterr().out
    assert "70,000 coins spent" in out  # 実測（200,000 - 130,000）で見積ではない
    assert "balance 130,000" in out


def test_run_gift_reset_reports_partial_summary_before_auth_error(mock_client, mock_sleep, capsys):
    client, mock_call = mock_client
    heroes = _heroes_payload(
        (1, 60000, GIFT_MAX_LEVEL, GIFT_FULL_COST),
        (2, 50000, GIFT_MAX_LEVEL, GIFT_FULL_COST),
    )
    mock_call.side_effect = [
        _res(_state_detail(heroes, 0)),
        _res(_drop_detail(GIFT_FULL_COST)),
        HWAuthError("expired"),
        HWAuthError("expired"),
    ]

    with pytest.raises(HWAuthError):
        run_gift_reset(client, account_alias="Alice", confirm=True)

    out = capsys.readouterr().out
    assert "1 hero(es) dropped" in out
    assert f"{GIFT_FULL_COST:,} coins recovered" in out
    assert "1 failed" in out


# --- summarize_gift の mode は必須引数 ---


def test_summarize_gift_mode_is_required(capsys):
    from hw_genie.runner import summarize_gift

    results = [("Alice", (GiftRunResult(account="Alice", mode="up", changed=1), None))]

    with pytest.raises(TypeError):
        summarize_gift(results, dry_run=False)  # 既定値 "gift" は表示上あり得ないため廃止

    assert summarize_gift(results, mode="up") == 0
    assert "gift up summary" in capsys.readouterr().out


# --- run_logs とサマリ表示の整合（multi gift） ---


def _multi_gift_args(mode, **overrides):
    base = {
        "mode": mode,
        "accounts": ["Alice"],
        "parallel": 1,
        "debug": False,
        "dry_run": False,
        "limit": None,
        "max_power": None,
        "yes": False,
    }
    base.update(overrides)
    return type("A", (), base)()


def _gift_success_result(account, mode="up"):
    return GiftRunResult(account=account, mode=mode, changed=2, coins=130300, balance=9700, blocked=1)


def _patch_multi_gift_workers(monkeypatch, results_fn):
    from hw_genie import main

    monkeypatch.setattr(main, "_preflight_multi_accounts", lambda accounts: (list(accounts), {}))
    monkeypatch.setattr("hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"})
    monkeypatch.setattr("hw_genie.main.run_all_accounts", results_fn)
    records = {}
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: records.update(k))
    return records


def test_run_log_account_failure_gift_branch_mirrors_summary():
    from hw_genie.main import _run_log_account_failure

    assert _run_log_account_failure("gift-up", _gift_success_result("Alice"), None) is None
    assert _run_log_account_failure("gift-reset", _gift_success_result("Bob", "reset"), None) is None
    # コイン不足の打ち切り（failed ゼロ・error なし）は失敗ではない
    blocked = GiftRunResult(account="Carol", mode="up", changed=0, blocked=41, balance=100)
    assert _run_log_account_failure("gift-up", blocked, None) is None

    errored = GiftRunResult(account="Dave", mode="up", changed=1, error="hero 5: NotEnough")
    assert _run_log_account_failure("gift-up", errored, None) == "hero 5: NotEnough"
    failed = GiftRunResult(account="Alice", mode="reset", changed=1, failed=2)
    assert _run_log_account_failure("gift-reset", failed, None) == "2 hero(es) failed"
    assert _run_log_account_failure("gift-up", "not a gift result", None) == "gift result unavailable"


def test_run_log_account_failure_gift_auth_error_reason_from_exception():
    """routine が例外で落ちた（result が None）アカウントも嘘の理由でなく失敗になる。"""
    from hw_genie.main import _build_run_log_summary, _run_log_account_failure

    exc = HWAuthError("Session expired for 'Alice': token gone\nre-sync via auth-server")
    assert _run_log_account_failure("gift-up", None, exc) == "Session expired for 'Alice': token gone"

    entries, summary = _build_run_log_summary("gift-up", {"Alice": (None, exc)})
    assert entries == [
        {
            "account": "Alice",
            "ok": False,
            "error": "Session expired for 'Alice': token gone",
            "interrupted": False,
        }
    ]
    assert summary == "1 account(s) failed: Alice (Session expired for 'Alice': token gone)"


def test_multi_gift_up_records_ok_rows_for_successful_accounts(monkeypatch):
    """完全成功の multi gift-up で run_logs が自己矛盾しない（以前は全行 ok:false）。"""
    from hw_genie import main

    records = _patch_multi_gift_workers(
        monkeypatch,
        lambda routine, accounts=None, max_parallel=None, cooperative_wait=False: {
            acc: (_gift_success_result(acc), None) for acc in (accounts or [])
        },
    )

    main.cmd_multi(_multi_gift_args("gift-up", yes=True))  # 失敗ゼロなら exit しない

    assert records["mode"] == "gift-up"
    assert records["status"] == "ok"
    assert records["exit_code"] == 0
    assert records["error_summary"] is None
    assert records["accounts"] == [
        {"account": "Alice", "ok": True, "error": None, "interrupted": False}
    ]


def test_multi_gift_reset_records_failure_rows_with_real_reason(monkeypatch):
    from hw_genie import main

    records = _patch_multi_gift_workers(
        monkeypatch,
        lambda routine, accounts=None, max_parallel=None, cooperative_wait=False: {
            acc: (GiftRunResult(account=acc, mode="reset", changed=1, failed=2), None)
            for acc in (accounts or [])
        },
    )

    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(_multi_gift_args("gift-reset", yes=True))

    assert exc.value.code == 1
    assert records["status"] == "failed"
    assert records["accounts"][0]["ok"] is False
    assert records["accounts"][0]["error"] == "2 hero(es) failed"
    assert records["error_summary"] == "1 account(s) failed: Alice (2 hero(es) failed)"


def test_multi_gift_cancel_without_failures_stays_ok(monkeypatch):
    """Ctrl+C 経路で失敗ゼロなら gift も ok / exit 0（既存 toe / seers-game と同じ契約）。"""
    from hw_genie import main, runner

    runner.reset_cancel()
    try:
        def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
            runner.request_cancel()
            return {acc: (_gift_success_result(acc), None) for acc in (accounts or [])}

        records = _patch_multi_gift_workers(monkeypatch, fake_run)
        main.cmd_multi(_multi_gift_args("gift-up", yes=True))  # exit しない

        assert records["status"] == "ok"
        assert records["exit_code"] == 0
        assert records["error_summary"] is None
    finally:
        runner.reset_cancel()


def test_multi_gift_cancel_with_real_failure_exits_1_for_that_account_only(monkeypatch):
    """中断経路でも実失敗アカウントだけを失敗として記録する。"""
    from hw_genie import main, runner

    runner.reset_cancel()
    try:
        def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
            runner.request_cancel()
            return {
                "Alice": (_gift_success_result("Alice"), None),
                "Bob": (GiftRunResult(account="Bob", mode="up", error="hero 7: NotEnough"), None),
            }

        monkeypatch.setattr(main, "_preflight_multi_accounts", lambda accounts: (["Alice", "Bob"], {}))
        monkeypatch.setattr("hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"})
        monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
        records = {}
        monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: records.update(k))

        with pytest.raises(SystemExit) as exc:
            main.cmd_multi(_multi_gift_args("gift-up", yes=True, accounts=["Alice", "Bob"]))

        assert exc.value.code == 1
        assert records["status"] == "failed"
        assert records["error_summary"] == "1 account(s) failed: Bob (hero 7: NotEnough)"
        by_account = {e["account"]: e for e in records["accounts"]}
        assert by_account["Alice"]["ok"] is True
        assert by_account["Bob"]["ok"] is False
    finally:
        runner.reset_cancel()


# --- multi の --yes ゲートと終了コード（CLI 層） ---


@pytest.mark.parametrize("mode_name", ["gift-up", "gift-reset"])
def test_multi_gift_write_without_yes_exits_2_before_workers(mode_name, monkeypatch, capsys):
    from hw_genie import main

    called = []
    monkeypatch.setattr(
        main, "run_all_accounts", lambda *a, **k: called.append(1) or {}
    )
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)

    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(_multi_gift_args(mode_name))

    assert exc.value.code == 2
    assert called == []
    assert "--yes" in capsys.readouterr().err


@pytest.mark.parametrize("mode_name", ["gift-up", "gift-reset"])
def test_multi_gift_dry_run_without_yes_is_allowed(mode_name, monkeypatch):
    """dry-run は読み取りのみなので `--yes` 不要（かつ逐次実行に強制される）。"""
    from hw_genie import main

    captured = {}
    result_mode = "up" if mode_name == "gift-up" else "reset"

    def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        captured["max_parallel"] = max_parallel
        return {
            acc: (GiftRunResult(account=acc, mode=result_mode, dry_run=True), None)
            for acc in (accounts or [])
        }

    records = _patch_multi_gift_workers(monkeypatch, fake_run)
    main.cmd_multi(_multi_gift_args(mode_name, dry_run=True))

    assert captured["max_parallel"] == 1
    assert records["status"] == "ok"


@pytest.mark.parametrize(
    "mode_name,overrides",
    [
        ("gift-up", {"limit": 0}),
        ("gift-reset", {"limit": 0}),
        ("gift-reset", {"max_power": -1}),
    ],
)
def test_multi_gift_invalid_limit_and_max_power_exit_2(mode_name, overrides, monkeypatch, capsys):
    from hw_genie import main

    called = []
    monkeypatch.setattr(
        main, "run_all_accounts", lambda *a, **k: called.append(1) or {}
    )
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)

    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(_multi_gift_args(mode_name, yes=True, **overrides))

    assert exc.value.code == 2
    assert called == []
    expected_flag = "--max-power" if "max_power" in overrides else "--limit"
    assert expected_flag in capsys.readouterr().err


@pytest.mark.parametrize(
    "action,overrides",
    [("up", {"limit": 0}), ("reset", {"max_power": -1})],
)
def test_cmd_gift_validates_flags_before_loading_session(action, overrides, monkeypatch, capsys):
    from hw_genie import main

    def _no_session(args):
        raise AssertionError("flags must be validated before the session is loaded")

    monkeypatch.setattr(main, "_ensure_session", _no_session)
    args = type(
        "A",
        (),
        {
            "action": action,
            "account": "Alice",
            "raw": False,
            "dry_run": False,
            "yes": True,
            "limit": overrides.get("limit"),
            "max_power": overrides.get("max_power"),
        },
    )()

    with pytest.raises(SystemExit) as exc:
        main.cmd_gift(args)

    assert exc.value.code == 2
    expected_flag = "--max-power" if action == "reset" else "--limit"
    assert expected_flag in capsys.readouterr().err
