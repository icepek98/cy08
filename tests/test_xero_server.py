import http.client
import os
import re
import threading
import unittest
from urllib.parse import urlencode
from unittest.mock import Mock, patch

from demo_system.server import (
    DemoHandler,
    XERO_PENDING_CONFIRMATIONS,
    XERO_PENDING_STATES,
    XERO_STATE_LOCK,
    ThreadingHTTPServer,
)
from xero.oauth import AuthorizationRequest, OAuthError, TokenResponse


class NoRedirectHandler(http.client.HTTPConnection):
    pass


class XeroServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        with XERO_STATE_LOCK:
            XERO_PENDING_STATES.clear()
            XERO_PENDING_CONFIRMATIONS.clear()
        self.environment = patch.dict(
            os.environ,
            {
                "XERO_CLIENT_ID": "test-client",
                "XERO_CLIENT_SECRET": "test-secret",
                "XERO_REDIRECT_URI": self.base_url + "/xero/callback",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        with XERO_STATE_LOCK:
            XERO_PENDING_STATES.clear()
            XERO_PENDING_CONFIRMATIONS.clear()

    def request(self, path, method="GET", body=None):
        connection = NoRedirectHandler("127.0.0.1", self.server.server_port)
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        return response.status, headers, body

    def add_state(self, state="state-1"):
        with XERO_STATE_LOCK:
            from demo_system import server

            XERO_PENDING_STATES[state] = server.time.monotonic() + 600
        return state

    def test_connect_generates_state_and_redirects(self):
        authorization = AuthorizationRequest("https://login.xero.com/authorize?state=generated", "generated")
        with patch("demo_system.server.XeroOAuthClient") as client_type:
            client_type.return_value.create_authorization_request.return_value = authorization
            status, headers, _ = self.request("/xero/connect")
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], authorization.url)
        with XERO_STATE_LOCK:
            self.assertIn("generated", XERO_PENDING_STATES)

    def test_callback_without_state_is_rejected(self):
        status, _, body = self.request("/xero/callback?code=code")
        self.assertEqual(status, 400)
        self.assertIn("Missing authorization state", body)

    def test_callback_with_invalid_state_is_rejected(self):
        status, _, body = self.request("/xero/callback?code=code&state=invalid")
        self.assertEqual(status, 400)
        self.assertIn("invalid or expired", body)

    def test_callback_with_expired_state_is_rejected(self):
        from demo_system import server

        with XERO_STATE_LOCK:
            XERO_PENDING_STATES["expired"] = server.time.monotonic() - 1
        status, _, body = self.request("/xero/callback?code=code&state=expired")
        self.assertEqual(status, 400)
        self.assertIn("invalid or expired", body)

    def test_callback_state_is_single_use(self):
        self.add_state()
        client = Mock()
        client.exchange_code.return_value = TokenResponse("access", "refresh", "Bearer", 3600)
        client.get_connections.return_value = [{"tenantId": "tenant-1", "tenantName": "Demo Company", "tenantType": "ORGANISATION"}]
        api = Mock()
        api.get_organisation.return_value = {"Name": "Demo Company"}
        api.get_contacts.return_value = []
        api.get_invoices.return_value = []
        with patch("demo_system.server.XeroOAuthClient", return_value=client), patch(
            "demo_system.server.XeroApiClient", return_value=api
        ):
            first = self.request("/xero/callback?code=code&state=state-1")
            second = self.request("/xero/callback?code=code&state=state-1")
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 400)
        client.exchange_code.assert_called_once_with("code")

    def test_callback_oauth_error_is_safe(self):
        self.add_state()
        status, _, body = self.request(
            "/xero/callback?state=state-1&error=access_denied&error_description=secret"
        )
        self.assertEqual(status, 400)
        self.assertIn("was not completed", body)
        self.assertNotIn("secret", body)

    def test_callback_without_code_is_rejected(self):
        self.add_state()
        status, _, body = self.request("/xero/callback?state=state-1")
        self.assertEqual(status, 400)
        self.assertIn("Missing authorization code", body)

    def test_valid_callback_shows_only_connection_details(self):
        self.add_state()
        client = Mock()
        client.exchange_code.return_value = TokenResponse("access-token", "refresh-token", "Bearer", 3600)
        client.get_connections.return_value = [{"tenantId": "tenant-1", "tenantName": "Demo Company", "tenantType": "ORGANISATION"}]
        api = Mock()
        api.get_organisation.return_value = {"Name": "Demo Company"}
        api.get_contacts.return_value = []
        api.get_invoices.return_value = []
        with patch("demo_system.server.XeroOAuthClient", return_value=client), patch(
            "demo_system.server.XeroApiClient", return_value=api
        ):
            status, _, body = self.request("/xero/callback?code=code&state=state-1")
        self.assertEqual(status, 200)
        self.assertIn("Demo Company", body)
        self.assertIn("ORGANISATION", body)
        self.assertNotIn("access-token", body)
        self.assertNotIn("refresh-token", body)
        self.assertNotIn("test-secret", body)

    def test_token_exchange_error_is_safe(self):
        self.add_state()
        client = Mock()
        client.exchange_code.side_effect = OAuthError("HTTP body with secret")
        with patch("demo_system.server.XeroOAuthClient", return_value=client):
            status, _, body = self.request("/xero/callback?code=code&state=state-1")
        self.assertEqual(status, 502)
        self.assertNotIn("secret", body)

    def test_invalid_connections_response_is_rejected(self):
        self.add_state()
        client = Mock()
        client.exchange_code.return_value = TokenResponse("access", "refresh", "Bearer", 3600)
        client.get_connections.return_value = []
        with patch("demo_system.server.XeroOAuthClient", return_value=client):
            status, _, body = self.request("/xero/callback?code=code&state=state-1")
        self.assertEqual(status, 502)
        self.assertIn("No Xero organization", body)

    def test_demo_login_route_remains_public(self):
        status, _, body = self.request("/login")
        self.assertEqual(status, 200)
        self.assertIn("Demo Login", body)

    def test_concurrent_callbacks_cannot_reuse_state(self):
        self.add_state()
        client = Mock()
        client.exchange_code.return_value = TokenResponse("access", "refresh", "Bearer", 3600)
        client.get_connections.return_value = [{"tenantId": "tenant-1", "tenantName": "Demo Company", "tenantType": "ORGANISATION"}]
        api_client = Mock()
        api_client.return_value.get_organisation.return_value = {"Name": "Demo Company"}
        api_client.return_value.get_contacts.return_value = []
        api_client.return_value.get_invoices.return_value = []
        with patch("demo_system.server.XeroOAuthClient", return_value=client), patch(
            "demo_system.server.XeroApiClient", api_client
        ):
            api_client.return_value.get_organisation.return_value = {"Name": "Demo Company"}
            results = []

            def callback():
                results.append(self.request("/xero/callback?code=code&state=state-1")[0])

            threads = [threading.Thread(target=callback) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(sorted(results), [200, 400])
        client.exchange_code.assert_called_once_with("code")

    @staticmethod
    def valid_invoice(number="INV-001"):
        return {
            "InvoiceID": f"xero-{number}",
            "Contact": {"ContactID": "client-1", "Name": "Cliente 1"},
            "Total": "100.00",
            "Date": "2026-09-25",
            "Reference": number,
        }

    def authorized_callback(self, api_client):
        api_client.tenant_id = "tenant-1"
        self.add_state()
        oauth_client = Mock()
        oauth_client.exchange_code.return_value = TokenResponse(
            "access-token", "refresh-token", "Bearer", 3600
        )
        oauth_client.get_connections.return_value = [
            {
                "tenantId": "tenant-1",
                "tenantName": "Demo Company",
                "tenantType": "ORGANISATION",
            }
        ]
        with patch("demo_system.server.XeroOAuthClient", return_value=oauth_client), patch(
            "demo_system.server.XeroApiClient", return_value=api_client
        ):
            response = self.request("/xero/callback?code=code&state=state-1")
        return response

    def test_callback_previews_invoices_without_executing_rpa(self):
        api_client = Mock()
        api_client.get_organisation.return_value = {"Name": "Demo Company"}
        api_client.get_contacts.return_value = []
        api_client.get_invoices.return_value = [self.valid_invoice()]

        with patch("demo_system.server.run_first_xero_invoice") as run_first:
            status, _, body = self.authorized_callback(api_client)

        self.assertEqual(status, 200)
        self.assertIn("INV-001", body)
        self.assertIn('action="/xero/confirm"', body)
        self.assertIn('value="confirm"', body)
        self.assertNotIn("access-token", body)
        self.assertNotIn("refresh-token", body)
        run_first.assert_not_called()
        self.assertEqual(len(XERO_PENDING_CONFIRMATIONS), 1)
        [(pending_record, pending_tenant_id, _)] = XERO_PENDING_CONFIRMATIONS.values()
        self.assertEqual(pending_tenant_id, "tenant-1")
        self.assertEqual(
            pending_record,
            {
                "client_id": "client-1",
                "client_name": "Cliente 1",
                "amount": unittest.mock.ANY,
                "date": "2026-09-25",
                "description": "INV-001",
                "xero_invoice_id": "xero-INV-001",
            },
        )

    def test_confirmation_is_not_handled_by_get(self):
        api_client = Mock()
        api_client.get_organisation.return_value = {"Name": "Demo Company"}
        api_client.get_contacts.return_value = []
        api_client.get_invoices.return_value = [self.valid_invoice()]
        _, _, preview = self.authorized_callback(api_client)
        confirmation_id = re.search(
            r'name="confirmation_id" value="([^"]+)"', preview
        ).group(1)

        with patch("demo_system.server.run_first_xero_invoice") as run_first:
            status, headers, _ = self.request(
                f"/xero/confirm?confirmation_id={confirmation_id}&decision=confirm"
            )

        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], "/login")
        self.assertIn(confirmation_id, XERO_PENDING_CONFIRMATIONS)
        run_first.assert_not_called()

    def test_confirmation_executes_preview_record_without_requerying_xero(self):
        api_client = Mock()
        api_client.get_organisation.return_value = {"Name": "Demo Company"}
        api_client.get_contacts.return_value = []
        api_client.get_invoices.side_effect = [
            [self.valid_invoice("INV-A")],
            [self.valid_invoice("INV-B")],
        ]
        _, _, preview = self.authorized_callback(api_client)
        self.assertIn("INV-A", preview)
        self.assertNotIn("INV-B", preview)
        confirmation_id = re.search(
            r'name="confirmation_id" value="([^"]+)"', preview
        ).group(1)
        body = urlencode(
            {"confirmation_id": confirmation_id, "decision": "confirm"}
        )
        expected_result = {
            "execution_batch_size": 1,
            "valid_record_count": 1,
            "rejection_count": 0,
            "results": [{"status": "SUCCESS"}],
        }

        with patch(
            "demo_system.server.run_first_xero_invoice",
            return_value=expected_result,
        ) as run_first:
            status, _, confirmation = self.request(
                "/xero/confirm", method="POST", body=body
            )
            replay_status, _, _ = self.request(
                "/xero/confirm", method="POST", body=body
            )

        self.assertEqual(status, 200)
        self.assertEqual(replay_status, 400)
        run_first.assert_called_once_with(
            None,
            confirmed=True,
            preselected_record={
                "client_id": "client-1",
                "client_name": "Cliente 1",
                "amount": unittest.mock.ANY,
                "date": "2026-09-25",
                "description": "INV-A",
                "xero_invoice_id": "xero-INV-A",
            },
            tenant_id="tenant-1",
        )
        api_client.get_invoices.assert_called_once_with()
        self.assertIn("Invoices processed: 1", confirmation)
        self.assertNotIn("access-token", confirmation)
        self.assertNotIn("refresh-token", confirmation)

    def test_cancelled_confirmation_does_not_execute_rpa(self):
        api_client = Mock()
        api_client.get_organisation.return_value = {"Name": "Demo Company"}
        api_client.get_contacts.return_value = []
        api_client.get_invoices.return_value = [self.valid_invoice()]
        _, _, preview = self.authorized_callback(api_client)
        confirmation_id = re.search(
            r'name="confirmation_id" value="([^"]+)"', preview
        ).group(1)
        body = urlencode(
            {"confirmation_id": confirmation_id, "decision": "cancel"}
        )

        with patch("demo_system.server.run_first_xero_invoice") as run_first:
            status, _, response = self.request(
                "/xero/confirm", method="POST", body=body
            )

        self.assertEqual(status, 200)
        self.assertIn("No invoice was sent", response)
        run_first.assert_not_called()

        api_client.get_invoices.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
