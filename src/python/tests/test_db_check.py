"""``get_data`` の壊れた JSON スキップと ``hw-genie db-check`` のテスト。

- 壊れた JSON 行が 1 行でもあると SQLAlchemy の JSON カラムはフェッチ時に
  JSONDecodeError を起こすため、``get_data`` は生 SQL で行ごとに個別パースし、
  壊れた行だけ警告してスキップする。
- ``hw-genie db-check``（``SessionRepository.check_configs``）はその検出手段。
"""

import pytest
from sqlalchemy import text

from hw_genie.core.database import get_session_local
from hw_genie.core.session_manager import SessionManager


# --- ヘルパー ---


def _register(account: str) -> None:
    SessionManager.save(
        account,
        {"player": {"id": account, "name": account}, "headers": {"x-auth-token": "test"}},
    )


def _inject_config(account: str, key: str, raw_value: str) -> None:
    """インメモリ DB に壊れた JSON（文字列）を直接注入する。

    ``update_config`` は値をシリアライズするため破損を再現できない。
    生 SQL で ``config_value`` に任意文字列を入れる。
    """
    with get_session_local()() as db:
        db.execute(
            text(
                "INSERT OR REPLACE INTO account_configs "
                "(account_id, config_key, config_value) "
                "SELECT a.id, :key, :value FROM accounts a WHERE a.alias = :account"
            ),
            {"key": key, "value": raw_value, "account": account},
        )
        db.commit()


# --- get_data のスキップ挙動 ---


def test_get_data_skips_broken_json_row():
    """壊れた quest_defaults があっても他の config（headers 等）は読み込める。"""
    _register("Alice")
    _inject_config("Alice", "quest_defaults", "{broken json!!")

    data = SessionManager.load("Alice")

    assert data["headers"] == {"x-auth-token": "test"}
    assert "quest_defaults" not in data


def test_get_data_keeps_valid_rows_next_to_broken_one():
    """壊れた行の前後にある正常な行はスキップされずに読まれる。"""
    _register("Bob")
    _inject_config("Bob", "status", '"ok"')
    _inject_config("Bob", "quest_defaults", "{broken json!!")
    _inject_config("Bob", "last_updated", '"2026-01-01T00:00:00"')

    data = SessionManager.load("Bob")

    assert data["status"] == "ok"
    assert data["last_updated"] == "2026-01-01T00:00:00"
    assert "quest_defaults" not in data


def test_get_data_warns_on_broken_row(caplog):
    """壊れた行は警告ログに記録される（サイレントにはならない）。"""
    _register("Carol")
    _inject_config("Carol", "quest_defaults", "{broken json!!")

    with caplog.at_level("WARNING", logger="hw_genie.core.repository"):
        SessionManager.load("Carol")

    assert any(
        "quest_defaults" in r.message and "broken JSON" in r.message
        for r in caplog.records
    )


def test_get_data_none_value_skipped_without_error():
    """NULL の config_value は破損扱いにせず読み飛ばす。"""
    _register("Dave")
    with get_session_local()() as db:
        db.execute(
            text(
                "INSERT INTO account_configs (account_id, config_key, config_value) "
                "SELECT a.id, 'quest_defaults', NULL FROM accounts a WHERE a.alias = 'Dave'"
            )
        )
        db.commit()

    data = SessionManager.load("Dave")

    assert "quest_defaults" not in data
    assert data["headers"] == {"x-auth-token": "test"}


def test_get_data_preserves_scalar_json_values():
    """int 等の JSON スカラー値も正しく復元する。

    ``_deserialize_config_value`` は文字列のみ ``json.loads`` を適用し、
    それ以外（生 SQL で直接書き込まれたネイティブ型など）をそのまま使う。
    """
    _register("Dave")
    SessionManager.save(
        "Dave",
        {"player": {"id": "Dave", "name": "Dave"}, "val": 1},
    )

    data = SessionManager.load("Dave")

    assert data["val"] == 1


# --- check_configs / db-check ---


def test_check_configs_empty_when_all_valid():
    """破損が無ければ空リストを返す。"""
    _register("Eve")
    SessionManager.save(
        "Eve",
        {"quest_defaults": {10024: {"enabled": True, "heroId": 61}}},
    )

    assert SessionManager.repo.check_configs() == []


def test_check_configs_detects_broken_rows():
    """壊れた行を（account, key, error）付きで検出する。"""
    _register("Frank")
    _inject_config("Frank", "quest_defaults", "{broken json!!")

    broken = SessionManager.repo.check_configs()

    assert len(broken) == 1
    assert broken[0]["account"] == "Frank"
    assert broken[0]["key"] == "quest_defaults"
    assert "property name" in broken[0]["error"].lower()


