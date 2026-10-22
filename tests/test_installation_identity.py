import os
import sqlite3
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

from installation_identity import (
    InstallationIdentityCorruptedError,
    InstallationIdentityStore,
    InstallationIdentityStorageError,
)


class InstallationIdentityStoreTests(unittest.TestCase):
    def test_generates_one_uuid_and_persists_it_across_store_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "identity.sqlite3"

            first_id = InstallationIdentityStore(database_path).get_installation_id()
            second_id = InstallationIdentityStore(database_path).get_installation_id()

        self.assertIsInstance(first_id, UUID)
        self.assertEqual(first_id, second_id)

    def test_concurrent_first_access_creates_one_persistent_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InstallationIdentityStore(Path(directory) / "identity.sqlite3")

            with ThreadPoolExecutor(max_workers=12) as executor:
                identities = list(
                    executor.map(
                        lambda _: store.get_installation_id(),
                        range(36),
                    )
                )

        self.assertEqual(len(set(identities)), 1)

    def test_invalid_stored_id_is_rejected_without_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "identity.sqlite3"
            store = InstallationIdentityStore(database_path)
            store.get_installation_id()

            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "UPDATE installation_identity SET installation_id = 'invalid'"
                )

            with self.assertRaises(InstallationIdentityCorruptedError):
                store.get_installation_id()

    def test_corrupt_database_is_rejected_instead_of_creating_a_new_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "identity.sqlite3"
            database_path.write_bytes(b"not a sqlite database")

            with self.assertRaises(InstallationIdentityStorageError):
                InstallationIdentityStore(database_path)

            self.assertEqual(database_path.read_bytes(), b"not a sqlite database")

    @unittest.skipIf(os.name == "nt", "POSIX permissions are not portable")
    def test_identity_storage_is_private_to_the_user(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "private" / "identity.sqlite3"
            InstallationIdentityStore(database_path).get_installation_id()

            self.assertEqual(stat.S_IMODE(database_path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(database_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
