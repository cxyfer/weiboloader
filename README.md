<div align="center">

# weiboloader

*A command-line tool for downloading media from [Weibo](https://weibo.com), inspired by [instaloader](https://github.com/instaloader/instaloader).*
*Built as a rewrite of [weiboPicDownloader](https://github.com/cxyfer/weiboPicDownloader) (a fork of [nondanee/weiboPicDownloader](https://github.com/nondanee/weiboPicDownloader)).*

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg?style=flat-square&logo=python)](https://www.python.org)
[![License](https://img.shields.io/badge/license-GPLv3-blue.svg?style=flat-square)](LICENSE)

</div>

## Features

- Download from multiple target types: user timeline, supertopic, search keyword, single post (MID/URL)
- Customizable file/directory naming with template patterns
- Incremental download with `--fast-update` and `--latest-stamps`
- Resumable downloads with checkpoint support
- API sliding-window rate control (default: 60 requests / 600s) with exponential backoff
- Captcha handling: auto (Playwright), browser-based, manual, or skip
- Cookie authentication: browser import, string, file, session persistence
- Visitor cookie auto-fetch via headless Playwright
- Concurrent media downloads with configurable worker count

## Installation

```bash
pip install .
```

Optional dependencies:

```bash
# Load cookies from local browser (Chrome/Firefox/Edge)
pip install ".[browser]"

# Auto-fetch visitor cookies & captcha auto-solve (requires: playwright install chromium)
pip install ".[captcha]"

# Development
pip install ".[dev]"
```

## Usage

```bash
# Download by user UID
weiboloader 1234567890

# Download by nickname
weiboloader nickname

# Download supertopic
weiboloader "#TopicName"

# Search keyword
weiboloader ":keyword"

# Single post by MID
weiboloader -mid 5120000000000000

# Single post by URL
weiboloader "https://m.weibo.cn/detail/5120000000000000"
```

### Authentication

```bash
# Import cookies from browser
weiboloader --load-cookies chrome 1234567890

# Cookie string
weiboloader --cookie "SUB=xxx; SUBP=yyy" 1234567890

# Cookie file
weiboloader --cookie-file cookies.txt 1234567890

# Session persistence (auto-saved after first auth)
weiboloader --sessionfile session.dat --cookie "SUB=xxx" 1234567890

# Auto-fetch visitor cookies (requires playwright)
weiboloader --visitor-cookies 1234567890
```

- `--load-cookies` supports `chrome`, `firefox`, and `edge` (`pip install ".[browser]"`).
- `--visitor-cookies` requires `pip install ".[captcha]"` and `playwright install chromium`.
- `--sessionfile FILE` lets you persist and reuse an authenticated session.

### Options

```
-mid, --mid MID          Download a single post by MID
--load-cookies BROWSER   Import cookies from chrome|firefox|edge
--cookie TEXT            Set cookies from a raw cookie string
--cookie-file FILE       Load cookies from a cookie file
--sessionfile FILE       Persist and reuse an authenticated session
--visitor-cookies        Auto-fetch visitor cookies via Playwright
--no-videos              Skip video downloads
--no-pictures            Skip picture downloads
--metadata-json          Save post metadata as JSON
--post-metadata-txt TXT  Save custom text per post
--dirname-pattern PAT    Directory naming pattern
--filename-pattern PAT   File naming pattern (default: {date}_{name})
--count N                Limit number of posts (0 = unlimited)
--fast-update            Stop at first existing file
--latest-stamps FILE     Track latest download timestamps
--no-resume              Disable checkpoint resume
--request-interval SEC   Minimum seconds between requests per bucket (default: 1)
--api-rate-limit N       API sliding-window quota (default: 60)
--api-rate-window SEC    API sliding-window window in seconds (default: 600)
--workers N              Concurrent media download workers (default: 1)
--captcha-mode MODE      auto|browser|manual|skip (default: auto)
```

### Request pacing

- API requests use a sliding-window quota of 60 requests per 600 seconds by default.
- Override the API quota with `--api-rate-limit` and `--api-rate-window`.
- `--request-interval` defaults to 1 second and applies separately to the `api` and `media` buckets.
- Media requests are isolated from API quota usage and do not use the sliding-window quota.
- Media downloads use 1 worker by default; override with `--workers`.

### Naming Patterns

Available template variables:

| Variable | Description |
|----------|-------------|
| `{nickname}` | User nickname |
| `{uid}` | User ID |
| `{mid}` | Post MID |
| `{bid}` | Post BID |
| `{date}` | Timestamp (default: `%Y%m%d_%H%M%S`) |
| `{date:%Y-%m-%d}` | Custom date format |
| `{text}` | Post text (truncated to 50 chars) |
| `{index}` | Media index |
| `{index:3}` | Zero-padded index |
| `{type}` | Media type (picture/video) |
| `{name}` | Original filename hint |
| `{topic_name}` | Supertopic name |
| `{keyword}` | Search keyword |

## Programmatic Usage

```python
from weiboloader import WeiboLoader, WeiboLoaderContext, UserTarget
from weiboloader.ratecontrol import SlidingWindowRateController

ctx = WeiboLoaderContext(
    rate_controller=SlidingWindowRateController(),
    captcha_mode="auto",
)
ctx.set_cookies_from_string("SUB=xxx")

loader = WeiboLoader(ctx, count=10)
loader.download_target(UserTarget(identifier="1234567890", is_uid=True))
```

## License

GPLv3
