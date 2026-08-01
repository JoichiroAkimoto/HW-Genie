import os
import shutil

import pytest
from hw_genie.core.utils import (
    display_width,
    format_number_with_suffix,
    pad,
    print_player_status,
    terminal_columns,
    wrap_display,
)
from hw_genie.core.client import PlayerStatus

@pytest.mark.parametrize("num, expected", [
    (0, "0"),
    (999, "999"),
    (1000, "1.0K"),
    (1500, "1.5K"),
    (10000, "10.0K"),
    (999900, "999.9K"),
    (1000000, "1.0M"),
    (2500000, "2.5M"),
    (1234567890, "1.2B"),
    (5000000000000, "5.0T"),
])
def test_format_number_with_suffix(num, expected):
    assert format_number_with_suffix(num) == expected

def test_print_player_status(capsys):
    status = PlayerStatus(
        name="TestPlayer",
        level=120,
        arena_rank=5,
        grand_rank=10,
        energy=100,
        gold=1234567,
        gems=0,  # gems と starMoney のマッピングを確認
    )
    status.gems = 9876
    
    print_player_status(status)
    captured = capsys.readouterr()
    
    assert "👤 Name: TestPlayer (Lv.120)" in captured.out
    assert "💰 Gold: 1.2M" in captured.out
    assert "💎 Emeralds: 9.9K" in captured.out


@pytest.mark.parametrize("text, expected", [
    ("abc", 3),
    ("山田", 4),           # 全角は2幅
    ("⚡", 2),             # 絵文字も2幅
    ("⚡\ufe0f", 2),       # VS16 (幅0) を含む絵文字シーケンス
    ("10のAdam", 8),       # 数字/ASCII=1、全角=2
])
def test_display_width(text, expected):
    assert display_width(text) == expected


def test_pad_is_display_width_aware():
    assert pad("ab", 4) == "ab  "
    assert pad("山", 4) == "山  "   # 全角1文字 = 表示幅2
    assert pad("abc", 2) == "abc"   # 幅超過時は詰めない


def test_wrap_display_keeps_short_lines():
    assert wrap_display("abc def", 20) == ["abc def"]


def test_wrap_display_breaks_at_width():
    assert wrap_display("abc def ghi", 8) == ["abc def", "ghi"]


def test_wrap_display_handles_fullwidth():
    # 全角は2幅なので「あいう」= 幅6 で折り返す
    assert wrap_display("あいうえお", 6) == ["あいう", "えお"]


def test_wrap_display_preserves_newlines():
    assert wrap_display("line1\nline2", 20) == ["line1", "line2"]
    assert wrap_display("a\n\nb", 10) == ["a", "", "b"]


def test_wrap_display_drops_trailing_blank_lines():
    assert wrap_display("note\n", 20) == ["note"]
    assert wrap_display("a\n\n", 10) == ["a"]
    assert wrap_display("\n", 10) == [""]
    assert wrap_display("", 10) == [""]


def test_wrap_display_handles_crlf_and_tab():
    # CR/LF/Tab は空白として語の区切りになり、出力から除去される
    assert wrap_display("a\r\nb", 20) == ["a", "b"]
    assert wrap_display("a\tb", 20) == ["a b"]
    assert wrap_display("a\r\nb", 0) == ["a", "b"]


def test_wrap_display_hard_breaks_long_tokens():
    assert wrap_display("a" * 10, 4) == ["aaaa", "aaaa", "aa"]


def test_wrap_display_single_char_wider_than_width_does_not_loop():
    # 1 文字が列幅より広い（全角 + 幅1）ケースでも無限ループしない
    assert wrap_display("あ", 1) == ["あ"]


def test_wrap_display_never_loses_content():
    text = "10のAdamのUnity of 30個"
    joined = "".join(wrap_display(text, 10))
    # 折り返しでは改行が空白に置き換わるため、文字の欠落だけを検証する
    assert joined.replace(" ", "") == text.replace(" ", "")


def test_wrap_display_empty_and_narrow():
    assert wrap_display("", 10) == [""]
    assert wrap_display("a\nb", 0) == ["a", "b"]


def test_terminal_columns_honors_columns_env(monkeypatch):
    monkeypatch.setenv("COLUMNS", "150")
    assert terminal_columns() == 150


@pytest.mark.parametrize("value", ["0", "abc", ""])
def test_terminal_columns_ignores_invalid_columns_env(monkeypatch, value):
    """不正な COLUMNS は無視され、shutil の実測へフォールバックする。"""
    monkeypatch.setenv("COLUMNS", value)
    monkeypatch.setattr(shutil, "get_terminal_size", lambda size=(80, 24): os.terminal_size((120, 24)))
    assert terminal_columns() == 120


def test_terminal_columns_falls_back_to_shutil(monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.setattr(shutil, "get_terminal_size", lambda size=(80, 24): os.terminal_size((120, 24)))
    assert terminal_columns() == 120


def test_terminal_columns_fallback_on_error(monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.setattr(shutil, "get_terminal_size", lambda size=(80, 24): (_ for _ in ()).throw(OSError()))
    assert terminal_columns(fallback=100) == 100
