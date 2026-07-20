import argparse
import logging
import os
import sys
import json
from hw_genie.core.client import HWClient, load_session_headers
from hw_genie.core.auth import load_session, update_session_with_headers, extract_headers_from_curl, extract_payload_from_curl
from hw_genie.core.utils import format_number_with_suffix
from hw_genie.commands.hero_raid import run_hero_raid
from hw_genie.commands.item_raid import run_item_raid
from hw_genie.commands.hero_shopping import run_hero_shopping
from hw_genie.commands.daily_raid import run_daily_raid
from hw_genie.commands.auth_server import run_server
from hw_genie.runner import run_all_accounts, summarize


def _prepare_info_for_json(info: dict) -> dict:
    """JSON出力用に PlayerStatus オブジェクトを辞書に変換する"""
    if "player" in info and hasattr(info["player"], "to_dict"):
        output = info.copy()
        output["player"] = info["player"].to_dict()
        return output
    return info


def _ensure_session(args) -> dict[str, str]:
    """セッションヘッダーを検証し、なければエラーを出して終了する"""
    headers = load_session_headers(args.account)
    if not headers:
        account_name = args.account or "default"
        print(f"Error: Session not found for account '{account_name}'. Please provide a valid curl with --curl.")
        sys.exit(1)
    return headers


def cmd_auth(args):
    """認証情報の更新・表示"""
    # 一覧表示
    if args.list or getattr(args, "list_names", False):
        from hw_genie.core.session_manager import SessionManager

        accounts = SessionManager.list_accounts()
        if not accounts:
            print("No accounts found in database.")
            return

        if getattr(args, "list_names", False):
            for alias in sorted(accounts):
                print(alias)
            return

        from hw_genie.core.utils import (
            display_timezone_name,
            format_timestamp_for_display,
        )

        tz_label = display_timezone_name()
        updated_col = f"Updated ({tz_label})"
        # Body timestamps are 19 chars ("YYYY-MM-DD HH:MM:SS"); widen the column
        # to the header label when the tz name makes it longer (e.g. Asia/Tokyo).
        updated_width = max(19, len(updated_col))

        header = f"\n{'Name':<10} | {'Arena':<5} | {'GA':<4} | {'Gold':<6} | {'Gems':<6} | {'Last Mission':<12} | {'Energy':<6} | {updated_col:<{updated_width}} | {'Memo':<20}"
        print(header)
        print("-" * len(header))
        for alias in sorted(accounts):
            data = SessionManager.load(alias)
            player = data.get("player", {})
            p_name = player.get("name", "Unknown")
            # 10文字を超える場合は「...」で省略
            p_name_display = (p_name[:7] + "...") if len(p_name) > 10 else p_name

            p_energy = player.get("energy", "-")
            p_arena = player.get("arena_rank", "-")
            p_grand = player.get("grand_rank", "-")
            p_gold = format_number_with_suffix(player.get("gold", 0)) if player.get("gold") is not None else "-"
            p_gems = format_number_with_suffix(player.get("gems", 0)) if player.get("gems") is not None else "-"
            p_last_id = data.get("last_item_raid_mission_id", "-")

            updated = data.get("last_updated", "Never")
            updated_short = format_timestamp_for_display(updated)

            memo = data.get("memo", "-")
            memo_display = (memo[:17] + "...") if len(memo) > 20 else memo

            print(f"{p_name_display:<10} | {p_arena:<5} | {p_grand:<4} | {p_gold:<6} | {p_gems:<6} | {p_last_id:<12} | {p_energy:<6} | {updated_short:<{updated_width}} | {memo_display:<20}")
        print()
        return

    account_alias = args.account or "default"

    # curl等による更新前に、単体でmemoが指定された場合
    if args.memo is not None:
        from hw_genie.core.session_manager import SessionManager
        session_data = SessionManager.load(account_alias)
        if not session_data:
            print(f"Error: Account '{account_alias}' not found in database. Please register the account first using --curl.")
            sys.exit(1)
        
        session_data["memo"] = args.memo
        SessionManager.save(account_alias, session_data)
        print(f"Successfully updated memo for account '{account_alias}' to: '{args.memo}'")
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
    session_data = load_session(account_alias)
    if not session_data:
        print(f"Error: Session not found for account '{account_alias}'")
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
    account_alias = args.account or "default"

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
    account_alias = args.account or "default"

    # curlコマンドから情報を抽出
    if args.curl:
        # 1. 認証ヘッダーを抽出してセッションを更新
        auth_headers = extract_headers_from_curl(args.curl)
        if auth_headers:
            info = update_session_with_headers(auth_headers, account_alias)
            if info["status"] == "success":
                headers = info["headers"]
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
    run_item_raid(client, payload, args.times)


def cmd_shop(args):
    """ショップ購入"""
    headers = _ensure_session(args)

    client = HWClient(headers)
    from hw_genie.commands.hero_shopping import TARGET_SHOP_IDS

    run_hero_shopping(client, buy_soul_shop_items=True, hero_shop_ids=TARGET_SHOP_IDS)
    client.exchange_stones()


def cmd_daily(args):
    """デイリーレイド実行"""
    headers = None
    item_payload = {}

    # curlコマンドから情報を抽出
    if args.curl:
        # 1. 認証ヘッダーを抽出してセッションを更新
        auth_headers = extract_headers_from_curl(args.curl)
        if auth_headers:
            info = update_session_with_headers(auth_headers, args.account or "default")
            if info["status"] == "success":
                headers = info["headers"]
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
    run_daily_raid(client, item_payload=item_payload, account_alias=args.account or "default")


def cmd_multi(args):
    """Run a routine against all accounts inside a single process (parallel)."""
    from hw_genie.runner import (
        daily_routine,
        full_routine,
        list_account_aliases,
    )

    mode = args.mode
    accounts = args.accounts
    if accounts:
        accounts = list(accounts)
    else:
        accounts = list_account_aliases()

    routine = full_routine if mode == "full" else daily_routine

    results = run_all_accounts(routine, accounts=accounts, max_parallel=args.parallel)
    failed = summarize(results.items())
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
        choices=["daily", "full"],
        nargs="?",
        default="daily",
        help="Routine to run: 'daily' (default) or 'full' (raid+shop+daily)",
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
        help="Max concurrent accounts (default: HWDA_MAX_PARALLEL / unbounded)",
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
