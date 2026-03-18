import argparse
import sys
import json
from hw_genie.core.client import HWClient, load_session_headers
from hw_genie.core.auth import load_session, update_session_with_headers
from hw_genie.commands.hero_raid import run_hero_raid
from hw_genie.commands.item_raid import run_item_raid
from hw_genie.commands.hero_shopping import run_hero_shopping
from hw_genie.commands.daily_raid import run_daily_raid


def cmd_auth(args):
    """認証情報の更新・表示"""
    account_alias = args.account or "default"

    # curl等からヘッダーJSONが渡された場合
    if args.update:
        try:
            headers = json.loads(args.update)
            info = update_session_with_headers(headers, account_alias)
            if info["status"] == "success":
                print(f"Successfully updated session for {info['player']['name']}")
                print(json.dumps(info, indent=2))
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
        print(json.dumps(info, indent=2))
    else:
        print(json.dumps(session_data.get("headers"), indent=2))


def cmd_raid_hero(args):
    """ヒーローレイド実行"""
    headers = load_session_headers()
    if not headers:
        print("Error: No session found. Please run 'hw-genie auth --update' first.")
        sys.exit(1)

    client = HWClient(headers)
    run_hero_raid(client, args.mission_ids, args.times)


def cmd_raid_item(args):
    """アイテムレイド実行"""
    headers = load_session_headers()
    if not headers:
        print("Error: No session found.")
        sys.exit(1)

    client = HWClient(headers)
    try:
        if args.payload.startswith("{"):
            payload = json.loads(args.payload)
        else:
            with open(args.payload, "r") as f:
                payload = json.load(f)
        run_item_raid(client, payload, args.times)
    except Exception as e:
        print(f"Error loading payload: {e}")
        sys.exit(1)


def cmd_shop(args):
    """ショップ購入"""
    headers = load_session_headers()
    if not headers:
        print("Error: No session found.")
        sys.exit(1)

    client = HWClient(headers)
    run_hero_shopping(client, soul_only=args.soul_only)
    client.exchange_stones()


def cmd_daily(args):
    """デイリーレイド実行"""
    headers = load_session_headers()
    if not headers:
        print("Error: No session found.")
        sys.exit(1)

    client = HWClient(headers)
    run_daily_raid(client)


def main():
    parser = argparse.ArgumentParser(prog="hw-genie", description="Hero Wars Genie CLI")
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Auth
    p_auth = subparsers.add_parser("auth", help="Authentication management")
    p_auth.add_argument("--account", "-a", help="Account alias")
    p_auth.add_argument("--update", "-u", help="Update session with JSON headers")
    p_auth.add_argument("--info", "-i", action="store_true", help="Get player info and update session")
    p_auth.set_defaults(func=cmd_auth)

    # Raid
    p_raid = subparsers.add_parser("raid", help="Raid operations")
    raid_sub = p_raid.add_subparsers(dest="raid_type", help="Raid type")

    # Raid Hero
    p_raid_hero = raid_sub.add_parser("hero", help="Hero mission raid")
    p_raid_hero.add_argument("mission_ids", type=int, nargs="*", help="Mission IDs (space separated)")
    p_raid_hero.add_argument("--times", "-t", type=int, default=3, help="Number of raids")
    p_raid_hero.set_defaults(func=cmd_raid_hero)

    # Raid Item
    p_raid_item = raid_sub.add_parser("item", help="Item raid using payload")
    p_raid_item.add_argument("payload", help="JSON payload string or path to JSON file")
    p_raid_item.add_argument("--times", "-t", type=int, default=10, help="Number of raids")
    p_raid_item.set_defaults(func=cmd_raid_item)

    # Shop
    p_shop = subparsers.add_parser("shop", help="Shop operations")
    p_shop.add_argument("--soul-only", action="store_true", help="Buy soul stones only")
    p_shop.set_defaults(func=cmd_shop)

    # Daily
    p_daily = subparsers.add_parser("daily", help="Daily routine")
    p_daily.set_defaults(func=cmd_daily)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    if hasattr(args, "func"):
        args.func(args)
    else:
        print(f"Command {args.command} not implemented yet.")


if __name__ == "__main__":
    main()
