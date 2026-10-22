import sqlite3
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from xero.idempotency import InvoiceIdempotencyStore


class XeroIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "state.sqlite3"
        self.store = InvoiceIdempotencyStore(
            self.database_path, processing_ttl=timedelta(hours=24)
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_first_reservation_creates_database_and_table(self):
        self.assertTrue(self.database_path.is_file())
        reservation = self.store.reserve_invoice("tenant-a", "invoice-1")

        self.assertTrue(reservation.reserved)
        self.assertFalse(reservation.already_success)
        self.assertFalse(reservation.already_processing)
        self.assertTrue(reservation.attempt_token)
        self.assertEqual(self.store.get_status("tenant-a", "invoice-1"), "PROCESSING")

        with sqlite3.connect(self.database_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(xero_invoice_state)")
            }
        self.assertEqual(
            columns,
            {"tenant_id", "xero_invoice_id", "status", "attempt_token", "updated_at"},
        )

    def test_success_prevents_another_reservation(self):
        reservation = self.store.reserve_invoice("tenant-a", "invoice-1")
        self.assertTrue(
            self.store.mark_success("tenant-a", "invoice-1", reservation.attempt_token)
        )

        repeated = self.store.reserve_invoice("tenant-a", "invoice-1")
        self.assertTrue(repeated.already_success)
        self.assertFalse(repeated.reserved)
        self.assertIsNone(repeated.attempt_token)

    def test_recent_processing_prevents_another_reservation(self):
        first = self.store.reserve_invoice("tenant-a", "invoice-1")
        second = self.store.reserve_invoice("tenant-a", "invoice-1")

        self.assertTrue(second.already_processing)
        self.assertFalse(second.reserved)
        self.assertNotEqual(second.attempt_token, first.attempt_token)

    def test_expired_processing_can_be_recovered_with_a_new_token(self):
        first = self.store.reserve_invoice("tenant-a", "invoice-1")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE xero_invoice_state
                SET updated_at = ?
                WHERE tenant_id = ? AND xero_invoice_id = ?
                """,
                ("2000-01-01T00:00:00+00:00", "tenant-a", "invoice-1"),
            )

        recovered = self.store.reserve_invoice("tenant-a", "invoice-1")

        self.assertTrue(recovered.reserved)
        self.assertNotEqual(recovered.attempt_token, first.attempt_token)

    def test_old_attempt_cannot_mark_recovered_reservation_successful(self):
        first = self.store.reserve_invoice("tenant-a", "invoice-1")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE xero_invoice_state
                SET updated_at = ?
                WHERE tenant_id = ? AND xero_invoice_id = ?
                """,
                ("2000-01-01T00:00:00+00:00", "tenant-a", "invoice-1"),
            )
        recovered = self.store.reserve_invoice("tenant-a", "invoice-1")

        self.assertFalse(
            self.store.mark_success("tenant-a", "invoice-1", first.attempt_token)
        )
        self.assertTrue(
            self.store.mark_success("tenant-a", "invoice-1", recovered.attempt_token)
        )

    def test_current_attempt_can_mark_success(self):
        reservation = self.store.reserve_invoice("tenant-a", "invoice-1")

        self.assertTrue(
            self.store.mark_success("tenant-a", "invoice-1", reservation.attempt_token)
        )
        self.assertEqual(self.store.get_status("tenant-a", "invoice-1"), "SUCCESS")

    def test_releasing_failed_or_cancelled_attempt_allows_retry(self):
        failed_attempt = self.store.reserve_invoice("tenant-a", "invoice-1")
        self.assertTrue(
            self.store.release_invoice(
                "tenant-a", "invoice-1", failed_attempt.attempt_token
            )
        )
        retry = self.store.reserve_invoice("tenant-a", "invoice-1")

        self.assertTrue(retry.reserved)
        self.assertEqual(self.store.get_status("tenant-a", "invoice-1"), "PROCESSING")

    def test_same_invoice_id_in_different_tenants_is_independent(self):
        tenant_a = self.store.reserve_invoice("tenant-a", "invoice-1")
        tenant_b = self.store.reserve_invoice("tenant-b", "invoice-1")

        self.assertTrue(tenant_a.reserved)
        self.assertTrue(tenant_b.reserved)

    def test_concurrent_reservations_have_only_one_winner(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def reserve():
            try:
                barrier.wait()
                results.append(self.store.reserve_invoice("tenant-a", "invoice-1"))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(result.reserved for result in results), 1)
        self.assertEqual(sum(result.already_processing for result in results), 1)


if __name__ == "__main__":
    unittest.main()
