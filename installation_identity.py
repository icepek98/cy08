"""Persistent, non-secret identifier for one FieldQuo installation."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4


_DATABASE_NAME = "fieldquo_installation_identity.sqlite3"


class InstallationIdentityError(RuntimeError):
    """Base error for installation identity storage failures."""


class InstallationIdentityStorageError(InstallationIdentityError):
    """Raised when the persistent installation identity cannot be read or written."""


class InstallationIdentityCorruptedError(InstallationIdentityStorageError):
    """Raised when persisted installation identity data is invalid."""


def default_installation_database_path() -> Path:
    """Return the per-user application-data path on supported desktop platforms."""
    home = Path.home()

    if sys.platform == "darwin":
        data_dir = home / "Library" / "Application Support" / "Cy08"
    elif os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", ""))
        if not appdata.is_absolute():
            appdata = home / "AppData" / "Roaming"
        data_dir = appdata / "Cy08"
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data_home) if xdg_data_home else home / ".local" / "share"
        if not base.is_absolute():
            base = home / ".local" / "share"
        data_dir = base / "cy08"

    return data_dir / _DATABASE_NAME


class InstallationIdentityStore:
    """Create and retain one random installation ID without storing credentials."""

    def __init__(self, database_path: str | os.PathLike[str] | None = None) -> None:
        self._database_path = (
            Path(database_path)
            if database_path is not None
            else default_installation_database_path()
        )
        self._initialize()

    def get_installation_id(self) -> UUID:
        """Return the existing ID or atomically create the first one."""
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT installation_id
                    FROM installation_identity
                    WHERE singleton = 1
                    """
                ).fetchone()

                if row is None:
                    installation_id = uuid4()
                    connection.execute(
                        """
                        INSERT INTO installation_identity (singleton, installation_id)
                        VALUES (1, ?)
                        """,
                        (str(installation_id),),
                    )
                else:
                    installation_id = self._parse_stored_id(row[0])

                connection.commit()
                self._set_private_permissions()
                return installation_id
        except InstallationIdentityError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise InstallationIdentityStorageError(
                "The installation identity store could not be read or written; "
                "it was not replaced."
            ) from exc

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS installation_identity (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        installation_id TEXT NOT NULL
                    )
                    """
                )
                connection.commit()
                self._set_private_permissions()
        except InstallationIdentityError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise InstallationIdentityStorageError(
                "The installation identity store could not be initialized."
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self._database_path.parent.chmod(0o700)
        return sqlite3.connect(self._database_path, timeout=10)

    def _set_private_permissions(self) -> None:
        if os.name != "nt":
            self._database_path.chmod(0o600)

    @staticmethod
    def _parse_stored_id(value: object) -> UUID:
        if not isinstance(value, str):
            raise InstallationIdentityCorruptedError(
                "The stored installation ID is invalid; it was not replaced."
            )
        try:
            return UUID(value)
        except ValueError as exc:
            raise InstallationIdentityCorruptedError(
                "The stored installation ID is invalid; it was not replaced."
            ) from exc
