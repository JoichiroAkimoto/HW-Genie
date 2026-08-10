"""``run_quest_execute``（デイリークエスト自動実行）のテスト。

- モッククライアントを使い、ネットワークに依存しない。
- quest_defaults の読み書きは conftest のインメモリ DB（SessionManager）を使う。
- 実行可否は quest_defaults[quest_id]["enabled"] で制御される（初期状態は無効）。
"""

from unittest.mock import MagicMock, patch

from hw_genie.commands.quests import (
    get_quest_defaults,
    get_quest_guild_defaults,
    run_quest_execute,
    set_quest_defaults,
    set_quest_guild_defaults,
)
from hw_genie.core.client import ApiAction, HWClient, PlayerStatus, ResponseStatus
from hw_genie.core.session_manager import SessionManager


# テスト用の固定実行時刻（2026-08-11 00:00 JST）。time.time をパッチして
# レシピ実行記録（last_recipe_at）の検証を clock 非依存にする。
FIXED_CLOCK = 1786374000


# --- ヘルパー ---


def _ok_response(detail: dict) -> MagicMock:
    res = MagicMock()
    res.status = ResponseStatus.SUCCESS
    res.error_name = None
    res.detail = detail
    return res


def _error_response(error: str) -> MagicMock:
    res = MagicMock()
    res.status = ResponseStatus.ERROR
    res.error_name = error
    res.detail = {}
    return res


def _register(account: str) -> None:
    SessionManager.save(account, {"player": {"id": account, "name": account}, "headers": {"x-auth-token": "test"}})


def _enable(account: str, quest_id: int) -> None:
    _register(account)
    set_quest_defaults(account, quest_id, "enabled", True)


def _make_client(raw_quests: list[dict], account: str = "Alex", player: PlayerStatus | None = None) -> HWClient:
    """アカウント登録＋quest_get_all モック済みクライアントを作る。

    ``player`` を渡すと fetch_player_status がその PlayerStatus を返すように
    モックされる（未指定なら next_day_ts=0 となりギルドレシピガードは無効）。
    """
    _register(account)
    client = HWClient(headers={"x-auth-token": "test"})
    res = _ok_response({"response": raw_quests})
    client.quest_get_all = MagicMock(return_value=res)
    client.quest_farm = MagicMock(return_value=_ok_response({}))
    client.call = MagicMock(return_value=_ok_response({"response": {}}))
    if player is not None:
        client.fetch_player_status = MagicMock(return_value=player)
    return client


def _shop_inventory(slots: dict) -> MagicMock:
    """shopGetAll のレスポンスを作る。slots は ``{"18": {"reward": ..., "cost": ...}}``。"""
    return _ok_response({"response": {"13": {"slots": slots}}})


def _active(qid: int) -> dict:
    return {"id": qid, "state": 1, "progress": 0, "reward": {}, "createTime": 0, "farmCount": 0}


def _claimable_guild(qid: int) -> dict:
    return {"id": qid, "state": 2, "progress": 100, "reward": {"stamina": 200}, "createTime": 0, "farmCount": 0}


def _active_guild(qid: int) -> dict:
    return {"id": qid, "state": 1, "progress": 0, "reward": {"clanQuestsPoints": 10, "prestige": 50}, "createTime": 0, "farmCount": 0, "order": 1}


# --- ギルドクエスト（2000xxxx/2001xxxx = Sparks of Power） ---


def test_guild_claimable_farmed_without_defaults(capsys):
    """state=2 のギルドクエストは quest_defaults 設定なしでも questFarm で受領される。"""
    client = _make_client([_claimable_guild(20010002)])
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 20010002
    assert failed == []
    client.quest_farm.assert_called_once_with(20010002)


def test_guild_active_skipped_when_disabled(capsys):
    """quest_guild_defaults 無効（初期状態）なら active ギルドクエストは操作されず skipped に入る。"""
    client = _make_client([_active_guild(20000111)])
    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)

    assert succeeded == []
    assert failed == []
    assert 20000111 in skipped
    client.quest_operation.assert_not_called()

    # 未初期化でも ensure により quest_guild_defaults が投入されている
    defaults = get_quest_guild_defaults("Alex")
    assert defaults.get("enabled") is False
    assert defaults.get("heroId") == 38


def test_guild_active_runs_recipe_and_claims_reached(capsys):
    """enabled=true なら heroTitanGift レシピ実行 → 再取得で state=2 になったものを claim。"""
    client = _make_client([_active_guild(20000111)])
    set_quest_guild_defaults("Alex", "enabled", True)

    def _op(action: ApiAction, args: dict):
        return _ok_response({"quests": [{"id": 20000111, "state": 1}]})

    client.quest_operation = MagicMock(side_effect=_op)

    # 1 回目は state=1（進行中）、レシピ実行後の再取得では state=2 になったものを返す
    res_first = _ok_response({"response": [_active_guild(20000111)]})
    res_after = _ok_response({"response": [_claimable_guild(20000111)]})
    client.quest_get_all = MagicMock(side_effect=[res_first, res_after])
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 20000111
    assert failed == []
    assert skipped == []
    client.quest_farm.assert_called_once_with(20000111)
    # quest_operation 呼び出しを確認
    ops = [c.args[0] for c in client.quest_operation.call_args_list]
    assert ops == [ApiAction.HERO_TITAN_GIFT_LEVEL_UP, ApiAction.HERO_TITAN_GIFT_LEVEL_UP, ApiAction.HERO_TITAN_GIFT_DROP]


def test_guild_dry_run_plan(capsys):
    """dry-run では active/claimable のギルドクエストがプランに現れる。"""
    client = _make_client([_claimable_guild(20010002), _active_guild(20000111)])
    set_quest_guild_defaults("Alex", "enabled", True)
    _ = client

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "20010002" in out
    assert "Guild quests (Sparks of Power)" in out
    assert "heroTitanGiftLevelUp" in out


# --- ギルドレシピ 1 日 1 回ガード（nextDayTs 境界） ---


def test_guild_cycle_boundary():
    """nextDayTs から現在のリセットサイクル開始時刻（-24h）が求まる。"""
    from hw_genie.commands.quests import _guild_cycle_boundary

    assert _guild_cycle_boundary(PlayerStatus(next_day_ts=1786287600)) == 1786287600 - 86400
    # nextDayTs 未取得（0 / None）はガード無効
    assert _guild_cycle_boundary(PlayerStatus()) is None
    assert _guild_cycle_boundary(PlayerStatus(next_day_ts=0)) is None


