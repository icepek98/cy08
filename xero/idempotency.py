"""Persistent local idempotency state for Xero invoices."""

from __future__ import annotations

import os
import secrets
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


# A day gives slow or unattended RPA runs ample time while allowing crashed
# reservations to be recovered without leaving invoices blocked indefinitely.
DEFAULT_PROCESSING_TTL = timedelta(hours=24)
_DATABASE_NAME = "xero_invoice_state.sqlite3"


@dataclass(frozen=True)
class ReservationResult:
    """Outcome of trying to reserve one tenant-scoped Xero invoice."""

    reserved: bool
    already_success: bool
    already_processing: bool
    attempt_token: str | None = None


def _database_path() -> Path:
    """Return a per-user data path, never relative to the application bundle."""
    home = Path.home()

    if sys.platform == "darwin":
        data_dir = home / "Library" / "Application Support" / "Cy08"
    elif os.name == "nt":
        roaming_dir = Path(os.environ.get("APPDATA", ""))
        if not roaming_dir.is_absolute():
            roaming_dir = home / "AppData" / "Roaming"
        data_dir = roaming_dir / "Cy08"
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data_home) if xdg_data_home else home / ".local" / "share"
        if not base.is_absolute():
            base = home / ".local" / "share"
        data_dir = base / "cy08"

    return data_dir / _DATABASE_NAME


class InvoiceIdempotencyStore:
    """SQLite ledger for reserving and completing Xero invoice attempts."""

    def __init__(
        self,
        database_path: str | os.PathLike[str] | None = None,
        processing_ttl: timedelta = DEFAULT_PROCESSING_TTL,
    ) -> None:
        if processing_ttl <= timedelta(0):
            raise ValueError("processing_ttl must be greater than zero")

        self._database_path = Path(database_path) if database_path else _database_path()
        self._processing_ttl = processing_ttl
        self._initialize()

    def reserve_invoice(self, tenant_id: str, xero_invoice_id: str) -> ReservationResult:
        """Atomically reserve an invoice unless it is successful or in progress."""
        self._validate_identity(tenant_id, xero_invoice_id)
        now = datetime.now(timezone.utc)
        now_text = now.isoformat()
        attempt_token = secrets.token_urlsafe(32)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, updated_at
                FROM xero_invoice_state
                WHERE tenant_id = ? AND xero_invoice_id = ?
                """,
                (tenant_id, xero_invoice_id),
            ).fetchone()

            if row is None:
                connection.execute(
                    """
                    INSERT INTO xero_invoice_state
                        (tenant_id, xero_invoice_id, status, attempt_token, updated_at)
                    VALUES (?, ?, 'PROCESSING', ?, ?)
                    """,
                    (tenant_id, xero_invoice_id, attempt_token, now_text),
                )
                return ReservationResult(True, False, False, attempt_token)

            status, updated_at = row
            if status == "SUCCESS":
                return ReservationResult(False, True, False)

            previous_update = datetime.fromisoformat(updated_at)
            if now - previous_update < self._processing_ttl:
                return ReservationResult(False, False, True)

            connection.execute(
                """
                UPDATE xero_invoice_state
                SET attempt_token = ?, updated_at = ?
                WHERE tenant_id = ? AND xero_invoice_id = ? AND status = 'PROCESSING'
                """,
                (attempt_token, now_text, tenant_id, xero_invoice_id),
            )
            return ReservationResult(True, False, False, attempt_token)

    def mark_success(
        self, tenant_id: str, xero_invoice_id: str, attempt_token: str
    ) -> bool:
        """Mark the current reservation successful only if its token still owns it."""
        self._validate_identity(tenant_id, xero_invoice_id)
        if not attempt_token:
            raise ValueError("attempt_token must not be empty")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE xero_invoice_state
                SET status = 'SUCCESS', updated_at = ?
                WHERE tenant_id = ? AND xero_invoice_id = ?
                  AND status = 'PROCESSING' AND attempt_token = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    tenant_id,
                    xero_invoice_id,
                    attempt_token,
                ),
            )
            return cursor.rowcount == 1

    def release_invoice(
        self, tenant_id: str, xero_invoice_id: str, attempt_token: str
    ) -> bool:
        """Release a failed/cancelled attempt so it can be retried immediately."""
        self._validate_identity(tenant_id, xero_invoice_id)
        if not attempt_token:
            raise ValueError("attempt_token must not be empty")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM xero_invoice_state
                WHERE tenant_id = ? AND xero_invoice_id = ?
                  AND status = 'PROCESSING' AND attempt_token = ?
                """,
                (tenant_id, xero_invoice_id, attempt_token),
            )
            return cursor.rowcount == 1

    def get_status(self, tenant_id: str, xero_invoice_id: str) -> str | None:
        """Return only the persisted status, or None when no ledger entry exists."""
        self._validate_identity(tenant_id, xero_invoice_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status
                FROM xero_invoice_state
                WHERE tenant_id = ? AND xero_invoice_id = ?
                """,
                (tenant_id, xero_invoice_id),
            ).fetchone()
        return row[0] if row else None

    def _initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS xero_invoice_state (
                    tenant_id TEXT NOT NULL,
                    xero_invoice_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('PROCESSING', 'SUCCESS')),
                    attempt_token TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, xero_invoice_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _validate_identity(tenant_id: str, xero_invoice_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if not isinstance(xero_invoice_id, str) or not xero_invoice_id.strip():
            raise ValueError("xero_invoice_id must be a non-empty string")
