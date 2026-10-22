import html
import secrets
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .input_contract import REQUIRED_COLUMNS, validate_transaction_row
from xero.api import XeroApiClient, XeroApiError
from xero.config import XeroConfig, XeroConfigurationError
from xero.first_run import run_first_xero_invoice
from xero.invoices_adapter import XeroInvoiceAdapterError, adapt_xero_invoice
from xero.oauth import OAuthError, XeroOAuthClient, validate_state


HOST = "127.0.0.1"
PORT = 8765
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password"
SESSIONS: set[str] = set()
TRANSACTIONS: list[dict[str, str]] = []
XERO_STATE_TTL_SECONDS = 600
XERO_PENDING_STATES: dict[str, float] = {}
XERO_PENDING_CONFIRMATIONS: dict[str, tuple[dict[str, object], str, float]] = {}
XERO_STATE_LOCK = threading.Lock()


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:Arial;max-width:800px;margin:40px auto}}label{{display:block;margin-top:12px}}input{{width:100%;padding:8px}}button{{margin-top:16px;padding:10px 16px}}</style>
</head><body>{body}</body></html>""".encode()


class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def send_page(self, title: str, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = page(title, body)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def session_is_valid(self) -> bool:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        session = cookie.get("session")
        return session is not None and session.value in SESSIONS

    def redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def xero_page(self, title: str, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_page(title, body, status)

    def register_xero_state(self, state: str) -> None:
        with XERO_STATE_LOCK:
            self.remove_expired_xero_states()
            self.remove_expired_xero_confirmations()
            XERO_PENDING_STATES[state] = time.monotonic() + XERO_STATE_TTL_SECONDS

    @staticmethod
    def remove_expired_xero_states() -> None:
        now = time.monotonic()
        expired = [
            state for state, expires_at in XERO_PENDING_STATES.items() if expires_at <= now
        ]
        for state in expired:
            del XERO_PENDING_STATES[state]

    @staticmethod
    def remove_expired_xero_confirmations() -> None:
        now = time.monotonic()
        expired = [
            confirmation_id
            for confirmation_id, (_, _, expires_at) in XERO_PENDING_CONFIRMATIONS.items()
            if expires_at <= now
        ]
        for confirmation_id in expired:
            del XERO_PENDING_CONFIRMATIONS[confirmation_id]

    @staticmethod
    def register_xero_confirmation(record: dict[str, object], tenant_id: str) -> str:
        confirmation_id = secrets.token_urlsafe(32)
        with XERO_STATE_LOCK:
            DemoHandler.remove_expired_xero_confirmations()
            XERO_PENDING_CONFIRMATIONS[confirmation_id] = (
                record,
                tenant_id,
                time.monotonic() + XERO_STATE_TTL_SECONDS,
            )
        return confirmation_id

    @staticmethod
    def consume_xero_confirmation(
        confirmation_id: str,
    ) -> tuple[dict[str, object], str] | None:
        with XERO_STATE_LOCK:
            DemoHandler.remove_expired_xero_confirmations()
            pending = XERO_PENDING_CONFIRMATIONS.pop(confirmation_id, None)
        return (pending[0], pending[1]) if pending is not None else None

    @staticmethod
    def consume_xero_state(received: str) -> bool:
        with XERO_STATE_LOCK:
            DemoHandler.remove_expired_xero_states()
            expires_at = XERO_PENDING_STATES.pop(received, None)
            if expires_at is None:
                return False
            try:
                validate_state(received, received)
            except OAuthError:
                return False
            return True

    def handle_xero_connect(self) -> None:
        try:
            config = XeroConfig.from_env()
            request = XeroOAuthClient(config).create_authorization_request()
            self.register_xero_state(request.state)
        except (XeroConfigurationError, OAuthError):
            self.xero_page(
                "Xero Connection",
                "<h1>Unable to start Xero authorization</h1>"
                "<p>Configure the Xero OAuth settings and try again.</p>",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self.redirect(request.url)

    def handle_xero_callback(self, query: dict[str, list[str]]) -> None:
        received_state = query.get("state", [""])[0]
        if not received_state:
            self.xero_page(
                "Xero Connection",
                "<h1>Invalid Xero callback</h1><p>Missing authorization state.</p>",
                HTTPStatus.BAD_REQUEST,
            )
            return
        if not self.consume_xero_state(received_state):
            self.xero_page(
                "Xero Connection",
                "<h1>Invalid Xero callback</h1><p>The authorization state is invalid or expired.</p>",
                HTTPStatus.BAD_REQUEST,
            )
            return

        if query.get("error"):
            self.xero_page(
                "Xero Connection",
                "<h1>Xero authorization was not completed</h1>"
                "<p>The authorization request was declined or could not be completed.</p>",
                HTTPStatus.BAD_REQUEST,
            )
            return

        code = query.get("code", [""])[0]
        if not code:
            self.xero_page(
                "Xero Connection",
                "<h1>Invalid Xero callback</h1><p>Missing authorization code.</p>",
                HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            config = XeroConfig.from_env()
            client = XeroOAuthClient(config)
            token = client.exchange_code(code)
            connections = client.get_connections(token.access_token)
        except (XeroConfigurationError, OAuthError):
            self.xero_page(
                "Xero Connection",
                "<h1>Xero connection failed</h1>"
                "<p>The authorization could not be completed. Try again.</p>",
                HTTPStatus.BAD_GATEWAY,
            )
            return

        if not connections:
            self.xero_page(
                "Xero Connection",
                "<h1>No Xero organization found</h1>"
                "<p>No organization was returned for this authorization.</p>",
                HTTPStatus.BAD_GATEWAY,
            )
            return

        connection = connections[0]
        tenant_id = connection.get("tenantId")
        tenant_name = str(connection.get("tenantName") or "Organization")
        tenant_type = str(connection.get("tenantType") or "Unknown")

        if not tenant_id:
            self.xero_page(
                "Xero Connection",
                "<h1>Xero connection authorized</h1>"
                f"<p>Organization: {html.escape(tenant_name)}</p>"
                f"<p>Type: {html.escape(tenant_type)}</p>",
            )
            return

        api_client = XeroApiClient(token.access_token, str(tenant_id))
        try:
            organisation = api_client.get_organisation()
        except XeroApiError:
            self.xero_page(
                "Xero Connection",
                "<h1>Xero connection authorized</h1>"
                f"<p>Organization: {html.escape(tenant_name)}</p>"
                f"<p>Type: {html.escape(tenant_type)}</p>"
                "<p>API Accounting: unavailable</p>",
            )
            return

        try:
            contacts = api_client.get_contacts()
        except XeroApiError as exc:
            organisation_name = html.escape(str(organisation.get("Name") or tenant_name))
            self.xero_page(
                "Xero Connection",
                "<h1>Xero connection authorized</h1>"
                f"<p>Organization: {html.escape(tenant_name)}</p>"
                f"<p>Type: {html.escape(tenant_type)}</p>"
                "<p>API Accounting: OK</p>"
                f"<p>Organização retornada pela API: {organisation_name}</p>"
                "<h2>Contatos Xero</h2>"
                f"<p>Leitura de contatos falhou: {html.escape(type(exc).__name__)}.</p>",
            )
            return

        organisation_name = html.escape(str(organisation.get("Name") or tenant_name))
        contact_names = []
        for contact in contacts[:3]:
            if isinstance(contact, dict):
                name = (
                    contact.get("Name")
                    or contact.get("FirstName")
                    or contact.get("LastName")
                    or contact.get("ContactID")
                )
                if name is not None:
                    contact_names.append(str(name))

        contact_items = "".join(f"<li>{html.escape(name)}</li>" for name in contact_names)
        if not contact_items:
            contact_items = "<li>Contatos retornados: 0</li>"

        try:
            invoices = api_client.get_invoices()
        except XeroApiError as exc:
            self.xero_page(
                "Xero Connection",
                "<h1>Xero connection authorized</h1>"
                f"<p>Organization: {html.escape(tenant_name)}</p>"
                f"<p>Type: {html.escape(tenant_type)}</p>"
                "<p>API Accounting: OK</p>"
                f"<p>Organização retornada pela API: {organisation_name}</p>"
                "<h2>Contatos Xero</h2>"
                f"<p>Quantidade total de contatos retornados: {len(contacts)}</p>"
                f"<ul>{contact_items}</ul>"
                "<h2>Invoices Xero</h2>"
                f"<p>Leitura de invoices falhou: {html.escape(type(exc).__name__)}.</p>",
            )
            return

        converted_invoices = []
        rejected_invoice_messages = []
        for invoice in invoices:
            try:
                converted_invoices.append(adapt_xero_invoice(invoice))
            except XeroInvoiceAdapterError as exc:
                rejected_invoice_messages.append(str(exc))

        invoice_items = []
        for transaction in converted_invoices[:3]:
            invoice_items.append(
                "<li>"
                f"Cliente: {html.escape(str(transaction['client_name']))}; "
                f"Valor: {html.escape(str(transaction['amount']))}; "
                f"Data: {html.escape(str(transaction['date']))}; "
                f"Descrição: {html.escape(str(transaction['description']))}"
                "</li>"
            )
        if not invoice_items:
            invoice_items.append("<li>Nenhuma invoice convertida.</li>")

        rejection_items = "".join(
            f"<li>{html.escape(message)}</li>" for message in rejected_invoice_messages
        )
        rejection_section = (
            "<h3>Invoices rejeitadas</h3>"
            f"<ul>{rejection_items}</ul>"
            if rejection_items
            else ""
        )
        confirmation_section = ""
        if converted_invoices:
            confirmation_id = self.register_xero_confirmation(
                converted_invoices[0],
                str(api_client.tenant_id),
            )
            confirmation_section = (
                "<h2>Primeira execução</h2>"
                "<p>Ao confirmar, somente a primeira invoice válida será processada.</p>"
                '<form method="post" action="/xero/confirm">'
                f'<input type="hidden" name="confirmation_id" value="{html.escape(confirmation_id)}">'
                '<button type="submit" name="decision" value="confirm">'
                "Confirmar e executar uma invoice</button>"
                '<button type="submit" name="decision" value="cancel">'
                "Cancelar</button></form>"
            )
        else:
            confirmation_section = (
                "<p>Não há invoices válidas para executar.</p>"
            )
        self.xero_page(
            "Xero Connection",
            "<h1>Xero connection authorized</h1>"
            f"<p>Organization: {html.escape(tenant_name)}</p>"
            f"<p>Type: {html.escape(tenant_type)}</p>"
            "<p>API Accounting: OK</p>"
            f"<p>Organização retornada pela API: {organisation_name}</p>"
            "<h2>Contatos Xero</h2>"
            f"<p>Quantidade total de contatos retornados: {len(contacts)}</p>"
            f"<ul>{contact_items}</ul>"
            "<h2>Invoices Xero</h2>"
            f"<p>Quantidade total de invoices retornadas: {len(invoices)}</p>"
            f"<p>Quantidade de invoices convertidas: {len(converted_invoices)}</p>"
            f"<p>Quantidade de invoices rejeitadas: {len(rejected_invoice_messages)}</p>"
            f"<ul>{''.join(invoice_items)}</ul>"
            f"{rejection_section}"
            f"{confirmation_section}",
        )

    def handle_xero_confirmation(self, values: dict[str, list[str]]) -> None:
        confirmation_id = values.get("confirmation_id", [""])[0]
        decision = values.get("decision", [""])[0]
        if not confirmation_id or decision not in {"confirm", "cancel"}:
            self.xero_page(
                "Xero Connection",
                "<h1>Invalid confirmation</h1>"
                "<p>The confirmation request is invalid or expired.</p>",
                HTTPStatus.BAD_REQUEST,
            )
            return

        confirmation = self.consume_xero_confirmation(confirmation_id)
        if confirmation is None:
            self.xero_page(
                "Xero Connection",
                "<h1>Confirmation expired</h1>"
                "<p>Reconnect to Xero and review the invoices again.</p>",
                HTTPStatus.BAD_REQUEST,
            )
            return

        preview_record, tenant_id = confirmation

        if decision == "cancel":
            self.xero_page(
                "Xero Connection",
                "<h1>Execution cancelled</h1>"
                "<p>No invoice was sent to the automation executor.</p>",
            )
            return

        try:
            result = run_first_xero_invoice(
                None,
                confirmed=True,
                preselected_record=preview_record,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            print(f"Xero first-invoice execution failed: {type(exc).__name__}", flush=True)
            self.xero_page(
                "Xero Connection",
                "<h1>Invoice execution failed</h1>"
                "<p>The first invoice could not be processed. Review the local server log.</p>",
                HTTPStatus.BAD_GATEWAY,
            )
            return

        self.xero_page(
            "Xero Connection",
            "<h1>First invoice execution finished</h1>"
            f"<p>Invoices processed: {result['execution_batch_size']}</p>"
            f"<p>Valid records found: {result['valid_record_count']}</p>"
            f"<p>Rejected invoices: {result['rejection_count']}</p>",
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
        if route == "/xero/connect":
            self.handle_xero_connect()
            return
        if route == "/xero/callback":
            self.handle_xero_callback(query)
            return
        if route == "/login":
            self.send_page(
                "Demo Login",
                """<h1>Demo Login</h1>
                <form method="post" action="/login">
                <label>Email<input name="email" type="email" data-testid="email" required></label>
                <label>Password<input name="password" type="password" data-testid="password" required></label>
                <button type="submit" data-testid="login-button">Login</button></form>""",
            )
            return

        if not self.session_is_valid():
            self.redirect("/login")
            return

        if route == "/":
            self.redirect("/dashboard")
        elif route == "/dashboard":
            self.send_page(
                "Dashboard",
                """<h1>Dashboard</h1><p>Local demonstration system.</p>
                <p><a href="/transactions" data-testid="transactions-link">Transactions</a></p>""",
            )
        elif route == "/transactions":
            self.send_page(
                "Transactions",
                """<h1>Transactions</h1><p><a href="/transactions/new" data-testid="new-transaction-link">New Transaction</a></p>""",
            )
        elif route == "/transactions/new":
            self.send_page("New Transaction", self.transaction_form())
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        values = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        route = urlparse(self.path).path

        if route == "/login":
            email = values.get("email", [""])[0]
            password = values.get("password", [""])[0]
            if email == DEMO_EMAIL and password == DEMO_PASSWORD:
                session = secrets.token_urlsafe(16)
                SESSIONS.add(session)
                self.redirect("/dashboard", f"session={session}; HttpOnly; SameSite=Strict")
            else:
                self.send_page("Demo Login", "<h1>Invalid credentials</h1>", HTTPStatus.UNAUTHORIZED)
            return

        if route == "/xero/confirm":
            self.handle_xero_confirmation(values)
            return

        if not self.session_is_valid():
            self.redirect("/login")
            return

        if route == "/transactions":
            row = {column: values.get(column, [""])[0] for column in REQUIRED_COLUMNS}
            try:
                validate_transaction_row(row)
            except ValueError as exc:
                self.send_page("New Transaction", f"<h1>Validation error</h1><p>{html.escape(str(exc))}</p>", HTTPStatus.BAD_REQUEST)
                return
            TRANSACTIONS.append(row)
            self.send_page("Transaction Created", "<h1>Transaction successfully created</h1>")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    @staticmethod
    def transaction_form() -> str:
        fields = "".join(
            f'<label>{column.replace("_", " ").title()}<input name="{column}" data-testid="{column.replace("_", "-")}" required></label>'
            for column in REQUIRED_COLUMNS
        )
        return f"<h1>New Transaction</h1><form method=\"post\" action=\"/transactions\">{fields}<button type=\"submit\" data-testid=\"save-transaction\">Save Transaction</button></form>"


def run_server() -> None:
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"Demo system listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
