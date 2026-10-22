from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import Browser, Playwright


@contextmanager
def chromium_browser(playwright: Playwright) -> Iterator[Browser]:
    browser = playwright.chromium.launch(headless=False)
    try:
        yield browser
    finally:
        browser.close()
