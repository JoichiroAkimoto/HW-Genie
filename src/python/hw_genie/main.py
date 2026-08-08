import argparse
import logging
import os
import sys
import json
from hw_genie.core.client import (
    HWClient,
    AccountResolutionError,
    resolve_account,
)
from hw_genie.core.auth import load_session, update_session_with_headers, extract_headers_from_curl, extract_payload_from_curl
from hw_genie.core.utils import format_number_with_suffix
from hw_genie.commands.hero_raid import run_hero_raid
from hw_genie.commands.item_raid import run_item_raid
from hw_genie.commands.hero_shopping import run_hero_shopping
from hw_genie.commands.daily_raid import run_daily_raid
from hw_genie.commands.auth_server import run_server
from hw_genie.runner import run_all_accounts, summarize, resolve_max_parallel


def _prepare_info_for_json(info: dict) -> dict:
    """JSON出力用に PlayerStatus オブジェクトを辞書に変換する"""
    if "player" in info and hasattr(info["player"], "to_dict"):
        output = info.copy()
        output["player"] = info["player"].to_dict()
        return output
    return info


def _ensure_session(args) -> dict[str, str]:
    """セッションヘッダーを検証し、なければエラーを出して終了する"""
    from hw_genie.core.session_manager import SessionManager

    resolved = resolve_account(args.account)
    data = SessionManager.load(resolved)
    headers = data.get("headers") if data else None
    if not headers:
        print(f"Error: Session not found for account '{resolved}'. Please provide a valid curl with --curl.")
        sys.exit(1)
    return headers


