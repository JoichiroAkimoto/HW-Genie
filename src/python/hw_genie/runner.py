"""Single-process multi-account parallel execution.

This module powers :mod:`bin.hwda` / :mod:`bin.hwsa` style bulk runs without
spawning one OS process per account. Running every account's routine inside a
single Python process means all libSQL connections share one process, so the
Embedded Replica (local file + Turso Syncs) can be opened concurrently without
the ``wal_insert_begin failed`` WAL contention that occurs when multiple
*separate* processes race to write the same local replica file (see issue #47).

The workload is I/O bound (network waits dominate), so a thread pool is enough
for good concurrency and keeps each account's work isolated behind its own
``try/except`` so one account's failure cannot abort the others.
"""

import logging
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Sequence

from hw_genie.core.client import HWClient, load_session_headers
from hw_genie.core.session_manager import SessionManager
from hw_genie.core.utils import display_width, pad, rank_color, style

logger = logging.getLogger(__name__)


def list_account_aliases() -> list[str]:
    """Return all registered account aliases in registration order.

    ``list_accounts`` already orders by id (the rowid alias), so multi runs
    and hwda/hwsa execute accounts in the order they were registered.
    """
    return SessionManager.list_accounts()


def resolve_max_parallel(
    max_parallel: int | None, account_count: int
) -> int:
    """Compute the effective worker count.

    ``HW_MAX_PARALLEL`` (and callers passing ``max_parallel``) control the
    concurrency ceiling. A value <= 0 means "unbounded" (clamped to the number
    of accounts). At least one worker is always returned.
    """
    if max_parallel is None:
        max_parallel = int(_env_int("HW_MAX_PARALLEL", 0))
    if max_parallel <= 0:
        return max(account_count, 1)
    return min(max_parallel, max(account_count, 1))


def _env_int(name: str, default: int) -> int:
    try:
        return int(__import__("os").environ.get(name, default))
    except (TypeError, ValueError):
        return default


def run_for_account(
    account: str,
    routine: Callable[[HWClient, str], object],
) -> tuple[str, object | None, BaseException | None]:
    """Execute ``routine(client, account)`` for a single account.

    Returns ``(account, result, error)``. Any exception is captured (not
    raised) so callers can decide how to report failures while keeping the
    other accounts running.
    """
    headers = load_session_headers(account)
    if not headers:
        err = RuntimeError(f"Session not found for account '{account}'.")
        logger.error("%s: %s", account, err)
        return account, None, err

    try:
        client = HWClient(headers)
        result = routine(client, account)
        return account, result, None
    except Exception as exc:  # noqa: BLE001 - isolate per-account failures
        logger.error(
            "Account '%s' failed: %s\n%s",
            account,
            exc,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        return account, None, exc


def run_all_accounts(
    routine: Callable[[HWClient, str], object],
    accounts: Sequence[str] | None = None,
    max_parallel: int | None = None,
) -> dict[str, tuple[object | None, BaseException | None]]:
    """Run ``routine`` for every registered account inside one process.

    Args:
        routine: Callable taking ``(client, account_alias)``.
        accounts: Explicit account list. When ``None`` every registered account
            is used.
        max_parallel: Concurrency limit. Defaults to ``HW_MAX_PARALLEL`` /
            unbounded.

    Returns:
        Mapping of ``account -> (result, error)``. Entries with an error are the
        accounts that failed (the exception object is provided for reporting).
    """
    if accounts is None:
        accounts = list_account_aliases()
    # 下記の dict comprehension で 2 回反復するため、ジェネレータ混入を防御
    accounts = list(accounts)

    if not accounts:
        logger.warning("No accounts found; nothing to run.")
        return {}

    workers = resolve_max_parallel(max_parallel, len(accounts))
    logger.info(
        "Running routine for %d account(s) (parallel, max %d)...",
        len(accounts),
        workers,
    )

    results: dict[str, tuple[object | None, BaseException | None]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_for_account, acc, routine): acc for acc in accounts
        }
        for fut in as_completed(futures):
            acc, res, err = fut.result()
            results[acc] = (res, err)

    # 完了順ではなく投入順（= 登録順）で返す。dict は挿入順を保持するため、
    # summarize などの呼び出し側はそのまま並び順を表示に使える。
    return {acc: results[acc] for acc in accounts}


# --- Convenience routines usable with run_all_accounts / run_for_account ---


def daily_routine(client: HWClient, account: str) -> object:
    """Run the full daily routine for ``account``.

    Returns the final :class:`PlayerStatus` so the runner can render a
    per-account summary table (see :func:`summarize`).

    Unlike the single-account CLI (which requires a ``--curl`` payload to know
    the item-raid mission), this builds an item-raid payload from the mission
    id stored in the DB (``SessionManager.build_item_raid_payload``) so the
    item raid actually runs to the stamina limit inside ``run_daily_raid``.
    """
    from hw_genie.commands.daily_raid import run_daily_raid
    from hw_genie.core.session_manager import SessionManager

    # The payload shape (calls/ident/context) is owned by SessionManager; the
    # runner just consumes it. Returns None when no mission id is configured.
    item_payload = SessionManager.build_item_raid_payload(account=account)

    run_daily_raid(client, item_payload=item_payload, account_alias=account)
    # Fetch the latest status for the summary table. run_daily_raid already
    # prints it, but returning it lets multi-account runs show a consolidated
    # view at the end.
    try:
        return client.fetch_player_status()
    except Exception:  # pragma: no cover - best-effort, never abort summary
        logger.error("Failed to fetch final status for account '%s'.", account)
        return None


