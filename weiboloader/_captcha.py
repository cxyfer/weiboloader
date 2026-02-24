from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from urllib.parse import urlparse

if TYPE_CHECKING:
    import requests

TIMEOUT_DEFAULT = 300


@runtime_checkable
class CaptchaHandler(Protocol):
    def solve(self, verify_url: str, session: requests.Session, timeout: int = TIMEOUT_DEFAULT) -> bool:
        ...


class PlaywrightCaptchaHandler:
    def __init__(self, headless: bool = False) -> None:
        self.headless = headless

    def solve(self, verify_url: str, session: requests.Session, timeout: int = TIMEOUT_DEFAULT) -> bool:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False

        deadline = time.monotonic() + timeout
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain or urlparse(verify_url).hostname or "",
                    "path": c.path or "/"} for c in session.cookies]

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            try:
                ctx = browser.new_context()
                if cookies:
                    ctx.add_cookies(cookies)
                page = ctx.new_page()
                page.goto(verify_url, wait_until="domcontentloaded", timeout=min(timeout, 30) * 1000)

                while time.monotonic() < deadline:
                    if not _is_captcha_url(page.url):
                        for c in ctx.cookies():
                            session.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
                        return True
                    time.sleep(1)
                return not _is_captcha_url(page.url)
            finally:
                browser.close()


class ManualCaptchaHandler:
    def solve(self, verify_url: str, session: requests.Session, timeout: int = TIMEOUT_DEFAULT) -> bool:
        print(f"CAPTCHA: {verify_url}")
        print(f"Press Enter within {timeout}s after solving...")

        done = threading.Event()
        def read():
            try:
                input()
                done.set()
            except EOFError:
                pass
        threading.Thread(target=read, daemon=True).start()
        return done.wait(timeout)


class SkipCaptchaHandler:
    def solve(self, verify_url: str, session: requests.Session, timeout: int = TIMEOUT_DEFAULT) -> bool:
        return False


def is_playwright_available() -> bool:
    try:
        __import__("playwright.sync_api")
        return True
    except ImportError:
        return False


def _is_captcha_url(url: str) -> bool:
    parts = urlparse(url)
    text = f"{parts.netloc}{parts.path}".lower()
    return any(h in text for h in ("passport.weibo", "login.sina", "verify", "captcha", "challenge"))


class VisitorCookieFetcher:
    """Fetch visitor cookies from m.weibo.cn using Playwright."""

    MOBILE_URL = "https://m.weibo.cn/"

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    def fetch(self, timeout: int = 30) -> dict[str, str]:
        """Launch browser, visit m.weibo.cn, return cookies dict.

        Raises ImportError if playwright not installed.
        Returns empty dict on failure.
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            try:
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
                    ),
                    viewport={"width": 393, "height": 851},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    is_mobile=True,
                    has_touch=True,
                )
                page = ctx.new_page()
                page.goto(self.MOBILE_URL, timeout=timeout * 1000, wait_until="networkidle")
                page.wait_for_timeout(2000)
                return {c["name"]: c["value"] for c in ctx.cookies()}
            finally:
                browser.close()


def extract_captcha_url(response: requests.Response) -> str | None:
    if response.status_code == 418:
        return response.url if _is_captcha_url(response.url) else None
    for attr in ("url", "headers"):
        val = getattr(response, attr)
        if attr == "headers":
            val = val.get("Location") or ""
        if _is_captcha_url(str(val)):
            return str(val)
    return None
