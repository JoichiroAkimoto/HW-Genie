"""CLI handlers for the battle log corpus (import / stats)."""
from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from rich import box
from rich.console import Console
from rich.table import Table

from hw_genie.core.battle_log import (
    BattleLogError,
    ImportStats,
    MatchupStat,
    PruneStats,
    SimulationComparison,
    build_battle_payload,
    compare_result,
    delete_stale_sim_seeds,
    import_battles,
    parse_battle_get_response,
    parse_battle_replays,
    prune_battles,
    query_matchups,
    record_comparison,
    upsert_simulation,
)
from hw_genie.core.client import HWClient
from hw_genie.core.hwa_log import HwaImportStats, import_hwa_file

_console = Console()


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


def _payload(res) -> dict:
    if not res.is_success:
        raise BattleLogError(
            f"battleGetByType failed ({res.error_name or res.status.value}): {res.detail}"
        )
    return res.detail if isinstance(res.detail, dict) else {}


def run_battle_import(
    client: HWClient,
    account_alias: str,
    *,
    battle_type: str = "arena",
    limit: int = 50,
    offset: int = 0,
    dry_run: bool = False,
    as_json: bool = False,
) -> ImportStats:
    """Fetch replays from the API and store them in the corpus."""
    res = client.battle_get_by_type(battle_type, limit=limit, offset=offset)
    parsed = parse_battle_get_response(_payload(res), battle_type=battle_type)
    stats = import_battles(account_alias, parsed, dry_run=dry_run)

    if as_json:
        print(
            json.dumps(
                {
                    "account": account_alias,
                    "battle_type": battle_type,
                    "dry_run": dry_run,
                    "total": stats.total,
                    "inserted": stats.inserted,
                    "duplicates": stats.duplicates,
                    "upgraded": stats.upgraded,
                    "team_configs_inserted": stats.team_configs_inserted,
                    "skipped": stats.skipped,
                    "errors": stats.errors,
                },
                ensure_ascii=False,
            )
        )
        return stats

    prefix = "[dry-run] would import" if dry_run else "imported"
    print(
        f"{prefix}: {stats.inserted} battle(s) "
        f"(fetched {stats.total}, duplicates {stats.duplicates}, "
        f"upgraded {stats.upgraded}, "
        f"new team configs {stats.team_configs_inserted}, skipped {stats.skipped})"
    )
    if stats.total == 0:
        print("No replays returned. Check --type / --limit / --offset.")
    for error in stats.errors:
        print(f"  skip {error}")
    return stats


def _render_stats(
    rows: list[MatchupStat], *, by: str, battle_type: str | None
) -> None:
    title = f"{battle_type or 'All'} matchup stats (grouped by {by})"
    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("Type", style="cyan", no_wrap=True)
    if by == "defender":
        table.add_column("Defender", style="magenta", no_wrap=True)
    table.add_column("Attacker lineup", style="green")
    table.add_column("Defender lineup", style="yellow")
    table.add_column("N", justify="right")
    table.add_column("W", justify="right")
    table.add_column("Rate", justify="right")
    table.add_column("Last seen", no_wrap=True)

    for row in rows:
        rate = f"{row.win_rate * 100:.0f}%"
        rate_style = "green" if row.win_rate >= 0.999 else "yellow" if row.win_rate >= 0.8 else "red"
        cells = [row.battle_type]
        if by == "defender":
            cells.append(row.defender_player_id or "-")
        cells.extend(
            [
                row.attacker_heroes or "-",
                row.defender_heroes or "-",
                str(row.battles),
                str(row.wins),
                f"[{rate_style}]{rate}[/{rate_style}]",
                _format_dt(row.last_seen),
            ]
        )
        table.add_row(*cells)
    _console.print(table)


