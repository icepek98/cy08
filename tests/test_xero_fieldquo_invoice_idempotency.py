import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from xero.fieldquo_invoice_idempotency import (
    FieldQuoInvoiceIdempotencyStore,
    OperationState,
)


class FieldQuoInvoiceIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "fieldquo_xero_state.sqlite3"
        self.store = FieldQuoInvoiceIdempotencyStore(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _expire_claim(self, tenant_id, source_identity):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE fieldquo_xero_invoice_operations
                SET claim_expires_at = ?
                WHERE tenant_id = ? AND source_identity = ?
                """,
                ("2000-01-01T00:00:00+00:00", tenant_id, source_identity),
            )

    def test_first_reservation_creates_operation_with_required_fields(self):
        operation = self.store.reserve(
            "tenant-a", "source-a", payload_fingerprint="fingerprint-a"
        )

        self.assertEqual(operation.tenant_id, "tenant-a")
        self.assertEqual(operation.source_identity, "source-a")
        self.assertTrue(operation.operation_id)
        self.assertTrue(operation.idempotency_key)
        self.assertEqual(operation.payload_fingerprint, "fingerprint-a")
        self.assertEqual(operation.state, OperationState.PROCESSING)
        self.assertIsNone(operation.xero_invoice_id)
        self.assertTrue(operation.created_at)
        self.assertTrue(operation.updated_at)
        self.assertTrue(operation.has_claim)
        self.assertTrue(operation.claim_expires_at)
        self.assertLessEqual(len(operation.idempotency_key), 128)

        with sqlite3.connect(self.database_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM fieldquo_xero_invoice_operations"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_same_identity_returns_existing_operation(self):
        first = self.store.reserve("tenant-a", "source-a", "fingerprint-a")
        repeated = self.store.reserve("tenant-a", "source-a", "different-fingerprint")

        self.assertEqual(repeated, first)
        self.assertFalse(repeated.has_claim)

    def test_success_preserves_xero_invoice_id_and_blocks_new_operation(self):
        operation = self.store.reserve("tenant-a", "source-a")

        self.assertTrue(
            self.store.mark_success(
                "tenant-a",
                "source-a",
                operation.operation_id,
                "xero-invoice-1",
                operation.claim_token,
            )
        )
        completed = self.store.get("tenant-a", "source-a")
        repeated = self.store.reserve("tenant-a", "source-a")

        self.assertEqual(completed.state, OperationState.SUCCESS)
        self.assertEqual(completed.xero_invoice_id, "xero-invoice-1")
        self.assertEqual(repeated, completed)
        self.assertFalse(repeated.has_claim)

    def test_outcome_unknown_preserves_same_idempotency_key(self):
        operation = self.store.reserve("tenant-a", "source-a")

        self.assertTrue(
            self.store.mark_outcome_unknown(
                "tenant-a", "source-a", operation.operation_id, operation.claim_token
            )
        )
        blocked = self.store.reserve("tenant-a", "source-a")
        self.assertFalse(blocked.has_claim)
        self.assertEqual(blocked.operation_id, operation.operation_id)
        self.assertEqual(blocked.idempotency_key, operation.idempotency_key)
        self.assertEqual(blocked.state, OperationState.OUTCOME_UNKNOWN)

        self._expire_claim("tenant-a", "source-a")
        recovered = self.store.reserve("tenant-a", "source-a")

        self.assertEqual(recovered.state, OperationState.OUTCOME_UNKNOWN)
        self.assertEqual(recovered.operation_id, operation.operation_id)
        self.assertEqual(recovered.idempotency_key, operation.idempotency_key)
        self.assertTrue(recovered.has_claim)

    def test_outcome_unknown_active_claim_blocks_concurrent_reservation(self):
        operation = self.store.reserve("tenant-a", "source-a")
        self.assertTrue(
            self.store.mark_outcome_unknown(
                "tenant-a", "source-a", operation.operation_id, operation.claim_token
            )
        )

        barrier = threading.Barrier(2)
        results = []

        def reserve():
            barrier.wait()
            results.append(self.store.reserve("tenant-a", "source-a"))

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(len(results), 2)
        self.assertTrue(all(not result.has_claim for result in results))
        self.assertEqual(
            {result.operation_id for result in results}, {operation.operation_id}
        )
        self.assertEqual(
            {result.idempotency_key for result in results},
            {operation.idempotency_key},
        )

    def test_restart_preserves_operation_fields(self):
        operation = self.store.reserve(
            "tenant-a", "source-a", payload_fingerprint="fingerprint-a"
        )
        self.assertTrue(
            self.store.mark_success(
                "tenant-a",
                "source-a",
                operation.operation_id,
                "xero-invoice-1",
                operation.claim_token,
            )
        )

        reopened_store = FieldQuoInvoiceIdempotencyStore(self.database_path)
        recovered = reopened_store.get("tenant-a", "source-a")

        self.assertEqual(recovered.operation_id, operation.operation_id)
        self.assertEqual(recovered.idempotency_key, operation.idempotency_key)
        self.assertEqual(recovered.state, OperationState.SUCCESS)
        self.assertEqual(recovered.xero_invoice_id, "xero-invoice-1")
        self.assertEqual(recovered.payload_fingerprint, "fingerprint-a")
        self.assertEqual(recovered.created_at, operation.created_at)

    def test_restart_preserves_active_claim_lease_and_operation(self):
        operation = self.store.reserve(
            "tenant-a", "source-a", payload_fingerprint="fingerprint-a"
        )
        reopened_store = FieldQuoInvoiceIdempotencyStore(self.database_path)

        self.assertEqual(reopened_store.get("tenant-a", "source-a"), operation)
        blocked = reopened_store.reserve("tenant-a", "source-a")

        self.assertFalse(blocked.has_claim)
        self.assertEqual(blocked.operation_id, operation.operation_id)
        self.assertEqual(blocked.idempotency_key, operation.idempotency_key)
        self.assertEqual(blocked.state, OperationState.PROCESSING)
        self.assertEqual(blocked.claim_expires_at, operation.claim_expires_at)
        self.assertEqual(blocked.payload_fingerprint, "fingerprint-a")

    def test_active_lease_blocks_second_caller_from_claiming(self):
        first = self.store.reserve("tenant-a", "source-a")
        second = self.store.reserve("tenant-a", "source-a")

        self.assertTrue(first.has_claim)
        self.assertFalse(second.has_claim)
        self.assertEqual(second.operation_id, first.operation_id)
        self.assertEqual(second.idempotency_key, first.idempotency_key)

    def test_claim_validation_requires_current_unexpired_token(self):
        operation = self.store.reserve("tenant-a", "source-a")

        self.assertTrue(
            self.store.validate_claim(
                "tenant-a",
                "source-a",
                operation.operation_id,
                operation.claim_token,
            )
        )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE fieldquo_xero_invoice_operations
                SET claim_expires_at = ?
                WHERE tenant_id = ? AND source_identity = ?
                """,
                ("2000-01-01T00:00:00+00:00", "tenant-a", "source-a"),
            )
        self.assertFalse(
            self.store.validate_claim(
                "tenant-a",
                "source-a",
                operation.operation_id,
                operation.claim_token,
            )
        )

    def test_stale_claim_cannot_finalize_after_recovery(self):
        original = self.store.reserve(
            "tenant-a", "source-a", payload_fingerprint="fingerprint-a"
        )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE fieldquo_xero_invoice_operations
                SET claim_expires_at = ?
                WHERE tenant_id = ? AND source_identity = ?
                """,
                ("2000-01-01T00:00:00+00:00", "tenant-a", "source-a"),
            )

        recovered = self.store.reserve("tenant-a", "source-a")

        self.assertEqual(recovered.operation_id, original.operation_id)
        self.assertEqual(recovered.idempotency_key, original.idempotency_key)
        self.assertNotEqual(recovered.claim_token, original.claim_token)
        self.assertFalse(
            self.store.mark_success(
                "tenant-a",
                "source-a",
                original.operation_id,
                "stale-invoice-id",
                original.claim_token,
            )
        )
        self.assertFalse(
            self.store.mark_outcome_unknown(
                "tenant-a",
                "source-a",
                original.operation_id,
                original.claim_token,
            )
        )
        self.assertEqual(
            self.store.get("tenant-a", "source-a").state,
            OperationState.PROCESSING,
        )
        self.assertTrue(
            self.store.mark_success(
                "tenant-a",
                "source-a",
                recovered.operation_id,
                "current-invoice-id",
                recovered.claim_token,
            )
        )

    def test_expired_lease_recovers_same_operation_and_key(self):
        first = self.store.reserve(
            "tenant-a", "source-a", payload_fingerprint="fingerprint-a"
        )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE fieldquo_xero_invoice_operations
                SET claim_expires_at = ?
                WHERE tenant_id = ? AND source_identity = ?
                """,
                ("2000-01-01T00:00:00+00:00", "tenant-a", "source-a"),
            )

        recovered = self.store.reserve("tenant-a", "source-a")

        self.assertTrue(recovered.has_claim)
        self.assertNotEqual(recovered.claim_token, first.claim_token)
        self.assertEqual(recovered.operation_id, first.operation_id)
        self.assertEqual(recovered.idempotency_key, first.idempotency_key)
        self.assertEqual(recovered.source_identity, first.source_identity)
        self.assertEqual(recovered.tenant_id, first.tenant_id)
        self.assertEqual(recovered.payload_fingerprint, "fingerprint-a")

    def test_success_cannot_be_recovered_for_another_post(self):
        operation = self.store.reserve("tenant-a", "source-a")
        self.assertTrue(
            self.store.mark_success(
                "tenant-a",
                "source-a",
                operation.operation_id,
                "xero-invoice-1",
                operation.claim_token,
            )
        )

        repeated = self.store.reserve("tenant-a", "source-a")

        self.assertFalse(repeated.has_claim)
        self.assertEqual(repeated.operation_id, operation.operation_id)
        self.assertEqual(repeated.idempotency_key, operation.idempotency_key)
        self.assertEqual(repeated.xero_invoice_id, "xero-invoice-1")

    def test_outcome_unknown_can_transition_to_success(self):
        operation = self.store.reserve("tenant-a", "source-a")
        self.assertTrue(
            self.store.mark_outcome_unknown(
                "tenant-a", "source-a", operation.operation_id, operation.claim_token
            )
        )
        self._expire_claim("tenant-a", "source-a")
        recovered = self.store.reserve("tenant-a", "source-a")

        self.assertTrue(
            self.store.mark_success(
                "tenant-a",
                "source-a",
                recovered.operation_id,
                "xero-invoice-1",
                recovered.claim_token,
            )
        )
        completed = self.store.get("tenant-a", "source-a")
        self.assertEqual(completed.state, OperationState.SUCCESS)
        self.assertEqual(completed.idempotency_key, operation.idempotency_key)
        self.assertEqual(completed.xero_invoice_id, "xero-invoice-1")

    def test_same_source_identity_is_isolated_between_tenants(self):
        tenant_a = self.store.reserve("tenant-a", "source-a")
        tenant_b = self.store.reserve("tenant-b", "source-a")

        self.assertNotEqual(tenant_a.operation_id, tenant_b.operation_id)
        self.assertEqual(self.store.get("tenant-a", "source-a"), tenant_a)
        self.assertEqual(self.store.get("tenant-b", "source-a"), tenant_b)

    def test_different_source_identities_create_different_operations(self):
        first = self.store.reserve("tenant-a", "source-a")
        second = self.store.reserve("tenant-a", "source-b")

        self.assertNotEqual(first.operation_id, second.operation_id)
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)

    def test_concurrent_reservations_persist_only_one_operation(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def reserve():
            try:
                barrier.wait()
                results.append(self.store.reserve("tenant-a", "source-a"))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(sum(result.has_claim for result in results), 1)
        with sqlite3.connect(self.database_path) as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM fieldquo_xero_invoice_operations
                WHERE tenant_id = ? AND source_identity = ?
                """,
                ("tenant-a", "source-a"),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_idempotency_key_is_within_xero_limit(self):
        operation = self.store.reserve("tenant-a", "source-a")

        self.assertLessEqual(len(operation.idempotency_key), 128)

    def test_marking_outcome_unknown_does_not_allow_success_to_be_reversed(self):
        operation = self.store.reserve("tenant-a", "source-a")
        self.assertTrue(
            self.store.mark_success(
                "tenant-a",
                "source-a",
                operation.operation_id,
                "xero-invoice-1",
                operation.claim_token,
            )
        )

        self.assertFalse(
            self.store.mark_outcome_unknown(
                "tenant-a", "source-a", operation.operation_id, operation.claim_token
            )
        )
        self.assertEqual(
            self.store.get("tenant-a", "source-a").state, OperationState.SUCCESS
        )


if __name__ == "__main__":
    unittest.main()