def test_guild_recipe_skipped_when_already_ran_today(capsys):
    """last_recipe_at が今日のサイクル開始以降なら（今日実行済み）スキップ。"""
    boundary = 1786287600 - 86400  # 現在のサイクル開始
    client = _make_client(
        [_active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    set_quest_guild_defaults("Alex", "enabled", True)
    set_quest_guild_defaults("Alex", "last_recipe_at", boundary + 100)  # 今日実行済み

    client.quest_operation = MagicMock()
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "recipe already run today" in out
    client.quest_operation.assert_not_called()


def test_guild_recipe_runs_when_record_is_previous_cycle(capsys):
    """last_recipe_at が前サイクルなら（今日未実行）レシピが実行される。"""
    boundary = 1786287600 - 86400
    client = _make_client(
        [_active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    set_quest_guild_defaults("Alex", "enabled", True)
    set_quest_guild_defaults("Alex", "last_recipe_at", boundary - 100)  # 昨日実行済み

    def _op(action: ApiAction, args: dict):
        return _ok_response({"quests": [{"id": 20000111, "state": 1}]})

    client.quest_operation = MagicMock(side_effect=_op)

    with patch("hw_genie.commands.quests.time.time", return_value=FIXED_CLOCK):
        succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert client.quest_operation.call_count == 3
    # 実行成功後、last_recipe_at が今日（サイクル開始以降）に更新される
    last = get_quest_guild_defaults("Alex").get("last_recipe_at")
    assert last == FIXED_CLOCK


def test_guild_recipe_guard_disabled_without_nextday(capsys):
    """nextDayTs が取れない環境（ガード無効データ）では実行される。"""
    client = _make_client([_active_guild(20000111)], player=PlayerStatus())
    set_quest_guild_defaults("Alex", "enabled", True)
    set_quest_guild_defaults("Alex", "last_recipe_at", 999)  # 境界不明のため無効

    def _op(action: ApiAction, args: dict):
        return _ok_response({"quests": [{"id": 20000111, "state": 1}]})

    client.quest_operation = MagicMock(side_effect=_op)

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert client.quest_operation.call_count == 3

    # ガードのロジック（next_day_ts なし = None）を直接確認
    from hw_genie.commands.quests import _guild_cycle_boundary

    assert _guild_cycle_boundary(PlayerStatus()) is None


def test_guild_dry_run_shows_guard_skip(capsys):
    """dry-run でも今日実行済みならスキップが表示される。"""
    client = _make_client(
        [_active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    set_quest_guild_defaults("Alex", "enabled", True)
    set_quest_guild_defaults("Alex", "last_recipe_at", 1786287600 - 86400 + 100)

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "recipe already run today" in out
    assert "heroTitanGiftLevelUp" not in out


def test_guild_recipe_runs_once_per_day(capsys):
    """レシピは 1 日 1 セットのみ。未達成でも 2 セット目は実行されない。

    1 回実行 → last_recipe_at 記録 → 同じサイクル内でもう一度実行すると
    スキップされる（進捗には依存しない時刻ベースの 1 日 1 回ガード）。
    """
    client = _make_client(
        [_active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    set_quest_guild_defaults("Alex", "enabled", True)

    res_initial = _ok_response({"response": [_active_guild(20000111)]})
    res_run1 = _ok_response({"response": [dict(_active_guild(20000111), progress=150)]})
    client.quest_get_all = MagicMock(side_effect=[res_initial, res_run1])
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    with patch("hw_genie.commands.quests.time.time", return_value=FIXED_CLOCK):
        succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    # 1 セット（LevelUp ×2 → Drop = 3 RPC）のみ。2 セット目は実行されない
    assert client.quest_operation.call_count == 3
    assert succeeded == []
    assert failed == []
    assert "today's run" in out
    assert "No guild quest reached claimable state yet." in out
    assert get_quest_guild_defaults("Alex").get("last_recipe_at") == FIXED_CLOCK

    # 同じサイクル内の再実行ではスキップされる（未達成でも繰り返さない）
    res2 = _ok_response({"response": [_active_guild(20000111)]})
    client.quest_get_all = MagicMock(return_value=res2)
    client.quest_operation = MagicMock()

    with patch("hw_genie.commands.quests.time.time", return_value=FIXED_CLOCK):
        succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out2 = capsys.readouterr().out

    assert "recipe already run today" in out2
    client.quest_operation.assert_not_called()


def test_guild_recipe_failure_not_marked_done(capsys):
    """レシピ失敗時（資源不足等）は last_recipe_at を記録せず、次の実行で再試行できる。"""
    client = _make_client(
        [_active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    set_quest_guild_defaults("Alex", "enabled", True)

    client.quest_operation = MagicMock(return_value=_error_response("NotEnough"))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert "NotEnough" in failed[0]["error"]
    assert get_quest_guild_defaults("Alex").get("last_recipe_at") is None


# --- フォールバック候補（candidates） ---


def test_candidates_fallback_used_on_not_enough(capsys):
    """リソース不足（NotEnough）時、candidates の候補 args で再実行して成功する。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "candidates", [{"heroId": 53, "slotId": 2}])

    sent_args = []

    def _op(action, args):
        sent_args.append(dict(args))
        if action == ApiAction.HERO_ARTIFACT_LEVEL_UP:
            if len(sent_args) == 1:
                return _error_response("NotEnough")
            return _ok_response({"quests": [{"id": 10024, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert "recovered with fallback" in out
    assert sent_args[0] == {"heroId": 61, "slotId": 1}
    assert sent_args[1] == {"heroId": 53, "slotId": 2}
    assert client.quest_farm.call_count == 1


def test_candidates_fallback_all_fail_reported(capsys):
    """全候補失敗時は失敗として報告される（エラーは候補の最後のもの）。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "candidates", [{"heroId": 53, "slotId": 2}])

    client.quest_operation = MagicMock(return_value=_error_response("NotEnough"))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "heroArtifactLevelUp"
    assert "NotEnough" in failed[0]["error"]
    assert client.quest_operation.call_count == 2  # 初回 + 候補1


def test_candidates_ignored_for_non_resource_error(capsys):
    """リソース系以外のエラー（スタミナ不足等）では候補を試さない。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "candidates", [{"heroId": 53}])

    client.quest_operation = MagicMock(return_value=_error_response("notEnoughStamina"))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert client.quest_operation.call_count == 1


def test_candidates_ignored_in_operation_args(capsys, caplog):
    """candidates は操作引数に混入せず、未知キー警告の対象外。"""
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "candidates", [{"heroId": 53}])

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert "does not match any arg" not in caplog.text
    for call in client.quest_operation.call_args_list:
        args = call.args[1]
        assert "candidates" not in args


# --- レビュー指摘対応の追加テスト ---


def test_candidates_unknown_keys_filtered_out(capsys):
    """ステップ args に存在しない候補キー（titanId 等）は送信 args に混入しない。

    フォールバック時も、サーバーへ未知キーを送らない（_resolve_operation_args の
    「既存キーのみ上書き」規則と同じ挙動を保証する）。
    """
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "candidates", [{"heroId": 53, "titanId": 999, "slotId": 2}])

    sent_args = []

    def _op(action, args):
        sent_args.append(dict(args))
        if len(sent_args) == 1:
            return _error_response("NotEnough")
        return _ok_response({"quests": [{"id": 10024, "state": 2}]})

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert len(succeeded) == 1
    assert failed == []
    # 10024 のステップ args は {heroId, slotId}。titanId は混入しない
    assert sent_args[1] == {"heroId": 53, "slotId": 2}
    assert "titanId" not in sent_args[1]


def test_candidates_only_unknown_keys_not_tried(capsys):
    """全ての候補キーがステップ args 外なら候補は 1 つも試されない（リトライなし）。"""
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "candidates", [{"titanId": 999}])

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock(return_value=_error_response("NotEnough"))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert client.quest_operation.call_count == 1  # リトライされない


def test_candidates_dry_run_plan_filters_unknown_keys(capsys):
    """dry-run の計画表示でも未知キーを含む候補はフィルタされて表示される。"""
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "candidates", [{"heroId": 53, "titanId": 999}])

    client = _make_client([_active(10024)])

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert "titanId" not in out
    assert "{'heroId': 53}" in out


def test_guild_recipe_claims_reached_partial(capsys):
    """1 セット実行後、達成分だけ受領し、未達成は残る（繰り返し実行しない）。"""
    client = _make_client(
        [_active_guild(20000111), _active_guild(20000112)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    set_quest_guild_defaults("Alex", "enabled", True)

    res_initial = _ok_response({"response": [_active_guild(20000111), _active_guild(20000112)]})
    # 実行後: 20000111 は claimable、20000112 は進捗ありで still active
    res_run1 = _ok_response({
        "response": [
            _claimable_guild(20000111),
            dict(_active_guild(20000112), progress=300),
        ]
    })
    client.quest_get_all = MagicMock(side_effect=[res_initial, res_run1])
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    with patch("hw_genie.commands.quests.time.time", return_value=FIXED_CLOCK):
        succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert client.quest_operation.call_count == 3  # 1 セットのみ（繰り返さない）
    assert client.quest_farm.call_count == 1
    assert {s["quest_id"] for s in succeeded} == {20000111}
    assert failed == []
    assert get_quest_guild_defaults("Alex").get("last_recipe_at") == FIXED_CLOCK


def test_guild_recipe_boundary_exact_and_just_before(capsys):
    """last_recipe_at == boundary ちょうどは実行済み扱い、boundary - 1 は未実行。"""
    boundary = 1786287600 - 86400

    # boundary ちょうど → 今日実行済み扱い（スキップ）
    client = _make_client(
        [_active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    set_quest_guild_defaults("Alex", "enabled", True)
    set_quest_guild_defaults("Alex", "last_recipe_at", boundary)
    client.quest_operation = MagicMock()
    run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out
    assert "recipe already run today" in out
    client.quest_operation.assert_not_called()

    # boundary - 1 → 未実行扱い（実行される）
    client2 = _make_client(
        [_active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    set_quest_guild_defaults("Alex", "enabled", True)
    set_quest_guild_defaults("Alex", "last_recipe_at", boundary - 1)
    client2.quest_operation = MagicMock(return_value=_ok_response({}))
    client2.quest_get_all = MagicMock(
        side_effect=[
            _ok_response({"response": [_active_guild(20000111)]}),
            _ok_response({"response": [_active_guild(20000111)]}),
        ]
    )
    client2.quest_farm = MagicMock(return_value=_ok_response({}))
    with patch("hw_genie.commands.quests.time.time", return_value=FIXED_CLOCK):
        run_quest_execute(client2, account_alias="Alex", confirm=True)
    capsys.readouterr().out
    assert client2.quest_operation.call_count == 3


def test_daily_10023_covers_guild_recipe_no_double_run(capsys):
    """デイリー 10023 成功時はギルドレシピを重複実行しない（同じ Gift 消費）。

    quest_defaults[10023].enabled + quest_guild_defaults.enabled の両方が
    有効なアカウントで、レシピが 1 回（LevelUp ×2 → Drop = 3 RPC）だけ
    実行されることを保証する。10023 の成功は last_recipe_at に記録され
    ギルドフェーズはスキップされる。
    """
    client = _make_client(
        [_active(10023), _active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    _enable("Alex", 10023)
    set_quest_guild_defaults("Alex", "enabled", True)

    calls = []

    def _op(action, args):
        calls.append((action, dict(args)))
        if len(calls) == 3:
            return _ok_response({"quests": [{"id": 10023, "state": 2}]})
        return _ok_response({"quests": [{"id": 10023, "state": 1}]})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    with patch("hw_genie.commands.quests.time.time", return_value=FIXED_CLOCK):
        succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    # レシピは計 3 RPC（LevelUp×2 → Drop）＝デイリー 10023 の実行分のみ
    assert client.quest_operation.call_count == 3
    assert "skipping duplicate recipe" in out
    assert len(succeeded) >= 1  # 10023 の claim は実行される

    # 10023 の成功が今日の実行済み記録（last_recipe_at）に反映される
    assert get_quest_guild_defaults("Alex").get("last_recipe_at") == FIXED_CLOCK


def test_guild_dry_run_shows_daily_coverage(capsys):
    """dry-run で 10023 がプランに載っている場合は重複レシピを計画しない。"""
    client = _make_client(
        [_active(10023), _active_guild(20000111)],
        player=PlayerStatus(next_day_ts=1786287600),
    )
    _enable("Alex", 10023)
    set_quest_guild_defaults("Alex", "enabled", True)

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "skipping duplicate recipe" in out
    # プランのギルドレシピセクションで heroTitanGiftLevelUp が提示されない
    assert "Guild quests (Sparks of Power): run recipe to gain Sparks" not in out


def test_guild_recipe_hero_id_prefers_quest_defaults(capsys):
    """ギルドレシピ heroId は quest_defaults[10023] が優先される。"""
    client = _make_client([_active_guild(20000111)])
    set_quest_guild_defaults("Alex", "enabled", True)
    set_quest_guild_defaults("Alex", "heroId", 42)
    _enable("Alex", 10023)
    set_quest_defaults("Alex", 10023, "heroId", 7)

    def _op(action, args):
        return _ok_response({"quests": [{"id": 20000111, "state": 1}]})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_get_all = MagicMock(
        side_effect=[
            _ok_response({"response": [_active_guild(20000111)]}),
            _ok_response({"response": [_active_guild(20000111)]}),  # レシピ後の再取得
        ]
    )
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    ops = [c.args[1] for c in client.quest_operation.call_args_list]
    assert ops
    assert all(args.get("heroId") == 7 for args in ops)


# --- read-modify-write 保持（update_config_merged の原子的 RMW） ---
# インメモリ SQLite はスレッドごとに別 DB になるため、真の並列スレッドテストは
# できない。代わりに「update_config_merged が既存値を保持してマージする」ことを
# 順序を変えて検証する（ロック付き RMW の本質 = lost update しない）。


def test_set_quest_guild_defaults_keeps_existing_keys():
    """set（last_recipe_at）→ set（heroId）の順でも両方のキーが保持される。"""
    _register("Para")
    set_quest_guild_defaults("Para", "last_recipe_at", 111)
    set_quest_guild_defaults("Para", "heroId", 42)

    defaults = get_quest_guild_defaults("Para")
    assert defaults.get("last_recipe_at") == 111
    assert defaults.get("heroId") == 42


def test_set_quest_defaults_keeps_existing_keys():
    """quest_defaults への複数キー書き込みで既存の quest 設定が保持される。"""
    _register("ParaQ")
    set_quest_defaults("ParaQ", 10024, "heroId", 61)
    set_quest_defaults("ParaQ", 10028, "titanId", 4022)

    defaults = get_quest_defaults("ParaQ")
    assert defaults[10024]["heroId"] == 61
    assert defaults[10028]["titanId"] == 4022


def test_ensure_preserves_existing_last_recipe_at():
    """ensure（補完）は既存キーを保持し、実行記録（last_recipe_at）を上書きしない。

    set で last_recipe_at を記録した後に ensure を実行しても、ensure の
    補完（enabled/heroId/note）がそれらを上書きしない。
    """
    from hw_genie.commands.quests import ensure_quest_guild_defaults

    _register("ParaG")
    set_quest_guild_defaults("ParaG", "last_recipe_at", 999)
    ensure_quest_guild_defaults("ParaG")

    defaults = get_quest_guild_defaults("ParaG")
    assert defaults.get("last_recipe_at") == 999  # 既存値が保持される
    assert defaults.get("enabled") is False  # ensure による初期化も反映
    assert "recipe_runs" not in defaults
    assert "max_recipes" not in defaults  # 旧方式のキーは補完されない


def test_set_preserves_ensure_completed_defaults():
    """逆順（ensure → set）でも ensure の補完が保持される。"""
    from hw_genie.commands.quests import ensure_quest_guild_defaults

    _register("ParaG2")
    ensure_quest_guild_defaults("ParaG2")
    set_quest_guild_defaults("ParaG2", "last_recipe_at", 888)

    defaults = get_quest_guild_defaults("ParaG2")
    assert defaults.get("last_recipe_at") == 888
    assert defaults.get("enabled") is False
    assert defaults.get("heroId") == 38
    assert "max_recipes" not in defaults


# --- テスト ---


def test_execute_runs_steps_and_claims(capsys):
    """enabled=true のクエストが操作→state=2 応答→questFarm で受領される。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)

    def _op(action: ApiAction, args: dict):
        if action == ApiAction.HERO_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10024, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 10024
    assert "Reward claimed" in out
    client.quest_farm.assert_called_once_with(10024)


def test_execute_unregistered_quest_not_run(capsys):
    """QUEST_OPERATIONS 未登録のクエストは実行されない。"""
    client = _make_client([_active(10004)])
    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_execute_not_enabled_quest_skipped(capsys):
    """enabled 未設定（初期状態 false）のクエストはスキップされ起動しない。"""
    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "not enabled in quest_defaults" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_execute_step_failure_reported(capsys):
    """ステップ失敗はアカウント×クエスト×ステップで報告される。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
    client.quest_operation = MagicMock(return_value=_error_response("notEnoughStamina"))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    f = failed[0]
    assert f["account"] == "Alex"
    assert f["quest_id"] == 10024
    assert f["step"] == "heroArtifactLevelUp"
    assert "notEnoughStamina" in f["error"]


def test_execute_claim_failure_captured(capsys):
    """操作成功でも questFarm 失敗は失敗リストに入る。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
    client.quest_operation = MagicMock(
        return_value=_ok_response({"quests": [{"id": 10024, "state": 2}]})
    )
    client.quest_farm = MagicMock(return_value=_error_response("AlreadyFarmed"))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "questFarm"
    assert "reward claim failed" in out


def test_dry_run_does_not_invoke_operations(capsys):
    """dry_run はプラン表示のみで操作・受領は実行しない。"""
    client = _make_client([_active(10024), _active(10028), _active(10030)])
    _enable("Alex", 10024)
    _enable("Alex", 10028)
    _enable("Alex", 10030)
    # 10028 の shopBuy 解決用の実在庫
    client.call.return_value = _shop_inventory(
        {"18": {"reward": {"fragmentTitanArtifact": {"2001": 1}}, "cost": {"coin": {"18": 12}}}}
    )
    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "[dry-run]" in out
    assert "heroArtifactLevelUp" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_dry_run_hides_unregistered_claimable(capsys):
    """QUEST_OPERATIONS 未登録の受領待ち（バトルパス等）は dry-run に表示されない。"""
    battlepass = {"id": 2609007076, "state": 2, "progress": 1, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([battlepass, _active(10024)])
    _enable("Alex", 10024)
    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "2609007076" not in out
    assert "10024" in out


def test_confirm_prompt_skips_when_declined(monkeypatch, capsys):
    """confirm=False のとき y 以外でステップをスキップし失敗報告する。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
    client.quest_operation = MagicMock()
    monkeypatch.setattr("builtins.input", lambda _: "n")

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=False)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "heroArtifactLevelUp"
    assert "skipped by user" in failed[0]["error"]
    client.quest_operation.assert_not_called()


def test_confirm_prompt_eof_reported(monkeypatch, capsys):
    """stdin が閉じている（非TTY）場合も EOFError で落ちず失敗報告される。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
    client.quest_operation = MagicMock()

    def _raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise_eof)

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=False)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "heroArtifactLevelUp"
    assert "use --yes" in failed[0]["error"]
    assert "--yes" in out
    client.quest_operation.assert_not_called()


def test_account_default_override_applied_in_plan(capsys):
    """quest_defaults の引数上書きが dry-run のプランに反映される。"""
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "heroId", 999)

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "heroId" in out
    assert "999" in out
    assert get_quest_defaults("Alex")[10024]["heroId"] == 999


def test_claimable_quest_claimed_without_operation(capsys):
    """state=2（受領待ち）のクエストは操作せず直接 questFarm で受領する。"""
    quest = {"id": 10024, "state": 2, "progress": 1, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([quest])
    client.quest_operation = MagicMock()
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 10024
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_called_once_with(10024)
    assert "already claimable" in out or "Reward claimed" in out


def test_claimable_claim_failure_reported(capsys):
    """state=2 クエストの quest claim 失敗は失敗リストに入る。"""
    quest = {"id": 10030, "state": 2, "progress": 1, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([quest])
    client.quest_operation = MagicMock()
    client.quest_farm = MagicMock(return_value=_error_response("AlreadyFarmed"))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["quest_id"] == 10030
    assert failed[0]["step"] == "questFarm"


def test_multistep_claim_after_second_step(capsys):
    """10028 の2ステップで、2番目のステップ応答後に claim 判定される。"""
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call.return_value = _shop_inventory(
        {"18": {"reward": {"fragmentTitanArtifact": {"2001": 1}}, "cost": {"coin": {"18": 12}}}}
    )
    calls = []

    def _op(action, args):
        calls.append(action)
        if action == ApiAction.TITAN_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10028, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 10028
    # 全2ステップ実行された
    assert calls == [ApiAction.SHOP_BUY, ApiAction.TITAN_ARTIFACT_LEVEL_UP]
    assert "Reward claimed" in out


def test_shop_buy_reward_resolved_from_inventory(capsys):
    """shopBuy の reward/cost が実在庫（shopGetAll）の slot から動的解決される。

    実在庫が slot 22 → fragment 2003 の場合、コード既定の 2001 ではなく
    2003 が送信される（slot を変えるアカウントでも在庫に追従できる）。
    """
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    # 実在庫: slot 22 には fragment 2003 が売られている（既定レシピは slot 18 / 2001）
    client.call.return_value = _shop_inventory(
        {
            "10": {"reward": {"fragmentTitanArtifact": {"1017": 1}}, "cost": {"coin": {"18": 12}}},
            "22": {"reward": {"fragmentTitanArtifact": {"2003": 1}}, "cost": {"coin": {"18": 12}}},
        }
    )
    set_quest_defaults("Alex", 10028, "slot", 22)

    sent_args = []

    def _op(action, args):
        if action == ApiAction.SHOP_BUY:
            sent_args.append(dict(args))
            return _ok_response({"quests": [{"id": 10028, "state": 1}]})
        if action == ApiAction.TITAN_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10028, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert len(sent_args) == 1
    # slot 22 → 実在庫の fragment 2003 / cost に追従
    assert sent_args[0]["slot"] == 22
    assert sent_args[0]["reward"] == {"fragmentTitanArtifact": {"2003": 1}}
    assert sent_args[0]["cost"] == {"coin": {"18": 12}}


def test_shop_buy_slot_not_in_inventory_fails(capsys):
    """指定 slot が実在庫に存在しない場合は実行せず失敗報告される。

    在庫は取得できたが slot が無い場合、固定 reward で shopBuy を送信すると
    必ず NotAvailable になるため、ステップ実行前に abort する。
    """
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call.return_value = _shop_inventory(
        {"10": {"reward": {"fragmentTitanArtifact": {"1017": 1}}, "cost": {"coin": {"18": 12}}}}
    )
    set_quest_defaults("Alex", 10028, "slot", 99)  # 在庫に存在しない slot

    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    f = failed[0]
    assert f["quest_id"] == 10028
    assert f["step"] == "shopBuy"
    assert "slot 99 not in shop 13 inventory" in f["error"]
    assert "cannot execute" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_shop_buy_auth_error_reraises(capsys):
    """shopGetAll で認証エラー（HWAuthError）は握りつぶさず再送出される。"""
    import pytest

    from hw_genie.core.client import HWAuthError

    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call = MagicMock(side_effect=HWAuthError("auth failed"))

    client.quest_operation = MagicMock()

    with pytest.raises(HWAuthError):
        run_quest_execute(client, account_alias="Alex", confirm=True)
    client.quest_operation.assert_not_called()


def test_shop_buy_inventory_fetch_failure_keeps_default(capsys, caplog):
    """shopGetAll 失敗（認証以外）は既定 reward のまま動作（取得失敗を警告で知らせる）。"""
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call = MagicMock(return_value=_error_response("NetworkError"))

    sent_args = []

    def _op(action, payload):
        if action == ApiAction.SHOP_BUY:
            sent_args.append(dict(payload))
            return _ok_response({"quests": [{"id": 10028, "state": 1}]})
        if action == ApiAction.TITAN_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10028, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert len(sent_args) == 1
    assert sent_args[0]["reward"] == {"fragmentTitanArtifact": {"2001": 1}}
    assert "shopGetAll failed" in caplog.text


def test_shop_buy_shop_not_found_fails(capsys):
    """在庫取得成功でも指定 shop が存在しない場合は実行せず失敗報告される。

    従来は在庫取得失敗と区別せずフォールバックして NotAvailable になるのを、
    取得成功（=在庫データがある）と失敗を分けて即座に報告する。
    """
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    # shop 13 は在庫に無い（shop 10 のみ）
    client.call.return_value = _ok_response(
        {"response": {"10": {"slots": {"10": {"reward": {"fragmentTitanArtifact": {"1017": 1}}, "cost": {}}}}}}
    )

    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    f = failed[0]
    assert f["quest_id"] == 10028
    assert f["step"] == "shopBuy"
    assert "shop 13 not found" in f["error"]
    assert "cannot execute" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_shop_buy_slot_bought_fails(capsys):
    """指定 slot が購入済み（bought）の場合は実行せず失敗報告される（再購入不可）。"""
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call.return_value = _shop_inventory(
        {"18": {"bought": True, "reward": {"fragmentTitanArtifact": {"2001": 1}}, "cost": {"coin": {"18": 12}}}}
    )

    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    f = failed[0]
    assert f["quest_id"] == 10028
    assert f["step"] == "shopBuy"
    assert "already bought" in f["error"]
    assert "cannot execute" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_shop_inventory_cached_across_quests(monkeypatch, capsys):
    """同一 shop を使う複数クエストでも shopGetAll は実行単位で1回だけ発行される。

    キャッシュはクエスト単位ではなく run_quest_execute 全体（_resolve_shop_buy_reward
    の呼び出し間）で共有され、重複取得を排除する。
    """
    import hw_genie.commands.quests as quests_mod

    monkeypatch.setattr(
        quests_mod,
        "QUEST_OPERATIONS",
        {
            10028: quests_mod.QUEST_OPERATIONS[10028],
            99999: {
                "enabled": False,
                "steps": [
                    {"rpc": ApiAction.SHOP_BUY, "args": {"shopId": 13, "slot": 18, "amount": 1}},
                    {"rpc": ApiAction.TITAN_ARTIFACT_LEVEL_UP, "args": {"titanId": 4012, "slotId": 1}},
                ],
            },
        },
    )
    client = _make_client([_active(10028), _active(99999)])
    _enable("Alex", 10028)
    _enable("Alex", 99999)
    client.call.return_value = _shop_inventory(
        {"18": {"reward": {"fragmentTitanArtifact": {"2001": 1}}, "cost": {"coin": {"18": 12}}}}
    )
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    # shopGetAll は 2 クエストで 1 回だけ発行される
    assert client.call.call_count == 1
    assert client.call.call_args.args[0]["calls"][0]["name"] == ApiAction.SHOP_GET_ALL


def test_shop_buy_inventory_cached_per_shop(capsys):
    """同一 shop の shopBuy が複数ステップあっても shopGetAll は1回だけ発行される。"""
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call.return_value = _shop_inventory(
        {"18": {"reward": {"fragmentTitanArtifact": {"2001": 1}}, "cost": {"coin": {"18": 12}}}}
    )

    sent_args = []

    def _op(action, args):
        if action == ApiAction.SHOP_BUY:
            sent_args.append(dict(args))
            return _ok_response({"quests": [{"id": 10028, "state": 1}]})
        if action == ApiAction.TITAN_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10028, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert client.call.call_count == 1
    assert client.call.call_args.args[0]["calls"][0]["name"] == ApiAction.SHOP_GET_ALL


def test_reached_claimable_with_string_state():
    """レスポンスの state が文字列 '2' でも claim 判定される（型安全）。"""
    from hw_genie.commands.quests import _quest_reached_claimable

    resp = _ok_response({"quests": [{"id": "10024", "state": "2"}]})
    assert _quest_reached_claimable(resp, 10024) is True

    # 別クエスト・別 state では False
    resp2 = _ok_response({"quests": [{"id": "10024", "state": "1"}]})
    assert _quest_reached_claimable(resp2, 10024) is False
    resp3 = _ok_response({"quests": [{"id": "10023", "state": "2"}]})
    assert _quest_reached_claimable(resp3, 10024) is False


def test_fetch_failure_reported(capsys):
    """questGetAll 自体が失敗すると fetch 失敗として報告される。"""
    client = _make_client([])
    client.quest_get_all = MagicMock(return_value=_error_response("InvalidSession"))
    client.quest_operation = MagicMock()

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "fetch"
    assert failed[0]["error"] == "InvalidSession"
    assert "Failed to fetch quests" in out


def test_claim_not_detected_after_all_steps(capsys):
    """全ステップ成功しても対応クエストが応答に出ない場合は注記される。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "claim not detected" in out


def test_defaults_unknown_key_warns(capsys, caplog):
    """quest_defaults の未知キーは適用されず警告ログが出る。"""
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "unknownArg", 1)

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()

    run_quest_execute(client, account_alias="Alex", dry_run=True)
    capsys.readouterr().out

    assert "does not match any arg" in caplog.text
    assert "unknownArg" in caplog.text


def test_set_default_parses_string_value():
    """set_quest_defaults は CLI 由来の文字列を bool/int に解釈する。"""
    _register("Alex")
    stored_true = set_quest_defaults("Alex", 10007, "free", "true")
    stored_id = set_quest_defaults("Alex", 10024, "heroId", "61")

    defaults = get_quest_defaults("Alex")
    assert defaults[10007]["free"] is True
    assert defaults[10024]["heroId"] == 61
    # 保存値（解釈後）が返る
    assert stored_true is True
    assert stored_id == 61


def test_progress_reached_target_claimed_without_operation(capsys):
    """state=1 でも progress>=target のクエストは操作せず直接受領する。"""
    quest = {"id": 10024, "state": 1, "progress": 1, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([quest])
    client.quest_operation = MagicMock()
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 10024
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_called_once_with(10024)


def test_unknown_target_quest_not_auto_claimable(capsys):
    """target 不明（10023）は progress>=target 判定に掛からず操作対象のまま。"""
    quest = {"id": 10023, "state": 1, "progress": 0, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([quest])
    _enable("Alex", 10023)
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed, skipped = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert succeeded == []
    # 操作は実行された（target 判定に掛からないため）
    client.quest_operation.assert_called()


def test_multistep_defaults_no_false_warning(caplog):
    """マルチステップのどこかのステップに一致するキーは警告されない。"""
    _enable("Alex", 10028)
    set_quest_defaults("Alex", 10028, "titanId", 999)

    client = _make_client([_active(10028)])
    client.quest_operation = MagicMock()

    run_quest_execute(client, account_alias="Alex", dry_run=True)

    # titanId は titanArtifactLevelUp ステップの args に存在するため警告しない
    assert "does not match any arg" not in caplog.text


# --- ensure_quest_defaults ---


def test_ensure_quest_defaults_seeds_disabled_for_all():
    """初回は QUEST_OPERATIONS 全キーが enabled=false で投入される。"""
    from hw_genie.commands.quests import QUEST_OPERATIONS, ensure_quest_defaults

    _register("Alex")
    defaults = ensure_quest_defaults("Alex")

    assert set(defaults) == set(QUEST_OPERATIONS)
    for qid, conf in defaults.items():
        assert conf.get("enabled") is False


def test_ensure_quest_defaults_seeds_operation_args():
    """初期投入時に操作ステップのデフォルト引数も埋まる。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    defaults = ensure_quest_defaults("Alex")

    assert defaults[10024]["heroId"] == 61
    assert defaults[10024]["slotId"] == 1
    assert defaults[10028]["titanId"] == 4012
    assert defaults[10028]["slotId"] == 1
    assert defaults[10028]["shopId"] == 13
    assert defaults[10030]["heroId"] == 59
    assert defaults[10030]["skinId"] == 313
    assert defaults[10023]["heroId"] == 38


def test_ensure_quest_defaults_backfills_missing_args():
    """既に初期化済みのアカウントには不足キーのみ補完される（既存値は保持）。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    set_quest_defaults("Alex", 10024, "enabled", True)
    set_quest_defaults("Alex", 10024, "heroId", 777)

    defaults = ensure_quest_defaults("Alex")
    assert defaults[10024]["enabled"] is True
    assert defaults[10024]["heroId"] == 777  # 既存値は上書きしない
    assert defaults[10024]["slotId"] == 1    # 不足キーは補完
    assert defaults[10028]["enabled"] is False


def test_ensure_quest_defaults_idempotent():
    """2回目以降は書き込み不要で同じ結果が返る。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    ensure_quest_defaults("Alex")
    again = ensure_quest_defaults("Alex")
    assert again == get_quest_defaults("Alex")


def test_ensure_quest_defaults_seeds_note():
    """note に操作ステップの RPC 名が連結されて補完される。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    defaults = ensure_quest_defaults("Alex")

    assert defaults[10024]["note"] == "heroArtifactLevelUp"
    assert defaults[10028]["note"] == "shopBuy → titanArtifactLevelUp"
    assert defaults[10023]["note"] == "heroTitanGiftLevelUp → heroTitanGiftLevelUp → heroTitanGiftDrop"
    assert defaults[10007]["note"] == "gacha_open"


def test_ensure_quest_defaults_skips_dict_args():
    """dict/list 型のデフォルト引数（10028 の cost/reward 等）はバックフィルしない。

    行編集（ウィザード/--set-default）で JSON→文字列に型崩れするのを防ぐため、
    スカラー引数のみ固定値化する。
    """
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    defaults = ensure_quest_defaults("Alex")

    assert defaults[10028]["titanId"] == 4012
    assert defaults[10028]["shopId"] == 13
    assert "cost" not in defaults[10028]
    assert "reward" not in defaults[10028]


def test_parse_config_value_json_dict():
    """set-default の値文字列は dict/list を JSON 解釈で復元できる（型崩れ防止）。"""
    from hw_genie.commands.quests import _parse_config_value

    assert _parse_config_value('{"coin": {"18": 12}}') == {"coin": {"18": 12}}
    assert _parse_config_value("[1, 2, 3]") == [1, 2, 3]
    assert _parse_config_value("true") is True
    assert _parse_config_value("123") == 123
    assert _parse_config_value("1.5") == 1.5
    assert _parse_config_value("hello") == "hello"
    assert _parse_config_value("123abc") == "123abc"


def test_set_default_stores_dict_value():
    """--set-default で dict 引数（10028 の cost 等）を JSON 文字列から復元保存する。"""
    from hw_genie.commands.quests import _parse_config_value, set_quest_defaults

    _register("Alex")
    set_quest_defaults("Alex", 10028, "cost", _parse_config_value('{"coin": {"18": 12}}'))

    assert get_quest_defaults("Alex")[10028]["cost"] == {"coin": {"18": 12}}


def test_ensure_quest_defaults_preserves_existing_note():
    """既存の note は上書きされない（他のキーと同じセマンティクス）。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    set_quest_defaults("Alex", 10024, "note", "my memo")
    defaults = ensure_quest_defaults("Alex")
    assert defaults[10024]["note"] == "my memo"


def test_note_ignored_in_operation_args(caplog, capsys):
    """note は操作引数の適用・未知キー警告の対象外。"""
    _register("Alex")
    set_quest_defaults("Alex", 10024, "note", "custom memo")
    set_quest_defaults("Alex", 10024, "enabled", True)

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock(return_value=_ok_response({}))

    run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert "does not match any arg" not in caplog.text
    # 操作は実際に実行され、その args に note が混入していない
    assert client.quest_operation.call_args_list
    for call in client.quest_operation.call_args_list:
        args = call.args[1]
        assert "note" not in args


# --- main.cmd_quests（exit code / 表示） ---


def test_cmd_quests_execute_failure_exits_nonzero():
    """main.cmd_quests は execute 失敗時に exit code 1 で終了する。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    _register("Alex")

    class _Args:
        account = "Alex"
        set_default = None
        init_defaults = False
        execute = True
        dry_run = False
        yes = True

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            quests_mod,
            "run_quest_execute",
            lambda *a, **k: ([], [{"step": "fetch", "error": "boom"}], []),
        )
        with pytest.raises(SystemExit) as exc_info:
            main_mod.cmd_quests(_Args())
    assert exc_info.value.code == 1


def test_cmd_quests_execute_success_exits_zero():
    """main.cmd_quests は execute 成功時に exit code 0 で終了する。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    _register("Bob")

    class _Args:
        account = "Bob"
        set_default = None
        init_defaults = False
        execute = True
        dry_run = False
        yes = True

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            quests_mod,
            "run_quest_execute",
            lambda *a, **k: ([{"quest_id": 10024}], [], []),
        )
        assert main_mod.cmd_quests(_Args()) is None


def test_cmd_quests_edit_defaults_requires_registered_account(capsys):
    """--edit-defaults は未登録アカウントでは他オプション同様の文言で exit 1。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    class _Args:
        account = "NoSuchAlias"
        edit_defaults = True

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            quests_mod,
            "edit_quest_defaults_interactive",
            lambda acct: (_ for _ in ()).throw(AssertionError("must not be called")),
        )
        with pytest.raises(SystemExit) as exc_info:
            main_mod.cmd_quests(_Args())
    assert exc_info.value.code == 1
    assert "Session not found for account 'NoSuchAlias'" in capsys.readouterr().out


def test_cmd_quests_edit_defaults_skips_session():
    """--edit-defaults は DB 編集のみなので認証セッション（_ensure_session）が不要。

    `_ensure_session` は**呼ばれたら失敗するモック**にして、edit 分岐が
    `_ensure_session` より前で return することを実効的に検証する。
    """
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    _register("Carol")

    class _Args:
        account = "Carol"
        edit_defaults = True

    called = {"edit": False}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            main_mod,
            "_ensure_session",
            lambda args: (_ for _ in ()).throw(AssertionError("_ensure_session must not be called")),
        )
        mp.setattr(quests_mod, "edit_quest_defaults_interactive", lambda acct: called.update(edit=True))
        assert main_mod.cmd_quests(_Args()) is None
    assert called["edit"] is True


# --- 対話的編集ウィザード（edit_quest_defaults_interactive） ---


def _make_input_sequence(*answers: str):
    """与えられた順に input() が返すイテレータを作る。"""
    it = iter(answers)
    return lambda _prompt: next(it)


def test_edit_defaults_wizard_enables_quest(monkeypatch, capsys):
    """クエスト番号→enabled 番号選択で有効化できる。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "1", "1", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert get_quest_defaults("Alex")[10007]["enabled"] is True
    assert "10007" in out
    assert "Soul Atrium" in out
    assert "enabled" in out


def test_edit_defaults_wizard_sets_override_value(monkeypatch, capsys):
    """クエスト番号→引数キー番号→値入力で上書きできる。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 3 番目が 10024（10007, 10023, 10024 の順）。キー一覧で 2 番目は heroId。
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("3", "2", "999", "q"))

    edit_quest_defaults_interactive("Alex")
    capsys.readouterr().out

    assert get_quest_defaults("Alex")[10024]["heroId"] == 999


def test_edit_defaults_wizard_displays_current_values(monkeypatch, capsys):
    """設定キーの現在値が一覧に表示される。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    set_quest_defaults("Alex", 10024, "enabled", True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("3", "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "10024" in out
    assert "heroArtifactLevelUp" in out
    assert "✅ enabled" in out
    assert "heroId" in out
    assert "61" in out


def test_edit_defaults_wizard_back_and_invalid(monkeypatch, capsys):
    """無効入力は再入力を促し、b で一覧に戻れる。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 1 回目は無効な 99 → 2 回目で 10007 選択 → キー選択で b → 一覧で q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("99", "1", "b", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "Invalid choice" in out
    assert "Bye." in out


def test_edit_defaults_wizard_value_input_bq_cancels(monkeypatch, capsys):
    """値入力で b/q はキャンセルとみなされ、設定値として保存されない。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 10024 選択 → heroId キー → b（キャンセル）→ キー一覧で q → 一覧で q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("3", "2", "b", "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "b/q: cancel" in out
    assert get_quest_defaults("Alex")[10024]["heroId"] == 61  # 変更されていない


def test_edit_defaults_wizard_rejects_dict_value(monkeypatch, capsys):
    """ウィザードの値入力で JSON（dict/list）は拒否され、--set-default を案内する。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 10024 選択 → heroId キー → JSON を入力（拒否される）→ q → 一覧で q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("3", "2", '{"a": 1}', "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "dict/list 値は --set-default で指定" in out
    assert get_quest_defaults("Alex")[10024]["heroId"] == 61  # 変更されていない


def test_edit_defaults_wizard_enabled_choice_cancel(monkeypatch, capsys):
    """enabled の true/false 選択中に b でキャンセルでき、DB は変更されない。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 10007 選択 → enabled キー → true/false 選択で b → キー一覧に戻る → q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "1", "b", "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert get_quest_defaults("Alex")[10007]["enabled"] is False
    assert "saved" not in out
    assert "Invalid choice" not in out
    assert "Bye." in out


def test_edit_defaults_wizard_enabled_choice_quit(monkeypatch, capsys):
    """enabled の true/false 選択中に q でキャンセルし、次の q で終了できる。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 10007 選択 → enabled キー → true/false 選択で q（キャンセル）→ キー一覧 → q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "1", "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert get_quest_defaults("Alex")[10007]["enabled"] is False
    assert "Bye." in out


def test_edit_defaults_wizard_eof_raises_systemexit(monkeypatch, capsys):
    """非TTY（EOF）では SystemExit(1) で終了する。"""
    import pytest

    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))

    with pytest.raises(SystemExit) as exc_info:
        edit_quest_defaults_interactive("Alex")
    assert exc_info.value.code == 1
    assert "No interactive input available" in capsys.readouterr().err


def test_edit_defaults_wizard_tty_path(monkeypatch, capsys):
    """stdin+stdout とも TTY なら rich パスの選択フローが動き、クリア制御コードが出る。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "1", "1", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert get_quest_defaults("Alex")[10007]["enabled"] is True
    assert "Soul Atrium" in out
    assert "Bye." in out
    # 全画面リフレッシュ（画面クリア制御コード）が出力に含まれる
    assert "\x1b[2J" in out


def test_edit_defaults_wizard_stdout_redirect_no_clear_codes(monkeypatch, capsys):
    """stdin が TTY でも stdout が非TTY（リダイレクト）ならクリア制御コードを混入しない。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "\x1b[2J" not in out
    assert "Bye." in out


def test_edit_defaults_wizard_tty_eof_raises_systemexit(monkeypatch, capsys):
    """TTY 判定でも EOF は SystemExit(1)（_prompt_input 共用）。"""
    import pytest

    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))

    with pytest.raises(SystemExit) as exc_info:
        edit_quest_defaults_interactive("Alex")
    assert exc_info.value.code == 1
    assert "No interactive input available" in capsys.readouterr().err


def test_edit_defaults_wizard_hides_note_from_keys(monkeypatch, capsys):
    """note はキー一覧に表示されず編集対象にならない（参照専用）。"""

    from rich.console import Console

    from hw_genie.commands.quests import _key_list_table, edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    # クエスト一覧には note（操作名）が表示される
    assert "gacha_open" in out
    # キー一覧テーブルには note 行が存在しない（enabled と ident/free/pack のみ）
    conf = get_quest_defaults("Alex")[10007]
    Console().print(_key_list_table(10007, conf))
    key_out = capsys.readouterr().out
    assert "note" not in key_out
    for key in ("enabled", "ident", "free", "pack"):
        assert key in key_out, f"{key} がキー一覧にない"
