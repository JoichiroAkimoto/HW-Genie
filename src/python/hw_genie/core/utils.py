import os
import shutil
import sys
import unicodedata

from datetime import datetime, timezone

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ANSI SGR 基本色（旧端末でも安全な 8 色のみ使用）
_FG_CODES = {
    "red": 31,
    "green": 32,
    "yellow": 33,
    "cyan": 36,
}


def supports_color(stream=None) -> bool:
    """Whether ANSI color codes should be emitted for ``stream`` (default stdout).

    Disabled when the stream is not a TTY (e.g. piped into hwda logs), when
    ``NO_COLOR`` is set (industry convention), or when ``TERM=dumb``.
    """
    if stream is None:
        stream = sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def style(text: str, *, bold: bool = False, dim: bool = False, fg: str | None = None) -> str:
    """Wrap ``text`` in ANSI SGR codes, composing all flags into one sequence.

    Returns ``text`` unchanged when color is disabled, so callers can apply
    styling unconditionally. Style the PADDED plain string (as the renderers
    do) so the width helpers never see escape codes.
    """
    if not supports_color():
        return text
    codes = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if fg:
        codes.append(str(_FG_CODES[fg]))
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


def rank_color(rank: int | None) -> str | None:
    """Color for an arena/GA rank: 1 → gold(yellow), 2-14 → green, else None."""
    if isinstance(rank, int) and rank > 0:
        if rank == 1:
            return "yellow"
        if rank <= 14:
            return "green"
    return None


def energy_over_cap(level: int | None, energy: int | None) -> bool:
    """True when ``energy`` exceeds the auto-regen cap (``level + 60``).

    Auto-recovery stops once energy is above the cap, so this is a "stuck"
    state worth highlighting red. Mirrors ``PlayerStatus.max_energy``.
    Unknown level/energy → False.
    """
    if level is None or energy is None:
        return False
    return energy > int(level) + 60


def format_number_with_suffix(num: int) -> str:
    """数値を K, M, B, T などの接尾辞付きでフォーマットする"""
    if num < 1000:
        return str(num)
    
    suffixes = ["", "K", "M", "B", "T"]
    magnitude = 0
    num_float = float(num)
    
    while abs(num_float) >= 1000 and magnitude < len(suffixes) - 1:
        magnitude += 1
        num_float /= 1000.0
        
    return f"{num_float:.1f}{suffixes[magnitude]}"


def display_timezone_name() -> str:
    """Return the configured display timezone label for column headers.

    Honors the ``HWGENIE_TZ`` env var (IANA name such as ``Asia/Tokyo``) and
    returns that name verbatim. When unset or invalid, returns ``"UTC"``. This
    is the single source of truth for the tz label shown by ``auth --list``.
    """
    tz_env = os.environ.get("HWGENIE_TZ")
    if not tz_env:
        return "UTC"
    try:
        ZoneInfo(tz_env)  # validate
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"
    return tz_env


def format_timestamp_for_display(iso: str) -> str:
    """Convert a stored UTC ISO timestamp to the configured display timezone.

    Stored timestamps are UTC (``datetime.now(timezone.utc).isoformat()``).
    Returns a compact ``YYYY-MM-DD HH:MM:SS`` string in the timezone named by
    ``HWGENIE_TZ`` (default UTC), with no timezone suffix (the caller is
    expected to label the column once). Invalid/missing values pass through.
    """
    if not iso or iso == "Never":
        return iso
    try:
        # Tolerate both "2026-07-20T04:55:42" and "...+00:00" forms.
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz_env = os.environ.get("HWGENIE_TZ")
    if tz_env:
        try:
            dt = dt.astimezone(ZoneInfo(tz_env))
        except (ZoneInfoNotFoundError, ValueError):
            dt = dt.astimezone(timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _char_width(ch: str) -> int:
    """Terminal display width of a single character.

    - Wide/full-width East Asian (W/F: Japanese, most emoji) -> 2 columns.
    - Combining marks (Mn/Me) and format chars (Cf: ZWJ U+200D, LRM, BOM) -> 0.
    - Ambiguous (A) characters (``±``, ``→`` ...) -> 2, matching CJK terminals
      where they render full-width (the user-facing default
      ``HWGENIE_TZ=Asia/Tokyo`` context). ``wcwidth`` counts them as 1, so on
      Western terminals long runs of A chars wrap one column early; this is a
      deliberate, documented trade-off.
    """
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F", "A"):
        return 2
    return 1


def display_width(text: str) -> int:
    """Terminal display width of ``text`` (double-width chars count as 2)."""
    return sum(_char_width(ch) for ch in text)


def pad(text: str, width: int) -> str:
    """Left-justify ``text`` to ``width`` display columns (width-aware)."""
    return text + " " * max(0, width - display_width(text))


def wrap_display(text: str, width: int) -> list[str]:
    """Wrap ``text`` to ``width`` display columns, preserving existing newlines.

    CR/LF/tab are normalized away (CRLF -> LF, tabs are word separators);
    tokens longer than the column are hard-broken so nothing is ever lost.
    Trailing blank lines (from a trailing newline) are dropped. Always
    returns at least one line.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if width < 1:
        lines = text.split("\n")
        _drop_trailing_empty_lines(lines)
        return lines
    out: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            if not line or display_width(line) + 1 + display_width(word) <= width:
                line = (line + " " + word) if line else word
            else:
                out.append(line)
                line = word
            while display_width(line) > width:
                chunk, line = _cut(line, width)
                out.append(chunk)
        out.append(line)
    _drop_trailing_empty_lines(out)
    return out


def _drop_trailing_empty_lines(lines: list[str]) -> None:
    """Remove trailing empty entries, always keeping at least one line."""
    while len(lines) > 1 and lines[-1] == "":
        lines.pop()


def _cut(text: str, width: int) -> tuple[str, str]:
    """Split ``text`` at ``width`` display columns; always advances by 1 char."""
    if not text:
        return "", ""
    acc = 0
    for i, ch in enumerate(text):
        if acc + _char_width(ch) > width:
            if i > 0:
                return text[:i], text[i:]
            # A single char is wider than the column: take it anyway to
            # guarantee progress (no infinite loop).
            return text[0], text[1:]
        acc += _char_width(ch)
    return text, ""


def terminal_columns(fallback: int = 100) -> int:
    """Number of terminal columns, honoring ``COLUMNS`` then the OS.

    Returns ``fallback`` when the size cannot be determined or is not a
    usable positive value.
    """
    env = os.environ.get("COLUMNS")
    if env:
        try:
            cols = int(env)
            if cols > 0:
                return cols
        except ValueError:
            pass
    try:
        cols = shutil.get_terminal_size((fallback, 24)).columns
    except Exception:
        return fallback
    return cols if cols > 0 else fallback

def print_player_status(status):
    """
    プレイヤー情報を標準出力に表示する。
    Args:
        status (PlayerStatus): PlayerStatus インスタンス
    """
    gold_str = format_number_with_suffix(status.gold)
    gems_str = format_number_with_suffix(status.gems)

    print("\n📊 --- Account Status ---")
    print(f"  👤 Name: {status.name} (Lv.{status.level})")
    print(f"  🏆 Arena Rank: {status.arena_rank}")
    print(f"  👑 Grand Rank: {status.grand_rank}")
    print(f"  ⚡️ Energy: {status.energy_text}")
    print(f"  💰 Gold: {gold_str}")
    print(f"  💎 Emeralds: {gems_str}")
