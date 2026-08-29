import argparse
import getpass
import logging
import os
import socket
import sys
import json
from functools import partial
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


def _positive_int(value: str) -> int:
    """argparse type: a positive integer (rejects 0 and negatives)."""
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid positive int value: {value!r}"
        ) from exc
    if n <= 0:
        raise argparse.ArgumentTypeError(f"must be positive: {value!r}")
    return n


def _run_host_identifier() -> str:
    """Return the execution environment identifier (``user@host``) for run_logs.

    ``HWGENIE_HOST`` explicitly overrides everything (used when a custom label
    is desired). Otherwise the user comes from ``HWGENIE_USER`` →
    ``HWGENIE_USER_UNIX`` → ``USER`` → ``USERNAME`` →
    :func:`getpass.getuser`, and the host from ``HWGENIE_MACHINE`` →
    ``HWGENIE_MACHINE_UNIX`` → ``COMPUTERNAME`` → ``HOSTNAME`` →
    :func:`socket.gethostname`. The ``HWGENIE_USER`` / ``HWGENIE_MACHINE``
    pair lets Docker Compose forward the host's own ``USERNAME`` /
    ``COMPUTERNAME`` (Windows), and the ``*_UNIX`` pair the ``USER`` /
    ``HOSTNAME`` (Mac/Linux), without any .env setup.

    Exception-safe: user lookup can raise in containers (e.g. ``--user`` with
    no matching passwd entry and no USER vars), where the identifier falls
    back to ``unknown``. Never raises, so run-log recording (best-effort)
    cannot crash the actual run.
    """
    explicit = os.environ.get("HWGENIE_HOST")
    if explicit:
        return explicit
    user = (
        os.environ.get("HWGENIE_USER")
        or os.environ.get("HWGENIE_USER_UNIX")
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
    )
    machine = (
        os.environ.get("HWGENIE_MACHINE")
        or os.environ.get("HWGENIE_MACHINE_UNIX")
        or os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
    )
    if not user:
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001 - best-effort identifier
            user = "unknown"
    if not machine:
        try:
            machine = socket.gethostname()
        except Exception:  # noqa: BLE001 - best-effort identifier
            machine = "unknown"
    return f"{user}@{machine}"


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
    max_iterations = (
        args.iterations if args.iterations is not None else args.times
    )
    run_item_raid(
        client,
        payload,
        max_iterations=max_iterations,
        account=account_alias or curl_player_name,
    )


def cmd_shop(args):
    """ショップ購入"""
    headers = _ensure_session(args)

    client = HWClient(headers)
    from hw_genie.commands.hero_shopping import TARGET_SHOP_IDS

    run_hero_shopping(client, buy_soul_shop_items=True, hero_shop_ids=TARGET_SHOP_IDS)
    client.exchange_stones()


def cmd_inventory(args):
    """consumable 等の在庫表示"""
    from hw_genie.commands.consumables import run_inventory

    headers = _ensure_session(args)
    client = HWClient(headers)

    raw = run_inventory(client, show_all=bool(args.all), min_amount=int(args.min or 0))
    if args.raw:
        print(json.dumps(raw, indent=2))


def cmd_consumable_run(args):
    """登録済み consumable の一括消費"""
    from hw_genie.commands.consumables import run_consumable_use

    headers = _ensure_session(args)
    client = HWClient(headers)

    results = run_consumable_use(
        client,
        lib_ids=args.lib_ids or None,
        method_override=args.method,
        dry_run=bool(args.dry_run),
        account_alias=None,
    )
    from hw_genie.core.client import ResponseStatus

    if any(r.status in (ResponseStatus.ERROR, ResponseStatus.UNEXPECTED) for r in results):
        sys.exit(1)


def cmd_asgard_shop(args):
    """Asgard（ギルドレイド）ショップの自動購入（Osh / Maestro 週）"""
    headers = _ensure_session(args)

    client = HWClient(headers)
    from hw_genie.commands.asgard_shop import run_asgard_shop

    result = run_asgard_shop(
        client,
        dry_run=bool(args.dry_run),
        account_alias=args.account or None,
        gold_buffs=args.gold_buffs,
    )
    if result.error:
        sys.exit(1)


