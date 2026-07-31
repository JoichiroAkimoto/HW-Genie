import logging
import threading
from typing import Any, List, TypedDict
from .database import (
    Account,
    AccountConfig,
    get_session_local,
    get_write_session_local,
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

# Process-wide lock serialising write transactions. When running all accounts
# inside one process (``hw-genie multi`` / bin/hwda), multiple threads may try
# to write the SAME local libSQL Embedded Replica file concurrently. SQLite WAL
# permits only a single writer, so without serialisation concurrent writers can
# raise ``wal_insert_begin failed`` / ``database is locked``. This lock (held
# only for the duration of a ``update_config`` transaction) guarantees a single
# writer at a time while reads and the network-heavy work stay parallel.
#
# NOTE: this lock only serialises threads WITHIN one process. Separate
# processes (e.g. a long-running auth-server plus a CLI command) share the same
# replica file too; their races are handled by the WAL-contention retry in
# :func:`hw_genie.core.database.retry_on_wal_contention`.
_write_lock = threading.Lock()

logger = logging.getLogger(__name__)


class SessionRepository:
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
        with get_session_local()() as db:
            account_rec = db.query(Account).filter(Account.alias == account).first()
            if not account_rec:
                return {}

            data = {}
            player_info = {}

            configs = db.query(AccountConfig).filter(AccountConfig.account_id == account_rec.id).all()
            
            for cfg in configs:
                key = cfg.config_key
                val = cfg.config_value

                if key == "headers":
                    data["headers"] = val
                elif key.startswith("player_"):
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

    def list_accounts(self) -> List[str]:
        """Returns a list of all account aliases."""
        with get_session_local()() as db:
            records = db.query(Account.alias).all()
            return [r.alias for r in records]

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
        with _write_lock:
            # The local replica's SQLite WAL only allows a single writer, and
            # OTHER processes sharing the same replica file (auth-server, a
            # concurrently launched CLI, ...) can transiently hold it. Retry
            # such races with exponential backoff instead of aborting the run.
            retry_on_wal_contention(
                lambda: self._update_config_tx(account, data),
                logger=logger,
            )

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
                        account_rec = db.query(Account).filter(Account.alias == account).first()
                    else:
                        account_rec = db.query(Account).filter(Account.player_id == player_id).first()

                    if not account_rec:
                        if player_id is not None and player_id != "":
                            account_rec = Account(player_id=player_id, alias=account)
                        else:
                            raise ValueError(f"player_id is required for new account alias: {account}")
                        db.add(account_rec)
                        db.flush()

                    # Update alias and basic info using model method
                    account_rec.alias = account.strip()
                    account_rec.update_from_dict(player)

                    # Store other player configs
                    status_fields = ("id", "name", "level", "gold", "gems", "energy", "arena_rank", "grand_rank")
                    for k, v in player.items():
                        if k in status_fields:
                            continue
                        self._upsert_config(db, account_rec.id, f"player_{k}", v)

                elif player is None:
                    account_rec = db.query(Account).filter(Account.alias == account).first()
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
        
        Args:
            db: The DB session.
            account_id (int): Internal Account ID.
            key (str): Config key.
            value (Any): Config value (JSON-serializable).
        """
        if not key or not isinstance(key, str):
            raise ValueError(f"Invalid config_key: {key}")
        existing = db.query(AccountConfig).filter_by(account_id=account_id, config_key=key).first()
        if existing:
            existing.config_value = value
        else:
            db.add(AccountConfig(account_id=account_id, config_key=key, config_value=value))
