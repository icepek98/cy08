"""Persistent local ledger for FieldQuo-to-Xero invoice creation operations."""

from __future__ import annotations

import os
import secrets
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator


_DATABASE_NAME = "fieldquo_xero_invoice_operations.sqlite3"


class OperationState(str, Enum):
    """Persisted states for a FieldQuo-to-Xero creation operation."""

    PROCESSING = "PROCESSING"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    SUCCESS = "SUCCESS"


@dataclass(frozen=True)
class FieldQuoInvoiceOperation:
    """Local operation record; source_identity is not a FieldQuo invoice ID."""

    tenant_id: str
    source_identity: str
    operation_id: str
    idempotency_key: str
    payload_fingerprint: str | None
    state: OperationState
    xero_invoice_id: str | None
    created_at: str
    updated_at: str
    claim_expires_at: str | None
    claim_token: str | None = field(default=None, compare=False)

    @property
    def has_claim(self) -> bool:
        """Whether this caller acquired authorization for the current attempt."""
        return self.claim_token is not None


@dataclass
class _ClaimExecution:
    store: FieldQuoInvoiceIdempotencyStore
    connection: sqlite3.Connection
    tenant_id: str
    source_identity: str
    operation_id: str
    claim_token: str
    authorized: bool

    def mark_success(self, xero_invoice_id: str) -> bool:
        if not self.authorized:
            return False
        return self.store._mark_success(
            self.connection,
            self.tenant_id,
            self.source_identity,
            self.operation_id,
            xero_invoice_id,
            self.claim_token,
            allow_expired=True,
        )

    def mark_outcome_unknown(self) -> bool:
        if not self.authorized:
            return False
        return self.store._mark_outcome_unknown(
            self.connection,
            self.tenant_id,
            self.source_identity,
            self.operation_id,
            self.claim_token,
            allow_expired=True,
        )


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