def full_routine(client: HWClient, account: str) -> object:
    """Run raid-hero + shop + daily, the equivalent of ``bin/hwsa``."""
    from hw_genie.commands.hero_raid import run_hero_raid
    from hw_genie.commands.hero_shopping import TARGET_SHOP_IDS, run_hero_shopping

    hero_res, _, _ = run_hero_raid(client, None, times=3, allow_recovery=True)
    run_hero_shopping(
        client, buy_soul_shop_items=True, hero_shop_ids=TARGET_SHOP_IDS
    )
    client.exchange_stones()
    return daily_routine(client, account)


def _status_cells(account: str, result: object) -> list[str] | None:
    """Return the column cells for one account, or None if status is unavailable."""
    from hw_genie.core.client import PlayerStatus
    from hw_genie.core.utils import format_number_with_suffix

    if not isinstance(result, PlayerStatus) or not result.is_valid:
        return None
    return [
        account,
        result.energy_text,
        str(result.arena_rank),
        str(result.grand_rank),
        format_number_with_suffix(result.gold),
        format_number_with_suffix(result.gems),
    ]


# Column headers (emoji-prefixed so each column is self-labeling and compact).
_SUMMARY_HEADERS = ["Account", "⚡Energy", "🏆Arena", "👑GA", "💰Gold", "💎Gems"]

# Display-width-aware padding shared with ``auth --list`` (utils.py). The old
# fixed emoji set (_WIDE_CHARS, all EAW-wide) is replaced by the East Asian
# Width classification, which additionally counts Japanese full-width as 2 and
# combining/format chars (VS16, ZWJ) as 0 instead of 1 — those aliases keep the
# renderer and its tests source-compatible.
_display_width = display_width
_pad = pad


def _energy_over_max(cell: str) -> bool:
    """True when an ``E / MAX`` energy cell shows energy above the cap."""
    m = re.match(r"(\d+)\s*/\s*(\d+)", cell)
    return bool(m) and int(m.group(1)) > int(m.group(2))


def _cell_int(cell: str) -> int | None:
    """Parse an int cell; None for placeholder cells like ``-``."""
    try:
        return int(cell)
    except ValueError:
        return None


def _summary_table_layout(rows: list[list[str]]) -> tuple[list[int], int]:
    """Shared column layout: ``(widths, rule_width)`` for the summary table.

    Widths are display-width based (emoji double-width, combining chars 0).
    ``summarize`` uses ``rule_width`` to align its own separator lines with
    the table borders.
    """
    headers = _SUMMARY_HEADERS
    widths = [
        max([_display_width(headers[i]), *(_display_width(r[i]) for r in rows)])
        for i in range(len(headers))
    ]
    plain_header = " | ".join(_pad(h, widths[i]) for i, h in enumerate(headers))
    return widths, _display_width(plain_header)


def _render_summary_table(rows: list[list[str]]) -> str:
    """Render the per-account table with widths derived from the actual content."""
    if not rows:
        return ""
    headers = _SUMMARY_HEADERS
    widths, rule_width = _summary_table_layout(rows)
    # 幅計算はプレーン文字列で行い、パディング後にスタイルを後付けする
    plain_header = " | ".join(_pad(h, widths[i]) for i, h in enumerate(headers))
    header_line = style(plain_header, bold=True, fg="cyan")
    body_lines = []
    for row_idx, row in enumerate(rows):
        # アカウント行のゼブラ: 偶数番目の行を全体 dim にして行を区切る
        dim_row = row_idx % 2 == 1
        cells = []
        for i, cell in enumerate(row):
            padded = _pad(cell, widths[i])
            if i == 0:
                padded = style(padded, bold=True, dim=dim_row)
            elif i == 1:
                padded = (
                    style(padded, fg="red")
                    if _energy_over_max(cell)
                    else style(padded, dim=dim_row)
                )
            elif i in (2, 3):
                color = rank_color(_cell_int(cell))
                # 色付きセルはゼブラでも dim しない（色を保つ）
                padded = style(padded, fg=color, dim=dim_row and not color)
            else:
                padded = style(padded, dim=dim_row)
            cells.append(padded)
        body_lines.append(" | ".join(cells))
    sep = style("=" * rule_width, dim=True)
    rule = style("-" * rule_width, dim=True)
    return "\n".join([sep, header_line, rule, *body_lines, sep])


def summarize(results: Iterable[tuple[str, tuple[object | None, BaseException | None]]]) -> int:
    """Print a per-account status table to stdout and return failed count."""
    ok = 0
    failed: list[str] = []
    rows: list[list[str]] = []
    for account, (res, err) in results:
        if err is None:
            cells = _status_cells(account, res)
            if cells is not None:
                ok += 1
                rows.append(cells)
            else:
                failed.append(f"{account} (status unavailable)")
        else:
            failed.append(account)

    # テーブルと同じ幅で見出し・失敗一覧の罫線を引く（rows が無い場合は固定幅）
    width = _summary_table_layout(rows)[1] if rows else 48

    # Separator so the table stands out from the per-account progress logs.
    print("\n" + "=" * width)
    print("📊 --- Multi-account summary ---")
    if rows:
        print(_render_summary_table(rows))
    if failed:
        print("-" * width)
        print(f"❌ Failed ({len(failed)}): {', '.join(failed)}")
    print("=" * width)
    print(f"✅ {ok} account(s) completed, ❌ {len(failed)} failed.\n")
    return len(failed)


__all__ = [
    "list_account_aliases",
    "run_for_account",
    "run_all_accounts",
    "daily_routine",
    "full_routine",
    "summarize",
]
