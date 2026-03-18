from unittest.mock import MagicMock, patch

# スクリプトのディレクトリをパスに追加

from hw_genie.commands.daily_raid import run_daily_raid


@patch("hw_genie.commands.daily_raid.HERO_MISSION_IDS", [1, 2])
def test_daily_raid_phase1_stamina_stops_phase2(mock_client, mock_sleep):
    """Phase 1 でスタミナ切れが発生した際、Phase 2 がスキップされることを検証"""
    client, mock_call = mock_client

    mock_responses = []

    # Mission 1: 成功
    res_m1 = MagicMock()
    res_m1.is_success = True
    mock_responses.append(res_m1)

    # Mission 2: スタミナ切れ
    res_m2 = MagicMock()
    res_m2.is_success = False
    res_m2.error_name = "notEnoughStamina"
    mock_responses.append(res_m2)

    # 換金 (Phase 3 は必ず実行される)
    res_ex = MagicMock()
    res_ex.is_success = True
    res_ex.exchange_info = None
    mock_responses.append(res_ex)

    mock_call.side_effect = mock_responses

    # 実行 (item_payload はダミー)
    run_daily_raid({"x-request-id": "100"}, {"calls": []})

    # 検証: mock_call は m1(1) + m2(1) + exchange(1) の計 3 回呼ばれる
    # (allow_recovery=False なので recovery は呼ばれず、Phase 2 の Item Raid も呼ばれないはず)
    assert mock_call.call_count == 3

    # 2 回目の呼び出しが Mission 2 であることを確認
    args, kwargs = mock_call.call_args_list[1]
    payload = args[0]
    assert payload["calls"][0]["args"]["id"] == 2


@patch("hw_genie.commands.daily_raid.HERO_MISSION_IDS", [1])
def test_daily_raid_full_success(mock_client, mock_sleep):
    """全フェーズ成功のフローを検証"""
    client, mock_call = mock_client

    mock_responses = []

    # Phase 1: Mission 1 成功
    res_m1 = MagicMock()
    res_m1.is_success = True
    mock_responses.append(res_m1)

    # Phase 2: Item Raid (1回成功、2回目スタミナ切れで停止)
    res_i1 = MagicMock()
    res_i1.is_success = True
    mock_responses.append(res_i1)

    res_i2 = MagicMock()
    res_i2.is_success = False
    res_i2.error_name = "notEnoughStamina"
    mock_responses.append(res_i2)

    # Phase 3: 換金
    res_ex = MagicMock()
    res_ex.is_success = True
    res_ex.exchange_info = MagicMock(stones=5)
    mock_responses.append(res_ex)

    mock_call.side_effect = mock_responses

    run_daily_raid({"x-request-id": "100"}, {"calls": []})

    # 検証: m1(1) + i1(1) + i2(1) + ex(1) = 4
    assert mock_call.call_count == 4