def cmd_auth(args):
    """認証情報の更新・表示"""
    # --fresh は --list との併用が必須（--list-names 併用は常にエラー）
    if getattr(args, "fresh", False):
        if getattr(args, "list_names", False):
            print(
                "Error: --fresh cannot be combined with --list-names.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not args.list:
            print("Error: --fresh requires --list.", file=sys.stderr)
            sys.exit(1)

    # 一覧表示
    if args.list or getattr(args, "list_names", False):
        from hw_genie.core.session_manager import SessionManager

        if getattr(args, "list_names", False):
            accounts = SessionManager.list_accounts()
            if not accounts:
                print("No accounts found in database.")
                return
            # 登録順（list_accounts が id 順で返す）
            for alias in accounts:
                print(alias)
            return

        accounts = SessionManager.list_accounts()
        if not accounts:
            print("No accounts found in database.")
            return

        if getattr(args, "fresh", False):
            from hw_genie.core.auth import refresh_all_accounts

            # -a 併用時はそのアカウントのみ最新化する
            targets = [args.account] if args.account else accounts
            # 並列数は multi と同様 HW_MAX_PARALLEL（上限）を尊重する
            refreshed = refresh_all_accounts(
                targets, max_parallel=resolve_max_parallel(None, len(targets))
            )
            failed = [err for _, err in refreshed if err]
            for account, err in refreshed:
                if err:
                    print(f"⚠️ {err}", file=sys.stderr)
            if failed:
                # 失敗したアカウントは DB の旧値のまま表示する
                print(
                    f"⚠️ Could not refresh {len(failed)} account(s); "
                    "showing cached values.",
                    file=sys.stderr,
                )
            # refresh で alias が変わった場合に備え、表示用リストを再取得する
            accounts = SessionManager.list_accounts()

        from hw_genie.core.utils import (
            display_timezone_name,
            display_width,
            energy_over_cap,
            format_timestamp_for_display,
            pad,
            rank_color,
            style,
            terminal_columns,
            wrap_display,
        )

        tz_label = display_timezone_name()
        updated_col = f"Updated ({tz_label})"

        # 固定列（Memo は端末幅の残りを取る最終列）。
        fixed_headers = [
            "Name",
            "Arena",
            "GA",
            "Gold",
            "Gems",
            "Mission",
            "Energy",
            updated_col,
        ]

        # まず全アカウントの固定セルを収集する（登録順 = list_accounts の id 順）
        row_data = []
        for alias in accounts:
            data = SessionManager.load(alias)
            player = data.get("player", {})
            p_name = player.get("name", "Unknown")

            p_energy = player.get("energy", "-")
            p_arena = player.get("arena_rank", "-")
            p_grand = player.get("grand_rank", "-")
            p_gold = format_number_with_suffix(player.get("gold", 0)) if player.get("gold") is not None else "-"
            p_gems = format_number_with_suffix(player.get("gems", 0)) if player.get("gems") is not None else "-"
            p_last_id = data.get("last_item_raid_mission_id", "-")

            updated = data.get("last_updated", "Never")
            updated_short = format_timestamp_for_display(updated)

            row_data.append(
                (
                    [str(p_name), str(p_arena), str(p_grand), str(p_gold),
                     str(p_gems), str(p_last_id), str(p_energy), str(updated_short)],
                    data.get("memo", "-"),
                    player,
                )
            )

        # 固定列幅は「ヘッダーラベルと最長セルのどちらか長い方」に内容駆動で調整
        # （runner のサマリーテーブルと同じ方式。名前の長いアカウントにも追従する）
        fixed_widths = [
            max(
                display_width(header),
                *(display_width(cells[i]) for cells, _, _ in row_data),
            )
            for i, header in enumerate(fixed_headers)
        ]
        # 固定列と末尾の Memo 列を区切る「 | 」は固定列数と同じ個数
        separators_width = len(fixed_headers) * len(" | ")
        # Fill the remaining terminal width with the Memo column, never
        # truncating: anything longer is wrapped onto continuation rows.
        memo_width = max(
            10, terminal_columns() - sum(fixed_widths) - separators_width
        )

        rows = [
            (cells, wrap_display(memo, memo_width), player)
            for cells, memo, player in row_data
        ]

        rank_keys = {
            fixed_headers.index("Arena"): "arena_rank",
            fixed_headers.index("GA"): "grand_rank",
        }
        energy_col = fixed_headers.index("Energy")

        header_cells = fixed_headers + ["Memo"]
        header_widths = fixed_widths + [memo_width]
        # 幅計算はプレーン文字列で行い、パディング後にスタイルを後付けする
        plain_header = " | ".join(pad(h, w) for h, w in zip(header_cells, header_widths))
        header = style(plain_header, bold=True, fg="cyan")
        print("\n" + header)
        print(style("-" * display_width(plain_header), dim=True))
        for row_idx, (fixed_cells, memo_lines, player) in enumerate(rows):
            # アカウント行のゼブラ: 偶数番目の行を全体 dim にして行を区切る
            dim_row = row_idx % 2 == 1
            for i, memo_line in enumerate(memo_lines):
                left = fixed_cells if i == 0 else [""] * len(fixed_cells)
                styled = []
                for j, (cell, w) in enumerate(zip(left, fixed_widths)):
                    padded = pad(cell, w)
                    if i > 0:
                        # 継続行の固定列は空なのでスタイル不要（行のゼブラ dim のみ）
                        styled.append(style(padded, dim=dim_row))
                        continue
                    if j == 0:
                        styled.append(style(padded, bold=True, dim=dim_row))
                    elif j in rank_keys:
                        color = rank_color(player.get(rank_keys[j]))
                        # 色付きセルはゼブラでも dim しない（色を保つ）
                        styled.append(style(padded, fg=color, dim=dim_row and not color))
                    elif j == energy_col:
                        if energy_over_cap(player.get("level"), player.get("energy")):
                            styled.append(style(padded, fg="red"))
                        else:
                            styled.append(style(padded, dim=dim_row))
                    else:
                        styled.append(style(padded, dim=dim_row))
                memo_padded = pad(memo_line, memo_width)
                # 継続行も 1 行目と同じ色（ゼブラ行では行ごと dim）
                styled.append(style(memo_padded, dim=dim_row))
                print(" | ".join(styled))
        print()
        return

    account_alias = args.account or None

    # curl等による更新前に、単体でmemoが指定された場合
    if args.memo is not None:
        from hw_genie.core.session_manager import SessionManager
        # memo 更新は既存アカウントが対象のため、未指定時は自動解決する
        resolved_account = resolve_account(account_alias)
        session_data = SessionManager.load(resolved_account)
        if not session_data:
            print(f"Error: Account '{resolved_account}' not found in database. Please register the account first using --curl.")
            sys.exit(1)
        
        session_data["memo"] = args.memo
        SessionManager.save(resolved_account, session_data)
        print(f"Successfully updated memo for account '{resolved_account}' to: '{args.memo}'")
        return

    # curlコマンドからヘッダーを抽出する場合
    if args.curl:
        headers = extract_headers_from_curl(args.curl)
        if not headers:
            print("Error: Could not extract x-auth-* headers from the provided curl command.")
            sys.exit(1)

        info = update_session_with_headers(headers, account_alias)
        if info["status"] == "success":
            print(f"Successfully updated session for {info['player'].name}")
            print(json.dumps(_prepare_info_for_json(info), indent=2))
        else:
            print(f"Error updating session: {info.get('message')}")
            sys.exit(1)
        return

    # curl等からヘッダーJSONが渡された場合
    if args.update:
        try:
            headers = json.loads(args.update)
            info = update_session_with_headers(headers, account_alias)
            if info["status"] == "success":
                print(f"Successfully updated session for {info['player'].name}")
                print(json.dumps(_prepare_info_for_json(info), indent=2))
            else:
                print(f"Error updating session: {info.get('message')}")
                sys.exit(1)
        except Exception as e:
            print(f"Error parsing headers: {e}")
            sys.exit(1)
        return

    # 単に情報を表示する場合
    # 既存アカウントが対象のため、未指定時は自動解決する
    resolved_account = resolve_account(account_alias)
    session_data = load_session(resolved_account)
    if not session_data:
        print(f"Error: Session not found for account '{resolved_account}'")
        sys.exit(1)

    if args.info:
        # 現在のヘッダーを使って最新情報を取得し直す
        info = update_session_with_headers(session_data["headers"], account_alias)
        print(json.dumps(_prepare_info_for_json(info), indent=2))
    else:
        headers = session_data.get("headers") or {}
        redacted_headers = {k: ("***" if k.startswith("x-auth-") else v) for k, v in headers.items()}
        print(json.dumps(redacted_headers, indent=2))


def cmd_auth_server(args):
    """認証キャプチャサーバーを起動"""
    host = os.environ.get("HW_GENIE_AUTH_HOST", "127.0.0.1")
    port = args.port or int(os.environ.get("HW_GENIE_AUTH_PORT", 8765))
    run_server(host=host, port=port, once=args.once)


def cmd_raid_hero(args):
    """ヒーローレイド実行"""
    headers = None
    account_alias = args.account or None

    # curlコマンドから認証情報を抽出
    if args.curl:
        auth_headers = extract_headers_from_curl(args.curl)
        if auth_headers:
            info = update_session_with_headers(auth_headers, account_alias)
            if info["status"] == "success":
                headers = info["headers"]
                print(f"Successfully updated session for {info['player'].name} from curl.")

    # セッション情報の読み込み（curlがない場合、または抽出に失敗した場合）
    if not headers:
        headers = _ensure_session(args)

    client = HWClient(headers)
    run_hero_raid(client, args.mission_ids, args.times)


def cmd_raid_item(args):
    """アイテムレイド実行"""
    headers = None
    payload = None
    account_alias = args.account or None
    # curl 登録で確定した実名（-a 未指定時に account として伝播させる）
    curl_player_name = None

    # curlコマンドから情報を抽出
    if args.curl:
        # 1. 認証ヘッダーを抽出してセッションを更新
        auth_headers = extract_headers_from_curl(args.curl)
        if auth_headers:
            info = update_session_with_headers(auth_headers, account_alias)
            if info["status"] == "success":
                headers = info["headers"]
                curl_player_name = info["player"].name
                print(f"Successfully updated session for {info['player'].name} from curl.")

        # 2. ペイロードを抽出
        payload = extract_payload_from_curl(args.curl)
        if not payload:
            print("Error: Could not extract JSON payload from the provided curl command.")
            sys.exit(1)
    else:
        try:
            if args.payload and args.payload.startswith("{"):
                payload = json.loads(args.payload)
            elif args.payload:
                with open(args.payload, "r") as f:
                    payload = json.load(f)
        except Exception as e:
            print(f"Error loading payload: {e}")
            sys.exit(1)

    # セッション情報の読み込み（curlがない場合、または抽出に失敗した場合）
    if not headers:
        headers = _ensure_session(args)

    if not payload:
        print("Error: No payload provided. Use --curl or provide a JSON payload.")
        sys.exit(1)

    client = HWClient(headers)
    run_item_raid(client, payload, max_iterations=args.times, account=account_alias or curl_player_name)


def cmd_shop(args):
    """ショップ購入"""
    headers = _ensure_session(args)

    client = HWClient(headers)
    from hw_genie.commands.hero_shopping import TARGET_SHOP_IDS

    run_hero_shopping(client, buy_soul_shop_items=True, hero_shop_ids=TARGET_SHOP_IDS)
    client.exchange_stones()


def cmd_quests(args):
    """クエスト（デイリー等）の取得・表示"""
    from hw_genie.commands.quests import (
        classify_quest,
        edit_quest_defaults_interactive,
        ensure_quest_defaults,
        run_quest_execute,
        run_quest_status,
        set_quest_defaults,
    )

    account = resolve_account(args.account)

    # quest_defaults を対話的に編集する（番号選択ウィザード）。
    # DB 内の設定編集のみなので認証セッションは不要（_ensure_session 前に処理）。
    # ただし未登録アカウントは他オプションと同じ「Session not found」文言で
    # 前置きを揃える（auth での登録を促す）。
    if getattr(args, "edit_defaults", False):
        from hw_genie.core.session_manager import SessionManager

        if not SessionManager.load(account):
            print(f"Error: Session not found for account '{account}'. Please provide a valid curl with --curl.")
            sys.exit(1)
        edit_quest_defaults_interactive(account)
        return

    headers = _ensure_session(args)
    client = HWClient(headers)

    # quest_defaults を初期化する（QUEST_OPERATIONS 登録済みクエストを enabled:false で投入）
    if args.init_defaults:
        defaults = ensure_quest_defaults(account)
        print(f"ℹ️  Initialized quest_defaults for {account}:")
        for qid in sorted(defaults):
            category, name = classify_quest(qid)
            print(f"    - {qid} ({name}) enabled={defaults[qid].get('enabled', False)}")
        return

    # アカウント固有の操作引数上書き（quest_defaults）を 1 件登録する
    if args.set_default:
        quest_id, key, value = args.set_default
        stored = set_quest_defaults(account, int(quest_id), key, value)
        _, name = classify_quest(int(quest_id))
        print(f"ℹ️  Registered quest_defaults[{quest_id} ({name})][{key}] = {stored} ({type(stored).__name__}) for {account}")
        return

    if args.execute or args.dry_run:
        _, failed = run_quest_execute(
            client,
            account_alias=args.account,
            dry_run=bool(args.dry_run),
            confirm=bool(args.yes),
        )
        if failed:
            sys.exit(1)
        return

    run_quest_status(
        client,
        account_alias=args.account,
        show_all=args.show_all,
        raw=args.raw,
        category=args.category,
    )


def cmd_daily(args):
    """デイリーレイド実行"""
    headers = None
    item_payload = {}
    # curl 登録で確定した実名（-a 未指定時に account として伝播させる）
    curl_player_name = None

    # curlコマンドから情報を抽出
    if args.curl:
        # 1. 認証ヘッダーを抽出してセッションを更新
        auth_headers = extract_headers_from_curl(args.curl)
        if auth_headers:
            info = update_session_with_headers(auth_headers, args.account or None)
            if info["status"] == "success":
                headers = info["headers"]
                curl_player_name = info["player"].name
                print(f"Successfully updated session for {info['player'].name} from curl.")
            else:
                print(f"Warning: Could not update session from curl: {info.get('message')}")

        # 2. アイテムレイド用ペイロードを抽出
        extracted_payload = extract_payload_from_curl(args.curl)
        if not extracted_payload:
            print("Error: Could not extract JSON payload from the provided curl command.")
            sys.exit(1)
        item_payload = extracted_payload

    # セッション情報の読み込み（curlがない場合、または抽出に失敗した場合）
    if not headers:
        headers = _ensure_session(args)

    client = HWClient(headers)
    run_daily_raid(
        client,
        item_payload=item_payload,
        account_alias=args.account or curl_player_name,
    )


def cmd_sync(args):
    """Sync the local Turso replica with the remote cloud database."""
    sync_url = os.environ.get("TURSO_SYNC_URL")
    if not sync_url:
        print("TURSO_SYNC_URL is not set — nothing to sync.")
        return

    from hw_genie.core.database import (
        get_engine,
        retry_on_wal_contention,
        _wal_io_lock,
    )

    try:
        engine = get_engine()
        with engine.connect() as conn:
            raw = conn.connection.dbapi_connection
            if hasattr(raw, "sync"):
                # sync() は WAL にフレームを書くため、他の書き込み操作と
                # 同じ共有ロックで直列化する（試行ごとに取り直し）。
                def _sync(raw=raw):
                    with _wal_io_lock:
                        raw.sync()

                retry_on_wal_contention(
                    _sync, logger=logging.getLogger(__name__)
                )
            else:
                from sqlalchemy import text

                conn.execute(text("SELECT 1"))
    except Exception as e:
        print(f"✗ Sync failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ Local replica synced with Turso cloud ({sync_url})")


def cmd_db_check(args):
    """全アカウントの account_configs に壊れた JSON が無いか検査する。

    ``get_data`` は壊れた行を（警告付きで）スキップして読み取りを続行する
    ため、破損があっても hwda / auth / quests 等は落ちない。その代わり、
    どこが破損しているかを確認する手段として本コマンドを提供する。
    壊れた行が 1 つでも見つかれば exit code 1 を返す。
    """
    from hw_genie.core.session_manager import SessionManager

    broken = SessionManager.repo.check_configs()
    if not broken:
        print("✓ No broken config JSON found.")
        return

    print(f"✗ {len(broken)} broken config JSON row(s) found:")
    for item in broken:
        print(
            f"  - account={item['account']} key={item['key']} "
            f"error={item['error']}"
        )
    sys.exit(1)


def cmd_multi(args):
    """Run a routine against all accounts inside a single process (parallel)."""
    from hw_genie.runner import (
        daily_routine,
        full_routine,
        list_account_aliases,
        quests_routine,
        summarize_quests,
    )

    mode = args.mode
    accounts = args.accounts
    if accounts:
        accounts = list(accounts)
    else:
        accounts = list_account_aliases()

    if mode != "quests" and getattr(args, "dry_run", False):
        print(
            "Error: --dry-run is only supported with the 'quests' mode "
            "(daily/full routines always execute their operations).",
            file=sys.stderr,
        )
        sys.exit(2)

    if mode == "quests":
        routine = quests_routine(dry_run=bool(getattr(args, "dry_run", False)))
    else:
        routine = full_routine if mode == "full" else daily_routine

    results = run_all_accounts(routine, accounts=accounts, max_parallel=args.parallel)
    failed = (
        summarize_quests(results.items())
        if mode == "quests"
        else summarize(results.items())
    )
    if failed:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(prog="hw-genie", description="Hero Wars Genie CLI")

    # Parent parser for common arguments
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--account", "-a", help="Account alias")
    parent_parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Auth
    p_auth = subparsers.add_parser("auth", parents=[parent_parser], help="Authentication management")
    p_auth.add_argument("--update", "-u", help="Update session with JSON headers")
    p_auth.add_argument("--curl", "-c", help="Update session with curl command")
    p_auth.add_argument("--info", "-i", action="store_true", help="Get player info and update session")
    p_auth.add_argument("--list", "-l", action="store_true", help="List all accounts in database")
    p_auth.add_argument("--fresh", action="store_true", help="Fetch the latest player status from the game API before --list")
    p_auth.add_argument("--list-names", action="store_true", help="List all account names in plain text")
    p_auth.add_argument("--memo", help="Set or update the memo for the account")
    p_auth.set_defaults(func=cmd_auth)

    # Auth Server
    p_auth_server = subparsers.add_parser("auth-server", help="Start auth capture server")
    p_auth_server.add_argument("--port", "-p", type=int, help="Port to listen on (default: 8765, env: HW_GENIE_AUTH_PORT)")
    p_auth_server.add_argument("--once", action="store_true", help="Exit after first successful auth capture")
    p_auth_server.add_argument("--debug", action="store_true", help="Enable debug logging")
    p_auth_server.set_defaults(func=cmd_auth_server)

    # Raid
    p_raid = subparsers.add_parser("raid", help="Raid operations")
    raid_sub = p_raid.add_subparsers(dest="raid_type", help="Raid type")

    # Raid Hero
    p_raid_hero = raid_sub.add_parser("hero", parents=[parent_parser], help="Hero mission raid")
    p_raid_hero.add_argument("mission_ids", type=int, nargs="*", help="Mission IDs (space separated)")
    p_raid_hero.add_argument("--curl", "-c", help="Curl command to extract auth headers")
    p_raid_hero.add_argument("--times", "-t", type=int, default=3, help="Number of raids")
    p_raid_hero.set_defaults(func=cmd_raid_hero)

    # Raid Item
    p_raid_item = raid_sub.add_parser("item", parents=[parent_parser], help="Item raid using payload")
    p_raid_item.add_argument("payload", nargs="?", help="JSON payload string or path to JSON file")
    p_raid_item.add_argument("--curl", "-c", help="Curl command to extract item raid payload")
    p_raid_item.add_argument("--times", "-t", type=int, default=9999, help="Number of raids")
    p_raid_item.set_defaults(func=cmd_raid_item)

    # Shop
    p_shop = subparsers.add_parser("shop", parents=[parent_parser], help="Shop operations")
    p_shop.set_defaults(func=cmd_shop)

    # Daily
    p_daily = subparsers.add_parser("daily", parents=[parent_parser], help="Daily routine")
    p_daily.add_argument("--curl", "-c", help="Curl command to extract item raid payload")
    p_daily.set_defaults(func=cmd_daily)

    # Quests
    p_quests = subparsers.add_parser("quests", parents=[parent_parser], help="Quest status (daily quests)")
    p_quests.add_argument("--show-all", action="store_true", help="Show completed quests too (default: uncompleted only)")
    p_quests.add_argument("--raw", action="store_true", help="Print the raw questGetAll response as JSON")
    p_quests.add_argument("--category", choices=["daily", "weekly", "guild", "main", "event", "battlepass", "one_time", "unknown"], help="Filter by quest category")
    p_quests.add_argument("--execute", action="store_true", help="Execute operations to complete uncompleted daily quests (destructive; asks confirmation per step unless --yes)")
    p_quests.add_argument("--dry-run", action="store_true", help="Show the quest execution plan without running anything")
    p_quests.add_argument("--yes", action="store_true", help="Skip per-step confirmation (only valid with --execute)")
    p_quests.add_argument("--set-default", nargs=3, metavar=("QUEST_ID", "KEY", "VALUE"), help="Register an account-specific operation arg override (e.g. --set-default 10024 heroId 999)")
    p_quests.add_argument("--init-defaults", action="store_true", help="Initialize quest_defaults for the account (seed all QUEST_OPERATIONS quests as enabled=false)")
    p_quests.add_argument("--edit-defaults", action="store_true", help="Edit quest_defaults interactively (numbered selection wizard)")
    p_quests.set_defaults(func=cmd_quests)

    # Sync
    p_sync = subparsers.add_parser("sync", parents=[parent_parser], help="Sync local Turso replica with cloud")
    p_sync.set_defaults(func=cmd_sync)

    # DB check (detect broken config JSON rows)
    p_db_check = subparsers.add_parser(
        "db-check",
        parents=[parent_parser],
        help="Scan account_configs for broken config JSON rows (exit 1 if any)",
    )
    p_db_check.set_defaults(func=cmd_db_check)

    # Multi (single-process parallel across accounts)
    # NOTE: do NOT inherit parent_parser — the ``--account`` flag is meaningless
    # here because ``multi`` orchestrates accounts internally, and exposing it
    # would be a dead, confusing option.
    p_multi = subparsers.add_parser(
        "multi",
        help="Run a routine for all accounts inside one process (parallel)",
    )
    p_multi.add_argument("--debug", action="store_true", help="Enable debug logging")
    p_multi.add_argument(
        "mode",
        choices=["daily", "full", "quests"],
        nargs="?",
        default="daily",
        help="Routine to run: 'daily' (default), 'full' (raid+shop+daily), or 'quests' (daily quest auto-completion)",
    )
    p_multi.add_argument(
        "accounts",
        nargs="*",
        help="Optional account aliases to limit the run (default: all)",
    )
    p_multi.add_argument(
        "--parallel",
        "-p",
        type=int,
        default=None,
        help="Max concurrent accounts (default: HW_MAX_PARALLEL / unbounded)",
    )
    p_multi.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the quest execution plan without running anything (quests mode only)",
    )
    p_multi.set_defaults(func=cmd_multi)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    from hw_genie.core.client import HWAuthError
    from hw_genie.core.database import init_db

    setup_logging(getattr(args, "debug", False))

    # Mask Turso auth tokens that may appear in logged DB connection URLs.
    from hw_genie.core.database import install_token_masking_filter

    install_token_masking_filter()

    try:
        # Ensure DB tables exist
        init_db()

        if hasattr(args, "func"):
            args.func(args)
        else:
            print(f"Command {args.command} not implemented yet.")
    except HWAuthError as e:
        from hw_genie.core.client import Emojis

        print(f"\n{Emojis.ERROR}Authentication Error: {e}", file=sys.stderr)
        sys.exit(1)
    except AccountResolutionError as e:
        from hw_genie.core.client import Emojis

        print(f"\n{Emojis.ERROR}{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        from hw_genie.core.client import Emojis

        print(f"\n{Emojis.ERROR}Unexpected Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()


def setup_logging(debug: bool = False) -> None:
    """Configure root logging.

    Defaults to INFO so the parallel runner's progress and summary table are
    shown. ``install_token_masking_filter`` (called later in ``main``) only
    calls ``basicConfig`` when no handler exists yet, so this INFO setup wins.
    """
    level = logging.DEBUG if debug else logging.INFO
    # basicConfig only acts when no handler exists yet; force the level so the
    # parallel runner's progress/summary is shown even if a handler (e.g. the
    # token-masking filter) was already attached.
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    logging.getLogger().setLevel(level)
