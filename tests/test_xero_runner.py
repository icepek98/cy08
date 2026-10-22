import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from xero.idempotency import InvoiceIdempotencyStore
from xero.runner import run_xero_invoices


class XeroRunnerTests(unittest.TestCase):
    def setUp(self):
        self.api_client = Mock()
        self.api_client.tenant_id = "tenant-1"
        self.executor = Mock()
        self.records = [
            {
                "client_id": "client-1",
                "client_name": "Cliente 1",
                "xero_invoice_id": "invoice-1",
                "amount": "100.00",
                "date": "2026-09-25",
                "description": "INV-001",
            },
            {
                "client_id": "client-2",
                "client_name": "Cliente 2",
                "xero_invoice_id": "invoice-2",
                "amount": "200.00",
                "date": "2026-09-26",
                "description": "INV-002",
            },
        ]
        self.rejection = {
            "source": "xero",
            "index": 2,
            "status": "REJECTED",
            "message": "Invoice Xero sem descrição utilizável.",
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = InvoiceIdempotencyStore(
            Path(self.temp_dir.name) / "state.sqlite3"
        )
        self.store_patcher = patch(
            "xero.runner.InvoiceIdempotencyStore", return_value=self.store
        )
        self.store_patcher.start()
        self.executor.run.return_value = [
            {"row": 2, "status": "SUCCESS"},
            {"row": 3, "status": "SUCCESS"},
        ]

    def tearDown(self):
        self.store_patcher.stop()
        self.temp_dir.cleanup()

    @patch("xero.runner.read_xero_invoices")
    def test_valid_records_are_passed_and_rejection_preserved(self, read_invoices):
        read_invoices.return_value = {
            "records": self.records,
            "rejections": [self.rejection],
        }
        self.executor.run.return_value = [
            {"row": 2, "status": "SUCCESS"},
            {"row": 3, "status": "SUCCESS"},
        ]

        result = run_xero_invoices(self.api_client, self.executor)

        self.executor.run.assert_called_once_with(records=self.records)
        read_invoices.assert_called_once_with(self.api_client)
        self.assertEqual(result["rejections"], [self.rejection])
        self.assertEqual(
            result["results"],
            [
                {"row": 2, "status": "SUCCESS"},
                {"row": 3, "status": "SUCCESS"},
            ],
        )
        self.assertEqual(
            self.store.get_status("tenant-1", "invoice-1"), "SUCCESS"
        )
        self.assertEqual(
            self.store.get_status("tenant-1", "invoice-2"), "SUCCESS"
        )

    @patch("xero.runner.read_xero_invoices")
    def test_executor_is_called_once_when_records_exist(self, read_invoices):
        read_invoices.return_value = {"records": self.records, "rejections": []}

        run_xero_invoices(self.api_client, self.executor)

        self.executor.run.assert_called_once_with(records=self.records)

    @patch("xero.runner.read_xero_invoices")
    def test_executor_is_not_called_when_records_are_empty(self, read_invoices):
        read_invoices.return_value = {
            "records": [],
            "rejections": [self.rejection],
        }

        result = run_xero_invoices(self.api_client, self.executor)

        self.executor.run.assert_not_called()
        self.assertEqual(result, {"results": [], "rejections": [self.rejection]})

    @patch("xero.runner.read_xero_invoices")
    def test_records_are_passed_without_modification(self, read_invoices):
        read_invoices.return_value = {"records": self.records, "rejections": []}

        run_xero_invoices(self.api_client, self.executor)

        passed_records = self.executor.run.call_args.kwargs["records"]
        self.assertEqual(passed_records, self.records)
        self.assertIs(passed_records[0], self.records[0])
        self.assertIs(passed_records[1], self.records[1])

    @patch("xero.runner.read_xero_invoices")
    def test_reader_is_called_once(self, read_invoices):
        read_invoices.return_value = {"records": [], "rejections": []}

        run_xero_invoices(self.api_client, self.executor)

        read_invoices.assert_called_once_with(self.api_client)

    @patch("xero.runner.read_xero_invoices")
    def test_executor_results_are_preserved(self, read_invoices):
        read_invoices.return_value = {"records": self.records, "rejections": []}
        expected_results = [{"row": 2, "status": "ERROR", "message": "falha"}]
        self.executor.run.return_value = expected_results

        result = run_xero_invoices(self.api_client, self.executor)

        self.assertIs(result["results"], expected_results)

    @patch("xero.runner.read_xero_invoices")
    def test_result_contains_no_payload_or_credentials(self, read_invoices):
        read_invoices.return_value = {
            "records": self.records,
            "rejections": [self.rejection],
        }
        self.executor.run.return_value = [{"status": "SUCCESS"}]

        result = run_xero_invoices(self.api_client, self.executor)

        serialized = str(result)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("refresh_token", serialized)
        self.assertNotIn("Client Secret", serialized)
        self.assertNotIn("InvoiceID", serialized)
        self.assertNotIn("Contact", serialized)

    @patch("xero.runner.read_xero_invoices")
    def test_limit_three_sends_first_three_of_86_records(self, read_invoices):
        records = [
            {
                "client_id": f"client-{index}",
                "client_name": f"Cliente {index}",
                "xero_invoice_id": f"invoice-{index}",
                "amount": "100.00",
                "date": "2026-09-25",
                "description": f"INV-{index:03d}",
            }
            for index in range(86)
        ]
        original_records = [record.copy() for record in records]
        read_invoices.return_value = {"records": records, "rejections": []}

        run_xero_invoices(self.api_client, self.executor, max_records=3)

        self.executor.run.assert_called_once()
        sent_records = self.executor.run.call_args.kwargs["records"]
        self.assertEqual(len(sent_records), 3)
        self.assertEqual(sent_records, records[:3])
        self.assertTrue(
            all(sent is original for sent, original in zip(sent_records, records[:3]))
        )
        self.assertEqual(records, original_records)

    @patch("xero.runner.read_xero_invoices")
    def test_limit_above_record_count_sends_both_records_once(self, read_invoices):
        records = self.records.copy()
        read_invoices.return_value = {"records": records, "rejections": []}

        run_xero_invoices(self.api_client, self.executor, max_records=3)

        self.executor.run.assert_called_once()
        sent_records = self.executor.run.call_args.kwargs["records"]
        self.assertEqual(len(sent_records), 2)
        self.assertEqual(sent_records, records)
        self.assertIs(sent_records[0], records[0])
        self.assertIs(sent_records[1], records[1])

    @patch("xero.runner.read_xero_invoices")
    def test_empty_records_do_not_call_executor_with_limit(self, read_invoices):
        rejections = [self.rejection]
        read_invoices.return_value = {"records": [], "rejections": rejections}

        result = run_xero_invoices(self.api_client, self.executor, max_records=3)

        self.executor.run.assert_not_called()
        self.assertEqual(result["results"], [])
        self.assertIs(result["rejections"], rejections)

    @patch("xero.runner.read_xero_invoices")
    def test_invalid_limits_are_rejected_before_reading(self, read_invoices):
        invalid_limits = (0, -1, 1.5, "3", True)

        for limit in invalid_limits:
            with self.subTest(max_records=limit):
                with self.assertRaises(ValueError):
                    run_xero_invoices(
                        self.api_client,
                        self.executor,
                        max_records=limit,
                    )

        read_invoices.assert_not_called()
        self.executor.run.assert_not_called()

    @patch("xero.runner.read_xero_invoices")
    def test_limit_does_not_modify_source_records(self, read_invoices):
        records = [record.copy() for record in self.records]
        original_records = [record.copy() for record in records]
        read_invoices.return_value = {"records": records, "rejections": []}

        run_xero_invoices(self.api_client, self.executor, max_records=1)

        self.assertEqual(records, original_records)
        self.executor.run.assert_called_once_with(records=[records[0]])

    @patch("xero.runner.read_xero_invoices")
    def test_limit_preserves_executor_results_and_rejections(self, read_invoices):
        rejections = [self.rejection]
        expected_results = [{"row": 2, "status": "SUCCESS"}]
        read_invoices.return_value = {
            "records": self.records,
            "rejections": rejections,
        }
        self.executor.run.return_value = expected_results

        result = run_xero_invoices(self.api_client, self.executor, max_records=1)

        self.executor.run.assert_called_once_with(records=[self.records[0]])
        self.assertIs(result["results"], expected_results)
        self.assertIs(result["rejections"], rejections)

    @patch("xero.runner.read_xero_invoices")
    def test_successful_invoice_is_not_executed_again(self, read_invoices):
        read_invoices.return_value = {"records": [self.records[0]], "rejections": []}
        self.executor.run.return_value = [{"row": 2, "status": "SUCCESS"}]

        run_xero_invoices(self.api_client, self.executor)
        run_xero_invoices(self.api_client, self.executor)

        self.executor.run.assert_called_once_with(records=[self.records[0]])

    @patch("xero.runner.read_xero_invoices")
    def test_recent_processing_invoice_is_not_executed(self, read_invoices):
        self.store.reserve_invoice("tenant-1", "invoice-1")
        read_invoices.return_value = {"records": [self.records[0]], "rejections": []}

        result = run_xero_invoices(self.api_client, self.executor)

        self.executor.run.assert_not_called()
        self.assertEqual(result["results"], [])

    @patch("xero.runner.read_xero_invoices")
    def test_error_and_cancelled_results_allow_retry(self, read_invoices):
        read_invoices.return_value = {"records": [self.records[0]], "rejections": []}
        self.executor.run.side_effect = [
            [{"row": 2, "status": "ERROR"}],
            [{"row": 2, "status": "CANCELLED"}],
            [{"row": 2, "status": "SUCCESS"}],
        ]

        for _ in range(3):
            run_xero_invoices(self.api_client, self.executor)

        self.assertEqual(self.executor.run.call_count, 3)
        self.assertEqual(
            self.store.get_status("tenant-1", "invoice-1"), "SUCCESS"
        )

    @patch("xero.runner.read_xero_invoices")
    def test_duplicate_invoice_id_in_batch_executes_once(self, read_invoices):
        duplicate = {**self.records[0], "client_id": "duplicate"}
        read_invoices.return_value = {
            "records": [self.records[0], duplicate],
            "rejections": [],
        }

        run_xero_invoices(self.api_client, self.executor)

        self.executor.run.assert_called_once_with(records=[self.records[0]])

    @patch("xero.runner.read_xero_invoices")
    def test_same_invoice_id_is_independent_across_tenants(self, read_invoices):
        record = {**self.records[0], "xero_invoice_id": "shared-id"}
        read_invoices.return_value = {"records": [record], "rejections": []}

        run_xero_invoices(self.api_client, self.executor)
        self.api_client.tenant_id = "tenant-2"
        run_xero_invoices(self.api_client, self.executor)

        self.assertEqual(self.executor.run.call_count, 2)

    @patch("xero.runner.read_xero_invoices")
    def test_record_without_invoice_id_is_rejected_not_executed(self, read_invoices):
        record = {
            key: value
            for key, value in self.records[0].items()
            if key != "xero_invoice_id"
        }
        read_invoices.return_value = {"records": [record], "rejections": []}

        result = run_xero_invoices(self.api_client, self.executor)

        self.executor.run.assert_not_called()
        self.assertEqual(result["rejections"][0]["status"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
