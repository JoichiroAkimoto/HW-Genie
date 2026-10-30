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
from hw_genie.commands.titan_arena import AUTO_RIVAL_SCORE_THRESHOLD
from hw_genie.runner import (
    _is_toe_interrupt_exc,
    _toe_real_errors,
    multi_headers_ready as _runner_headers_ready,
    preflight_multi_accounts as _runner_preflight,
)
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


def _non_negative_int(value: str) -> int:
    """argparse type: an integer >= 0."""
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid non-negative int value: {value!r}"
        ) from exc
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be non-negative: {value!r}")
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


def _require_registered_account(account: str) -> None:
    """Exit with a clear error when the account has no DB row."""
    from hw_genie.core.session_manager import SessionManager

    if not SessionManager.load(account):
        print(
            f"Error: Account '{account}' is not registered in the database. "
            "Use `auth --list` to see registered accounts.",
            file=sys.stderr,
        )
        sys.exit(1)


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

    run_hero_shopping(client, buy_soul_shop_items=True, hero_shop_ids=TARGET_SHOP_IDS, buy_town_shop_items=True)
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


def cmd_seers_game(args):
    """Seer's Game をコイン枯渇まで自動周回する"""
    # argparse に choices= は付けない（live の event.size 上限は実行時に
    # _check_pick が検証するため）。ここでは正の整数であることだけを、
    # いかなる API 呼び出しよりも前に parser.error スタイル（stderr + exit 2）
    # で検査する（multi 分岐と共有の validate_seers_args を使用）。
    from hw_genie.commands.seers_game import validate_seers_args

    pick, max_games = validate_seers_args(
        getattr(args, "pick", 2), getattr(args, "max_games", None)
    )

    headers = _ensure_session(args)

    client = HWClient(headers)
    from hw_genie.commands.seers_game import SeersGameReadError, run_seers_game

    try:
        result = run_seers_game(
            client,
            pick=pick,
            max_games=max_games,
            dry_run=bool(args.dry_run),
            account_alias=args.account or None,
        )
    except (SeersGameReadError, ValueError) as e:
        # 読み取り失敗・pick 範囲外はトレースバックなしで exit 1。
        # HWAuthError / AccountResolutionError は main() の共通ハンドラに任せる。
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    # exit-code 判定（cmd_asgard_shop の `if result.error: sys.exit(1)` に対応）。
    # 枯渇 break（NotEnough。0 ゲームでも含む）は正常終了 exit 0。
    # 枯渇以外の last_error が残った場合（getState 読み取り失敗等）は失敗として exit 1。
    from hw_genie.commands.seers_game import is_clean_depletion

    if result.games_failed or (
        result.games_played == 0
        and result.last_error
        and not is_clean_depletion(result.last_error)
    ):
        print(f"Error: Seer's Game failed: {result.last_error}", file=sys.stderr)
        sys.exit(1)


def cmd_arena_status(args):
    """Show Arena rank, attempts/cooldown, saved team, and attack window."""
    from hw_genie.commands.arena import ArenaError, run_arena_status

    headers = _ensure_session(args)
    client = HWClient(headers)
    alias = resolve_account(args.account) if args.account else None
    try:
        run_arena_status(client, account_alias=alias)
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_arena_top(args):
    """Show the top-50 Arena ranking."""
    from hw_genie.commands.arena import ArenaError, run_arena_top

    headers = _ensure_session(args)
    client = HWClient(headers)
    try:
        run_arena_top(client, limit=args.limit, raw=bool(args.raw))
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_arena_find_enemies(args):
    """Call arenaFindEnemies once and save the candidate snapshot."""
    from hw_genie.commands.arena import ArenaError, run_arena_find_enemies

    account = resolve_account(args.account)
    _require_registered_account(account)
    client = HWClient(_ensure_session(args))
    try:
        run_arena_find_enemies(client, account, raw=bool(args.raw))
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_arena_snapshot(args):
    """Show the saved snapshot alongside current own/target ranks."""
    from hw_genie.commands.arena import ArenaError, run_arena_snapshot

    account = resolve_account(args.account)
    _require_registered_account(account)
    client = HWClient(_ensure_session(args))
    try:
        run_arena_snapshot(client, account)
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_arena_team_set(args):
    """Create/update a named Arena team preset."""
    from hw_genie.commands.arena import (
        ArenaError,
        parse_favor_args,
        run_arena_team_set,
    )

    account = resolve_account(args.account)
    _require_registered_account(account)
    client = HWClient(_ensure_session(args)) if args.from_saved else None
    try:
        favor = parse_favor_args(args.favor)
        run_arena_team_set(
            account,
            args.name,
            client=client,
            from_saved=bool(args.from_saved),
            heroes=args.heroes,
            pet=args.pet,
            banner=args.banner,
            favor=favor,
        )
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_arena_team_list(args):
    """List stored Arena team presets."""
    from hw_genie.commands.arena import ArenaError, run_arena_team_list

    account = resolve_account(args.account)
    _require_registered_account(account)
    try:
        run_arena_team_list(account)
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_arena_team_remove(args):
    """Delete a stored Arena team preset."""
    from hw_genie.commands.arena import ArenaError, run_arena_team_remove

    account = resolve_account(args.account)
    _require_registered_account(account)
    try:
        run_arena_team_remove(account, args.name)
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_arena_attack(args):
    """Attack a player ranked above the current account."""
    from hw_genie.commands.arena import (
        ArenaError,
        fetch_arena_status,
        load_arena_presets,
        load_arena_snapshot,
        parse_favor_args,
        resolve_snapshot_target,
        resolve_target,
        resolve_team,
        run_arena_attack,
        run_arena_attack_from_snapshot,
        validate_partial_team,
    )

    account = resolve_account(args.account)
    headers = _ensure_session(args)
    client = HWClient(headers)
    try:
        from_snapshot = bool(getattr(args, "from_snapshot", False))
        force = bool(getattr(args, "force", False))
        if force and not from_snapshot:
            raise ArenaError("--force is only valid with --from-snapshot.")

        favor = parse_favor_args(args.favor)
        # Validate direct flags before the first API call. Fields omitted here
        # are filled from the preset/saved team later (favor merge included).
        validate_partial_team(
            heroes=args.heroes,
            pet=args.pet,
            banner=args.banner,
            favor=favor,
        )
        preset = None
        if args.team:
            presets = load_arena_presets(account)
            preset = presets.get(args.team)
            if preset is None:
                raise ArenaError(
                    f"Unknown Arena team preset '{args.team}'. Use `arena team list`."
                )
        snapshot = None
        if from_snapshot:
            snapshot = load_arena_snapshot(account)
            if snapshot is None:
                raise ArenaError(
                    "No saved Arena snapshot. Run `arena find-enemies` first."
                )
        status = fetch_arena_status(client)
        if status.saved_team is None and preset is None and args.heroes is not None:
            print(
                "⚠️  Could not read the saved Arena team; pet/banner/favor fall "
                "back to --pet/--banner/--favor only.",
                file=sys.stderr,
            )
        team, missing = resolve_team(
            heroes=args.heroes,
            pet=args.pet,
            banner=args.banner,
            favor=favor,
            preset=preset,
            saved=status.saved_team,
        )
        if missing:
            print(
                f"⚠️  No favor entry for heroes: {', '.join(map(str, missing))}",
                file=sys.stderr,
            )
        if from_snapshot:
            target = resolve_snapshot_target(
                snapshot, rank=args.rank, user_id=args.user_id
            )
            run_arena_attack_from_snapshot(
                client,
                status,
                target,
                team,
                dry_run=bool(args.dry_run),
                force=force,
            )
        else:
            target = resolve_target(
                client,
                status,
                rank=args.rank,
                user_id=args.user_id,
                candidate_retries=args.candidate_retries,
            )
            run_arena_attack(
                client,
                status,
                target,
                team,
                dry_run=bool(args.dry_run),
                candidate_retries=args.candidate_retries,
            )
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _resolve_toe_progress(args, default: str = "line") -> str:
    """Return a valid ``--progress`` mode for ToE commands.

    ``MagicMock``-style legacy args (tests) auto-create attributes, so only
    exact ``quiet``/``line``/``verbose`` strings are honored; anything else
    falls back to ``default`` (the CLI parser default ``line``).
    """
    try:
        value = getattr(args, "progress", default)
    except Exception:
        return default
    if isinstance(value, str) and value in ("quiet", "line", "verbose"):
        return value
    return default


