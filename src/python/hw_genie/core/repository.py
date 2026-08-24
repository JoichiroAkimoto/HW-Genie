import copy
import json
import logging
from typing import Any, Callable, List, TypedDict
from sqlalchemy import text
from .database import (
    Account,
    _wal_io_lock,
    get_engine,
    get_session_local,
    get_write_engine,
    get_write_session_local,
    is_hrana_stream_error,
    retry_on_transient_db_error,
    retry_on_wal_contention,
)


class HeadersConfig(TypedDict, total=False):
    """``config_key = "headers"`` — authentication headers stored per account."""
    x_auth_session_id: str
    x_auth_token: str
    x_auth_user_id: str
    x_request_id: str


class PlayerInfo(TypedDict, total=False):
    """Player profile assembled from ``Account`` columns and ``player_*`` config keys."""
    id: str
    name: str
    level: int
    gold: int
    gems: int
    energy: int
    arena_rank: int
    grand_rank: int


class AccountData(TypedDict, total=False):
    """Full account data dict returned by :meth:`get_data` and accepted by :meth:`update_config`."""
    headers: HeadersConfig
    player: PlayerInfo
    status: str
    last_updated: str
    last_item_raid_mission_id: int
    memo: str

# Process-wide lock serialising every operation that writes into the shared
# local libSQL Embedded Replica WAL. When running all accounts inside one
# process (``hw-genie multi`` / bin/hwda / the parallel ``auth --list --fresh``
# refreshes), multiple threads may try to sync() or write the SAME local file
# concurrently. SQLite WAL permits only a single writer, so without
# serialisation concurrent writers can raise ``wal_insert_begin failed`` /
# ``database is locked``. ``_wal_io_lock`` (an RLock, shared with the
# on-connect ``sync()`` in database.py) guarantees a single writer at a time
# while reads and the network-heavy work stay parallel.
#
# NOTE: this lock only serialises threads WITHIN one process. Separate
# processes (e.g. a long-running auth-server plus a CLI command) share the same
# replica file too; their races are handled by the WAL-contention retry in
# :func:`hw_genie.core.database.retry_on_wal_contention`.

logger = logging.getLogger(__name__)


def _find_account_by_alias(db, alias: str):
    """Find an Account row by alias with canonical fallback.

    Exact (stripped) match is tried first. If not found, a
    case-insensitive, whitespace-insensitive fallback is used so that
    inputs like ``champion`` / ``Champion `` resolve to the registered
    ``Champion`` (see run_logs failed cases id 54/55/59). Returns None
    when no account matches.
    """
    if not isinstance(alias, str):
        return None
    stripped = alias.strip()
    if not stripped:
        return None
    rec = db.query(Account).filter(Account.alias == stripped).order_by(Account.id).first()
    if rec is not None:
        return rec
    # fallback: case-insensitive, whitespace-insensitive
    #
    # あえて Python 側で ``.lower()`` している: SQLite の SQL ``LOWER()`` は
    # ASCII 専用のため、非 ASCII（日本語など）のエイリアスを SQL 側で
    # 小文字比較すると一致しなくなる。走査は id/alias の 2 列に限定し、
    # Account 行全体のロード（JSON カラムのデシリアライズ含む）を避ける。
    # ``order_by(Account.id)`` で複数ヒット時の決定性も保証する。
    rows = db.query(Account.id, Account.alias).order_by(Account.id).all()
    target = stripped.lower()
    for account_id, cand_alias in rows:
        if isinstance(cand_alias, str) and cand_alias.strip().lower() == target:
            return db.get(Account, account_id)
    return None


def _deserialize_config_value(raw_value: Any) -> Any:
    """生 SQL で取得した ``config_value`` を Python オブジェクトへ復元する。

    SQLAlchemy の JSON 型は書き込み時に ``json.dumps`` で文字列化され保存
    されるため（int/float/bool のスカラーも同様）、読み出しは通常 str で返る。
    一方、SQLite の動的型付け（NUMERIC affinity）により、直接書き込まれた
    純数値文字列等は整数型で返ることもある。``str`` のみ ``json.loads`` し、
    それ以外はそのまま返す（壊れた JSON は例外を送出する）。
    """
    if isinstance(raw_value, str):
        return json.loads(raw_value)
    return raw_value