class FieldQuoInvoiceIdempotencyStore:
    """SQLite ledger keyed by Xero tenant and caller-provided source identity."""

    def __init__(
        self,
        database_path: str | os.PathLike[str] | None = None,
        *,
        lease_seconds: int = 300,
    ) -> None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        self._database_path = Path(database_path) if database_path else _database_path()
        self._lease_seconds = lease_seconds
        self._initialize()

    def reserve(
        self,
        tenant_id: str,
        source_identity: str,
        payload_fingerprint: str | None = None,
    ) -> FieldQuoInvoiceOperation:
        """Return the existing operation or atomically create the first one.

        The fingerprint is stored with the first operation. Comparing fingerprints
        and deciding whether changed data is a new invoice is the caller's job.
        """
        self._validate_identity(tenant_id, source_identity)
        if payload_fingerprint is not None and not isinstance(payload_fingerprint, str):
            raise ValueError("payload_fingerprint must be a string or None")

        now = datetime.now(timezone.utc)
        now_text = now.isoformat()
        claim_token: str | None = None
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT tenant_id, source_identity, operation_id, idempotency_key,
                       payload_fingerprint, state, xero_invoice_id, created_at, updated_at,
                       claim_expires_at
                FROM fieldquo_xero_invoice_operations
                WHERE tenant_id = ? AND source_identity = ?
                """,
                (tenant_id, source_identity),
            ).fetchone()

            if row is None:
                operation_id = str(uuid.uuid4())
                idempotency_key = secrets.token_urlsafe(32)
                claim_token = secrets.token_urlsafe(32)
                claim_expires_at = _lease_expiry(now, self._lease_seconds)
                connection.execute(
                    """
                    INSERT INTO fieldquo_xero_invoice_operations (
                        tenant_id, source_identity, operation_id, idempotency_key,
                        payload_fingerprint, state, xero_invoice_id, created_at, updated_at,
                        claim_token, claim_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        source_identity,
                        operation_id,
                        idempotency_key,
                        payload_fingerprint,
                        OperationState.PROCESSING.value,
                        now_text,
                        now_text,
                        claim_token,
                        claim_expires_at,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT tenant_id, source_identity, operation_id, idempotency_key,
                           payload_fingerprint, state, xero_invoice_id, created_at, updated_at,
                           claim_expires_at
                    FROM fieldquo_xero_invoice_operations
                    WHERE tenant_id = ? AND source_identity = ?
                    """,
                    (tenant_id, source_identity),
                ).fetchone()
            elif row[5] != OperationState.SUCCESS.value:
                expires_at = row[9]
                if (
                    expires_at is None
                    or str(expires_at) <= now_text
                ):
                    claim_token = secrets.token_urlsafe(32)
                    claim_expires_at = _lease_expiry(now, self._lease_seconds)
                    connection.execute(
                        """
                        UPDATE fieldquo_xero_invoice_operations
                        SET claim_token = ?, claim_expires_at = ?, updated_at = ?
                        WHERE tenant_id = ? AND source_identity = ?
                        """,
                        (
                            claim_token,
                            claim_expires_at,
                            now_text,
                            tenant_id,
                            source_identity,
                        ),
                    )
                    row = (*row[:9], claim_expires_at)

        if row is None:
            raise RuntimeError("Failed to retrieve reserved invoice operation")
        return self._operation_from_row(row, claim_token=claim_token)

    def get(
        self, tenant_id: str, source_identity: str
    ) -> FieldQuoInvoiceOperation | None:
        """Return the persisted operation, if one exists."""
        self._validate_identity(tenant_id, source_identity)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, source_identity, operation_id, idempotency_key,
                       payload_fingerprint, state, xero_invoice_id, created_at, updated_at,
                       claim_expires_at
                FROM fieldquo_xero_invoice_operations
                WHERE tenant_id = ? AND source_identity = ?
                """,
                (tenant_id, source_identity),
            ).fetchone()
        return self._operation_from_row(row) if row else None

    def validate_claim(
        self,
        tenant_id: str,
        source_identity: str,
        operation_id: str,
        claim_token: str,
    ) -> bool:
        """Return whether this token still owns an unexpired operation claim."""
        self._validate_identity(tenant_id, source_identity)
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        if not isinstance(claim_token, str) or not claim_token:
            return False

        now_text = _utc_now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM fieldquo_xero_invoice_operations
                WHERE tenant_id = ? AND source_identity = ? AND operation_id = ?
                  AND claim_token = ? AND claim_expires_at > ?
                  AND state IN (?, ?)
                """,
                (
                    tenant_id,
                    source_identity,
                    operation_id,
                    claim_token,
                    now_text,
                    OperationState.PROCESSING.value,
                    OperationState.OUTCOME_UNKNOWN.value,
                ),
            ).fetchone()
        return row is not None

    def mark_success(
        self,
        tenant_id: str,
        source_identity: str,
        operation_id: str,
        xero_invoice_id: str,
        claim_token: str | None = None,
    ) -> bool:
        """Mark an operation successful only for its current, unexpired claimant."""
        self._validate_identity(tenant_id, source_identity)
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        if not isinstance(xero_invoice_id, str) or not xero_invoice_id.strip():
            raise ValueError("xero_invoice_id must be a non-empty string")
        if not isinstance(claim_token, str) or not claim_token:
            return False

        with self._connection() as connection:
            return self._mark_success(
                connection,
                tenant_id,
                source_identity,
                operation_id,
                xero_invoice_id,
                claim_token,
            )

    @contextmanager
    def claim_execution(
        self,
        tenant_id: str,
        source_identity: str,
        operation_id: str,
        claim_token: str,
    ) -> Iterator[_ClaimExecution]:
        """Hold SQLite's writer lock for claim validation and the delegated call."""
        self._validate_identity(tenant_id, source_identity)
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        if not isinstance(claim_token, str) or not claim_token:
            raise ValueError("claim_token must be a non-empty string")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now_text = _utc_now()
            row = connection.execute(
                """
                SELECT 1
                FROM fieldquo_xero_invoice_operations
                WHERE tenant_id = ? AND source_identity = ? AND operation_id = ?
                  AND claim_token = ? AND claim_expires_at > ?
                  AND state IN (?, ?)
                """,
                (
                    tenant_id,
                    source_identity,
                    operation_id,
                    claim_token,
                    now_text,
                    OperationState.PROCESSING.value,
                    OperationState.OUTCOME_UNKNOWN.value,
                ),
            ).fetchone()
            execution = _ClaimExecution(
                self,
                connection,
                tenant_id,
                source_identity,
                operation_id,
                claim_token,
                row is not None,
            )
            yield execution
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def mark_outcome_unknown(
        self,
        tenant_id: str,
        source_identity: str,
        operation_id: str,
        claim_token: str | None = None,
    ) -> bool:
        """Record uncertainty only for the current, unexpired claimant."""
        self._validate_identity(tenant_id, source_identity)
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        if not isinstance(claim_token, str) or not claim_token:
            return False

        with self._connection() as connection:
            return self._mark_outcome_unknown(
                connection,
                tenant_id,
                source_identity,
                operation_id,
                claim_token,
            )

    def _mark_success(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        source_identity: str,
        operation_id: str,
        xero_invoice_id: str,
        claim_token: str,
        *,
        allow_expired: bool = False,
    ) -> bool:
        if not isinstance(xero_invoice_id, str) or not xero_invoice_id.strip():
            raise ValueError("xero_invoice_id must be a non-empty string")
        now_text = _utc_now()
        expiry_condition = "" if allow_expired else "AND claim_expires_at > ?"
        parameters: tuple[object, ...] = (
            OperationState.SUCCESS.value,
            xero_invoice_id,
            now_text,
            tenant_id,
            source_identity,
            operation_id,
            claim_token,
        )
        if not allow_expired:
            parameters += (now_text,)
        parameters += (
            OperationState.PROCESSING.value,
            OperationState.OUTCOME_UNKNOWN.value,
        )
        cursor = connection.execute(
            f"""
            UPDATE fieldquo_xero_invoice_operations
            SET state = ?, xero_invoice_id = ?, updated_at = ?,
                claim_token = NULL, claim_expires_at = NULL
            WHERE tenant_id = ? AND source_identity = ? AND operation_id = ?
              AND claim_token = ? {expiry_condition}
              AND state IN (?, ?)
            """,
            parameters,
        )
        return cursor.rowcount == 1

    def _mark_outcome_unknown(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        source_identity: str,
        operation_id: str,
        claim_token: str,
        *,
        allow_expired: bool = False,
    ) -> bool:
        now_text = _utc_now()
        expiry_condition = "" if allow_expired else "AND claim_expires_at > ?"
        parameters: tuple[object, ...] = (
            OperationState.OUTCOME_UNKNOWN.value,
            now_text,
            tenant_id,
            source_identity,
            operation_id,
            claim_token,
        )
        if not allow_expired:
            parameters += (now_text,)
        parameters += (
            OperationState.PROCESSING.value,
            OperationState.OUTCOME_UNKNOWN.value,
        )
        cursor = connection.execute(
            f"""
            UPDATE fieldquo_xero_invoice_operations
            SET state = ?, updated_at = ?
            WHERE tenant_id = ? AND source_identity = ? AND operation_id = ?
              AND claim_token = ? {expiry_condition}
              AND state IN (?, ?)
            """,
            parameters,
        )
        return cursor.rowcount == 1

    def _initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fieldquo_xero_invoice_operations (
                    tenant_id TEXT NOT NULL,
                    source_identity TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE
                        CHECK (length(idempotency_key) <= 128),
                    payload_fingerprint TEXT,
                    state TEXT NOT NULL
                        CHECK (state IN ('PROCESSING', 'OUTCOME_UNKNOWN', 'SUCCESS')),
                    xero_invoice_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    claim_token TEXT,
                    claim_expires_at TEXT,
                    PRIMARY KEY (tenant_id, source_identity)
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(fieldquo_xero_invoice_operations)"
                )
            }
            if "claim_token" not in columns:
                connection.execute(
                    "ALTER TABLE fieldquo_xero_invoice_operations ADD COLUMN claim_token TEXT"
                )
            if "claim_expires_at" not in columns:
                connection.execute(
                    "ALTER TABLE fieldquo_xero_invoice_operations ADD COLUMN claim_expires_at TEXT"
                )

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _validate_identity(tenant_id: str, source_identity: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if not isinstance(source_identity, str) or not source_identity.strip():
            raise ValueError("source_identity must be a non-empty string")

    @staticmethod
    def _operation_from_row(
        row: tuple[object, ...], *, claim_token: str | None = None
    ) -> FieldQuoInvoiceOperation:
        return FieldQuoInvoiceOperation(
            tenant_id=str(row[0]),
            source_identity=str(row[1]),
            operation_id=str(row[2]),
            idempotency_key=str(row[3]),
            payload_fingerprint=row[4] if row[4] is None else str(row[4]),
            state=OperationState(str(row[5])),
            xero_invoice_id=row[6] if row[6] is None else str(row[6]),
            created_at=str(row[7]),
            updated_at=str(row[8]),
            claim_expires_at=row[9] if row[9] is None else str(row[9]),
            claim_token=claim_token,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_expiry(now: datetime, lease_seconds: int) -> str:
    return (now + timedelta(seconds=lease_seconds)).isoformat()