def _resolve_toe_bank_best_loss(args) -> bool:
    """Return whether improving-loss banking is enabled for ToE commands.

    Only an explicit ``--no-bank-best-loss`` (real ``True``) disables it;
    anything else — including auto-created ``MagicMock`` attributes in
    legacy tests — keeps the default ``True``.
    """
    try:
        value = getattr(args, "no_bank_best_loss", False)
    except Exception:
        return True
    return False if value is True else True


def _resolve_toe_end_on_loss(args) -> bool | None:
    """Return the loss-banking policy for ToE tier runs (``None`` = auto).

    ``--no-end-on-loss`` (real ``True``) abandons non-improving losses;
    ``--end-on-loss`` (real ``True``) banks every verified loss. Anything
    else — including auto-created ``MagicMock`` attributes in legacy tests —
    returns ``None`` so the tier loop decides: the default banks every
    verified loss (max-score aggregation makes a worse bank harmless).
    ``--no-bank-best-loss`` separately disables the improving-loss banking.
    """
    try:
        no_value = getattr(args, "no_end_on_loss", False)
        yes_value = getattr(args, "end_on_loss", False)
    except Exception:
        return None
    if no_value is True:
        return False
    if yes_value is True:
        return True
    return None


def _sanitize_progress_log_text(text: str | None) -> str | None:
    """Strip ``\\r`` in-place updates before persisting run-log output."""
    if text is None or "\r" not in text:
        return text
    try:
        from hw_genie.runner import sanitize_progress_log

        return sanitize_progress_log(text)
    except Exception:
        return "\n".join(
            line.rsplit("\r", 1)[-1] if "\r" in line else line
            for line in text.split("\n")
        )


def cmd_arena_plan(args):
    """Recommend attacker lineups from the imported battle corpus."""
    from hw_genie.commands.arena import ArenaError, run_arena_plan

    headers = _ensure_session(args)
    client = HWClient(headers)
    try:
        run_arena_plan(
            client,
            rank=args.rank,
            user_id=args.user_id,
            min_samples=args.min_samples,
            min_win_rate=args.min_win_rate,
            limit=args.limit,
            candidate_retries=args.candidate_retries,
            game_version=args.game_version,
            as_json=bool(args.json),
        )
    except ArenaError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_toe_attack(args):
    """Titan Arena attack with arbitrary titans (app-native, no browser)."""
    headers = _ensure_session(args)
    client = HWClient(headers)
    from hw_genie.commands.titan_arena import run_titan_arena
    from hw_genie.battle.engine import get_default_engine

    # ToE bridge は userscript の Game.ModelManager.player.userInfo.id (x-auth-user-id)
    # と同じ文字列で job を紐付ける。alias ("Alice") ではなく数値 userId を使う。
    engine_mode = getattr(args, "engine", "estimate")
    if engine_mode == "playwright":
        engine = get_default_engine(mode=engine_mode, headers=headers)
    else:
        user_id = headers.get("x-auth-user-id", "") if engine_mode != "estimate" else ""
        if not user_id and engine_mode != "estimate":
            try:
                from hw_genie.core.session_manager import SessionManager

                resolved = resolve_account(args.account)
                data = SessionManager.load(resolved)
                user_id = str((data.get("player") or {}).get("id") or data.get("player", {}).get("userId") or "")
            except Exception:
                user_id = ""
        engine = get_default_engine(
            mode=engine_mode,
            auth_server_url=getattr(args, "auth_server_url", "http://127.0.0.1:8765"),
            user_id=user_id,
        )
    rival = getattr(args, "rival", None)
    titans = list(args.titans) if args.titans else None
    try:
        account_label = resolve_account(args.account)
    except Exception:
        account_label = None
    run_titan_arena(
        client,
        rival_id=rival,
        titans=titans,
        engine=engine,
        dry_run=bool(args.dry_run),
        estimate_only=bool(args.estimate_only),
        account_label=account_label,
        progress=_resolve_toe_progress(args),
    )


def cmd_toe_run(args):
    """Drive a full ToE tier end-to-end (raid + rivals + CompleteTier)."""
    headers = _ensure_session(args)
    client = HWClient(headers)
    from hw_genie.battle.engine import get_default_engine
    from hw_genie.commands.titan_arena import report_bridge_timing, run_titan_arena_tier

    mode = getattr(args, "engine", "estimate")
    if mode == "playwright":
        engine = get_default_engine(mode=mode, headers=headers)
    elif mode != "estimate":
        user_id = headers.get("x-auth-user-id", "")
        if not user_id:
            try:
                from hw_genie.core.session_manager import SessionManager

                resolved = resolve_account(args.account)
                data = SessionManager.load(resolved)
                user_id = str((data.get("player") or {}).get("id") or data.get("player", {}).get("userId") or "")
            except Exception:
                user_id = ""
        engine = get_default_engine(
            mode=mode,
            auth_server_url=getattr(args, "auth_server_url", "http://127.0.0.1:8765"),
            user_id=user_id,
        )
    else:
        engine = get_default_engine(mode=mode)
    titans = list(args.titans) if getattr(args, "titans", None) else None
    _max_attempts = getattr(args, "max_attempts", None)
    try:
        account_label = resolve_account(args.account)
    except Exception:
        account_label = None
    progress = _resolve_toe_progress(args)
    try:
        summary = run_titan_arena_tier(
            client,
            titans=titans,
            engine=engine,
            attack_score_threshold=args.threshold,
            stop_on_first_loss=bool(args.stop_on_loss),
            end_on_loss=_resolve_toe_end_on_loss(args),
            seeds_per_team=int(getattr(args, "seeds", 10) or 10),
            bank_best_loss=_resolve_toe_bank_best_loss(args),
            max_total_attempts=int(_max_attempts) if _max_attempts is not None else None,
            account_label=account_label,
            progress=progress,
        )
    except (KeyboardInterrupt, InterruptedError):
        # Cooperative 1st-press stop before a summary exists: clean complete.
        # (2nd-press SystemExit(130) is not caught here and still aborts.)
        return
    finally:
        # S1: report where the wall clock went (hybrid bridge only).
        report_bridge_timing(engine, account_label, progress)
    if isinstance(summary, dict) and summary.get("interrupted"):
        # User-aborted tier counts as COMPLETE (ok) unless real bridge/API
        # errors ride along; the interrupt marker itself never fails.
        if _toe_real_errors(summary.get("errors", [])):
            sys.exit(1)
        return


def cmd_toe_status(args):
    """Show Titan Arena status."""
    headers = _ensure_session(args)
    client = HWClient(headers)
    from hw_genie.commands.titan_arena import fetch_titan_arena_status
    status = fetch_titan_arena_status(client)
    import json
    print(json.dumps(status, indent=2, ensure_ascii=False))


