import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from xero.first_run import run_first_xero_invoice
from xero.idempotency import InvoiceIdempotencyStore


class XeroFirstRunTests(unittest.TestCase):
    def setUp(self):
        self.api_client = Mock()
        self.api_client.tenant_id = "tenant-1"
        self.first_record = {
            "client_id": "client-1",
            "client_name": "Cliente 1",
            "xero_invoice_id": "invoice-1",
            "amount": "100.00",
            "date": "2026-09-25",
            "description": "INV-001",
        }
        self.second_record = {
            "client_id": "client-2",
            "client_name": "Cliente 2",
            "xero_invoice_id": "invoice-2",
            "amount": "200.00",
            "date": "2026-09-26",
            "description": "INV-002",
        }
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
            "xero.first_run.InvoiceIdempotencyStore", return_value=self.store
        )
        self.store_patcher.start()

    def tearDown(self):
        self.store_patcher.stop()
        self.temp_dir.cleanup()

    @patch("xero.first_run.read_xero_invoices")
    @patch("xero.first_run.AutomationExecutor")
    def test_without_confirmation_returns_preview_without_creating_executor(
        self, executor_class, read_invoices
    ):
        records = [self.first_record, self.second_record]
        rejections = [self.rejection]
        read_invoices.return_value = {
            "records": records,
            "rejections": rejections,
        }

        result = run_first_xero_invoice(
            self.api_client,
            confirmed=False,
        )

        read_invoices.assert_called_once_with(self.api_client)
        executor_class.assert_not_called()
        self.assertIs(result["records"], records)
        self.assertIs(result["rejections"], rejections)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["invoice_count"], 3)
        self.assertEqual(result["valid_record_count"], 2)
        self.assertEqual(result["rejection_count"], 1)
        self.assertEqual(result["execution_batch_size"], 0)

    @patch("xero.first_run.read_xero_invoices")
    @patch("xero.first_run.AutomationExecutor")
    def test_confirmation_executes_only_the_first_record_without_file_path(
        self, executor_class, read_invoices
    ):
        records = [self.first_record, self.second_record]
        rejections = [self.rejection]
        read_invoices.return_value = {
            "records": records,
            "rejections": rejections,
        }

        executor = executor_class.return_value
        expected_results = [{"status": "SUCCESS", "row": 2}]
        executor.run.return_value = expected_results

        result = run_first_xero_invoice(
            self.api_client,
            confirmed=True,
        )

        executor_class.assert_called_once_with()
        executor.run.assert_called_once_with(
            records=[self.first_record]
        )

        sent_records = executor.run.call_args.kwargs["records"]

        self.assertEqual(len(sent_records), 1)
        self.assertIs(sent_records[0], self.first_record)
        self.assertNotIn(self.second_record, sent_records)
        self.assertIs(result["records"], records)
        self.assertIs(result["rejections"], rejections)
        self.assertIs(result["results"], expected_results)
        self.assertEqual(result["execution_batch_size"], 1)

    @patch("xero.first_run.read_xero_invoices")
    @patch("xero.first_run.AutomationExecutor")
    def test_confirmation_with_no_valid_records_does_not_create_executor(
        self, executor_class, read_invoices
    ):
        rejections = [self.rejection]

        read_invoices.return_value = {
            "records": [],
            "rejections": rejections,
        }

        result = run_first_xero_invoice(
            self.api_client,
            confirmed=True,
        )

        executor_class.assert_not_called()
        self.assertEqual(result["records"], [])
        self.assertIs(result["rejections"], rejections)
        self.assertEqual(result["execution_batch_size"], 0)

    @patch("xero.first_run.read_xero_invoices")
    @patch("xero.first_run.AutomationExecutor")
    def test_preselected_preview_record_executes_without_reading_xero(
        self, executor_class, read_invoices
    ):
        executor = executor_class.return_value
        executor.run.return_value = [{"status": "SUCCESS", "row": 2}]
        hypothetical_new_record = self.second_record

        result = run_first_xero_invoice(
            None,
            confirmed=True,
            preselected_record=self.first_record,
            tenant_id="tenant-1",
        )

        read_invoices.assert_not_called()
        executor.run.assert_called_once_with(records=[self.first_record])
        sent_records = executor.run.call_args.kwargs["records"]
        self.assertEqual(sent_records, [self.first_record])
        self.assertNotIn(hypothetical_new_record, sent_records)
        self.assertIs(result["records"][0], self.first_record)
        self.assertEqual(result["execution_batch_size"], 1)
        self.assertEqual(result["valid_record_count"], 1)

    @patch("xero.first_run.read_xero_invoices")
    @patch("xero.first_run.AutomationExecutor")
    def test_success_prevents_second_execution(self, executor_class, read_invoices):
        executor_class.return_value.run.return_value = [{"row": 2, "status": "SUCCESS"}]

        run_first_xero_invoice(
            None,
            confirmed=True,
            preselected_record=self.first_record,
            tenant_id="tenant-1",
        )
        second_result = run_first_xero_invoice(
            None,
            confirmed=True,
            preselected_record=self.first_record,
            tenant_id="tenant-1",
        )

        self.assertEqual(executor_class.call_count, 1)
        self.assertEqual(second_result["execution_batch_size"], 0)
        read_invoices.assert_not_called()

    @patch("xero.first_run.AutomationExecutor")
    def test_recent_processing_prevents_execution(self, executor_class):
        self.store.reserve_invoice("tenant-1", "invoice-1")

        result = run_first_xero_invoice(
            None,
            confirmed=True,
            preselected_record=self.first_record,
            tenant_id="tenant-1",
        )

        executor_class.assert_not_called()
        self.assertEqual(result["execution_batch_size"], 0)

    @patch("xero.first_run.AutomationExecutor")
    def test_error_and_cancelled_results_allow_retry(self, executor_class):
        executor = executor_class.return_value
        executor.run.side_effect = [
            [{"row": 2, "status": "ERROR"}],
            [{"row": 2, "status": "CANCELLED"}],
            [{"row": 2, "status": "SUCCESS"}],
        ]

        for _ in range(3):
            run_first_xero_invoice(
                None,
                confirmed=True,
                preselected_record=self.first_record,
                tenant_id="tenant-1",
            )

        self.assertEqual(executor.run.call_count, 3)
        self.assertEqual(
            self.store.get_status("tenant-1", "invoice-1"), "SUCCESS"
        )

    @patch("xero.first_run.AutomationExecutor")
    def test_missing_invoice_id_is_rejected_without_execution(self, executor_class):
        record = {
            key: value
            for key, value in self.first_record.items()
            if key != "xero_invoice_id"
        }

        result = run_first_xero_invoice(
            None,
            confirmed=True,
            preselected_record=record,
            tenant_id="tenant-1",
        )

        executor_class.assert_not_called()
        self.assertEqual(result["rejections"][0]["status"], "REJECTED")

    def test_confirmation_must_be_a_boolean(self):
        with self.assertRaises(TypeError):
            run_first_xero_invoice(
                self.api_client,
                confirmed="yes",
            )


if __name__ == "__main__":
    unittest.main()
