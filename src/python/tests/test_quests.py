"""``hw_genie.commands.quests`` のテスト。

モックデータは VitaminD / Champion の実レスポンス（questGetAll）の一部を元に
作成している。ID・state・progress・報酬の形状は実際の API レスポンスに従う。
"""

import json
from unittest.mock import MagicMock

from hw_genie.commands.quests import (
    classify_quest,
    format_create_time,
    format_reward,
    parse_quests,
    run_quest_status,
)
from hw_genie.core.client import HWClient, ResponseStatus


# --- 実レスポンスを模したモックデータ ---
MOCK_QUESTS = [
    {"id": 10004, "state": 1, "progress": 2, "reward": {"consumable": {"56": 1}, "gold": 6400}, "createTime": 1783046661, "farmCount": 0},
    {"id": 10006, "state": 1, "progress": 0, "reward": {"consumable": {"56": 1}, "gold": 4500}, "createTime": 1783046661, "farmCount": 0},
    {"id": 10007, "state": 1, "progress": 0, "reward": {"consumable": {"56": 1}, "gold": 6000}, "createTime": 1783046661, "farmCount": 0},
    {"id": 10024, "state": 2, "progress": 1, "reward": {"consumable": {"45": 1}}, "createTime": 1783046661, "farmCount": 0},
    {"id": 10028, "state": 2, "progress": 1, "reward": {"consumable": {"53": 400}}, "createTime": 1783046661, "farmCount": 0},
    {"id": 10030, "state": 2, "progress": 1, "reward": {"coin": {"4": 100}}, "createTime": 1783046661, "farmCount": 0},
    {"id": 10033, "state": 1, "progress": 0, "reward": {"consumable": {"81": 15}, "dungeonActivity": 150}, "createTime": 1783046661, "farmCount": 0},
    {"id": 20000010, "state": 1, "progress": 12, "reward": {"clanQuestsPoints": 4, "prestige": 20}, "createTime": 1783046661, "farmCount": 0},
    {"id": 20010002, "state": 2, "progress": 948, "reward": {"stamina": 200}, "createTime": 1783046661, "farmCount": 0},
    {"id": 232010, "state": 1, "progress": 1900, "reward": {"coin": {"24": "500"}}, "createTime": 1783046661, "farmCount": 0},
    {"id": 398703, "state": 1, "progress": 0, "reward": {"consumable": {"215": "3"}}, "createTime": 1783046661, "farmCount": 0},
    {"id": "2609007064", "state": 1, "progress": 1, "reward": {"battlePassExp": {"2608000109": 350}}, "createTime": 1783046661, "farmCount": 0},
    {"id": 11004, "state": 1, "progress": 0, "reward": {"consumable": {"172": 2, "173": 1}}, "createTime": 1783046661, "farmCount": 0, "order": 1},
    {"id": 784, "state": 1, "progress": 0, "reward": {"starmoney": 50}, "createTime": 1705763533, "farmCount": 0},
]


def _make_client(quests: list[dict] | None = None) -> HWClient:
    client = HWClient(headers={"x-auth-token": "test"})
    res = MagicMock()
    res.status = ResponseStatus.SUCCESS
    res.error_name = None
    res.detail = {"response": quests if quests is not None else MOCK_QUESTS}
    client.quest_get_all = MagicMock(return_value=res)
    return client


# --- parse_quests ---


def test_parse_quests_normalizes_fields():
    quests = parse_quests(MOCK_QUESTS)
    assert len(quests) == 14

    by_id = {q.id: q for q in quests}
    # id が int/str 混在でも int に正規化される
    assert by_id[2609007064].name.startswith("Battle Pass")
    assert by_id[10004].progress == 2
    assert by_id[10004].state == 1
    assert by_id[10024].is_completed
    assert by_id[10004].reward == {"consumable": {"56": 1}, "gold": 6400}
    # マスタから target が引き継がれる
    assert by_id[10004].target == 3
    assert by_id[20000010].target is None


def test_parse_quests_ignores_invalid_entries():
    assert parse_quests("not-a-list") == []
    assert parse_quests([{"state": 1}, None, 42, {"id": "x"}]) == []


# --- classify_quest ---


def test_classify_quest_master_entries():
    category, name = classify_quest(10004)
    assert category == "daily"
    assert "Arena" in name


def test_classify_quest_family_rules():
    assert classify_quest(20000010)[0] == "guild"
    assert classify_quest(20010002)[0] == "guild"
    assert classify_quest(11004)[0] == "weekly"
    assert classify_quest(232010)[0] == "main"
    assert classify_quest(398703)[0] == "event"
    assert classify_quest(2609007064)[0] == "battlepass"
    assert classify_quest(2735007330)[0] == "battlepass"
    assert classify_quest(784)[0] == "one_time"
    assert classify_quest(30658)[0] == "one_time"


# --- format helpers ---


def test_format_reward_nested_dict():
    assert format_reward({"starmoney": 50}) == "starmoney 50"
    assert format_reward({"consumable": {"56": 1}, "gold": 6400}) == "consumable[56×1] + gold 6400"
    assert format_reward(None) == "-"
    assert format_reward({}) == "-"


def test_format_create_time(monkeypatch):
    # エポック秒を表示タイムゾーン（既定 UTC）の ISO に変換する
    monkeypatch.setenv("HWGENIE_TZ", "UTC")
    out = format_create_time(1705763533)
    assert out.startswith("2024-01-20 15:12:13")
    # HWGENIE_TZ に従って変換される
    monkeypatch.setenv("HWGENIE_TZ", "Asia/Tokyo")
    assert format_create_time(1705763533).startswith("2024-01-21 00:12:13")
    monkeypatch.delenv("HWGENIE_TZ")
    assert format_create_time(0) == "-"


# --- run_quest_status ---


def test_run_quest_status_default_shows_only_uncompleted(capsys):
    client = _make_client()
    quests = run_quest_status(client, account_alias="VitaminD")
    out = capsys.readouterr().out

    # 未完了デイリーが名称付きで表示される
    assert "Fight 3 times in the Arena or Grand Arena" in out
    assert "10004" in out
    # 完了済み（state=2）はデフォルトで非表示
    assert "Level up any Hero's Artifact 1 time" not in out
    assert "10024" not in out
    # カテゴリ別表示
    assert "Daily Quests" in out
    assert "Guild Quests" in out
    # 戻り値は全件
    assert len(quests) == len(MOCK_QUESTS)


def test_run_quest_status_show_all_includes_completed(capsys):
    client = _make_client()
    run_quest_status(client, account_alias="VitaminD", show_all=True)
    out = capsys.readouterr().out
    assert "Level up any Hero's Artifact 1 time" in out
    assert "✅" in out


def test_run_quest_status_category_filter(capsys):
    client = _make_client()
    run_quest_status(client, account_alias="VitaminD", category="daily")
    out = capsys.readouterr().out
    assert "Daily Quests" in out
    assert "Guild Quests" not in out
    assert "20000010" not in out


def test_run_quest_status_raw(capsys):
    client = _make_client()
    run_quest_status(client, account_alias="VitaminD", raw=True)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert payload[0]["id"] == 10004


def test_run_quest_status_failure(capsys):
    client = HWClient(headers={"x-auth-token": "test"})
    res = MagicMock()
    res.status = ResponseStatus.ERROR
    res.error_name = "InvalidSession"
    client.quest_get_all = MagicMock(return_value=res)
    quests = run_quest_status(client, account_alias="VitaminD")
    out = capsys.readouterr().out
    assert quests == []
    assert "Failed to fetch quests" in out
