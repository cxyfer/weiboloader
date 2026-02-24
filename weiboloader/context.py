from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import requests

from ._captcha import (
    TIMEOUT_DEFAULT,
    ManualCaptchaHandler,
    PlaywrightCaptchaHandler,
    SkipCaptchaHandler,
    VisitorCookieFetcher,
    extract_captcha_url,
    is_playwright_available,
)
from .adapter import extract_next_cursor, parse_post, parse_supertopic, parse_user_info
from .exceptions import AuthError, RateLimitError, TargetError
from .ratecontrol import BaseRateController, SlidingWindowRateController
from .structures import Post, SuperTopic, User

Browser = Literal["chrome", "firefox", "edge"]
CaptchaMode = Literal["auto", "browser", "manual", "skip"]


class WeiboLoaderContext:
    BASE_URL = "https://m.weibo.cn"
    SESSION_FILE = "session.dat"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://m.weibo.cn/",
    }

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        rate_controller: BaseRateController | None = None,
        captcha_mode: CaptchaMode = "auto",
        captcha_timeout: int = TIMEOUT_DEFAULT,
        request_timeout: int = 20,
        session_path: str | Path | None = None,
    ):
        self.session = session or requests.Session()
        for k, v in self.HEADERS.items():
            self.session.headers.setdefault(k, v)

        self.rate = rate_controller or SlidingWindowRateController()
        self.captcha_mode = captcha_mode
        self.captcha_timeout = captcha_timeout
        self.req_timeout = request_timeout
        self._session_path = self._resolve_path(session_path)

        self._captcha_handlers = {
            "manual": ManualCaptchaHandler(),
            "browser": PlaywrightCaptchaHandler(),
            "skip": SkipCaptchaHandler(),
        }

    def _handle_response(
        self,
        resp: requests.Response,
        target: str,
        allow_captcha: bool,
        attempt: int,
        retries: int,
    ) -> requests.Response | None:
        """Handle response status codes. Returns response on success, None to retry."""
        if allow_captcha and (vurl := extract_captcha_url(resp)):
            resp.close()
            if self._solve_captcha(vurl):
                return None  # Retry after captcha solved
            raise AuthError("captcha not solved")

        if resp.status_code in (403, 418):
            resp.close()
            if attempt < retries:
                return None  # Will retry with backoff
            raise RateLimitError(f"rate limited: {target}")

        if resp.status_code == 401:
            resp.close()
            raise AuthError("authentication failed")

        if resp.status_code >= 500:
            resp.close()
            if attempt < retries:
                return None  # Will retry
            raise TargetError(f"server error {resp.status_code}")

        if resp.status_code >= 400:
            resp.close()
            raise TargetError(f"http {resp.status_code}")

        return resp

    def request(
        self,
        method: str,
        url: str,
        *,
        bucket: str = "api",
        allow_captcha: bool = True,
        retries: int = 3,
        **kwargs,
    ) -> requests.Response:
        """Make an HTTP request with rate control, retry, and captcha handling."""
        target = url if url.startswith("http") else urljoin(f"{self.BASE_URL}/", url.lstrip("/"))
        timeout = kwargs.pop("timeout", self.req_timeout)

        for attempt in range(retries + 1):
            self.rate.wait_before_request(bucket)

            try:
                resp = self.session.request(method, target, timeout=timeout, **kwargs)
            except requests.RequestException as e:
                if attempt >= retries:
                    raise TargetError(f"request failed: {target}") from e
                continue

            self.rate.handle_response(bucket, resp.status_code)

            result = self._handle_response(resp, target, allow_captcha, attempt, retries)
            if result is not None:
                return result
            # result is None means retry

        raise TargetError(f"request failed: {target}")

    def load_browser_cookies(self, browser: Browser) -> None:
        try:
            import browser_cookie3 as bc3
        except ImportError as e:
            raise AuthError("browser_cookie3 not installed") from e

        getter = {"chrome": bc3.chrome, "firefox": bc3.firefox, "edge": bc3.edge}.get(browser)
        if not getter:
            raise AuthError(f"unsupported browser: {browser}")

        try:
            jar = getter(domain_name="weibo.cn")
        except TypeError:
            jar = getter()
        except Exception as e:
            raise AuthError(f"failed to load cookies: {e}") from e

        loaded = False
        for c in jar:
            if "weibo" in (c.domain or ""):
                self.session.cookies.set_cookie(copy.copy(c))
                loaded = True
        if not loaded:
            raise AuthError("no weibo cookies found")

    def set_cookies_from_string(self, s: str) -> None:
        s = s.strip()
        if not s:
            raise AuthError("empty cookie string")

        for part in s.replace("\n", ";").split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name, value = name.strip(), value.strip()
            if name:
                self.session.cookies.set(name, value, domain=".weibo.cn", path="/")

    def set_cookies_from_file(self, path: str | Path) -> None:
        self.set_cookies_from_string(Path(path).expanduser().read_text(encoding="utf-8"))

    def fetch_visitor_cookies(self, headless: bool = True, timeout: int = 30) -> None:
        """Auto-fetch visitor cookies via Playwright browser."""
        fetcher = VisitorCookieFetcher(headless=headless)
        cookies = fetcher.fetch(timeout=timeout)
        if not cookies:
            raise AuthError("failed to fetch visitor cookies")
        for name, value in cookies.items():
            self.session.cookies.set(name, value, domain=".weibo.cn", path="/")

    def validate_cookie(self) -> None:
        has_sub = any(c.name == "SUB" and c.value for c in self.session.cookies)
        if not has_sub:
            raise AuthError("missing SUB cookie")

    def save_session(self, path: str | Path | None = None) -> Path:
        p = Path(path).expanduser() if path else self._session_path
        p.parent.mkdir(parents=True, exist_ok=True)
        # Serialize cookies as list of dicts to avoid pickle security risk
        cookie_list = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in self.session.cookies
        ]
        payload = {"cookies": cookie_list, "headers": dict(self.session.headers)}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return p

    def load_session(self, path: str | Path | None = None) -> bool:
        p = Path(path).expanduser() if path else self._session_path
        if not p.exists():
            return False
        try:
            with open(p, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload.get("cookies"), list):
                for c in payload["cookies"]:
                    self.session.cookies.set(
                        c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/")
                    )
            if isinstance(payload.get("headers"), dict):
                self.session.headers.update(payload["headers"])
            return True
        except Exception:
            return False

    def resolve_nickname_to_uid(self, nickname: str) -> str:
        name = quote(nickname.strip(), safe="")
        resp = self.request("GET", f"/n/{name}", allow_redirects=False, retries=2)
        loc = resp.headers.get("Location", "")
        resp.close()

        uid = self._extract_uid(loc or resp.url)
        if uid:
            return uid

        resp = self.request("GET", f"/n/{name}", retries=2)
        uid = self._extract_uid(resp.url) or self._extract_uid(resp.text)
        resp.close()
        if uid:
            return uid
        raise TargetError(f"cannot resolve nickname: {nickname}")

    def get_user_info(self, uid: str) -> User:
        data = self._get_index({"type": "uid", "value": str(uid)})
        user = data.get("userInfo") or data.get("user")
        if not isinstance(user, dict):
            user = next((c.get("user") for c in data.get("cards", []) if c.get("user")), None)
        if not isinstance(user, dict):
            raise AuthError("user not found")
        return parse_user_info(user)

    def get_user_posts(self, uid: str, page: int) -> tuple[list[Post], str | None]:
        data = self._get_index({"containerid": f"107603{uid}", "page": int(page)})
        return self._parse_posts(data), extract_next_cursor(data)

    def get_supertopic_posts(self, cid: str, page: int) -> tuple[list[Post], str | None]:
        cid = cid if cid.endswith("_-_feed") else f"{cid}_-_feed"
        data = self._get_index({"containerid": cid, "page": int(page)})
        return self._parse_posts(data), extract_next_cursor(data)

    def search_supertopic(self, keyword: str) -> list[SuperTopic]:
        data = self._get_index({"containerid": f"100103type=98&q={keyword}"})
        topics: list[SuperTopic] = []
        seen: set[str] = set()
        for c in data.get("cards", []):
            raw = dict(c)
            if "containerid" not in raw:
                if m := re.search(r"containerid=([^&]+)", str(c.get("scheme", ""))):
                    raw["containerid"] = m.group(1)
            if "topic_title" not in raw:
                if title := c.get("title_sub") or c.get("title"):
                    raw["topic_title"] = title.strip("# ")
            try:
                t = parse_supertopic(raw)
                if t.containerid not in seen:
                    seen.add(t.containerid)
                    topics.append(t)
            except Exception:
                continue
        return topics

    def search_posts(self, keyword: str, page: int) -> tuple[list[Post], str | None]:
        data = self._get_index({"containerid": f"100103type=1&q={keyword}", "page": int(page)})
        return self._parse_posts(data), extract_next_cursor(data)

    def get_post_by_mid(self, mid: str) -> Post:
        resp = self.request("GET", f"/detail/{mid}", retries=2)
        text = resp.text
        resp.close()

        status = self._extract_status_from_html(text)
        if status:
            return parse_post(status)

        payload = self._get_json("/api/statuses/show", params={"id": str(mid)})
        if status := payload.get("data") or payload:
            return parse_post(status)
        raise TargetError(f"post not found: {mid}")

    def _solve_captcha(self, url: str) -> bool:
        handler = self._captcha_handlers.get(self.captcha_mode)
        if self.captcha_mode == "auto":
            handler = self._captcha_handlers["browser"] if is_playwright_available() else self._captcha_handlers["manual"]
        if not handler:
            raise AuthError(f"captcha mode not available: {self.captcha_mode}")
        try:
            return handler.solve(url, self.session, self.captcha_timeout)
        except Exception:
            return False

    def _get_json(self, url: str, **kwargs) -> dict[str, Any]:
        resp = self.request("GET", url, **kwargs)
        try:
            return resp.json()
        finally:
            resp.close()

    def _get_index(self, params: dict[str, Any]) -> dict[str, Any]:
        payload = self._get_json("/api/container/getIndex", params=params)
        if isinstance(data := payload.get("data"), dict):
            return data
        raise TargetError(payload.get("msg") or "api error")

    def _parse_posts(self, data: dict[str, Any]) -> list[Post]:
        posts: list[Post] = []
        seen: set[str] = set()
        for c in data.get("cards", []):
            if not isinstance(c, dict):
                continue
            for item in [c, *(c.get("card_group") or [])]:
                if not isinstance(item, dict) or "mblog" not in item:
                    continue
                try:
                    p = parse_post(item)
                    if p.mid not in seen:
                        seen.add(p.mid)
                        posts.append(p)
                except Exception:
                    continue
        return posts

    def _extract_uid(self, text: str) -> str | None:
        if not text:
            return None
        decoded = unquote(text)
        parsed = urlparse(decoded)
        query = parse_qs(parsed.query)
        for key in ("uid", "value", "id"):
            if values := query.get(key):
                return values[0] if values else None
        for pat in (r"/u/(\d{5,})", r"/profile/(\d{5,})"):
            if m := re.search(pat, parsed.path):
                return m.group(1)
        if m := re.search(r"\d{5,}", decoded):
            return m.group(0)
        return None

    def _extract_status_from_html(self, html: str) -> dict[str, Any] | None:
        if not html:
            return None
        if m := re.search(r'\$render_data\s*=\s*(\[[^\]]+\])\s*\[0\]', html):
            try:
                import json
                data = json.loads(m.group(1))
                if isinstance(data, list) and data:
                    return data[0].get("status")
            except Exception:
                pass
        if m := re.search(r'"status"\s*:\s*(\{[^}]+\})', html):
            try:
                import json
                return json.loads(m.group(1))
            except Exception:
                pass
        return None

    def _resolve_path(self, path: str | Path | None) -> Path:
        if path:
            return Path(path).expanduser()
        return Path.home() / ".config" / "weiboloader" / self.SESSION_FILE
