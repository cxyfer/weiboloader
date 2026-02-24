from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse

from .exceptions import APISchemaError
from .structures import MediaItem, Post, SuperTopic, User

CST = timezone(timedelta(hours=8))


def parse_weibo_datetime(raw: str, now: datetime | None = None) -> datetime:
    if now is None:
        now = datetime.now(CST)
    else:
        now = now.astimezone(CST)

    raw = raw.strip()

    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y").astimezone(CST)
    except ValueError:
        pass

    m = re.match(r"(\d+)\s*(?:分钟前|分鐘前)$", raw)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).replace(second=0, microsecond=0)

    m = re.match(r"昨天\s*(\d{2}):(\d{2})", raw)
    if m:
        return (now - timedelta(days=1)).replace(
            hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0
        )

    m = re.match(r"(\d{2})-(\d{2})$", raw)
    if m:
        try:
            return now.replace(month=int(m.group(1)), day=int(m.group(2)),
                              hour=0, minute=0, second=0, microsecond=0)
        except ValueError as e:
            raise APISchemaError(f"invalid date: {raw}") from e

    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=CST)
    except ValueError:
        pass

    raise APISchemaError(f"unknown date format: {raw}")


def parse_user_info(raw: Mapping[str, Any]) -> User:
    uid = str(raw.get("id") or raw.get("idstr") or "")
    if not uid:
        raise APISchemaError("user missing id")

    return User(
        uid=uid,
        nickname=raw.get("screen_name") or raw.get("nickname") or f"user_{uid}",
        avatar=raw.get("avatar_large") or raw.get("profile_image_url"),
        raw=dict(raw),
    )


def parse_supertopic(raw: Mapping[str, Any]) -> SuperTopic:
    cid = raw.get("containerid") or raw.get("id")
    if not cid:
        raise APISchemaError("supertopic missing containerid")

    return SuperTopic(
        containerid=str(cid),
        name=raw.get("topic_title") or raw.get("topic_name") or "topic",
        raw=dict(raw),
    )


def _extract_media(mblog: Mapping[str, Any]) -> list[MediaItem]:
    items: list[MediaItem] = []

    for i, pic in enumerate(mblog.get("pics", [])):
        url = pic.get("large", {}).get("url") or pic.get("url")
        if url:
            hint = PurePosixPath(urlparse(url).path).stem or None
            items.append(MediaItem(media_type="picture", url=url, index=i, filename_hint=hint, raw=pic))

    page = mblog.get("page_info")
    if page and page.get("type") == "video":
        info = page.get("media_info") or {}
        url = info.get("stream_url_hd") or info.get("mp4_720p_mp4") or \
              info.get("mp4_hd_url") or info.get("stream_url")
        if url:
            hint = PurePosixPath(urlparse(url).path).stem or None
            items.append(MediaItem(media_type="video", url=url, index=len(items), filename_hint=hint, raw=page))

    return items


def parse_post(raw_card: Mapping[str, Any]) -> Post:
    mblog = raw_card.get("mblog") or raw_card

    mid = str(mblog.get("mid") or mblog.get("id") or "")
    if not mid:
        raise APISchemaError("post missing mid")

    created_raw = mblog.get("created_at")
    if not created_raw:
        raise APISchemaError(f"post {mid} missing created_at")

    user_raw = mblog.get("user")
    user = parse_user_info(user_raw) if user_raw else None

    return Post(
        mid=mid,
        bid=mblog.get("bid"),
        text=mblog.get("text_raw") or mblog.get("text") or "",
        created_at=parse_weibo_datetime(created_raw),
        user=user,
        media_items=_extract_media(mblog),
        raw=dict(raw_card),
    )


def extract_next_cursor(raw_page: Mapping[str, Any]) -> str | None:
    info = raw_page.get("cardlistInfo") or {}
    sid = info.get("since_id")
    return str(sid) if sid else None
