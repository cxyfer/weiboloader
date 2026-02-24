from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


# --- Core data models ---


@dataclass
class User:
    uid: str
    nickname: str
    avatar: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class SuperTopic:
    containerid: str
    name: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class MediaItem:
    media_type: Literal["picture", "video"]
    url: str
    index: int
    filename_hint: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Post:
    mid: str
    bid: str | None
    text: str
    created_at: datetime
    user: User | None = None
    media_items: list[MediaItem] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class CursorState:
    page: int
    cursor: str | None = None
    seen_mids: list[str] = field(default_factory=list)
    options_hash: str = ""
    timestamp: str | None = None


# --- Target spec union ---


@dataclass
class UserTarget:
    identifier: str
    is_uid: bool


@dataclass
class SuperTopicTarget:
    identifier: str
    is_containerid: bool


@dataclass
class MidTarget:
    mid: str


@dataclass
class SearchTarget:
    keyword: str


TargetSpec = UserTarget | SuperTopicTarget | MidTarget | SearchTarget