def run_battle_stats(
    *,
    account_alias: str | None = None,
    battle_type: str | None = None,
    defender_id: str | None = None,
    game_version: str | None = None,
    by: str = "defender",
    min_samples: int = 1,
    limit: int = 25,
    as_json: bool = False,
) -> list[MatchupStat]:
    """Print win rates aggregated by defender or by lineup matchup.

    ``account_alias`` filters by involvement (the player was attacker or
    defender), not by who imported the replay.
    """
    rows = query_matchups(
        account_alias=account_alias,
        battle_type=battle_type,
        defender_id=defender_id,
        game_version=game_version,
        by=by,
        min_samples=min_samples,
        limit=limit,
    )
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "battle_type": row.battle_type,
                        "defender_player_id": row.defender_player_id,
                        "matchup_key": row.matchup_key,
                        "attacker_heroes": row.attacker_heroes,
                        "defender_heroes": row.defender_heroes,
                        "battles": row.battles,
                        "wins": row.wins,
                        "win_rate": round(row.win_rate, 4),
                        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
                    }
                    for row in rows
                ],
                ensure_ascii=False,
            )
        )
        return rows

    if not rows:
        print(
            "No matching battles in the corpus. Run `battle import` or relax "
            "the filters."
        )
        return rows
    _render_stats(rows, by=by, battle_type=battle_type)
    return rows


def _result_label(win: bool | None, stars: int | None) -> str:
    if win is None:
        return "-"
    label = "W" if win else "L"
    return f"{label} {stars}★" if stars is not None else label


def run_battle_config_getters(client: HWClient, *, as_json: bool = False) -> dict:
    """List ``BattleConfigStorage`` getters from the headless game page.

    Used to discover the Arena config getter; the ToE getter
    (``get_titanPvpManual``) is the only one verified so far.
    """
    from hw_genie.battle.engine import PersistentPlaywrightBattleEngine

    engine = PersistentPlaywrightBattleEngine(headers=client.get_headers())
    try:
        data = engine.list_config_getters()
    finally:
        engine.close()

    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return data

    static_keys = data.get("staticKeys") or []
    properties = data.get("properties") or []
    print(f"staticKeys ({len(static_keys)}):")
    for key in static_keys:
        print(f"  {key}")
    print(f"properties ({len(properties)}):")
    for prop in properties:
        print(f"  {prop}")
    return data


