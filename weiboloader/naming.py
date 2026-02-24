from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from .structures import MidTarget, SearchTarget, SuperTopicTarget, TargetSpec, UserTarget

ILLEGAL = '\\/:*?"<>|'
MAX_TEXT = 50
DATE_FMT = "%Y%m%d_%H%M%S"

DEFAULT_PATTERNS: dict[str, str] = {
    "user": "./{nickname}/",
    "supertopic": "./topic/{topic_name}/",
    "search": "./search/{keyword}/",
    "mid": "./",
}

_TRANS = str.maketrans("", "", ILLEGAL)
_TMPL_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::([^{}]*))?\}")


def sanitize(s: str) -> str:
    result = str(s).translate(_TRANS)
    # Prevent path traversal: reject '.' and '..' as they have special meaning in filesystem paths
    if result in (".", ".."):
        return ""
    return result


def render_template(template: str, **kwargs) -> str:
    date = kwargs.get("date") or datetime.now()
    text = str(kwargs.get("text", ""))[:MAX_TEXT]
    idx = kwargs.get("index")

    vars = {
        "nickname": str(kwargs.get("nickname", "")),
        "uid": str(kwargs.get("uid", "")),
        "mid": str(kwargs.get("mid", "")),
        "bid": str(kwargs.get("bid") or ""),
        "text": text,
        "type": str(kwargs.get("type", "")),
        "topic_name": str(kwargs.get("topic_name", "")),
        "keyword": str(kwargs.get("keyword", "")),
        "name": str(kwargs.get("name", "")),
    }

    def repl(m: re.Match) -> str:
        key, spec = m.group(1), m.group(2)
        if key == "date":
            try:
                return date.strftime(spec) if spec else date.strftime(DATE_FMT)
            except ValueError:
                return date.strftime(DATE_FMT)
        if key == "index":
            if idx is None:
                return ""
            return f"{idx:0{int(spec)}d}" if spec and spec.isdigit() else str(idx)
        return vars.get(key, "")

    return _TMPL_RE.sub(repl, template)


def build_filename(template: str, mid: str, **kwargs) -> str:
    rendered = render_template(template, mid=mid, **kwargs)
    sanitized = sanitize(rendered)
    return sanitized or sanitize(mid) or "file"


def _target_type(target: TargetSpec) -> Literal["user", "supertopic", "search", "mid"]:
    if isinstance(target, UserTarget):
        return "user"
    if isinstance(target, SuperTopicTarget):
        return "supertopic"
    if isinstance(target, SearchTarget):
        return "search"
    return "mid"


def build_directory(target: TargetSpec, pattern: str | None = None, **kwargs) -> str:
    ttype = _target_type(target)
    pat = pattern or DEFAULT_PATTERNS[ttype]

    if isinstance(target, UserTarget):
        kwargs.setdefault("uid" if target.is_uid else "nickname", target.identifier)
    elif isinstance(target, SuperTopicTarget):
        kwargs.setdefault("topic_name", target.identifier)
    elif isinstance(target, SearchTarget):
        kwargs.setdefault("keyword", target.keyword)
    elif isinstance(target, MidTarget):
        kwargs.setdefault("mid", target.mid)

    rendered = render_template(pat, **kwargs)
    parts = rendered.replace("\\", "/").split("/")
    sanitized_parts: list[str] = []
    for i, p in enumerate(parts):
        if not p:
            continue
        # Preserve leading "./" (relative path indicator) only at position 0
        if i == 0 and p == ".":
            sanitized_parts.append(p)
        else:
            sp = sanitize(p) or "x"
            sanitized_parts.append(sp)
    sanitized = "/".join(sanitized_parts)
    return sanitized + "/" if rendered.endswith("/") else sanitized