def cmd_quests(args):
    """クエスト（デイリー等）の取得・表示"""
    from hw_genie.commands.quests import (
        QUEST_GUILD_DEFAULTS_KEY,
        classify_quest,
        edit_quest_defaults_interactive,
        ensure_quest_defaults,
        ensure_quest_guild_defaults,
        run_quest_execute,
        run_quest_status,
        set_quest_defaults,
        set_quest_guild_defaults,
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

    # quest_defaults / quest_guild_defaults を初期化する（QUEST_OPERATIONS 登録済み
    # クエスト + ギルドクエスト設定を enabled:false で投入）
    if args.init_defaults:
        defaults = ensure_quest_defaults(account)
        print(f"ℹ️  Initialized quest_defaults for {account}:")
        for qid in sorted(defaults):
            category, name = classify_quest(qid)
            print(f"    - {qid} ({name}) enabled={defaults[qid].get('enabled', False)}")
        guild_defaults = ensure_quest_guild_defaults(account)
        print(f"ℹ️  Initialized {QUEST_GUILD_DEFAULTS_KEY} for {account}:")
        print(f"    - guild quests (Sparks of Power) enabled={guild_defaults.get('enabled', False)}")
        return

    # アカウント固有の操作引数上書き（quest_defaults / quest_guild_defaults）を 1 件登録する
    if args.set_default:
        quest_id, key, value = args.set_default
        if str(quest_id) == "guild":
            stored = set_quest_guild_defaults(account, key, value)
            print(f"ℹ️  Registered quest_guild_defaults[{key}] = {stored} ({type(stored).__name__}) for {account}")
        else:
            stored = set_quest_defaults(account, int(quest_id), key, value)
            _, name = classify_quest(int(quest_id))
            print(f"ℹ️  Registered quest_defaults[{quest_id} ({name})][{key}] = {stored} ({type(stored).__name__}) for {account}")
        return

    if args.execute or args.dry_run:
        _, failed, _ = run_quest_execute(
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
        item_max_iterations=args.iterations,
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
    import traceback
    from datetime import datetime, timezone

    from hw_genie.core.run_log import OutputCapture, record_run_log
    from hw_genie.runner import (
        asgard_shop_routine,
        consumable_routine,
        daily_routine,
        full_routine,
        list_account_aliases,
        quests_routine,
        summarize_asgard_shop,
        summarize_consumable,
        summarize_quests,
    )

    mode = args.mode
    accounts = args.accounts
    if accounts:
        accounts = list(accounts)
    else:
        accounts = list_account_aliases()

    dry_run = bool(getattr(args, "dry_run", False))
    if mode not in ("quests", "consumable") and dry_run:
        print(
            "Error: --dry-run is only supported with the 'quests' and 'consumable' modes "
            "(daily/full/asgard-shop routines always execute their operations).",
            file=sys.stderr,
        )
        sys.exit(2)

    if mode == "quests":
        routine = quests_routine(dry_run=dry_run)
        # dry-run は計画表示のため逐次実行（出力がアカウント順に並び、確認しやすい）
        max_parallel = 1 if dry_run else args.parallel
    elif mode == "asgard-shop":
        routine = asgard_shop_routine(gold_buffs=args.gold_buffs)
        max_parallel = args.parallel
    elif mode == "consumable":
        routine = consumable_routine(
            lib_ids=args.lib, method_override=args.method, dry_run=dry_run
        )
        max_parallel = 1 if dry_run else args.parallel
    else:
        routine = partial(
            full_routine if mode == "full" else daily_routine,
            item_max_iterations=getattr(args, "iterations", 9999),
        )
        max_parallel = args.parallel

    # 実行ログ（run_logs）記録: 実行中の出力をキャプチャし、終了時に 1 レコード
    # 書き込む（best-effort: DB 失敗でも実行自体は落とさない）。
    started_at = datetime.now(timezone.utc)
    capture = OutputCapture()
    results: dict = {}
    try:
        with capture:
            results = run_all_accounts(
                routine, accounts=accounts, max_parallel=max_parallel
            )
            if mode == "quests":
                failed = summarize_quests(results.items(), dry_run=dry_run)
            elif mode == "asgard-shop":
                failed = summarize_asgard_shop(results.items())
            elif mode == "consumable":
                failed = summarize_consumable(results.items(), dry_run=dry_run)
            else:
                failed = summarize(results.items())
    except BaseException as exc:
        # 例外・割り込み（KeyboardInterrupt 等）でも失敗として記録する。
        # トレースは main() のハンドラが capture 終了後に stderr へ出すため、
        # ここでキャプチャ済み出力に追記して DB 側にも残す。ハンドラ内の
        # サマリ構築が失敗しても元例外を隠蔽しないよう防御する。
        trace = traceback.format_exc()
        if isinstance(exc, SystemExit) and isinstance(exc.code, int):
            exit_code = exc.code
        elif isinstance(exc, KeyboardInterrupt):
            exit_code = 130
        else:
            exit_code = 1
        try:
            accounts = _build_run_log_summary(mode, results)[0]
        except Exception:  # pragma: no cover - defensive
            accounts = []
        record_run_log(
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            mode=mode,
            status="failed",
            exit_code=exit_code,
            accounts=accounts,
            error_summary=str(exc) or type(exc).__name__,
            log_text=(capture.getvalue() + "\n" + trace).strip() or None,
            log_file=os.environ.get("HWGENIE_LOG_FILE"),
            hostname=_run_host_identifier(),
        )
        raise
    account_logs, error_summary = _build_run_log_summary(mode, results)
    record_run_log(
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        mode=mode,
        status="failed" if failed else "ok",
        exit_code=1 if failed else 0,
        accounts=account_logs,
        error_summary=error_summary,
        log_text=capture.getvalue() or None,
        log_file=os.environ.get("HWGENIE_LOG_FILE"),
        hostname=_run_host_identifier(),
    )
    if failed:
        sys.exit(1)


def _run_log_account_failure(
    mode: str, result: object | None, err: BaseException | None
) -> str | None:
    """Return the failure reason for one account's run-log entry, or None when ok.

    Mirrors the per-account failure judgement of the runner's ``summarize_*``
    functions so ``run_logs`` rows stay consistent with the printed summary:
    quest failures, consumable ERROR/UNEXPECTED items, Asgard purchase errors
    and unavailable statuses all count as failures, matching the ``failed``
    counter returned by ``summarize``. Exceptions are reported by their first
    message line.
    """
    if err is not None:
        message = str(err).strip().splitlines()
        return message[0] if message else type(err).__name__
    if mode == "quests":
        if isinstance(result, tuple) and len(result) == 3:
            return f"{len(result[1])} quest(s) failed" if result[1] else None
        return "quest result unavailable"
    if mode == "consumable":
        from hw_genie.core.client import ResponseStatus

        if isinstance(result, list):
            errors = sum(
                1
                for r in result
                if r.status in (ResponseStatus.ERROR, ResponseStatus.UNEXPECTED)
            )
            return f"{errors} consumable use(s) failed" if errors else None
        return "consumable result unavailable"
    if mode == "asgard-shop":
        from hw_genie.commands.asgard_shop import AsgardRunResult

        if isinstance(result, AsgardRunResult):
            if result.error is not None:
                return f"shop fetch failed: {result.error}"
            return (
                f"{result.failed_count} purchase error(s)"
                if result.failed_count
                else None
            )
        return "asgard-shop result unavailable"
    # daily / full: 最終ステータスが取れない場合のみ失敗（summarize と同様）。
    from hw_genie.core.client import PlayerStatus

    if not isinstance(result, PlayerStatus) or not result.is_valid:
        return "status unavailable"
    return None


def _build_run_log_summary(
    mode: str,
    results: dict[str, tuple[object | None, BaseException | None]],
) -> tuple[list[dict], str | None]:
    """Build the per-account summary list and error summary for ``run_logs``.

    An entry with ``ok: false`` carries the failure reason (exception message,
    quest/consumable/Asgard failure count, or unavailable status). Returns
    ``(entries, error_summary)``; ``error_summary`` is ``None`` when no account
    failed.
    """
    entries: list[dict] = []
    failed_accounts: list[str] = []
    for account, (res, err) in results.items():
        reason = _run_log_account_failure(mode, res, err)
        if reason is None:
            entries.append({"account": account, "ok": True, "error": None})
            continue
        entries.append({"account": account, "ok": False, "error": reason})
        failed_accounts.append(f"{account} ({reason})")
    error_summary = None
    if failed_accounts:
        error_summary = (
            f"{len(failed_accounts)} account(s) failed: "
            + ", ".join(failed_accounts)
        )
    return entries, error_summary


def cmd_log_ls(args):
    """List recent run logs (newest first)."""
    from hw_genie.core.run_log import list_run_logs
    from hw_genie.core.utils import format_timestamp_for_display

    rows = list_run_logs(limit=args.limit)
    if not rows:
        print("No run logs recorded yet.")
        return
    for row in rows:
        ok = sum(1 for a in row.accounts if a.get("ok"))
        total = len(row.accounts)
        started = format_timestamp_for_display(row.started_at.isoformat())
        host = row.hostname or "-"
        print(
            f"{row.id:>4}  {started}  {row.mode:<11} "
            f"{row.status:<6} {ok}/{total} ok  exit={row.exit_code}  {host}"
        )


def cmd_log_show(args):
    """Show one run log in full (metadata + captured output)."""
    from hw_genie.core.run_log import get_run_log
    from hw_genie.core.utils import format_timestamp_for_display

    row = get_run_log(args.run_id)
    if row is None:
        print(f"Run log #{args.run_id} not found.")
        sys.exit(1)
    print(f"ID:       {row.id}")
    print(f"Started:  {format_timestamp_for_display(row.started_at.isoformat())}")
    print(f"Finished: {format_timestamp_for_display(row.finished_at.isoformat())}")
    print(f"Mode:     {row.mode}")
    print(f"Status:   {row.status}  (exit code {row.exit_code})")
    if row.hostname:
        print(f"Host:     {row.hostname}")
    if row.log_file:
        print(f"Log file: {row.log_file}")
    if row.error_summary:
        print(f"Errors:   {row.error_summary}")
    for entry in row.accounts:
        if entry.get("ok"):
            print(f"  - {entry['account']}: ok")
        else:
            print(f"  - {entry['account']}: failed ({entry.get('error')})")
    if row.log_text:
        print("\n--- Output ---")
        print(row.log_text, end="" if row.log_text.endswith("\n") else "\n")


def cmd_titan_arena(args):
    """タイタンアリーナ自動バトル実行"""
    headers = None
    # "default" リテラルを作らず resolve_account 経由で解決する
    try:
        account_alias: str | None = resolve_account(args.account)
    except AccountResolutionError:
        account_alias = args.account

    # curlコマンドから認証情報を抽出
    if args.curl:
        auth_headers = extract_headers_from_curl(args.curl)
        if auth_headers:
            info = update_session_with_headers(auth_headers, account_alias)
            if info["status"] == "success":
                headers = info["headers"]
                print(f"Successfully updated session for {info['player'].name} from curl.")
                # curl 登録で実名が確定したら account_alias を更新
                try:
                    account_alias = resolve_account(info["player"].name)
                except AccountResolutionError:
                    account_alias = info["player"].name
            else:
                print(f"Warning: Could not update session from curl: {info.get('message')}")

    # セッション情報の読み込み（curlがない場合、または抽出に失敗した場合）
    if not headers:
        headers = _ensure_session(args)
        # _ensure_session 後に canonical な account_alias を再解決
        try:
            account_alias = resolve_account(args.account)
        except AccountResolutionError:
            pass

    client = HWClient(headers)

    # 任意の編成リスト（--teams で "4023,4043,4024,4022,4040;..." のように指定）
    team_rotation = None
    if getattr(args, "teams", None):
        try:
            team_rotation = []
            for group in args.teams.split(";"):
                ids = [int(x.strip()) for x in group.split(",") if x.strip()]
                if ids:
                    team_rotation.append(ids)
            if team_rotation is not None and len(team_rotation) == 0:
                print("Error: Invalid --teams value: empty", file=sys.stderr)
                sys.exit(1)
        except ValueError as e:
            print(f"Error: Invalid --teams value: {e}", file=sys.stderr)
            sys.exit(1)

    rival_id = args.rival or "default"
    if rival_id in (None, "default", ""):
        from hw_genie.commands.titan_arena import DEFAULT_RIVAL_ID
        rival_id = DEFAULT_RIVAL_ID

    # 戦闘シミュレーター（正しい progress 生成のため）
    battle_sim = None
    if getattr(args, "battle_sim", None) == "hwh":
        # HWH 拡張の BattleCalc を CDP 経由で呼ぶ戦闘シミュレーター
        from hw_genie.commands.titan_sim_hwh import TitanSimulatorHWH

        battle_sim = TitanSimulatorHWH(headers=headers)

    if getattr(args, "auto", False):
        from hw_genie.commands.titan_arena import run_titan_arena_auto

        run_titan_arena_auto(
            client,
            initial_rival_id=rival_id,
            team_rotation=team_rotation,
            max_attempts_per_team=args.max_attempts,
            max_stages=getattr(args, "max_stages", 20),
            account=account_alias,
            battle_sim=battle_sim,
        )
    else:
        from hw_genie.commands.titan_arena import run_titan_arena

        run_titan_arena(
            client,
            rival_id=rival_id,
            team_rotation=team_rotation,
            max_attempts_per_team=args.max_attempts,
            account=account_alias,
            battle_sim=battle_sim,
        )


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
    p_raid_item.add_argument("--times", "-t", type=int, default=9999, help="Item raid iteration count (alias of --iterations)")
    p_raid_item.add_argument("--iterations", type=int, default=None, help="Item raid iteration count (alias of --times)")
    p_raid_item.set_defaults(func=cmd_raid_item)

    # Shop
    p_shop = subparsers.add_parser("shop", parents=[parent_parser], help="Shop operations")
    p_shop.set_defaults(func=cmd_shop)

    # Inventory (consumable 等の在庫表示)
    p_inventory = subparsers.add_parser(
        "inventory", parents=[parent_parser], help="Show inventory (consumable by default)"
    )
    p_inventory.add_argument("--all", action="store_true", help="Show all inventory categories")
    p_inventory.add_argument("--min", type=int, default=0, help="Only show items with at least N in stock")
    p_inventory.add_argument("--raw", action="store_true", help="Print the raw inventoryGet response as JSON")
    p_inventory.set_defaults(func=cmd_inventory)

    # Consumable
    p_consumable = subparsers.add_parser("consumable", help="Consumable operations")
    consumable_sub = p_consumable.add_subparsers(dest="consumable_type", help="Consumable operation")

    # Consumable Run (登録済みアイテムの一括消費)
    p_consumable_run = consumable_sub.add_parser(
        "run", parents=[parent_parser], help="Consume all registered consumables"
    )
    p_consumable_run.add_argument(
        "lib_ids", type=int, nargs="*", help="Target libIds (default: CONSUMABLE_USE_TARGETS)"
    )
    p_consumable_run.add_argument(
        "--method", help="Override the RPC method (e.g. consumableUseLootBox)"
    )
    p_consumable_run.add_argument(
        "--dry-run", action="store_true", help="Show the consumption plan without consuming anything"
    )
    p_consumable_run.set_defaults(func=cmd_consumable_run)

    # Asgard Shop (Guild Raid merchant; Osh / Maestro weeks)
    p_asgard_shop = subparsers.add_parser(
        "asgard-shop",
        parents=[parent_parser],
        help="Asgard Guild Raid shop operations (Osh / Maestro weeks)",
    )
    p_asgard_shop.add_argument(
        "--dry-run", action="store_true", help="Show the purchase plan without buying anything"
    )
    gold_group = p_asgard_shop.add_mutually_exclusive_group()
    gold_group.add_argument(
        "--gold",
        dest="gold_buffs",
        action="store_true",
        default=None,
        help="Buy gold buffs (slot 1-5; default: off for Osh week, on for Maestro week)",
    )
    gold_group.add_argument(
        "--no-gold",
        dest="gold_buffs",
        action="store_false",
        help="Skip gold buff purchases (slot 1-5)",
    )
    p_asgard_shop.set_defaults(func=cmd_asgard_shop)

    # Daily
    p_daily = subparsers.add_parser("daily", parents=[parent_parser], help="Daily routine")
    p_daily.add_argument("--curl", "-c", help="Curl command to extract item raid payload")
    p_daily.add_argument(
        "--iterations",
        type=int,
        default=9999,
        help="Item raid iteration count (each request raids 10 times). Default: until stamina runs out.",
    )
    p_daily.set_defaults(func=cmd_daily)

    # Titan Arena
    p_titan = subparsers.add_parser("titan-arena", parents=[parent_parser], help="Titan Arena auto-battle")
    p_titan.add_argument("--curl", "-c", help="Curl command to extract auth headers")
    p_titan.add_argument("--rival", "-r", help="Rival ID (default: built-in constant)")
    p_titan.add_argument(
        "--teams",
        help="Semicolon-separated titan team compositions, e.g. '4023,4043,4024,4022,4040;4023,4043,4024,4022,4044'",
    )
    p_titan.add_argument(
        "--max-attempts",
        "-m",
        type=int,
        default=10,
        help="Max attempts per team before switching to next team (default: 10)",
    )
    p_titan.add_argument(
        "--auto",
        "-A",
        action="store_true",
        help="Auto-advance through stages: after each win, call titanArenaCompleteTier to "
        "progress; stops at the final stage (tier == maxTier).",
    )
    p_titan.add_argument(
        "--max-stages",
        "-S",
        type=int,
        default=20,
        help="Auto mode: maximum number of stages to attempt (safety cap, default: 20)",
    )
    p_titan.add_argument(
        "--battle-sim",
        choices=["hwh"],
        default=None,
        help="Battle simulator: 'hwh' uses the Hero Wars Helper extension "
        "(Chrome remote-debugging) to compute the real battle progress. "
        "Required for the server to accept wins (the server recomputes the "
        "battle from seed+placement and verifies the submitted HP).",
    )
    p_titan.set_defaults(func=cmd_titan_arena)

    # Quests
    p_quests = subparsers.add_parser("quests", parents=[parent_parser], help="Quest status (daily quests)")
    p_quests.add_argument("--show-all", action="store_true", help="Show completed quests too (default: uncompleted only)")
    p_quests.add_argument("--raw", action="store_true", help="Print the raw questGetAll response as JSON")
    p_quests.add_argument("--category", choices=["daily", "weekly", "guild", "main", "event", "battlepass", "one_time", "unknown"], help="Filter by quest category")
    p_quests.add_argument("--execute", action="store_true", help="Execute operations to complete uncompleted daily quests (destructive; asks confirmation per step unless --yes)")
    p_quests.add_argument("--dry-run", action="store_true", help="Show the quest execution plan without running anything")
    p_quests.add_argument("--yes", action="store_true", help="Skip per-step confirmation (only valid with --execute)")
    p_quests.add_argument("--set-default", nargs=3, metavar=("QUEST_ID", "KEY", "VALUE"), help="Register an account-specific operation arg override (e.g. --set-default 10024 heroId 999, or --set-default guild enabled true)")
    p_quests.add_argument("--init-defaults", action="store_true", help="Initialize quest_defaults and quest_guild_defaults for the account (seed as enabled=false)")
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
        choices=["daily", "full", "quests", "asgard-shop", "consumable"],
        nargs="?",
        default="daily",
        help="Routine to run: 'daily' (default), 'full' (raid+shop+daily), 'quests' (daily quest auto-completion), 'asgard-shop' (Osh/Maestro Guild Raid merchant auto-buy), or 'consumable' (consume all registered consumables)",
    )
    gold_group = p_multi.add_mutually_exclusive_group()
    gold_group.add_argument(
        "--gold",
        dest="gold_buffs",
        action="store_true",
        default=None,
        help="Buy gold buffs (slot 1-5) in the 'asgard-shop' mode (default: off for Osh week, on for Maestro week)",
    )
    gold_group.add_argument(
        "--no-gold",
        dest="gold_buffs",
        action="store_false",
        help="Skip gold buff purchases (slot 1-5) in the 'asgard-shop' mode",
    )
    p_multi.add_argument(
        "--lib",
        type=int,
        action="append",
        help="Target consumable libId for the 'consumable' mode (repeatable; default: registered targets)",
    )
    p_multi.add_argument(
        "--method",
        help="Override the RPC method for the 'consumable' mode (e.g. consumableUseLootBox)",
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
        help="Show the execution plan without running anything (quests/consumable modes only)",
    )
    p_multi.add_argument(
        "--iterations",
        type=int,
        default=9999,
        help="Item raid iteration count for 'daily'/'full' modes (each request raids 10 times). Default: until stamina runs out.",
    )
    p_multi.set_defaults(func=cmd_multi)

    p_log = subparsers.add_parser(
        "log", help="Show execution run logs (stored in the database)"
    )
    log_sub = p_log.add_subparsers(dest="log_command", required=True)
    p_log_ls = log_sub.add_parser("ls", help="List recent run logs (newest first)")
    p_log_ls.add_argument(
        "--limit",
        "-n",
        type=_positive_int,
        default=10,
        help="Max rows to show (default: 10)",
    )
    p_log_ls.set_defaults(func=cmd_log_ls)
    p_log_show = log_sub.add_parser("show", help="Show one run log in full")
    p_log_show.add_argument("run_id", type=int, help="Run log ID from `log ls`")
    p_log_show.set_defaults(func=cmd_log_show)

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
