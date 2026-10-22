from typing import Mapping

from playwright.sync_api import Page


class AccountingDemoFlow:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        email: str = "demo@example.com",
        password: str = "demo-password",
    ):
        self.base_url = base_url
        self.email = email
        self.password = password

    def login(self, page: Page) -> None:
        page.goto(f"{self.base_url}/login")
        page.get_by_test_id("email").fill(self.email)
        page.get_by_test_id("password").fill(self.password)
        page.get_by_test_id("login-button").click()
        page.get_by_role("heading", name="Dashboard").wait_for()
        page.get_by_test_id("transactions-link").click()

    def create_transaction(
        self, page: Page, transaction: Mapping[str, object]
    ) -> None:
        page.goto(f"{self.base_url}/transactions/new")
        page.get_by_test_id("client-id").fill(str(transaction["client_id"]))
        page.get_by_test_id("client-name").fill(str(transaction["client_name"]))
        page.get_by_test_id("amount").fill(str(transaction["amount"]))
        page.get_by_test_id("date").fill(str(transaction["date"]))
        page.get_by_test_id("description").fill(str(transaction["description"]))
        page.get_by_test_id("save-transaction").click()
        page.get_by_text("Transaction successfully created", exact=True).wait_for()