def test_check_configs_detects_multiple_accounts():
    """複数アカウントの破損をまとめて検出する。"""
    _register("Grace")
    _register("Heidi")
    _inject_config("Grace", "status", "{not json")
    _inject_config("Heidi", "headers", "[broken")

    broken = SessionManager.repo.check_configs()

    assert {b["account"] for b in broken} == {"Grace", "Heidi"}


def test_check_configs_ignores_null_values():
    """NULL 値は破損扱いにしない。"""
    _register("Ivan")
    with get_session_local()() as db:
        db.execute(
            text(
                "INSERT INTO account_configs (account_id, config_key, config_value) "
                "SELECT a.id, 'quest_defaults', NULL FROM accounts a WHERE a.alias = 'Ivan'"
            )
        )
        db.commit()

    assert SessionManager.repo.check_configs() == []


def test_db_check_exit_zero_when_clean(capsys):
    """破損が無ければ exit code 0（SystemExit を投げない）。"""
    from hw_genie.main import cmd_db_check

    _register("Judy")

    class _Args:
        account = None

    cmd_db_check(_Args())

    assert "No broken config JSON found" in capsys.readouterr().out


def test_db_check_exit_one_when_broken():
    """破損があれば exit code 1 で壊れた行を出力する。"""
    from hw_genie.main import cmd_db_check

    _register("Mallory")
    _inject_config("Mallory", "quest_defaults", "{broken json!!")

    class _Args:
        account = None

    with pytest.raises(SystemExit) as exc_info:
        cmd_db_check(_Args())

    assert exc_info.value.code == 1


def test_get_quest_defaults_survives_broken_row(caplog):
    """quests コマンドの quest_defaults 読み込みも壊れた行で落ちない。"""
    from hw_genie.commands.quests import get_quest_defaults

    _register("Nia")
    _inject_config("Nia", "quest_defaults", "{broken json!!")

    with caplog.at_level("WARNING", logger="hw_genie.core.repository"):
        defaults = get_quest_defaults("Nia")

    assert defaults == {}
    assert any("broken JSON" in r.message for r in caplog.records)


def test_check_configs_reports_stored_quest_defaults():
    """正常に保存された quest_defaults（辞書）は破損として誤検出しない。"""
    _register("Oscar")
    stored = SessionManager.save(
        "Oscar",
        {"quest_defaults": {10024: {"enabled": True, "note": "heroArtifactLevelUp"}}},
    )
    assert stored is None  # save は None を返す（update_config ラッパー）

    # 保存された行の JSON が有効であることを確認してから検出関数を試す
    assert SessionManager.repo.check_configs() == []


def test_db_check_output_lists_broken_details(capsys):
    """壊れた行の詳細（account/key/error）が出力される。"""
    from hw_genie.main import cmd_db_check

    _register("Peggy")
    _inject_config("Peggy", "quest_defaults", "{broken json!!")

    class _Args:
        account = None

    with pytest.raises(SystemExit):
        cmd_db_check(_Args())

    out = capsys.readouterr().out
    assert "1 broken config JSON row(s)" in out
    assert "account=Peggy" in out
    assert "key=quest_defaults" in out
    assert "error=" in out


def test_set_quest_defaults_repairs_broken_row():
    """``--set-default`` 経路（set_quest_defaults → update_config）で壊れた行を修復できる。

    ``_upsert_config`` は生 SQL の ``INSERT ... ON CONFLICT DO UPDATE`` で
    上書きするため、破損行を SELECT せずに置き換えられる（ORM 経路だと
    JSONDecodeError で修復が不可能）。
    """
    from hw_genie.commands.quests import get_quest_defaults, set_quest_defaults

    _register("Ruth")
    _inject_config("Ruth", "quest_defaults", "{broken json!!")

    set_quest_defaults("Ruth", 10024, "enabled", True)

    defaults = get_quest_defaults("Ruth")
    assert defaults == {10024: {"enabled": True}}
    assert SessionManager.repo.check_configs() == []


def test_update_config_repairs_broken_row():
    """``update_config`` 直接呼び出しでも壊れた行を修復できる。"""
    _register("Steve")
    _inject_config("Steve", "status", "{broken json!!")

    SessionManager.repo.update_config("Steve", {"status": "ok"})

    assert SessionManager.load("Steve")["status"] == "ok"
    assert SessionManager.repo.check_configs() == []
