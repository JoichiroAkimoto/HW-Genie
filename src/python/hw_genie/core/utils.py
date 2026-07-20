import os

from datetime import datetime, timezone

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