def run_battle_simulate(
    client: HWClient,
    account_alias: str,
    *,
    battle_type: str = "arena",
    limit: int = 20,
    offset: int = 0,
    battle_id: str | None = None,
    dry_run: bool = False,
    as_json: bool = False,
    recycle_every: int = 100,
    config_getter: str = "get_titanPvpManual",
    seeds: int = 1,
    oracle=None,
) -> list[SimulationComparison]:
    """Re-simulate fetched replays with the headless oracle and record parity.

    The raw replay is used as the oracle input (including ``effects`` and
    ``seed``). Recorded runs write a ``source='sim'`` row plus a
    ``battle_comparisons`` row; historical stats/planning ignore sim rows.

    ``seeds > 1`` re-simulates the same battle with the observed seed plus
    additional deterministic seeds; the observed seed is compared with the
    replay, and ``sweep_*`` fields report the statistical win rate.
    """
    if seeds < 1:
        raise BattleLogError("--seeds must be >= 1")
    if (
        oracle is None
        and battle_type != "titan_arena"
        and config_getter == "get_titanPvpManual"
    ):
        raise BattleLogError(
            f"--type {battle_type} needs a verified --config-getter; the default "
            "get_titanPvpManual is only verified for titan_arena. Run "
            "`hw-genie battle config-getters` and pass --config-getter <name>."
        )

    res = client.battle_get_by_type(battle_type, limit=limit, offset=offset)
    pairs = parse_battle_replays(_payload(res), battle_type)
    if battle_id is not None:
        pairs = [
            (raw, parsed)
            for raw, parsed in pairs
            if parsed.source_record_id == str(battle_id)
        ]
        if not pairs:
            raise BattleLogError(
                f"battle {battle_id!r} was not found in the fetched replays."
            )
    if not pairs:
        if as_json:
            print(
                json.dumps(
                    {
                        "account": account_alias,
                        "battle_type": battle_type,
                        "dry_run": dry_run,
                        "engine": None,
                        "processed": 0,
                        "succeeded": 0,
                        "matched": 0,
                        "total": 0,
                        "results": [],
                        "failures": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("No replays returned. Check --type / --limit / --offset.")
        return []

    engine = oracle
    owns_engine = False
    if engine is None:
        from hw_genie.battle.engine import PersistentPlaywrightBattleEngine

        engine = PersistentPlaywrightBattleEngine(
            headers=client.get_headers(),
            recycle_every=recycle_every,
            config_getter=config_getter,
        )
        owns_engine = True
    engine_name = getattr(engine, "engine_name", type(engine).__name__)[:24]

    comparisons: list[SimulationComparison] = []
    failures: list[tuple[str, str]] = []
    try:
        for raw, parsed in pairs:
            # One bad battle (oracle crash, DB error) must not abort the rest.
            try:
                payload = build_battle_payload(raw, battle_type)
                base_seed = int(payload.get("seed") or 0)
                if seeds > 1:
                    rng = random.Random(base_seed)
                    seed_values = [base_seed] + [
                        rng.getrandbits(32) for _ in range(seeds - 1)
                    ]
                else:
                    seed_values = [base_seed]

                estimates = []
                for seed_value in seed_values:
                    sim_payload = dict(payload)
                    sim_payload["seed"] = seed_value
                    started = perf_counter()
                    estimate = engine.calc(sim_payload)
                    elapsed = perf_counter() - started
                    estimates.append((seed_value, estimate, elapsed))

                _, observed, observed_elapsed = estimates[0]
                sweep_total = len(estimates)
                sweep_wins = sum(1 for _, estimate, _ in estimates if estimate.win)

                if dry_run:
                    comparison = compare_result(
                        parsed,
                        observed.win,
                        observed.stars,
                        elapsed_seconds=observed_elapsed,
                    )
                else:
                    # Ensure the observed row exists so the comparison has an
                    # FK target (duplicates are skipped by import_battles, and
                    # an HWA partial row may be upgraded in place instead).
                    stats = import_battles(account_alias, [parsed])
                    if stats.inserted + stats.duplicates + stats.upgraded < 1:
                        detail = "; ".join(stats.errors) or "unknown import error"
                        raise BattleLogError(
                            f"battle {parsed.source_record_id}: replay could not "
                            f"be stored in the corpus ({detail})"
                        )
                    upsert_simulation(
                        account_alias,
                        replace(
                            parsed,
                            seed=base_seed,
                            win=observed.win,
                            stars=observed.stars,
                        ),
                        engine=engine_name,
                    )
                    # Extra seeds are stored as separate sim rows (excluded
                    # from stats/planning) so the sweep is auditable.
                    for seed_value, estimate, _ in estimates[1:]:
                        upsert_simulation(
                            account_alias,
                            replace(
                                parsed,
                                source_record_id=(
                                    f"{parsed.source_record_id}#seed={seed_value}"
                                ),
                                seed=seed_value,
                                win=estimate.win,
                                stars=estimate.stars,
                            ),
                            engine=engine_name,
                        )
                    keep_ids = {
                        f"{parsed.source_record_id}#seed={seed}"
                        for seed in seed_values[1:]
                    }
                    delete_stale_sim_seeds(parsed.source_record_id, keep_ids)
                    comparison = record_comparison(parsed.source_record_id)
                    if comparison is None:
                        raise BattleLogError(
                            f"battle {parsed.source_record_id}: comparison could "
                            "not be recorded (imported or sim row is missing)"
                        )
                    comparison = replace(
                        comparison, elapsed_seconds=observed_elapsed
                    )
                comparison = replace(
                    comparison,
                    sweep_seeds=sweep_total,
                    sweep_wins=sweep_wins,
                    sweep_win_rate=(
                        sweep_wins / sweep_total if sweep_total else 0.0
                    ),
                )
            except (KeyboardInterrupt, InterruptedError):
                raise
            except Exception as exc:
                failures.append((parsed.source_record_id, str(exc)))
                continue
            comparisons.append(comparison)
    finally:
        if owns_engine:
            close = getattr(engine, "close", None)
            if callable(close):
                close()

    matched = sum(1 for comparison in comparisons if comparison.matched)
    if as_json:
        print(
            json.dumps(
                {
                    "account": account_alias,
                    "battle_type": battle_type,
                    "dry_run": dry_run,
                    "engine": engine_name,
                    "processed": len(pairs),
                    "succeeded": len(comparisons),
                    "matched": matched,
                    "total": len(comparisons),
                    "results": [asdict(comparison) for comparison in comparisons],
                    "failures": [
                        {"battle_id": failed_id, "error": error}
                        for failed_id, error in failures
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        table = Table(
            title=(
                f"Oracle parity ({battle_type}, "
                f"{'dry-run' if dry_run else 'recorded'})"
            ),
            box=box.SIMPLE_HEAVY,
        )
        table.add_column("Battle", style="cyan", no_wrap=True)
        table.add_column("Actual", justify="center")
        table.add_column("Simulated", justify="center")
        table.add_column("Match", justify="center")
        table.add_column("Mismatch", style="yellow")
        table.add_column("ms", justify="right")
        if seeds > 1:
            table.add_column("Sweep", justify="center")
        for comparison in comparisons:
            row = [
                comparison.battle_id,
                _result_label(comparison.actual_win, comparison.actual_stars),
                _result_label(comparison.simulated_win, comparison.simulated_stars),
                "yes" if comparison.matched else "no",
                comparison.mismatch_type,
                f"{comparison.elapsed_seconds * 1000:.0f}",
            ]
            if seeds > 1:
                row.append(
                    f"{comparison.sweep_wins}/{comparison.sweep_seeds} "
                    f"({comparison.sweep_win_rate * 100:.0f}%)"
                )
            table.add_row(*row)
        _console.print(table)
        rate = (matched / len(comparisons) * 100) if comparisons else 0.0
        print(f"Matched {matched}/{len(comparisons)} ({rate:.0f}%) with {engine_name}")
        if seeds > 1:
            total_wins = sum(comparison.sweep_wins for comparison in comparisons)
            total_seeds = sum(comparison.sweep_seeds for comparison in comparisons)
            sweep_rate = (total_wins / total_seeds * 100) if total_seeds else 0.0
            print(
                f"Sweep: {total_wins}/{total_seeds} wins "
                f"({sweep_rate:.0f}%) across {seeds} seed(s) per battle"
            )
        print(
            f"Processed {len(pairs)} battle(s): {len(comparisons)} succeeded, "
            f"{len(failures)} failed"
        )
        for failed_id, error in failures:
            print(f"  failed {failed_id}: {error}")
        if dry_run:
            print("[dry-run] no simulation rows or comparisons were written")

    if failures and not comparisons:
        detail = "; ".join(f"{failed_id}: {error}" for failed_id, error in failures)
        raise BattleLogError(f"all simulations failed: {detail}")
    return comparisons


def run_battle_prune(
    before: datetime, *, dry_run: bool = False, as_json: bool = False
) -> PruneStats:
    """Delete battle rows (and comparisons) older than ``before``."""
    stats = prune_battles(before, dry_run=dry_run)
    if as_json:
        print(
            json.dumps(
                {
                    "before": stats.before.replace(
                        tzinfo=timezone.utc
                    ).isoformat(),
                    "dry_run": dry_run,
                    "matched": stats.matched,
                    "deleted": stats.deleted,
                    "comparisons": stats.comparisons,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return stats

    count = stats.matched if dry_run else stats.deleted
    prefix = "[dry-run] would delete" if dry_run else "deleted"
    print(
        f"{prefix}: {count} battle(s) and {stats.comparisons} comparison(s) "
        f"older than {stats.before.date().isoformat()}"
    )
    return stats


def parse_battle_types(value: str) -> tuple[str, ...]:
    """Parse a comma-separated ``--types`` value (``ga`` aliases ``grand``)."""
    allowed = {"arena", "grand", "ga"}
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise BattleLogError("--types must list at least one of arena,grand")
    invalid = [item for item in items if item not in allowed]
    if invalid:
        raise BattleLogError(f"unsupported --types value(s): {', '.join(invalid)}")
    # Normalize before deduping so ``grand,ga`` collapses to one type.
    return tuple(
        dict.fromkeys("grand" if item == "ga" else item for item in items)
    )


def run_battle_import_hwa(
    account_alias: str,
    source: str,
    *,
    types: tuple[str, ...] = ("arena", "grand"),
    since: datetime | None = None,
    offset: int = 0,
    limit: int | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
    as_json: bool = False,
) -> HwaImportStats:
    """Import an HWAssistant ``*.json.gz`` snapshot."""
    path = Path(source).expanduser()
    if not path.exists():
        raise BattleLogError(f"HWA source not found: {path}")

    stats = import_hwa_file(
        account_alias,
        path,
        types=types,
        since=since,
        offset=offset,
        limit=limit,
        batch_size=batch_size,
        dry_run=dry_run,
    )

    if as_json:
        print(
            json.dumps(
                {
                    "source": str(path),
                    "default_account": account_alias,
                    "dry_run": dry_run,
                    "types": list(types),
                    "since": since.isoformat() if since else None,
                    "offset": offset,
                    "limit": limit,
                    "total": stats.total,
                    "inserted": stats.inserted,
                    "duplicates": stats.duplicates,
                    "upgraded": stats.upgraded,
                    "team_configs_inserted": stats.team_configs_inserted,
                    "filtered": stats.filtered,
                    "skipped": stats.skipped,
                    "errors": stats.errors,
                    "by_account": stats.by_account,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return stats

    prefix = "[dry-run] would import" if dry_run else "imported"
    print(
        f"{prefix}: {stats.inserted} HWA battle(s) "
        f"(parsed {stats.total} incl. duplicates/skips, "
        f"filtered {stats.filtered}, duplicates {stats.duplicates}, "
        f"upgraded {stats.upgraded}, "
        f"new team configs {stats.team_configs_inserted}, skipped {stats.skipped})"
    )
    for alias, count in sorted(stats.by_account.items()):
        print(f"  {alias}: {count} processed")
    if since is not None:
        print(f"  cutoff: {since.date().isoformat()}")
    for error in stats.errors[:10]:
        print(f"  skip {error}")
    return stats


def run_battle_watch(
    client: HWClient,
    account_alias: str,
    *,
    types: tuple[str, ...] = ("arena", "grand"),
    interval: int = 300,
    limit: int = 50,
    offset: int = 0,
    once: bool = False,
    as_json: bool = False,
) -> list[dict]:
    """Poll arena/grand replays and import new battles until interrupted.

    ``once`` performs a single pass (cron/tests). With ``as_json`` each pass is
    printed as one JSON object per line. The returned history is capped at the
    most recent 100 passes so a long-running watcher does not grow unbounded.
    """
    max_passes = 100
    passes: list[dict] = []
    pass_number = 0
    sleep = getattr(client, "sleep", None)

    while True:
        results: dict[str, dict] = {}
        for battle_type in types:
            try:
                res = client.battle_get_by_type(
                    battle_type, limit=limit, offset=offset
                )
                parsed = parse_battle_get_response(_payload(res), battle_type)
                stats = import_battles(
                    account_alias,
                    parsed,
                    source="api_import",
                    engine="game-server",
                )
                results[battle_type] = {
                    "fetched": stats.total,
                    "inserted": stats.inserted,
                    "duplicates": stats.duplicates,
                    "upgraded": stats.upgraded,
                    "errors": stats.errors,
                }
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                results[battle_type] = {"error": str(exc)}
            if callable(sleep):
                sleep()

        pass_number += 1
        payload = {
            "pass": pass_number,
            "account": account_alias,
            "results": results,
        }
        passes.append(payload)
        if len(passes) > max_passes:
            del passes[: len(passes) - max_passes]
        if as_json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            parts = []
            for battle_type, result in results.items():
                if "error" in result:
                    parts.append(f"{battle_type}: error ({result['error']})")
                else:
                    parts.append(
                        f"{battle_type}: fetched {result['fetched']}, "
                        f"new {result['inserted']}, dup {result['duplicates']}"
                    )
            print(f"[pass {pass_number}] " + " | ".join(parts))

        if once:
            return passes
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            raise
