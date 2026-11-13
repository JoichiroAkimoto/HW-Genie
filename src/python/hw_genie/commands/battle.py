"""CLI handlers for the battle log corpus (import / stats)."""
from __future__ import annotations

import json
from datetime import datetime

from rich import box
from rich.console import Console
from rich.table import Table

from hw_genie.core.battle_log import (
    BattleLogError,
    ImportStats,
    MatchupStat,
    import_battles,
    parse_battle_get_response,
    query_matchups,
)
from hw_genie.core.client import HWClient

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