class SessionRepository:
    def _read_with_retry(self, fn):
        """Run a read ``fn``, retrying transient DB errors (Hrana stream death).

        In remote-direct mode (``TURSO_READ_REMOTE=true``, used by the
        auth-server container) a long-idle Hrana stream can die between
        pool_pre_ping and the actual statement. Dispose the read pool on such
        errors so the next checkout opens a fresh stream. WAL contention and
        validation errors do not dispose (connections stay healthy).
        """

        def _attempt():
            try:
                return fn()
            except Exception as exc:
                if is_hrana_stream_error(exc):
                    logger.warning(
                        "Transient Hrana stream error on read; disposing read "
                        "pool before retry: %s",
                        exc,
                    )
                    self._dispose_pool(get_engine, "read")
                raise

        return retry_on_transient_db_error(_attempt, logger=logger)

    def _dispose_pool(self, engine_getter, label: str) -> None:
        """Discard pooled connections so the next checkout is fresh.

        A Turso Hrana stream that died while idle cannot be revived in place;
        reusing the pooled connection would fail again with the same error on
        the next attempt. Disposing the pool forces SQLAlchemy to open a new
        connection (new stream) on the next checkout. Best-effort: a failure
        here must not mask the original error.

        NOTE: in the default configuration (``TURSO_WRITE_REMOTE`` unset) the
        write engine IS the read engine, so disposing the write pool also
        empties the shared read pool. Callers must therefore gate this on dead
        Hrana streams only (WAL contention leaves connections healthy and
        disposing would re-trigger sync() into the contended WAL).
        """
        try:
            engine = engine_getter()
            if hasattr(engine, "pool"):
                engine.pool.dispose()
        except Exception:
            logger.warning("Failed to dispose %s pool", label, exc_info=True)

    def get_data(self, account: str) -> AccountData:
        """
        Retrieves all data for an account, merging info from Account and AccountConfig tables.

        Reads use the (local replica) session: with TURSO_SYNC_ON_CONNECT the
        replica pulls the latest from the remote before querying.

        Args:
            account (str): The account alias.

        Returns:
            AccountData: A dictionary containing merged account data.
        """

        def _read() -> AccountData:
            with get_session_local()() as db:
                account_rec = _find_account_by_alias(db, account)
                if not account_rec:
                    return {}

                data = {}
                player_info = {}

                configs = db.execute(
                    text(
                        "SELECT config_key, config_value FROM account_configs "
                        "WHERE account_id = :account_id"
                    ),
                    {"account_id": account_rec.id},
                ).all()

                for key, raw_value in configs:
                    # SQLAlchemy の JSON カラムはフェッチ時にデシリアライズされ、
                    # 1 行でも壊れた JSON があると `.all()` 全体が失敗する。
                    # 生 SQL で取得して行ごとに個別パースし、壊れた行は警告して
                    # スキップする（hwda / auth / quests 等すべての読み取り経路
                    # が単一の壊れた行で落ちるのを防ぐ）。
                    if raw_value is None:
                        continue
                    try:
                        val = _deserialize_config_value(raw_value)
                    except (TypeError, ValueError) as exc:
                        logger.warning(
                            "config_key=%r for account %r has broken JSON; "
                            "skipping: %s",
                            key,
                            account,
                            exc,
                        )
                        continue

                    if key == "headers":
                        data["headers"] = val
                    elif key.startswith("player_"):
                        # レガシー互換: 旧バージョンが保存した player_* 行の読み取り
                        # （書き込み側は廃止済み。既存データのみを再構成する）
                        player_info[key[7:]] = val
                    else:
                        data[key] = val

                # Add basic player info from Account table (Source of Truth)
                status_mapping = {
                    "player_name": "name",
                    "level": "level",
                    "gold": "gold",
                    "gems": "gems",
                    "energy": "energy",
                    "arena_rank": "arena_rank",
                    "grand_rank": "grand_rank",
                }
                for attr, p_key in status_mapping.items():
                    val = getattr(account_rec, attr)
                    # To maintain compatibility with tests that expect minimal player info,
                    # we skip default values (0 or "Unknown") or None.
                    if val is not None and val != 0 and val != "Unknown":
                        player_info[p_key] = val

                # Add last_mission_id to data (Source of Truth)
                if account_rec.last_mission_id is not None:
                    data["last_item_raid_mission_id"] = account_rec.last_mission_id

                if account_rec.memo is not None:
                    data["memo"] = account_rec.memo

                if player_info:
                    data["player"] = player_info

                return data

        return self._read_with_retry(_read)

    def check_configs(self) -> List[dict]:
        """全アカウントの account_configs を走査し、壊れた JSON 行を列挙する。

        ``get_data`` は壊れた行を黙ってスキップするため、手動編集等で破損が
        混入しても日常操作は続行できる。その代わり、どこが壊れているかを
        確認する手段として本関数（``hw-genie db-check``）を提供する。

        Returns:
            list[dict]: 壊れた行のリスト。
            各 dict は ``{"account": alias, "key": config_key, "error": str}``。
        """

        def _read() -> List[dict]:
            broken = []
            with get_session_local()() as db:
                rows = db.execute(
                    text(
                        "SELECT a.alias, c.config_key, c.config_value "
                        "FROM account_configs c JOIN accounts a ON a.id = c.account_id"
                    )
                ).all()
                for alias, key, raw_value in rows:
                    if raw_value is None:
                        continue
                    try:
                        _deserialize_config_value(raw_value)
                    except (TypeError, ValueError) as exc:
                        broken.append(
                            {
                                "account": alias,
                                "key": key,
                                "error": str(exc),
                            }
                        )
            return broken

        return self._read_with_retry(_read)

    def list_accounts(self) -> List[str]:
        """Returns all account aliases in registration order (by ``id``).

        ``id`` is the rowid alias of the accounts table, so ordering by it is
        deterministic regardless of physical layout or replica rebuilds. All
        consumers (``auth --list``, ``multi``, hwda/hwsa) rely on this order.
        """

        def _read() -> List[str]:
            with get_session_local()() as db:
                records = db.query(Account.alias).order_by(Account.id).all()
                return [r.alias for r in records]

        return self._read_with_retry(_read)

    def save_data(self, account: str, data: AccountData) -> None:
        """Backward compatibility wrapper for update_config."""
        self.update_config(account, data)

    def update_config(self, account: str, data: AccountData) -> None:
        """
        Updates account data in both Account and AccountConfig tables.
        
        Args:
            account (str): The account alias.
            data (AccountData): The data to save.
        """
        # The local replica's SQLite WAL only allows a single writer, and OTHER
        # processes sharing the same replica file (auth-server, a concurrently
        # launched CLI, ...) can transiently hold it. A remote (Turso) write
        # can additionally fail because the Hrana stream died while idle. Retry
        # such transient errors with exponential backoff instead of aborting
        # the run. The lock is taken per attempt (NOT around the whole retry
        # loop) so that backoff sleeps between attempts do not block other
        # threads in this process.
        def _attempt() -> None:
            try:
                return self._update_config_locked(account, data)
            except Exception as exc:
                # Dispose ONLY on dead Hrana streams. WAL contention
                # (wal_insert_begin failed / database is locked) leaves the
                # connection healthy: disposing would discard warm connections
                # and force a new on-connect sync() into the very WAL that is
                # already contended, amplifying the problem. Validation errors
                # are non-transient and need no dispose either.
                if is_hrana_stream_error(exc):
                    logger.warning(
                        "Transient Hrana stream error; disposing write pool "
                        "before retry: %s",
                        exc,
                    )
                    self._dispose_pool(get_write_engine, "write")
                raise

        retry_on_wal_contention(_attempt, logger=logger)

    def update_config_merged(
        self, account: str, key: str, merge: Callable[[Any | None], Any]
    ) -> Any:
        """Atomically read-modify-write one config key inside the WAL lock.

        ``merge(existing)`` receives the current stored value for ``key``
        (``None`` when the row is absent) and must return the new value to
        store. The read and the write happen under ``_wal_io_lock``, so
        concurrent threads in this process cannot lose updates (e.g. two
        ``quest_guild_defaults`` writers clobbering each other's
        ``last_recipe_at``). Returns the stored value.

        The read uses the write session so it sees the same state the write
        will replace. WAL contention is retried like ``update_config``.
        """
        def _attempt() -> Any:
            try:
                with _wal_io_lock:
                    with get_write_session_local()() as db:
                        account_rec = _find_account_by_alias(db, account)
                        existing: Any = None
                        if account_rec is not None:
                            row = db.execute(
                                text(
                                    "SELECT config_value FROM account_configs "
                                    "WHERE account_id = :account_id AND config_key = :config_key"
                                ),
                                {"account_id": account_rec.id, "config_key": key},
                            ).first()
                            if row is not None and row[0] is not None:
                                try:
                                    existing = json.loads(row[0])
                                except (TypeError, ValueError):
                                    existing = None
                        # merge が existing を変異させる（quests の set/ensure は
                        # 既存 dict に直接キーを足す）ため、比較用に変異前の
                        # スナップショットを取っておく。変異後は existing と
                        # new_value が同じオブジェクトになり値比較が無意味に
                        # なる（書き込みスキップの誤判定）のを防ぐ。
                        existing_snapshot = copy.deepcopy(existing)
                        new_value = merge(existing)
                        if account_rec is None:
                            raise ValueError(f"Account not found for alias: {account}")
                        # 値が変わっていなければ書き込みをスキップ（ensure_* が毎回
                        # 呼ばれても WAL 書き込みが走らないようにする）
                        if existing_snapshot != new_value:
                            self._upsert_config(db, account_rec.id, key, new_value)
                            db.commit()
                        return new_value
            except Exception as exc:
                if is_hrana_stream_error(exc):
                    logger.warning(
                        "Transient Hrana stream error; disposing write pool "
                        "before retry: %s",
                        exc,
                    )
                    self._dispose_pool(get_write_engine, "write")
                raise

        return retry_on_wal_contention(_attempt, logger=logger)

    def _update_config_locked(self, account: str, data: AccountData) -> None:
        """Single locked ``update_config`` attempt (retried on WAL contention)."""
        with _wal_io_lock:
            return self._update_config_tx(account, data)

    def _update_config_tx(self, account: str, data: AccountData) -> None:
        """Single ``update_config`` transaction attempt (retried on WAL contention)."""
        with get_write_session_local()() as db:
            try:
                # 1. Update/Create Account
                player = data.get("player")
                if player is not None and isinstance(player, dict):
                    player_id = player.get("id")
                    if player_id is None or player_id == "":
                        # Fallback to alias for compatibility
                        account_rec = _find_account_by_alias(db, account)
                    else:
                        account_rec = db.query(Account).filter(Account.player_id == player_id).first()

                    if not account_rec:
                        if player_id is not None and player_id != "":
                            account_rec = Account(player_id=player_id, alias=account.strip())
                        else:
                            raise ValueError(f"player_id is required for new account alias: {account}")
                        db.add(account_rec)
                        db.flush()

                    # Update alias and basic info using model method
                    new_alias = account.strip()
                    existing_alias = (
                        account_rec.alias if isinstance(account_rec.alias, str) else None
                    )
                    # case/whitespace-insensitive に既存行へ一致した場合、大文字
                    # 小文字のみの差なら既存（正規）alias を保持する。入力値で
                    # 無条件に上書きすると ``save("champion")`` が正規行 ``Champion``
                    # を小文字へリネームしてしまい、エイリアス揺れ防止の目的が
                    # 無効化される。
                    # - 前後空白のみの差 -> トリム済み入力で正規化
                    #   （test_save_normalizes_trailing_space_existing_alias の回帰防止）
                    # - それ以外の不一致 -> 意図的なリネームとして入力を採用
                    #   （test_prevent_account_duplication のリネーム経路）
                    if (
                        not existing_alias
                        or existing_alias.strip().lower() != new_alias.lower()
                    ):
                        account_rec.alias = new_alias
                    elif existing_alias != new_alias and existing_alias.strip() == new_alias:
                        account_rec.alias = new_alias
                    account_rec.update_from_dict(player)

                elif player is None:
                    account_rec = _find_account_by_alias(db, account)
                    if not account_rec:
                        raise ValueError(f"Account not found for alias: {account}")

                # 2. Update other configs
                for k, v in data.items():
                    if k == "player":
                        continue
                    if k == "memo":
                        account_rec.memo = v
                    elif k == "last_item_raid_mission_id":
                        try:
                            account_rec.last_mission_id = int(v)
                        except (ValueError, TypeError):
                            pass
                    else:
                        self._upsert_config(db, account_rec.id, k, v)

                db.commit()
            except Exception:
                db.rollback()
                raise

    def _upsert_config(self, db, account_id: int, key: str, value: Any) -> None:
        """
        Helper to insert or update a config entry.

        Uses raw SQL (``INSERT ... ON CONFLICT DO UPDATE``) so that a row whose
        JSON is broken can still be overwritten: the ORM path
        (``db.query(AccountConfig).filter_by(...).first()``) would try to
        deserialize the JSON column while SELECTing the row and crash with
        ``JSONDecodeError`` -- making ``--set-default`` unable to repair the
        very row ``hw-genie db-check`` reports.

        Args:
            db: The DB session.
            account_id (int): Internal Account ID.
            key (str): Config key.
            value (Any): Config value (JSON-serializable).
        """
        if not key or not isinstance(key, str):
            raise ValueError(f"Invalid config_key: {key}")
        db.execute(
            text(
                "INSERT INTO account_configs (account_id, config_key, config_value) "
                "VALUES (:account_id, :config_key, :config_value) "
                "ON CONFLICT (account_id, config_key) "
                "DO UPDATE SET config_value = excluded.config_value"
            ),
            {
                "account_id": account_id,
                "config_key": key,
                "config_value": json.dumps(value),
            },
        )
