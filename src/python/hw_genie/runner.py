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
    """Run the full daily routine for ``account``."""
    from hw_genie.commands.daily_raid import run_daily_raid

    # run_daily_raid accepts a client and resolves the item-raid mission id
    # internally from the DB, so no item_payload is required here.
    return run_daily_raid(client, item_payload=None, account_alias=account)


def full_routine(client: HWClient, account: str) -> object:
    """Run raid-hero + shop + daily, the equivalent of ``bin/hwsa``."""
    from hw_genie.commands.hero_raid import run_hero_raid
    from hw_genie.commands.hero_shopping import TARGET_SHOP_IDS, run_hero_shopping

    hero_res, _, _ = run_hero_raid(client, None, times=3, allow_recovery=True)
    run_hero_shopping(
        client, buy_soul_shop_items=True, hero_shop_ids=TARGET_SHOP_IDS
    )
    client.exchange_stones()
    daily_routine(client, account)
    return hero_res


def summarize(results: Iterable[tuple[str, tuple[object | None, BaseException | None]]]) -> int:
    """Log a summary and return the number of failed accounts."""
    ok = 0
    failed: list[str] = []
    for account, (_, err) in results:
        if err is None:
            ok += 1
        else:
            failed.append(account)

    if failed:
        logger.warning(
            "%d account(s) succeeded, %d failed: %s",
            ok,
            len(failed),
            ", ".join(failed),
        )
    else:
        logger.info("%d account(s) completed successfully.", ok)
    return len(failed)


__all__ = [
    "list_account_aliases",
    "run_for_account",
    "run_all_accounts",
    "daily_routine",
    "full_routine",
    "summarize",
]