def cmd_battle_import(args):
    """Import battle replays into the local corpus."""
    from hw_genie.commands.battle import BattleLogError, run_battle_import

    headers = _ensure_session(args)
    client = HWClient(headers)
    alias = resolve_account(args.account)
    battle_type = "titan_arena" if args.battle_type == "toe" else args.battle_type
    try:
        run_battle_import(
            client,
            alias,
            battle_type=battle_type,
            limit=args.limit,
            offset=args.offset,
            dry_run=bool(args.dry_run),
            as_json=bool(args.json),
        )
    except BattleLogError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_battle_stats(args):
    """Show win rates from the imported battle corpus."""
    from hw_genie.commands.battle import BattleLogError, run_battle_stats

    alias = resolve_account(args.account) if args.account else None
    battle_type = None
    if args.battle_type:
        battle_type = "titan_arena" if args.battle_type == "toe" else args.battle_type
    try:
        run_battle_stats(
            account_alias=alias,
            battle_type=battle_type,
            defender_id=args.defender_id,
            game_version=args.game_version,
            by=args.by,
            min_samples=args.min_samples,
            limit=args.limit,
            as_json=bool(args.json),
        )
    except BattleLogError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_battle_simulate(args):
    """Re-simulate fetched replays with the headless oracle."""
    from hw_genie.battle.engine import BridgeError
    from hw_genie.commands.battle import BattleLogError, run_battle_simulate

    headers = _ensure_session(args)
    client = HWClient(headers)
    alias = resolve_account(args.account)
    battle_type = "titan_arena" if args.battle_type == "toe" else args.battle_type
    try:
        run_battle_simulate(
            client,
            alias,
            battle_type=battle_type,
            limit=args.limit,
            offset=args.offset,
            battle_id=args.battle_id,
            dry_run=bool(args.dry_run),
            as_json=bool(args.json),
            recycle_every=args.recycle_every,
            config_getter=getattr(args, "config_getter", None),
            seeds=getattr(args, "seeds", 1),
        )
    except (BattleLogError, BridgeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_battle_config_getters(args):
    """List BattleConfigStorage getters from the headless game page."""
    from hw_genie.battle.engine import BridgeError
    from hw_genie.commands.battle import run_battle_config_getters

    headers = _ensure_session(args)
    client = HWClient(headers)
    try:
        run_battle_config_getters(client, as_json=bool(args.json))
    except BridgeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_battle_import_hwa(args):
    """Import an HWAssistant snapshot into the battle corpus."""
    from datetime import datetime, timedelta, timezone

    from hw_genie.commands.battle import (
        BattleLogError,
        parse_battle_types,
        run_battle_import_hwa,
    )

    alias = resolve_account(args.account)
    since = None
    since_arg = getattr(args, "since", None)
    if since_arg:
        try:
            since = datetime.strptime(since_arg, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            print("Error: --since must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(2)
    else:
        days = getattr(args, "days", None)
        days = 90 if days is None else days
        if days > 0:
            since = datetime.now(timezone.utc) - timedelta(days=days)

    if getattr(args, "no_sync", False):
        # Bulk imports write many local frames; the per-connection sync makes
        # each batch wait on the remote. Skip it and sync once at the end.
        os.environ["TURSO_SYNC_ON_CONNECT"] = "false"
    try:
        try:
            types = parse_battle_types(args.types)
            run_battle_import_hwa(
                alias,
                args.source,
                types=types,
                since=since,
                offset=getattr(args, "offset", 0),
                limit=getattr(args, "limit", None),
                batch_size=args.batch_size,
                dry_run=bool(args.dry_run),
                as_json=bool(args.json),
            )
        finally:
            # Sync whatever batches were committed even if the import failed
            # part-way through; dry runs never write. The quiet sync never
            # exits, so a sync failure neither masks the original exception nor
            # pollutes stdout (which must stay pure JSON for --json).
            if getattr(args, "no_sync", False) and not args.dry_run:
                if not _sync_replica(quiet=True):
                    print(
                        "replica sync failed; run `hw-genie sync`",
                        file=sys.stderr,
                    )
    except (BattleLogError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_battle_watch(args):
    """Poll arena/grand replays and import new battles."""
    from hw_genie.commands.battle import (
        BattleLogError,
        parse_battle_types,
        run_battle_watch,
    )

    headers = _ensure_session(args)
    client = HWClient(headers)
    alias = resolve_account(args.account)
    try:
        types = parse_battle_types(args.types)
        passes = run_battle_watch(
            client,
            alias,
            types=types,
            interval=args.interval,
            limit=args.limit,
            offset=getattr(args, "offset", 0),
            once=bool(args.once),
            as_json=bool(args.json),
        )
        if args.once and passes:
            results = passes[-1].get("results", {})
            if results and all("error" in result for result in results.values()):
                sys.exit(1)
    except BattleLogError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_battle_prune(args):
    """Delete old battle-corpus rows (and comparisons)."""
    from datetime import datetime, timedelta, timezone

    from hw_genie.commands.battle import BattleLogError, run_battle_prune

    before = None
    if getattr(args, "before", None):
        try:
            before = datetime.strptime(args.before, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            print("Error: --before must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(2)
    else:
        days = getattr(args, "days", None)
        days = 90 if days is None else days
        if days == 0 and not args.dry_run:
            print(
                "WARNING: --days 0 deletes ALL battle rows in the corpus",
                file=sys.stderr,
            )
        before = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        run_battle_prune(
            before,
            dry_run=bool(args.dry_run),
            as_json=bool(args.json),
        )
    except BattleLogError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_chat(args):
    """ギルドチャット（chatGetAll）の取得・表示"""
    from hw_genie.commands.chat import run_chat
    from hw_genie.core.session_manager import SessionManager

    # account は一度だけ resolve し、headers 取得と run_chat へ同じ値を渡す（二重呼び出し解消）
    account = resolve_account(args.account or None)
    data = SessionManager.load(account)
    headers = data.get("headers") if data else None
    if not headers:
        print(f"Error: Session not found for account '{account}'. Please provide a valid curl with --curl.")
        sys.exit(1)
    client = HWClient(headers)

    result = run_chat(
        client,
        account_alias=account,
        chat_type=args.type,
        count=args.count,
        raw=bool(args.raw),
        json_output=bool(args.json),
        last_id=getattr(args, "last_id", None),
    )
    # 失敗時は exit 1（空チャットは success=True なので正常終了）
    if hasattr(result, "success") and not result.success:
        sys.exit(1)


def cmd_quests(args):
    """クエスト（デイリー等）の取得・表示"""
    from hw_genie.commands.quests import (
        GUILD_GLYPH_DEFAULTS_KEY,
        QUEST_GUILD_DEFAULTS_KEY,
        classify_quest,
        edit_quest_defaults_interactive,
        ensure_guild_glyph_defaults,
        ensure_quest_defaults,
        ensure_quest_guild_defaults,
        run_quest_execute,
        run_quest_status,
        set_guild_glyph_defaults,
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
        glyph_defaults = ensure_guild_glyph_defaults(account)
        print(f"ℹ️  Initialized {GUILD_GLYPH_DEFAULTS_KEY} for {account}:")
        print(
            f"    - guild glyph activity enabled={glyph_defaults.get('enabled', False)}"
            f" (heroId={glyph_defaults.get('heroId')} tier={glyph_defaults.get('tier')})"
        )
        return

    # アカウント固有の操作引数上書き（quest_defaults / quest_guild_defaults /
    # guild_glyph_defaults）を 1 件登録する
    if args.set_default:
        quest_id, key, value = args.set_default
        if str(quest_id) == "guild":
            stored = set_quest_guild_defaults(account, key, value)
            print(f"ℹ️  Registered quest_guild_defaults[{key}] = {stored} ({type(stored).__name__}) for {account}")
        elif str(quest_id) == "glyph":
            try:
                stored = set_guild_glyph_defaults(account, key, value)
            except ValueError as exc:
                print(f"Error: {exc}")
                sys.exit(1)
            print(f"ℹ️  Registered guild_glyph_defaults[{key}] = {stored} ({type(stored).__name__}) for {account}")
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


def _sync_replica(quiet: bool = False) -> bool:
    """Sync the local Turso replica with the remote cloud database.

    Never raises or calls ``sys.exit``: returns ``True`` on success (including
    "nothing to sync") and ``False`` on failure. ``quiet`` suppresses both the
    success line and the failure message, so callers that own the output (e.g.
    ``battle import-hwa --json``) can keep stdout JSON-clean and report the
    failure themselves on stderr.
    """
    sync_url = os.environ.get("TURSO_SYNC_URL")
    if not sync_url:
        if not quiet:
            print("TURSO_SYNC_URL is not set — nothing to sync.")
        return True

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
        if not quiet:
            print(f"✗ Sync failed: {e}", file=sys.stderr)
        return False

    if not quiet:
        print(f"✓ Local replica synced with Turso cloud ({sync_url})")
    return True


def cmd_sync(args):
    """Sync the local Turso replica with the remote cloud database."""
    if not _sync_replica(quiet=False):
        sys.exit(1)


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


def _multi_headers_ready(headers: object) -> bool:
    """True when stored headers exist and carry at least one ``x-auth-*`` key.

    Thin wrapper over :func:`runner.multi_headers_ready` (the canonical
    implementation). Presence gate only: the full header contract lives in
    ``auth_server.REQUIRED_HEADER_KEYS`` (all six keys required for bridge
    calls); preflight deliberately accepts any single ``x-auth-*`` key so a
    partially-synced session still reaches a worker and fails loudly at the
    first API call instead of being misreported as missing. Non-dict /
    empty-dict headers are never ready (boundary: ``None``, ``{}``,
    ``{"content-type": ...}`` → ``False``).
    """
    return bool(_runner_headers_ready(headers))


def _preflight_multi_accounts(
    accounts: list[str],
) -> tuple[list[str], dict[str, tuple[object | None, BaseException | None]]]:
    """Split ``accounts`` into ``(ready, prefailed)`` without any network I/O.

    Thin wrapper over :func:`runner.preflight_multi_accounts` (the
    canonical implementation; kept here so existing ``main.*`` references
    and patches keep working). See the runner docstring for the contract:
    presence-only check of ``x-auth-*`` keys, missing accounts become
    ``Session not found`` failures, and a stored-but-expired session is
    *not* detectable here — expiry only surfaces at the first API call as
    :class:`HWAuthError`.
    """
    return _runner_preflight(accounts)


def _is_interrupted_account(mode: str, result: object | None, err: BaseException | None) -> bool:
    """True when the account's outcome was shaped by a cooperative user cancel.

    This is the run-log ``interrupted`` *flag* (per-entry marker), not the
    ``summarize_toe`` display *bucket*: the flag is set for any
    interrupt-shaped outcome (interrupt exception in toe mode, or a tier
    summary with ``interrupted=True``), even when real errors ride along
    (flag=True + ok=False). The summarize bucket instead shows
    ``interrupted=True`` summaries *with* real errors as failed. A
    non-toe ``InterruptedError`` (e.g. Ctrl+C during a daily run) is a real
    failure there, so the exception branch is mode-gated to ``"toe"`` and
    never leaks toe semantics into other modes.
    """
    if mode == "toe" and _is_toe_interrupt_exc(err):
        return True
    if (
        mode == "toe"
        and isinstance(result, dict)
        and bool(result.get("interrupted"))
    ):
        return True
    return False


def cmd_multi(args):
    """Run a routine against all accounts inside a single process (parallel)."""
    import signal
    import traceback
    from datetime import datetime, timezone

    from hw_genie.core.run_log import OutputCapture, record_run_log
    from hw_genie.runner import (
        asgard_shop_routine,
        consumable_routine,
        daily_routine,
        full_routine,
        is_cancelled,
        list_account_aliases,
        quests_routine,
        request_cancel,
        reset_cancel,
        seers_game_routine,
        summarize_asgard_shop,
        summarize_consumable,
        summarize_quests,
        summarize_seers_game,
        summarize_toe,
        toe_routine,
    )

    mode = args.mode
    accounts = args.accounts
    if accounts:
        accounts = list(accounts)
    else:
        accounts = list_account_aliases()
    # Duplicate aliases collapse to first-seen order (spec: no double-run of
    # the same account; the results dict below could not hold two entries
    # for one key anyway). Empty input is an explicit no-op: no worker is
    # spawned and the run records an ok run-log with zero entries.
    seen: set[str] = set()
    deduped: list[str] = []
    for acc in accounts:
        if acc not in seen:
            seen.add(acc)
            deduped.append(acc)
    accounts = deduped

    dry_run = bool(getattr(args, "dry_run", False))
    if mode not in ("quests", "consumable", "seers-game") and dry_run:
        print(
            "Error: --dry-run is only supported with the 'quests', 'consumable' and 'seers-game' modes "
            "(daily/full/asgard-shop/toe routines always execute their operations).",
            file=sys.stderr,
        )
        sys.exit(2)

    if mode == "quests":
        routine = quests_routine(dry_run=dry_run)
        # dry-run は計画表示のため逐次実行（出力がアカウント順に並び、確認しやすい）
        max_parallel = 1 if dry_run else args.parallel
    elif mode == "seers-game":
        # 単体ハンドラと同じ共有バリデータで、最初の API 呼び出しより前に
        # 検査する（不正値は stderr + exit 2）。
        from hw_genie.commands.seers_game import validate_seers_args as _validate_seers

        _pick, _max_games = _validate_seers(
            getattr(args, "pick", 2), getattr(args, "max_games", None)
        )
        routine = partial(
            seers_game_routine,
            pick=_pick,
            max_games=_max_games,
            dry_run=dry_run,
        )
        # dry-run は read-only の計画表示のため逐次実行
        max_parallel = 1 if dry_run else args.parallel
    elif mode == "asgard-shop":
        routine = asgard_shop_routine(gold_buffs=args.gold_buffs)
        max_parallel = args.parallel
    elif mode == "consumable":
        routine = consumable_routine(
            lib_ids=args.lib, method_override=args.method, dry_run=dry_run
        )
        max_parallel = 1 if dry_run else args.parallel
    elif mode == "toe":
        _max_attempts = getattr(args, "max_attempts", None)
        # Factory probe is suppressed here: the explicit post-preflight
        # probe below runs only when at least one account is ready, so an
        # all-missing/empty run never blocks ~2s on a socket.
        routine = toe_routine(
            engine=getattr(args, "engine", "hybrid") or "hybrid",
            seeds_per_team=int(getattr(args, "seeds", 10) or 10),
            threshold=int(getattr(args, "threshold", 250) or 250),
            auth_server_url=getattr(args, "auth_server_url", "http://127.0.0.1:8765") or "http://127.0.0.1:8765",
            max_total_attempts=int(_max_attempts) if _max_attempts is not None else None,
            end_on_loss=_resolve_toe_end_on_loss(args),
            bank_best_loss=_resolve_toe_bank_best_loss(args),
            progress=_resolve_toe_progress(args),
            probe_bridge=False,
        )
        # daily 等と同様 --parallel 未指定時は HW_MAX_PARALLEL 環境変数に
        # フォールバックする（run_all_accounts 内の resolve_max_parallel が
        # 解決）。bridge の job キューは account 照合＋claimed_by ガード済み
        # のため並列実行可。
        max_parallel = args.parallel
    else:
        routine = partial(
            full_routine if mode == "full" else daily_routine,
            item_max_iterations=getattr(args, "iterations", 9999),
        )
        max_parallel = args.parallel

    # Preflight (network-free): drop accounts without stored sessions before
    # spawning workers; their failure entries merge into the results below so
    # summaries and run-logs report them like any other failure. When every
    # account is missing, no worker is spawned and the run records a failed
    # run-log via the normal path (exit 1). Empty input skips everything and
    # records ok with zero entries (explicit guard below).
    ready_accounts, prefailed_results = _preflight_multi_accounts(accounts or [])

    if mode == "toe":
        # Post-preflight bridge probe (exactly once, warning-only, never
        # aborts): runs only when at least one account is ready, so empty /
        # all-missing runs skip the ~2s socket probe entirely.
        try:
            _toe_engine = getattr(args, "engine", "hybrid") or "hybrid"
        except Exception:
            _toe_engine = "hybrid"
        if _toe_engine == "hybrid" and ready_accounts:
            try:
                _toe_url = getattr(args, "auth_server_url", "http://127.0.0.1:8765") or "http://127.0.0.1:8765"
            except Exception:
                _toe_url = "http://127.0.0.1:8765"
            try:
                from hw_genie.runner import _warn_if_bridge_unreachable as _probe_bridge
            except Exception:
                _probe_bridge = None
            if _probe_bridge is not None:
                _probe_bridge(_toe_url)

    # 実行ログ（run_logs）記録: 実行中の出力をキャプチャし、終了時に 1 レコード
    # 書き込む（best-effort: DB 失敗でも実行自体は落とさない）。
    started_at = datetime.now(timezone.utc)
    capture = OutputCapture()
    results: dict = {}
    # Cooperative Ctrl+C: 1st press sets the shared cancel event so tier/
    # bridge loops stop after the current attempt; 2nd press forces abort.
    reset_cancel()
    try:
        _orig_sigint = signal.getsignal(signal.SIGINT)
    except Exception:  # pragma: no cover - defensive
        _orig_sigint = None
    _sig_state: dict = {"count": 0}

    def _sigint_handler(signum, frame):  # pragma: no cover - signal path
        if _sig_state["count"] == 0:
            _sig_state["count"] += 1
            try:
                request_cancel()
            except Exception:
                pass
            try:
                print(
                    "\nstopping after current attempt... (press again to force abort)",
                    flush=True,
                )
            except Exception:
                pass
            raise KeyboardInterrupt
        # 2nd press: restore the original handler, then force an immediate
        # abort. SystemExit(130) is not caught by the cooperative
        # `except KeyboardInterrupt` paths (runner / tier loops), so it
        # bypasses partial-result gathering instead of re-entering it.
        try:
            if _orig_sigint is not None:
                signal.signal(signal.SIGINT, _orig_sigint)
            else:
                signal.signal(signal.SIGINT, signal.SIG_DFL)
        except Exception:
            pass
        try:
            print("\nforced abort.", flush=True)
        except Exception:
            pass
        raise SystemExit(130)

    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except Exception:  # pragma: no cover - non-main thread
        pass
    try:
        try:
            with capture:
                results = dict(prefailed_results)
                for acc, (_res, _err) in prefailed_results.items():
                    print(
                        f"⚠️  Session not found for account '{acc}'. "
                        "Skipping (re-sync via auth-server/curl, then retry).",
                        flush=True,
                    )
                if not accounts and not prefailed_results:
                    # Explicit empty-accounts guard (message only): the
                    # worker call below still runs with [] and returns {}
                    # (run_all_accounts warns), so the run records ok/0.
                    print("No accounts found; nothing to run.", flush=True)
                if ready_accounts or not prefailed_results:
                    worker_results = run_all_accounts(
                        routine,
                        accounts=ready_accounts,
                        max_parallel=max_parallel,
                        cooperative_wait=(mode == "toe"),
                    )
                    results.update(worker_results)
                # Preserve the original (registration/request) account order.
                results = {acc: results[acc] for acc in accounts if acc in results}
                if mode == "quests":
                    failed = summarize_quests(results.items(), dry_run=dry_run)
                elif mode == "asgard-shop":
                    failed = summarize_asgard_shop(results.items())
                elif mode == "seers-game":
                    failed = summarize_seers_game(results.items(), dry_run=dry_run)
                elif mode == "consumable":
                    failed = summarize_consumable(results.items(), dry_run=dry_run)
                elif mode == "toe":
                    failed = summarize_toe(results.items())
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
            try:
                record_run_log(
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    mode=mode,
                    status="failed",
                    exit_code=exit_code,
                    accounts=accounts,
                    error_summary=str(exc) or type(exc).__name__,
                    log_text=(_sanitize_progress_log_text(capture.getvalue()) + "\n" + trace).strip() or None,
                    log_file=os.environ.get("HWGENIE_LOG_FILE"),
                    hostname=_run_host_identifier(),
                )
            except BaseException as log_exc:  # noqa: BLE001 - logging must never mask the original failure (e.g. 2nd Ctrl+C)
                print(f"Warning: failed to record run log: {str(log_exc) or type(log_exc).__name__}", file=sys.stderr)
            raise
    finally:
        try:
            if _orig_sigint is None:
                signal.signal(signal.SIGINT, signal.SIG_DFL)
            else:
                signal.signal(signal.SIGINT, _orig_sigint)
        except Exception:  # pragma: no cover - defensive
            pass
    if is_cancelled():
        # Cooperative 1st-press stop: the mid-run summary was already
        # printed inside the capture above. A user-aborted account/tier
        # counts as COMPLETE (ok); only real failures fail the run.
        # error_summary covers real failures only — the interrupt itself is
        # never merged into it (no "; interrupted by user" synthesis), and a
        # cancel with zero real failures stays a clean complete. Real
        # failures are derived from the structured (res, err) outcome via a
        # single _build_run_log_summary call (entries with ok=False are
        # exactly the real failures: toe interrupts already map to ok=True
        # there); no error-text substring matching is used, and non-toe
        # InterruptedError stays a real failure via the mode gate.
        try:
            account_logs, _ = _build_run_log_summary(mode, results)
        except Exception:  # pragma: no cover - defensive
            account_logs, _ = [], None
        real_failed_entries = [
            e for e in account_logs
            if isinstance(e, dict) and not e.get("ok", True)
        ]
        if real_failed_entries:
            status, exit_code = "failed", 1
            error_summary = (
                f"{len(real_failed_entries)} account(s) failed: "
                + ", ".join(
                    f"{e['account']} ({e.get('error')})" for e in real_failed_entries
                )
            )
        else:
            status, exit_code = "ok", 0
            error_summary = None
        try:
            record_run_log(
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                mode=mode,
                status=status,
                exit_code=exit_code,
                accounts=account_logs,
                error_summary=error_summary,
                log_text=_sanitize_progress_log_text(capture.getvalue()) or None,
                log_file=os.environ.get("HWGENIE_LOG_FILE"),
                hostname=_run_host_identifier(),
            )
        except BaseException as log_exc:  # noqa: BLE001 - logging must never mask the interrupt (e.g. 2nd Ctrl+C)
            print(f"Warning: failed to record run log: {str(log_exc) or type(log_exc).__name__}", file=sys.stderr)
            if isinstance(log_exc, (KeyboardInterrupt, SystemExit)):
                # 2nd-press force abort during the DB write stays immediate
                # (130) and is never masked as a clean complete.
                if isinstance(log_exc, SystemExit) and isinstance(log_exc.code, int):
                    raise
                sys.exit(130)
        if exit_code:
            sys.exit(exit_code)
        return
    account_logs, error_summary = _build_run_log_summary(mode, results)
    record_run_log(
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        mode=mode,
        status="failed" if failed else "ok",
        exit_code=1 if failed else 0,
        accounts=account_logs,
        error_summary=error_summary,
        log_text=_sanitize_progress_log_text(capture.getvalue()) or None,
        log_file=os.environ.get("HWGENIE_LOG_FILE"),
        hostname=_run_host_identifier(),
    )
    if failed:
        sys.exit(1)


# NOTE: ``_is_toe_interrupt_exc`` / ``_is_toe_interrupt_entry`` /
# ``_toe_real_errors`` live canonically in :mod:`hw_genie.runner` and are
# imported at module top; ``main`` keeps no duplicate implementation.


def _run_log_account_failure(
    mode: str, result: object | None, err: BaseException | None
) -> str | None:
    """Return the failure reason for one account's run-log entry, or None when ok.

    Mirrors the per-account failure judgement of the runner's ``summarize_*``
    functions so ``run_logs`` rows stay consistent with the printed summary:
    quest failures, consumable ERROR/UNEXPECTED items, Asgard purchase errors,
    Seer's Game failures and unavailable statuses all count as failures,
    matching the ``failed`` counter returned by ``summarize``. A cooperative user cancel counts as
    COMPLETE (ok) for ``toe``: ``InterruptedError`` entries and
    ``interrupted=True`` tier summaries without real bridge/API errors map
    to None. Exceptions are reported by their first message line.
    """
    if err is not None:
        if mode == "toe" and _is_toe_interrupt_exc(err):
            return None
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
    if mode == "seers-game":
        from hw_genie.commands.seers_game import SeersGameResult, is_clean_depletion

        if isinstance(result, SeersGameResult):
            if result.games_failed:
                return f"{result.games_failed} seers-game(s) failed"
            if (
                result.games_played == 0
                and result.last_error
                and not is_clean_depletion(result.last_error)
            ):
                return f"seers-game failed: {result.last_error}"
            return None
        return "seers-game result unavailable"
    if mode == "toe":
        if isinstance(result, dict):
            real_errors = _toe_real_errors(result.get("errors", []))
            if result.get("interrupted"):
                return f"{len(real_errors)} toe error(s)" if real_errors else None
            if real_errors:
                return f"{len(real_errors)} toe error(s)"
            if result.get("remaining_rivals") == 0:
                return None
            if not result.get("rival_results") and not result.get("completed_tier"):
                return None
            wins = sum(1 for r in result.get("rival_results", []) if r.get("win"))
            if not wins and not result.get("completed_tier"):
                return "no rival cleared"
            return None
        return "toe result unavailable"
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
    failed. Each entry also carries an ``interrupted`` flag marking outcomes
    shaped by a cooperative user cancel (no schema migration: it is just an
    extra JSON key in the stored ``accounts`` payload). The ``N`` in ``"N
    account(s) failed"`` counts entries with ``ok: false`` (one per failed
    account in ``results`` order); ``ok: true`` entries — including clean
    cooperative cancels — are excluded.
    """
    entries: list[dict] = []
    failed_accounts: list[str] = []
    for account, (res, err) in results.items():
        reason = _run_log_account_failure(mode, res, err)
        interrupted = _is_interrupted_account(mode, res, err)
        if reason is None:
            entries.append(
                {"account": account, "ok": True, "error": None, "interrupted": interrupted}
            )
            continue
        entries.append(
            {"account": account, "ok": False, "error": reason, "interrupted": interrupted}
        )
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
        help="Buy gold buffs (slot 1-5; default: Osh week buff 63/64 only, Maestro week all)",
    )
    gold_group.add_argument(
        "--no-gold",
        dest="gold_buffs",
        action="store_false",
        help="Skip gold buff purchases (slot 1-5)",
    )
    p_asgard_shop.set_defaults(func=cmd_asgard_shop)

    # Seer's Game (start -> play x4 -> finish until coins run out)
    p_seers = subparsers.add_parser(
        "seers-game",
        parents=[parent_parser],
        help="Play Seer's Game until coins run out",
    )
    p_seers.add_argument(
        "--dry-run", action="store_true", help="Show state/coin balance without playing"
    )
    p_seers.add_argument(
        "--pick",
        type=int,
        default=2,
        help="Card to pick each round (default: 2; validated against live event.size)",
    )
    p_seers.add_argument(
        "--max-games",
        type=int,
        default=None,
        help="Max games to play (default: until coins run out)",
    )
    p_seers.set_defaults(func=cmd_seers_game)

    # Arena (status / top / team presets / targeted attack)
    # NOTE: do not put parents=[parent_parser] on the intermediate `arena` /
    # `arena team` parsers. argparse would parse `-a` there and the leaf
    # subparser's default (None) would overwrite it. Leaves inherit it instead.
    p_arena = subparsers.add_parser(
        "arena", help="Arena operations (status/top/team/attack)"
    )
    arena_sub = p_arena.add_subparsers(dest="arena_type", required=True)

    p_arena_status = arena_sub.add_parser(
        "status", parents=[parent_parser], help="Show Arena rank, attempts and saved team"
    )
    p_arena_status.set_defaults(func=cmd_arena_status)

    p_arena_top = arena_sub.add_parser(
        "top", parents=[parent_parser], help="Show the Arena top-50 ranking"
    )
    p_arena_top.add_argument(
        "--limit", type=_positive_int, default=None, help="Max rows to show (default: all returned)"
    )
    p_arena_top.add_argument("--raw", action="store_true", help="Print the raw topGet response as JSON")
    p_arena_top.set_defaults(func=cmd_arena_top)

    p_arena_plan = arena_sub.add_parser(
        "plan",
        parents=[parent_parser],
        help="Recommend attacker lineups from the imported battle corpus",
    )
    plan_target = p_arena_plan.add_mutually_exclusive_group(required=True)
    plan_target.add_argument(
        "--rank", type=_positive_int, default=None, help="Target rank"
    )
    plan_target.add_argument(
        "--user-id", type=_positive_int, default=None, help="Target userId"
    )
    p_arena_plan.add_argument(
        "--min-samples",
        type=_positive_int,
        default=3,
        help="Minimum battles for the gate (default: 3)",
    )
    p_arena_plan.add_argument(
        "--min-win-rate",
        type=float,
        default=1.0,
        help="Minimum win rate for the gate (default: 1.0)",
    )
    p_arena_plan.add_argument(
        "--limit", type=_positive_int, default=10, help="Max plans to show (default: 10)"
    )
    p_arena_plan.add_argument(
        "--candidate-retries",
        type=_non_negative_int,
        default=None,
        help="Candidate list refresh attempts (default: 10)",
    )
    p_arena_plan.add_argument(
        "--game-version",
        dest="game_version",
        default=None,
        help="Only use battles from this server version (default: all)",
    )
    p_arena_plan.add_argument(
        "--json", action="store_true", help="Print the plans as JSON"
    )
    p_arena_plan.set_defaults(func=cmd_arena_plan)

    p_arena_find = arena_sub.add_parser(
        "find-enemies",
        parents=[parent_parser],
        help="(experimental) Call arenaFindEnemies once and save the candidate snapshot",
    )
    p_arena_find.add_argument(
        "--raw", action="store_true", help="Print the saved snapshot as JSON"
    )
    p_arena_find.set_defaults(func=cmd_arena_find_enemies)

    p_arena_snapshot = arena_sub.add_parser(
        "snapshot",
        parents=[parent_parser],
        help="(experimental) Show the saved snapshot against current own/target ranks",
    )
    p_arena_snapshot.set_defaults(func=cmd_arena_snapshot)

    p_arena_team = arena_sub.add_parser(
        "team", help="Manage named Arena team presets"
    )
    arena_team_sub = p_arena_team.add_subparsers(dest="arena_team_action", required=True)

    p_arena_team_set = arena_team_sub.add_parser(
        "set", parents=[parent_parser], help="Create/update a team preset"
    )
    p_arena_team_set.add_argument("name", help="Preset name (e.g. atk1)")
    team_source = p_arena_team_set.add_mutually_exclusive_group(required=True)
    team_source.add_argument(
        "--from-saved", action="store_true", help="Import the in-game saved Arena team"
    )
    team_source.add_argument(
        "--heroes", nargs=5, type=int, metavar="HERO_ID", help="Exactly 5 hero IDs"
    )
    p_arena_team_set.add_argument("--pet", type=int, help="Pet ID (>= 6000)")
    p_arena_team_set.add_argument("--banner", type=int, help="Banner ID")
    p_arena_team_set.add_argument(
        "--favor", action="append", metavar="HERO=PATRON", help="Favor patron (repeatable)"
    )
    p_arena_team_set.set_defaults(func=cmd_arena_team_set)

    p_arena_team_list = arena_team_sub.add_parser(
        "list", parents=[parent_parser], help="List team presets"
    )
    p_arena_team_list.set_defaults(func=cmd_arena_team_list)

    p_arena_team_remove = arena_team_sub.add_parser(
        "remove", parents=[parent_parser], help="Delete a team preset"
    )
    p_arena_team_remove.add_argument("name", help="Preset name")
    p_arena_team_remove.set_defaults(func=cmd_arena_team_remove)

    from hw_genie.commands.arena import DEFAULT_CANDIDATE_RETRIES

    p_arena_attack = arena_sub.add_parser(
        "attack", parents=[parent_parser], help="Attack a player ranked above you"
    )
    target_group = p_arena_attack.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--rank", type=_positive_int, help="Target rank (must be above yours)")
    target_group.add_argument(
        "--user-id", dest="user_id", type=_positive_int, help="Target userId (must be above yours)"
    )
    p_arena_attack.add_argument("--team", help="Team preset name (default: in-game saved Arena team)")
    p_arena_attack.add_argument(
        "--heroes", nargs=5, type=int, metavar="HERO_ID", help="Override with exactly 5 hero IDs"
    )
    p_arena_attack.add_argument("--pet", type=int, help="Override pet ID (>= 6000)")
    p_arena_attack.add_argument("--banner", type=int, help="Override banner ID")
    p_arena_attack.add_argument(
        "--favor", action="append", metavar="HERO=PATRON", help="Override favor patron (repeatable)"
    )
    p_arena_attack.add_argument(
        "--dry-run", action="store_true", help="Resolve target and check range without attacking"
    )
    p_arena_attack.add_argument(
        "--from-snapshot",
        action="store_true",
        help=(
            "Attack a target from the saved snapshot without calling "
            "arenaFindEnemies (experimental)"
        ),
    )
    p_arena_attack.add_argument(
        "--force",
        action="store_true",
        help=(
            "With --from-snapshot, send even when arenaCheckTargetRange is false "
            "(upper-only is still enforced)"
        ),
    )
    p_arena_attack.add_argument(
        "--candidate-retries",
        type=_non_negative_int,
        default=DEFAULT_CANDIDATE_RETRIES,
        metavar="N",
        help=(
            "Extra arenaFindEnemies refreshes while waiting for the target to "
            "appear in the current candidate list (default: "
            f"{DEFAULT_CANDIDATE_RETRIES}; not used with --from-snapshot)"
        ),
    )
    p_arena_attack.set_defaults(func=cmd_arena_attack)

    # Battle log corpus (import replays / aggregate win rates)
    p_battle = subparsers.add_parser(
        "battle", help="Battle log corpus (import replays / matchup stats)"
    )
    battle_sub = p_battle.add_subparsers(dest="battle_command", required=True)

    p_battle_import = battle_sub.add_parser(
        "import", parents=[parent_parser], help="Import battle replays from the game API"
    )
    p_battle_import.add_argument(
        "--type",
        dest="battle_type",
        default="arena",
        choices=["arena", "grand", "titan_arena", "toe"],
        help="Replay type to fetch (default: arena; 'toe' is an alias for titan_arena)",
    )
    p_battle_import.add_argument(
        "--limit",
        type=_positive_int,
        default=50,
        help="Replays to fetch (default: 50)",
    )
    p_battle_import.add_argument(
        "--offset",
        type=_non_negative_int,
        default=0,
        help="Pagination offset (default: 0)",
    )
    p_battle_import.add_argument(
        "--dry-run", action="store_true", help="Parse and validate without writing"
    )
    p_battle_import.add_argument(
        "--json", action="store_true", help="Print the result as JSON"
    )
    p_battle_import.set_defaults(func=cmd_battle_import)

    p_battle_stats = battle_sub.add_parser(
        "stats",
        parents=[parent_parser],
        help="Show win rates by defender or lineup matchup",
    )
    p_battle_stats.add_argument(
        "--type",
        dest="battle_type",
        default=None,
        choices=["arena", "grand", "titan_arena", "toe"],
        help="Filter by battle type ('toe' is an alias for titan_arena)",
    )
    p_battle_stats.add_argument(
        "--defender-id", dest="defender_id", default=None, help="Filter by defender player id"
    )
    p_battle_stats.add_argument(
        "--by",
        choices=["defender", "matchup"],
        default="defender",
        help="Group by defender (default) or lineup matchup",
    )
    p_battle_stats.add_argument(
        "--min-samples",
        type=_positive_int,
        default=1,
        help="Only show groups with at least N battles",
    )
    p_battle_stats.add_argument(
        "--game-version",
        dest="game_version",
        default=None,
        help="Only count battles from this server version (default: all)",
    )
    p_battle_stats.add_argument(
        "--limit", type=_positive_int, default=25, help="Max rows (default: 25)"
    )
    p_battle_stats.add_argument(
        "--json", action="store_true", help="Print the result as JSON"
    )
    p_battle_stats.set_defaults(func=cmd_battle_stats)

    p_battle_simulate = battle_sub.add_parser(
        "simulate",
        parents=[parent_parser],
        help="Re-simulate fetched replays with the headless oracle and record parity",
    )
    p_battle_simulate.add_argument(
        "--type",
        dest="battle_type",
        default="titan_arena",
        choices=["arena", "grand", "titan_arena", "toe"],
        help=(
            "Replay type to simulate (default: titan_arena; 'toe' is an alias). "
            "Arena needs the correct --config-getter, which is not verified yet."
        ),
    )
    p_battle_simulate.add_argument(
        "--limit", type=_positive_int, default=20, help="Replays to simulate (default: 20)"
    )
    p_battle_simulate.add_argument(
        "--offset", type=_non_negative_int, default=0, help="Pagination offset (default: 0)"
    )
    p_battle_simulate.add_argument(
        "--battle-id", default=None, help="Simulate only this replay id"
    )
    p_battle_simulate.add_argument(
        "--recycle-every",
        type=_non_negative_int,
        default=100,
        help=(
            "Recreate the headless page after N battles "
            "(default: 100; 0 = never recycle)"
        ),
    )
    p_battle_simulate.add_argument(
        "--config-getter",
        default=None,
        help=(
            "Override the BattleConfigStorage getter. Default: automatic "
            "(arena/grand -> get_pvp, titan_arena -> get_titanPvpManual, "
            "pvp_manual/challenge -> get_pvpManual)."
        ),
    )
    p_battle_simulate.add_argument(
        "--seeds",
        type=_positive_int,
        default=1,
        help=(
            "Total simulations per replay including the observed seed "
            "(default: 1). Extra deterministic seeds estimate the win rate."
        ),
    )
    p_battle_simulate.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate but do not write sim/comparison rows",
    )
    p_battle_simulate.add_argument(
        "--json", action="store_true", help="Print the parity results as JSON"
    )
    p_battle_simulate.set_defaults(func=cmd_battle_simulate)

    p_battle_getters = battle_sub.add_parser(
        "config-getters",
        parents=[parent_parser],
        help="List BattleConfigStorage getters from the headless game page",
    )
    p_battle_getters.add_argument(
        "--json", action="store_true", help="Print the getter list as JSON"
    )
    p_battle_getters.set_defaults(func=cmd_battle_config_getters)

    p_battle_import_hwa = battle_sub.add_parser(
        "import-hwa",
        parents=[parent_parser],
        help="Import an HWAssistant *.json.gz snapshot (arena/GA)",
    )
    p_battle_import_hwa.add_argument(
        "--source", required=True, help="Path to hwa-db-*.json.gz"
    )
    p_battle_import_hwa.add_argument(
        "--types",
        default="arena,grand",
        help="Comma-separated sections (default: arena,grand)",
    )
    p_battle_import_hwa.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="Max battles to process after the cutoff/offset",
    )
    cutoff_group = p_battle_import_hwa.add_mutually_exclusive_group()
    cutoff_group.add_argument(
        "--days",
        type=_non_negative_int,
        default=None,
        help=(
            "Only battles from the last N days (default: 90; 0 = all). "
            "Older battles are counted as filtered."
        ),
    )
    cutoff_group.add_argument(
        "--since",
        default=None,
        help="Only battles on/after YYYY-MM-DD (UTC)",
    )
    p_battle_import_hwa.add_argument(
        "--offset",
        type=_non_negative_int,
        default=0,
        help="Skip this many battles after the cutoff (default: 0)",
    )
    p_battle_import_hwa.add_argument(
        "--batch-size",
        type=_positive_int,
        default=500,
        help="DB batch size (default: 500)",
    )
    p_battle_import_hwa.add_argument(
        "--dry-run", action="store_true", help="Parse and validate without writing"
    )
    p_battle_import_hwa.add_argument(
        "--no-sync",
        action="store_true",
        help=(
            "Skip the per-connection Turso replica sync during the import and "
            "sync once at the end (recommended for large snapshots)"
        ),
    )
    p_battle_import_hwa.add_argument(
        "--json", action="store_true", help="Print the result as JSON"
    )
    p_battle_import_hwa.set_defaults(func=cmd_battle_import_hwa)

    p_battle_watch = battle_sub.add_parser(
        "watch",
        parents=[parent_parser],
        help="Poll arena/grand replays and import new battles",
    )
    p_battle_watch.add_argument(
        "--types",
        default="arena,grand",
        help="Comma-separated types (default: arena,grand)",
    )
    p_battle_watch.add_argument(
        "--interval",
        type=_positive_int,
        default=300,
        help="Seconds between passes (default: 300)",
    )
    p_battle_watch.add_argument(
        "--limit",
        type=_positive_int,
        default=50,
        help="Replays per type per pass (default: 50)",
    )
    p_battle_watch.add_argument(
        "--offset",
        type=_non_negative_int,
        default=0,
        help="Pagination offset passed to battleGetByType (default: 0)",
    )
    p_battle_watch.add_argument(
        "--once", action="store_true", help="Run one pass and exit"
    )
    p_battle_watch.add_argument(
        "--json", action="store_true", help="One JSON object per pass"
    )
    p_battle_watch.set_defaults(func=cmd_battle_watch)

    p_battle_prune = battle_sub.add_parser(
        "prune",
        parents=[parent_parser],
        help="Delete battle rows older than a cutoff",
    )
    prune_cutoff = p_battle_prune.add_mutually_exclusive_group()
    prune_cutoff.add_argument(
        "--days",
        type=_non_negative_int,
        default=None,
        help="Delete battles older than N days (default: 90; 0 = all)",
    )
    prune_cutoff.add_argument(
        "--before",
        default=None,
        help="Delete battles before YYYY-MM-DD (UTC)",
    )
    p_battle_prune.add_argument(
        "--dry-run", action="store_true", help="Only report what would be deleted"
    )
    p_battle_prune.add_argument(
        "--json", action="store_true", help="Print the result as JSON"
    )
    p_battle_prune.set_defaults(func=cmd_battle_prune)

    # Chat (guild chat)
    from hw_genie.commands.chat import CHAT_TYPES as _CHAT_TYPES

    p_chat = subparsers.add_parser(
        "chat", parents=[parent_parser], help="Show guild chat (chatGetAll)"
    )
    p_chat.add_argument(
        "--type",
        choices=_CHAT_TYPES,
        default="clan",
        help="Chat type (default: clan)",
    )
    p_chat.add_argument(
        "--count",
        type=_positive_int,
        default=50,
        help="Number of messages to fetch (default: 50, clamp 1-200)",
    )
    p_chat.add_argument("--last-id", dest="last_id", default=None, help="Pagination: fetch messages before this ID")
    raw_json_group = p_chat.add_mutually_exclusive_group()
    raw_json_group.add_argument("--raw", action="store_true", help="Print raw chatGetAll response as JSON")
    raw_json_group.add_argument("--json", action="store_true", help="Print parsed messages as JSON")
    p_chat.set_defaults(func=cmd_chat)

    # Titan Arena (ToE)
    p_toe = subparsers.add_parser("toe", parents=[parent_parser], help="Titan Arena (ToE) operations")
    toe_sub = p_toe.add_subparsers(dest="toe_type", help="ToE operation")
    p_toe_attack = toe_sub.add_parser("attack", parents=[parent_parser], help="Start a Titan Arena battle with arbitrary titans")
    p_toe_attack.add_argument(
        "--rival",
        required=False,
        default=None,
        help="Rival ID (omitted → auto-select lowest score wall/player via titanArenaGetStatus, threshold=250)",
    )
    p_toe_attack.add_argument(
        "--titans",
        nargs=5,
        type=int,
        required=False,
        default=None,
        metavar="TITAN_ID",
        help="5 titan IDs (omitted → auto-resolve via teamGetAll.titan_arena)",
    )
    p_toe_attack.add_argument("--dry-run", action="store_true", help="Verify startBattle only (no endBattle)")
    p_toe_attack.add_argument("--estimate-only", action="store_true", help="Estimate win/lose without calling endBattle")
    p_toe_attack.add_argument(
        "--engine",
        choices=["estimate", "hybrid", "playwright"],
        default="estimate",
        help="Battle engine: 'estimate' (power-based, server rejects EndBattle), 'hybrid' (delegate to userscript via auth server), or 'playwright' (headless Chromium, no manual screen required)",
    )
    p_toe_attack.add_argument(
        "--auth-server-url",
        default="http://127.0.0.1:8765",
        help="auth server base URL for the JS bridge (used with --engine hybrid)",
    )
    p_toe_attack.add_argument(
        "--progress",
        choices=["quiet", "line", "verbose"],
        default="line",
        help="Output compactness: 'quiet' (rival-decided + summary + errors), 'line' (one updating status line per account), or 'verbose' (full per-attempt output)",
    )
    p_toe_attack.set_defaults(func=cmd_toe_attack)
    p_toe_run = toe_sub.add_parser("run", parents=[parent_parser], help="Drive a full ToE tier end-to-end (raid + rivals + CompleteTier)")
    p_toe_run.add_argument(
        "--titans",
        nargs=5,
        type=int,
        required=False,
        default=None,
        metavar="TITAN_ID",
        help="5 titan IDs (omitted → auto-resolve via teamGetAll.titan_arena)",
    )
    p_toe_run.add_argument(
        "--engine",
        choices=["estimate", "hybrid", "playwright"],
        default="estimate",
        help="Battle engine: 'estimate' (tier planning only), 'hybrid' (full automation via userscript), or 'playwright' (headless Chromium, no manual screen required)",
    )
    p_toe_run.add_argument(
        "--auth-server-url",
        default="http://127.0.0.1:8765",
        help="auth server base URL for the JS bridge (used with --engine hybrid)",
    )
    p_toe_run.add_argument(
        "--threshold",
        type=int,
        default=AUTO_RIVAL_SCORE_THRESHOLD,
        help=f"attackScore threshold that defines 'rivals worth finishing' (default: {AUTO_RIVAL_SCORE_THRESHOLD} = cleared)",
    )
    p_toe_run.add_argument(
        "--stop-on-loss",
        action="store_true",
        help="Abort the tier after the first losing rival attempt (stops rotation retries; combine --no-end-on-loss and --no-bank-best-loss for no score banking)",
    )
    p_toe_run.add_argument(
        "--no-bank-best-loss",
        action="store_true",
        help="Disable improving-loss banking (--no-end-on-loss sweeps bank only a new best; the default banks every loss; there is no separate extra banking attempt)",
    )
    p_toe_run.add_argument(
        "--seeds",
        type=int,
        default=10,
        help="Seeds tried per team per rival (each seed = fresh StartBattle; loss banking follows --end-on-loss/--no-end-on-loss, default: bank every loss)",
    )
    toe_loss_group = p_toe_run.add_mutually_exclusive_group()
    toe_loss_group.add_argument(
        "--end-on-loss",
        action="store_true",
        help="Bank EndBattle on every losing sim (default; a no-win pass keeps sweeping while banked scores rise). --no-end-on-loss banks only a new best for faster sweeps",
    )
    toe_loss_group.add_argument(
        "--no-end-on-loss",
        action="store_true",
        help="Abandon losing sims except a new best (fastest sweep; combine with --no-bank-best-loss to never bank a loss)",
    )
    p_toe_run.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Cap total StartBattle attempts per rival (default: full pass = teams x seeds; estimate-engine runs should pass an explicit cap)",
    )
    p_toe_run.add_argument(
        "--progress",
        choices=["quiet", "line", "verbose"],
        default="line",
        help="Output compactness: 'quiet' (rival-decided + summary + errors), 'line' (one updating status line per account), or 'verbose' (full per-attempt output)",
    )
    p_toe_run.set_defaults(func=cmd_toe_run)
    p_toe_status = toe_sub.add_parser("status", parents=[parent_parser], help="Show Titan Arena status (tier, rivals)")
    p_toe_status.set_defaults(func=cmd_toe_status)

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

    # Quests
    p_quests = subparsers.add_parser("quests", parents=[parent_parser], help="Quest status (daily quests)")
    p_quests.add_argument("--show-all", action="store_true", help="Show completed quests too (default: uncompleted only)")
    p_quests.add_argument("--raw", action="store_true", help="Print the raw questGetAll response as JSON")
    p_quests.add_argument("--category", choices=["daily", "weekly", "guild", "main", "event", "battlepass", "one_time", "unknown"], help="Filter by quest category")
    p_quests.add_argument("--execute", action="store_true", help="Execute operations to complete uncompleted daily quests (destructive; asks confirmation per step unless --yes)")
    p_quests.add_argument("--dry-run", action="store_true", help="Show the quest execution plan without running anything")
    p_quests.add_argument("--yes", action="store_true", help="Skip per-step confirmation (only valid with --execute)")
    p_quests.add_argument("--set-default", nargs=3, metavar=("QUEST_ID", "KEY", "VALUE"), help="Register an account-specific operation arg override (e.g. --set-default 10024 heroId 999, --set-default guild enabled true, or --set-default glyph heroId 46)")
    p_quests.add_argument("--init-defaults", action="store_true", help="Initialize quest_defaults, quest_guild_defaults and guild_glyph_defaults for the account (seed as enabled=false)")
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
        choices=["daily", "full", "quests", "asgard-shop", "consumable", "toe", "seers-game"],
        nargs="?",
        default="daily",
        help="Routine to run: 'daily' (default), 'full' (raid+shop+daily), 'quests' (daily quest auto-completion), 'asgard-shop' (Osh/Maestro Guild Raid merchant auto-buy), 'consumable' (consume all registered consumables), 'toe' (Titan Arena tier clear), or 'seers-game' (play Seer's Game until coins run out)",
    )
    p_multi.add_argument(
        "--engine",
        choices=["estimate", "hybrid", "playwright"],
        default="hybrid",
        help="Battle engine for the 'toe' mode (default: hybrid)",
    )
    p_multi.add_argument(
        "--seeds",
        type=int,
        default=10,
        help="Seeds tried per team per rival for the 'toe' mode (default: 10)",
    )
    p_multi.add_argument(
        "--threshold",
        type=int,
        default=250,
        help="attackScore threshold for the 'toe' mode (default: 250 = cleared)",
    )
    p_multi.add_argument(
        "--auth-server-url",
        default="http://127.0.0.1:8765",
        help="auth server base URL for the 'toe' mode JS bridge (default: http://127.0.0.1:8765)",
    )
    p_multi.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Cap total StartBattle attempts per rival for the 'toe' mode (default: full pass; estimate-engine runs should pass an explicit cap)",
    )
    p_multi.add_argument(
        "--no-bank-best-loss",
        action="store_true",
        help="Disable improving-loss banking in the 'toe' mode (--no-end-on-loss sweeps bank only a new best; the default banks every loss; there is no separate extra banking attempt)",
    )
    multi_loss_group = p_multi.add_mutually_exclusive_group()
    multi_loss_group.add_argument(
        "--end-on-loss",
        action="store_true",
        help="Bank EndBattle on every losing sim in the 'toe' mode (default; a no-win pass keeps sweeping while banked scores rise)",
    )
    multi_loss_group.add_argument(
        "--no-end-on-loss",
        action="store_true",
        help="Abandon losing sims except a new best in the 'toe' mode (fastest sweep; combine with --no-bank-best-loss to never bank)",
    )
    gold_group = p_multi.add_mutually_exclusive_group()
    gold_group.add_argument(
        "--gold",
        dest="gold_buffs",
        action="store_true",
        default=None,
        help="Buy gold buffs (slot 1-5) in the 'asgard-shop' mode (default: Osh week buff 63/64 only, Maestro week all)",
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
        "--pick",
        type=int,
        default=2,
        help="Card to pick each round for the 'seers-game' mode (default: 2; validated against live event.size)",
    )
    p_multi.add_argument(
        "--max-games",
        type=int,
        default=None,
        help="Max games to play for the 'seers-game' mode (default: until coins run out)",
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
        help="Show the execution plan without running anything (quests/consumable/seers-game modes only)",
    )
    p_multi.add_argument(
        "--iterations",
        type=int,
        default=9999,
        help="Item raid iteration count for 'daily'/'full' modes (each request raids 10 times). Default: until stamina runs out.",
    )
    p_multi.add_argument(
        "--progress",
        choices=["quiet", "line", "verbose"],
        default="line",
        help="Output compactness for the 'toe' mode: 'quiet' (rival-decided + summary + errors), 'line' (one updating status line per account), or 'verbose' (full per-attempt output)",
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

    # The replica-connection sync must be disabled before init_db() opens the
    # engine, otherwise the first connection still performs a remote sync.
    if getattr(args, "no_sync", False):
        os.environ["TURSO_SYNC_ON_CONNECT"] = "false"

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
    except KeyboardInterrupt:
        print("\nInterrupted (Ctrl+C).", file=sys.stderr)
        sys.exit(130)
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
