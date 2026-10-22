import json
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

import requests

from xero.api import XeroApiClient, XeroApiError


def build_response(payload, status=200):
    result = Mock()
    result.status_code = status
    result.raise_for_status.return_value = None
    result.json.return_value = payload
    if status >= 400:
        from requests import HTTPError

        result.raise_for_status.side_effect = HTTPError("HTTP failure", response=result)
    return result


class XeroApiClientTests(unittest.TestCase):
    def test_get_organisation_success(self):
        client = XeroApiClient("access-token", "tenant-id")
        payload = {
            "Organisations": [
                {"Name": "Empresa de Demonstração (EUA)", "OrganisationType": "ORGANISATION"}
            ]
        }
        with patch("xero.api.requests.get", return_value=build_response(payload)) as get_call:
            organisation = client.get_organisation()

        self.assertEqual(organisation["Name"], "Empresa de Demonstração (EUA)")
        self.assertEqual(get_call.call_args.kwargs["headers"]["Authorization"], "Bearer access-token")
        self.assertEqual(get_call.call_args.kwargs["headers"]["Xero-tenant-id"], "tenant-id")
        self.assertEqual(get_call.call_args.kwargs["headers"]["Accept"], "application/json")
        self.assertEqual(get_call.call_args.args[0], "https://api.xero.com/api.xro/2.0/Organisation")

    def test_client_secret_is_not_sent_to_accounting_api(self):
        client = XeroApiClient("access-token", "tenant-id")
        payload = {"Organisations": [{"Name": "Demo"}]}
        with patch("xero.api.requests.get", return_value=build_response(payload)) as get_call:
            client.get_organisation()
        authorization = get_call.call_args.kwargs["headers"]["Authorization"]
        self.assertEqual(authorization, "Bearer access-token")
        self.assertNotIn("client-secret", authorization)

    def test_all_accounting_requests_send_the_client_bearer_token(self):
        client = XeroApiClient("expected-access-token", "tenant-id")
        calls = (
            ("get", client.get_organisation, {"Organisations": [{"Name": "Demo"}]}),
            ("get", client.get_contacts, {"Contacts": []}),
            ("get", client.get_invoices, {"Invoices": []}),
            ("post", lambda: client.create_invoice({"Invoices": []}), {"Invoices": []}),
        )

        for method, operation, response_payload in calls:
            with self.subTest(method=method, operation=operation):
                patch_target = f"xero.api.requests.{method}"
                with patch(patch_target, return_value=build_response(response_payload)) as request:
                    operation()

                headers = request.call_args.kwargs["headers"]
                self.assertEqual(headers["Authorization"], "Bearer expected-access-token")
                self.assertEqual(headers["Xero-tenant-id"], "tenant-id")
                self.assertNotIn("client-secret", " ".join(headers.values()))

    def test_timeout_is_handled(self):
        client = XeroApiClient("access-token", "tenant-id")
        with patch("xero.api.requests.get", side_effect=requests.exceptions.Timeout("timed out")):
            with self.assertRaisesRegex(XeroApiError, "timed out|Xero API timed out"):
                client.get_organisation()

    def test_http_401(self):
        client = XeroApiClient("access-token", "tenant-id")
        with patch("xero.api.requests.get", return_value=build_response({}, 401)):
            with self.assertRaisesRegex(XeroApiError, "401"):
                client.get_organisation()

    def test_http_403(self):
        client = XeroApiClient("access-token", "tenant-id")
        with patch("xero.api.requests.get", return_value=build_response({}, 403)):
            with self.assertRaisesRegex(XeroApiError, "403"):
                client.get_organisation()

    def test_http_429(self):
        client = XeroApiClient("access-token", "tenant-id")
        with patch("xero.api.requests.get", return_value=build_response({}, 429)):
            with self.assertRaisesRegex(XeroApiError, "429"):
                client.get_organisation()

    def test_http_500(self):
        client = XeroApiClient("access-token", "tenant-id")
        with patch("xero.api.requests.get", return_value=build_response({}, 500)):
            with self.assertRaisesRegex(XeroApiError, "500"):
                client.get_organisation()

    def test_invalid_json(self):
        client = XeroApiClient("access-token", "tenant-id")
        result = Mock()
        result.status_code = 200
        result.raise_for_status.return_value = None
        result.json.side_effect = ValueError("bad json")
        with patch("xero.api.requests.get", return_value=result):
            with self.assertRaisesRegex(XeroApiError, "JSON inválida"):
                client.get_organisation()

    def test_missing_organisations_key(self):
        client = XeroApiClient("access-token", "tenant-id")
        with patch("xero.api.requests.get", return_value=build_response({"foo": "bar"})):
            with self.assertRaisesRegex(XeroApiError, "Organisations"):
                client.get_organisation()

    def test_get_invoices_success(self):
        client = XeroApiClient("access-token", "tenant-id")
        payload = {"Invoices": [{"InvoiceID": "invoice-1", "Total": 125.50}]}
        with patch("xero.api.requests.get", return_value=build_response(payload)) as get_call:
            invoices = client.get_invoices()

        self.assertEqual(invoices, payload["Invoices"])
        self.assertEqual(
            get_call.call_args.kwargs["headers"]["Authorization"],
            "Bearer access-token",
        )
        self.assertEqual(get_call.call_args.kwargs["headers"]["Xero-tenant-id"], "tenant-id")
        self.assertEqual(get_call.call_args.kwargs["headers"]["Accept"], "application/json")
        self.assertEqual(get_call.call_args.args[0], "https://api.xero.com/api.xro/2.0/Invoices")

    def test_tokens_are_not_exposed_in_errors(self):
        client = XeroApiClient("really-secret-token", "tenant-id")
        with patch("xero.api.requests.get", side_effect=requests.exceptions.ConnectionError("boom")):
            with self.assertRaisesRegex(XeroApiError, "Erro de conexão") as exc:
                client.get_organisation()
        self.assertNotIn("really-secret-token", str(exc.exception))

    def test_empty_access_token_raises(self):
        with self.assertRaises(XeroApiError):
            XeroApiClient("", "tenant-id")

    def test_empty_tenant_id_raises(self):
        with self.assertRaises(XeroApiError):
            XeroApiClient("access-token", "")

    def test_create_invoice_success_posts_payload_and_returns_json(self):
        client = XeroApiClient("access-token", "tenant-id")
        invoice_payload = {
            "Invoices": [
                {
                    "Type": "ACCREC",
                    "Contact": {"ContactID": "contact-id"},
                    "LineItems": [{"UnitAmount": "12.34"}],
                }
            ]
        }
        response_payload = {"Invoices": [{"InvoiceID": "invoice-id"}]}

        with patch(
            "xero.api.requests.post",
            return_value=build_response(response_payload),
        ) as post_call:
            result = client.create_invoice(invoice_payload)

        self.assertEqual(result, response_payload)
        self.assertEqual(post_call.call_args.args[0], "https://api.xero.com/api.xro/2.0/Invoices")
        self.assertEqual(post_call.call_args.kwargs["headers"]["Authorization"], "Bearer access-token")
        self.assertEqual(post_call.call_args.kwargs["headers"]["Xero-tenant-id"], "tenant-id")
        self.assertEqual(post_call.call_args.kwargs["headers"]["Accept"], "application/json")
        self.assertEqual(post_call.call_args.kwargs["headers"]["Content-Type"], "application/json")
        self.assertNotIn("Idempotency-Key", post_call.call_args.kwargs["headers"])
        self.assertEqual(
            json.loads(post_call.call_args.kwargs["data"]),
            invoice_payload,
        )
        self.assertEqual(post_call.call_args.kwargs["timeout"], 15)
        self.assertEqual(
            invoice_payload,
            {
                "Invoices": [
                    {
                        "Type": "ACCREC",
                        "Contact": {"ContactID": "contact-id"},
                        "LineItems": [{"UnitAmount": "12.34"}],
                    }
                ]
            },
        )

    def test_create_invoice_sends_provided_idempotency_key_as_header(self):
        client = XeroApiClient("access-token", "tenant-id")
        idempotency_key = "k" * 128
        invoice_payload = {"Invoices": [{"Type": "ACCREC"}]}

        with patch(
            "xero.api.requests.post",
            return_value=build_response({"Invoices": [{"InvoiceID": "invoice-id"}]}),
        ) as post_call:
            client.create_invoice(invoice_payload, idempotency_key=idempotency_key)

        self.assertEqual(post_call.call_count, 1)
        headers = post_call.call_args.kwargs["headers"]
        self.assertEqual(headers["Idempotency-Key"], idempotency_key)
        self.assertEqual(headers["Authorization"], f"Bearer {client.access_token}")
        self.assertEqual(headers["Xero-tenant-id"], "tenant-id")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Content-Type"], "application/json")
        request_payload = json.loads(post_call.call_args.kwargs["data"])
        self.assertEqual(request_payload, invoice_payload)
        self.assertNotIn(idempotency_key, repr(request_payload))

    def test_create_invoice_serializes_decimal_as_an_exact_json_number(self):
        client = XeroApiClient("access-token", "tenant-id")
        amount = Decimal("12345678901234567890.123456789")
        invoice_payload = {"Invoices": [{"LineItems": [{"UnitAmount": amount}]}]}

        with patch(
            "xero.api.requests.post",
            return_value=build_response({"Invoices": [{"InvoiceID": "invoice-id"}]}),
        ) as post_call:
            client.create_invoice(invoice_payload)

        body = post_call.call_args.kwargs["data"]
        self.assertIn('"UnitAmount":12345678901234567890.123456789', body)
        parsed_payload = json.loads(body, parse_float=Decimal, parse_int=Decimal)
        self.assertEqual(
            parsed_payload["Invoices"][0]["LineItems"][0]["UnitAmount"],
            amount,
        )

    def test_create_invoice_http_errors_are_reported_without_credentials(self):
        client = XeroApiClient("really-secret-token", "tenant-id")
        for status_code in (401, 403, 429, 500):
            with self.subTest(status_code=status_code):
                with patch(
                    "xero.api.requests.post",
                    return_value=build_response({}, status_code),
                ):
                    with self.assertRaises(XeroApiError) as exc:
                        client.create_invoice({"Invoices": []})
                self.assertIn(str(status_code), str(exc.exception))
                self.assertNotIn("really-secret-token", str(exc.exception))

    def test_create_invoice_timeout_is_handled(self):
        client = XeroApiClient("access-token", "tenant-id")
        with patch(
            "xero.api.requests.post",
            side_effect=requests.exceptions.Timeout("timed out"),
        ):
            with self.assertRaisesRegex(XeroApiError, "Xero API timed out"):
                client.create_invoice({"Invoices": []})

    def test_create_invoice_invalid_json_is_handled(self):
        client = XeroApiClient("access-token", "tenant-id")
        result = Mock()
        result.raise_for_status.return_value = None
        result.json.side_effect = ValueError("bad json")
        with patch("xero.api.requests.post", return_value=result):
            with self.assertRaisesRegex(XeroApiError, "JSON inválida"):
                client.create_invoice({"Invoices": []})

    def test_create_invoice_connection_error_does_not_expose_credentials(self):
        access_token = "really-secret-token"
        client_secret = "really-secret-client-secret"
        client = XeroApiClient(access_token, "tenant-id")
        with patch(
            "xero.api.requests.post",
            side_effect=requests.exceptions.ConnectionError(
                f"request failed: {access_token} {client_secret}"
            ),
        ):
            with self.assertRaisesRegex(XeroApiError, "Erro de conexão") as exc:
                client.create_invoice({"Invoices": []})

        self.assertNotIn(access_token, str(exc.exception))
        self.assertNotIn(client_secret, str(exc.exception))


if __name__ == "__main__":
    unittest.main()
