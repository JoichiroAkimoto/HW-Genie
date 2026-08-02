import os
import shutil

import pytest
from hw_genie.core.utils import (
    display_width,
    energy_over_cap,
    format_number_with_suffix,
    pad,
    print_player_status,
    rank_color,
    style,
    supports_color,
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


class _FakeStream:
    def __init__(self, isatty_value):
        self._isatty = isatty_value

    def isatty(self):
        return self._isatty


def test_supports_color_requires_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert not supports_color(_FakeStream(False))
    assert supports_color(_FakeStream(True))


def test_supports_color_disabled_by_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert not supports_color(_FakeStream(True))


def test_supports_color_forced_by_force_color(monkeypatch):
    """FORCE_COLOR は非 TTY ストリームでも色を有効化する（hwda/hwsa 用の opt-in）。"""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert supports_color(_FakeStream(False))
    assert supports_color(_FakeStream(True))


def test_supports_color_no_color_wins_over_force_color(monkeypatch):
    """NO_COLOR は FORCE_COLOR より優先される（ユーザー明示の無効化を尊重）。"""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert not supports_color(_FakeStream(True))


def test_supports_color_disabled_by_dumb_term(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert not supports_color(_FakeStream(True))


def test_style_is_noop_when_color_disabled(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert style("abc", bold=True, fg="cyan") == "abc"


def test_style_composes_codes(monkeypatch):
    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    assert style("abc", bold=True, fg="cyan") == "\033[1;36mabc\033[0m"
    assert style("abc", dim=True) == "\033[2mabc\033[0m"
    assert style("abc") == "abc"


@pytest.mark.parametrize("rank, expected", [
    (1, "yellow"),
    (2, "green"),
    (14, "green"),
    (15, None),
    (0, None),
    (None, None),
])
def test_rank_color(rank, expected):
    assert rank_color(rank) == expected


@pytest.mark.parametrize("level, energy, expected", [
    (130, 190, False),   # 上限ちょうどは赤ではない
    (130, 191, True),    # 上限超過（自動回復停止）で赤
    (130, 39, False),    # 低スタミナは色付けなし
    (None, 500, False),  # レベル不明は判定不能
    (130, None, False),
    ("130", "191", True),  # 数値文字列も正規化して判定
    ("abc", 191, False),   # 非数値は False
])
def test_energy_over_cap(level, energy, expected):
    assert energy_over_cap(level, energy) == expected


def test_max_energy_single_source_matches_player_status():
    """上限式（level + 60）は utils と PlayerStatus で同一ソース。"""
    from hw_genie.core.utils import max_energy_for_level
    from hw_genie.core.client import PlayerStatus

    for level in (1, 60, 130, 250):
        assert PlayerStatus(level=level).max_energy == max_energy_for_level(level)
