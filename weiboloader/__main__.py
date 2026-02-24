from __future__ import annotations

import argparse
import logging
import re
import sys
from urllib.parse import parse_qs, urlparse

from .context import WeiboLoaderContext
from .exceptions import InitError, map_exception_to_exit_code
from .ratecontrol import SlidingWindowRateController
from .structures import MidTarget, SearchTarget, SuperTopicTarget, TargetSpec, UserTarget
from .ui import EventKind, NullSink, RichSink, UIEvent
from .weiboloader import WeiboLoader

_DETAIL_MID_RE = re.compile(r"/detail/([^/?#]+)")


def _extract_mid_from_url(raw_url: str) -> str | None:
    parsed = urlparse(raw_url)
    if m := _DETAIL_MID_RE.search(parsed.path):
        return m.group(1).strip() or None

    query = parse_qs(parsed.query)
    for key in ("mid", "id"):
        if values := query.get(key):
            if value := values[0].strip():
                return value
    return None


def _looks_like_containerid(identifier: str) -> bool:
    return identifier.startswith("100808") or identifier.endswith("_-_feed")


def parse_target(raw: str, mid_flag: str | None) -> TargetSpec:
    token = (raw or "").strip()

    if token.startswith(("http://", "https://")):
        mid = _extract_mid_from_url(token)
        if not mid:
            raise InitError(f"cannot parse mid from url: {raw}")
        return MidTarget(mid=mid)

    if mid_flag is not None and mid_flag.strip():
        return MidTarget(mid=mid_flag.strip())

    if token.startswith("#"):
        identifier = token[1:].strip()
        if not identifier:
            raise InitError("empty supertopic target")
        return SuperTopicTarget(identifier=identifier, is_containerid=_looks_like_containerid(identifier))

    if token.startswith(":"):
        keyword = token[1:].strip()
        if not keyword:
            raise InitError("empty search target")
        return SearchTarget(keyword=keyword)

    if not token:
        raise InitError("missing target")

    return UserTarget(identifier=token, is_uid=token.isdigit())


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="weiboloader")
    parser.add_argument("targets", nargs="*", help="Targets: UID/nickname, #supertopic, :search or URL")
    parser.add_argument("-mid", "--mid", dest="mid", help="Single post MID")

    parser.add_argument("--load-cookies", choices=("chrome", "firefox", "edge"))
    parser.add_argument("--cookie")
    parser.add_argument("--cookie-file")
    parser.add_argument("--sessionfile")

    parser.add_argument("--no-videos", action="store_true")
    parser.add_argument("--no-pictures", action="store_true")
    parser.add_argument("--metadata-json", action="store_true")
    parser.add_argument("--post-metadata-txt")

    parser.add_argument("--dirname-pattern")
    parser.add_argument("--filename-pattern", default="{date}_{name}")

    parser.add_argument("--post-filter")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--fast-update", action="store_true")
    parser.add_argument("--latest-stamps")

    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--request-interval", type=float, default=0.0)
    parser.add_argument("--captcha-mode", choices=("auto", "browser", "manual", "skip"), default="auto")
    parser.add_argument("--visitor-cookies", action="store_true",
                        help="Auto-fetch visitor cookies via Playwright (requires playwright)")

    args = parser.parse_args(argv)

    if args.count < 0:
        parser.error("--count must be >= 0")
    if args.request_interval < 0:
        parser.error("--request-interval must be >= 0")
    if not args.targets and not args.mid:
        parser.error("at least one target or -mid/--mid is required")

    return args


def main(argv: list[str] | None = None) -> int:
    loader: WeiboLoader | None = None
    sink: NullSink | RichSink | None = None

    try:
        args = parse_args(argv)

        use_rich = sys.stderr.isatty()
        if use_rich:
            from rich.console import Console
            from rich.logging import RichHandler

            console = Console(stderr=True)
            sink = RichSink(console)
            logging.basicConfig(
                handlers=[RichHandler(console=console, show_path=False)],
                level=logging.WARNING,
                format="%(message)s",
            )
        else:
            sink = NullSink()

        captcha_pause = getattr(sink, "pause", None)
        captcha_resume = getattr(sink, "resume", None)

        context = WeiboLoaderContext(
            rate_controller=SlidingWindowRateController(request_interval=args.request_interval),
            captcha_mode=args.captcha_mode,
            session_path=args.sessionfile,
            on_captcha_pause=captcha_pause,
            on_captcha_resume=captcha_resume,
        )

        has_auth = context.load_session(args.sessionfile)
        if args.load_cookies:
            context.load_browser_cookies(args.load_cookies)
            has_auth = True
        if args.cookie:
            context.set_cookies_from_string(args.cookie)
            has_auth = True
        if args.cookie_file:
            context.set_cookies_from_file(args.cookie_file)
            has_auth = True
        if args.visitor_cookies:
            sink.emit(UIEvent(kind=EventKind.STAGE, message="Fetching visitor cookies"))
            context.fetch_visitor_cookies()
            has_auth = True

        if has_auth:
            context.validate_cookie()
            context.save_session(args.sessionfile)

        if args.post_filter:
            print("warning: --post-filter is not implemented yet and will be ignored", file=sys.stderr)

        loader = WeiboLoader(
            context,
            dirname_pattern=args.dirname_pattern,
            filename_pattern=args.filename_pattern,
            no_videos=args.no_videos,
            no_pictures=args.no_pictures,
            count=args.count,
            fast_update=args.fast_update,
            latest_stamps=args.latest_stamps,
            metadata_json=args.metadata_json,
            post_metadata_txt=args.post_metadata_txt,
            no_resume=args.no_resume,
            progress=sink,
        )

        raw_targets = args.targets if args.targets else [""]
        targets = [parse_target(raw, args.mid) for raw in raw_targets]
        results = loader.download_targets(targets)
        return 0 if results and all(results.values()) else 1

    except KeyboardInterrupt:
        if loader is not None:
            try:
                loader.flush()
            except Exception:
                pass
        return 5
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return 0 if code == 0 else 2
    except BaseException as exc:
        return map_exception_to_exit_code(exc)
    finally:
        if sink is not None:
            sink.close()


if __name__ == "__main__":
    sys.exit(main())
