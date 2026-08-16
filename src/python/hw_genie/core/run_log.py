"""Execution run log recording (Turso-backed).

``multi`` 実行（hwda / hwsa / Docker / 別ホストの CLI 直実行）の結果サマリーと
出力全文を ``run_logs`` テーブルに保存し、Turso レプリカ同期経由で全環境から
閲覧できるようにする（``hw-genie log ls`` / ``log show <id>``）。

設計方針:
- 書き込みは実行終了時に 1 回・1 レコード（running 状態は持たない。プロセスが
  途中で死んだ場合はレコード自体が残らない = ファイルログと同じ挙動）。
- best-effort: DB 書き込みに失敗しても呼び出し元の実行は落とさない。
- 古いレコードは ``HW_LOG_KEEP_DAYS``（デフォルト 7 日、0 で無効化）に基づき
  記録のたびに削除する（``bin/hwda`` / ``bin/hwsa`` のファイル削除と同じ値）。
"""

import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .database import (
    RunLog,
    _wal_io_lock,
    get_session_local,
    get_write_session_local,
    retry_on_transient_db_error,
    retry_on_wal_contention,
)

logger = logging.getLogger(__name__)

# bin/hwda の perl 除去と同じ ANSI SGR エスケープ（\x1b[...m）パターン
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI SGR escape sequences (e.g. ``\\x1b[31m``) from ``text``."""
    return _ANSI_RE.sub("", text)


def log_keep_days() -> int:
    """Retention days for ``run_logs`` rows (``HW_LOG_KEEP_DAYS``).

    Default 7; ``0`` disables pruning (matching ``bin/hwda``'s file cleanup).
    Invalid values fall back to the default.
    """
    try:
        return int(os.environ.get("HW_LOG_KEEP_DAYS", "7"))
    except (TypeError, ValueError):
        return 7


def _utcnow_naive() -> datetime:
    """Current UTC time as a naive datetime (SQLite DateTime カラムと統一)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _TeeStream:
    """Write-through stream: mirrors writes to ``target`` and appends to ``buffer``."""

    def __init__(self, target: Any, buffer: list[str]) -> None:
        self._target = target
        self._buffer = buffer

    def write(self, data: str) -> int:
        self._buffer.append(data)
        return self._target.write(data)

    def flush(self) -> None:
        self._target.flush()

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)

    def isatty(self) -> bool:
        try:
            return self._target.isatty()
        except Exception:
            return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


class _BufferHandler(logging.Handler):
    """A logging handler that appends formatted records to a buffer list.

    The terminal still receives records via the pre-existing handlers (e.g. the
    ``basicConfig`` stderr handler); this handler only adds a copy to the buffer.
    """

    def __init__(self, buffer: list[str]) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record) + "\n")
        except Exception:  # pragma: no cover - defensive
            self.handleError(record)


class OutputCapture:
    """Mirror console output to the terminal while buffering a copy for storage.

    Wraps ``sys.stdout`` with a tee (so ``print`` output is captured) and adds a
    temporary logging handler (so ``logger`` output — which goes to stderr via
    ``basicConfig`` — is captured too). :meth:`getvalue` returns the buffered
    text with ANSI codes stripped; the terminal mirror keeps them untouched.
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._stdout = sys.stdout
        self._handler: logging.Handler | None = None

    def __enter__(self) -> "OutputCapture":
        sys.stdout = _TeeStream(self._stdout, self._buffer)
        handler = _BufferHandler(self._buffer)
        # basicConfig のフォーマッタ（"%(levelname)s: %(message)s"）と同じ表示に
        # するため、既存ハンドラのフォーマッタを流用する。
        for existing in logging.getLogger().handlers:
            if existing.formatter is not None:
                handler.setFormatter(existing.formatter)
                break
        logging.getLogger().addHandler(handler)
        self._handler = handler
        return self

    def __exit__(self, *exc: object) -> None:
        sys.stdout = self._stdout
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
            self._handler = None

    def getvalue(self) -> str:
        """Buffered output with ANSI escape sequences removed."""
        return strip_ansi("".join(self._buffer))


def record_run_log(
    *,
    started_at: datetime,
    finished_at: datetime,
    mode: str,
    status: str,
    exit_code: int | None,
    accounts: list[dict[str, Any]],
    error_summary: str | None,
    log_text: str | None,
    log_file: str | None = None,
) -> int | None:
    """Insert one ``run_logs`` row and prune expired rows.

    Best-effort: on any DB error the failure is logged and ``None`` is returned
    so the caller's actual work is never aborted by log recording. Returns the
    new row id on success. Timestamps are normalised to naive UTC.
    """
    started_at = started_at.replace(tzinfo=None)
    finished_at = finished_at.replace(tzinfo=None)

    def _attempt() -> int | None:
        with _wal_io_lock:
            with get_write_session_local()() as db:
                keep_days = log_keep_days()
                if keep_days > 0:
                    cutoff = _utcnow_naive() - timedelta(days=keep_days)
                    db.query(RunLog).filter(
                        RunLog.started_at < cutoff
                    ).delete(synchronize_session=False)
                row = RunLog(
                    started_at=started_at,
                    finished_at=finished_at,
                    mode=mode,
                    status=status,
                    exit_code=exit_code,
                    accounts=accounts,
                    error_summary=error_summary,
                    log_text=log_text,
                    log_file=log_file,
                )
                db.add(row)
                db.commit()
                return row.id

    try:
        return retry_on_wal_contention(_attempt, logger=logger)
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("Failed to record run log: %s", exc)
        return None


def list_run_logs(limit: int = 10) -> list[RunLog]:
    """Return the most recent ``limit`` run logs (newest first)."""

    def _read() -> list[RunLog]:
        with get_session_local()() as db:
            return (
                db.query(RunLog)
                .order_by(RunLog.id.desc())
                .limit(limit)
                .all()
            )

    return retry_on_transient_db_error(_read, logger=logger)


def get_run_log(run_id: int) -> RunLog | None:
    """Return the run log with ``run_id``, or ``None`` when absent."""

    def _read() -> RunLog | None:
        with get_session_local()() as db:
            return db.query(RunLog).filter(RunLog.id == run_id).first()

    return retry_on_transient_db_error(_read, logger=logger)