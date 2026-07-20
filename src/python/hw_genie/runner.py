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
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Sequence

from hw_genie.core.client import HWClient, load_session_headers
from hw_genie.core.session_manager import SessionManager

logger = logging.getLogger(__name__)


def list_account_aliases() -> list[str]:
    """Return all registered account aliases sorted for stable ordering."""
    return sorted(SessionManager.list_accounts())


def _resolve_max_parallel(
    max_parallel: int | None, account_count: int
) -> int:
    """Compute the effective worker count.

    ``HWDA_MAX_PARALLEL`` (and callers passing ``max_parallel``) control the
    concurrency ceiling. A value <= 0 means "unbounded" (clamped to the number
    of accounts). At least one worker is always returned.
    """
    if max_parallel is None:
        max_parallel = int(_env_int("HWDA_MAX_PARALLEL", 0))
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
        max_parallel: Concurrency limit. Defaults to ``HWDA_MAX_PARALLEL`` /
            unbounded.

    Returns:
        Mapping of ``account -> (result, error)``. Entries with an error are the
        accounts that failed (the exception object is provided for reporting).
    """
    if accounts is None:
        accounts = list_account_aliases()

    if not accounts:
        logger.warning("No accounts found; nothing to run.")
        return {}

    workers = _resolve_max_parallel(max_parallel, len(accounts))
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

    return results


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


def _render_status_row(account: str, result: object) -> str:
    """Format one account's PlayerStatus (or error) as a table row."""
    from hw_genie.core.client import PlayerStatus
    from hw_genie.core.utils import format_number_with_suffix

    if isinstance(result, PlayerStatus):
        return (
            f"  {account:<18} | Lv.{result.level:<4} | "
            f"⚡{result.energy_text:<12} | 🏆{result.arena_rank:<5} | "
            f"👑{result.grand_rank:<5} | 💰{format_number_with_suffix(result.gold):<7} | "
            f"💎{format_number_with_suffix(result.gems)}"
        )
    return f"  {account:<18} | (status unavailable)"


def summarize(results: Iterable[tuple[str, tuple[object | None, BaseException | None]]]) -> int:
    """Print a per-account status table to stdout and return failed count."""
    ok = 0
    failed: list[str] = []
    rows: list[str] = []
    for account, (res, err) in results:
        if err is None:
            ok += 1
            rows.append(_render_status_row(account, res))
        else:
            failed.append(account)

    # Separator so the table stands out from the per-account progress logs.
    print("\n" + "=" * 64)
    print("📊 --- Multi-account summary ---")
    if rows:
        print(f"  {'Account':<18} | {'Lv':<6} | {'Energy':<12} | {'Arena':<5} | "
              f"{'GA':<5} | {'Gold':<7} | {'Gems'}")
        print("  " + "-" * 60)
        print("\n".join(rows))
    if failed:
        print("-" * 64)
        print(f"❌ Failed ({len(failed)}): {', '.join(failed)}")
    print("=" * 64)
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
