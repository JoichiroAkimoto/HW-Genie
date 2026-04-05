import argparse
import os
import sys
import json
from hw_genie.core.client import HWClient, load_session_headers
from hw_genie.core.auth import load_session, update_session_with_headers, extract_headers_from_curl, extract_payload_from_curl
from hw_genie.commands.hero_raid import run_hero_raid
from hw_genie.commands.item_raid import run_item_raid
from hw_genie.commands.hero_shopping import run_hero_shopping
from hw_genie.commands.daily_raid import run_daily_raid
from hw_genie.commands.auth_server import run_server


def _prepare_info_for_json(info: dict) -> dict:
    """JSON出力用に PlayerStatus オブジェクトを辞書に変換する"""
    if "player" in info and hasattr(info["player"], "to_dict"):
        output = info.copy()
        output["player"] = info["player"].to_dict()
        return output
    return info


def cmd_auth(args):
    """認証情報の更新・表示"""
    account_alias = args.account or "default"

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
        print(json.dumps(session_data.get("headers"), indent=2))


def cmd_auth_server(args):
    """認証キャプチャサーバーを起動"""
    port = args.port or int(os.environ.get("HW_GENIE_AUTH_PORT", 8765))
    run_server(port=port, once=args.once)


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
        headers = load_session_headers()

    if not headers:
        print("Error: No session found. Please provide a valid curl with --curl.")
        sys.exit(1)

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
        headers = load_session_headers()

    if not headers:
        print("Error: No session found. Please provide a valid curl with --curl.")
        sys.exit(1)

    if not payload:
        print("Error: No payload provided. Use --curl or provide a JSON payload.")
        sys.exit(1)

    client = HWClient(headers)
    run_item_raid(client, payload, args.times)


def cmd_shop(args):
    """ショップ購入"""
    headers = load_session_headers()
    if not headers:
        print("Error: No session found.")
        sys.exit(1)

    client = HWClient(headers)
    from hw_genie.commands.hero_shopping import TARGET_SHOP_IDS
    run_hero_shopping(client, buy_soul_shop_items=True, hero_shop_ids=TARGET_SHOP_IDS)
    client.exchange_stones()


def cmd_daily(args):
    """デイリーレイド実行"""
    headers = None
    item_payload = None

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
        item_payload = extract_payload_from_curl(args.curl)
        if not item_payload:
            print("Error: Could not extract JSON payload from the provided curl command.")
            sys.exit(1)

    # セッション情報の読み込み（curlがない場合、または抽出に失敗した場合）
    if not headers:
        headers = load_session_headers()

    if not headers:
        print("Error: No session found. Please provide a valid curl with --curl.")
        sys.exit(1)

    client = HWClient(headers)
    run_daily_raid(client, item_payload=item_payload)


def main():
    parser = argparse.ArgumentParser(prog="hw-genie", description="Hero Wars Genie CLI")
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Auth
    p_auth = subparsers.add_parser("auth", help="Authentication management")
    p_auth.add_argument("--account", "-a", help="Account alias")
    p_auth.add_argument("--update", "-u", help="Update session with JSON headers")
    p_auth.add_argument("--curl", "-c", help="Update session with curl command")
    p_auth.add_argument("--info", "-i", action="store_true", help="Get player info and update session")
    p_auth.set_defaults(func=cmd_auth)

    # Auth Server
    p_auth_server = subparsers.add_parser("auth-server", help="Start auth capture server")
    p_auth_server.add_argument("--port", "-p", type=int, help="Port to listen on (default: 8765, env: HW_GENIE_AUTH_PORT)")
    p_auth_server.add_argument("--once", action="store_true", help="Exit after first successful auth capture")
    p_auth_server.set_defaults(func=cmd_auth_server)

    # Raid
    p_raid = subparsers.add_parser("raid", help="Raid operations")
    raid_sub = p_raid.add_subparsers(dest="raid_type", help="Raid type")

    # Raid Hero
    p_raid_hero = raid_sub.add_parser("hero", help="Hero mission raid")
    p_raid_hero.add_argument("--account", "-a", help="Account alias")
    p_raid_hero.add_argument("mission_ids", type=int, nargs="*", help="Mission IDs (space separated)")
    p_raid_hero.add_argument("--curl", "-c", help="Curl command to extract auth headers")
    p_raid_hero.add_argument("--times", "-t", type=int, default=3, help="Number of raids")
    p_raid_hero.set_defaults(func=cmd_raid_hero)

    # Raid Item
    p_raid_item = raid_sub.add_parser("item", help="Item raid using payload")
    p_raid_item.add_argument("--account", "-a", help="Account alias")
    p_raid_item.add_argument("payload", nargs="?", help="JSON payload string or path to JSON file")
    p_raid_item.add_argument("--curl", "-c", help="Curl command to extract item raid payload")
    p_raid_item.add_argument("--times", "-t", type=int, default=9999, help="Number of raids")
    p_raid_item.set_defaults(func=cmd_raid_item)

    # Shop
    p_shop = subparsers.add_parser("shop", help="Shop operations")
    p_shop.set_defaults(func=cmd_shop)

    # Daily
    p_daily = subparsers.add_parser("daily", help="Daily routine")
    p_daily.add_argument("--account", "-a", help="Account alias")
    p_daily.add_argument("--curl", "-c", help="Curl command to extract item raid payload")
    p_daily.set_defaults(func=cmd_daily)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    from hw_genie.core.client import HWAuthError
    try:
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
