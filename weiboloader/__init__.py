from __future__ import annotations

from pathlib import Path

from weiboloader.exceptions import (
    APISchemaError,
    AuthError,
    CheckpointError,
    InitError,
    RateLimitError,
    TargetError,
    WeiboLoaderException,
)
from weiboloader.structures import (
    CursorState,
    MediaItem,
    MidTarget,
    Post,
    SearchTarget,
    SuperTopic,
    SuperTopicTarget,
    TargetSpec,
    User,
    UserTarget,
)
from weiboloader.weiboloader import WeiboLoader

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # exceptions
    "WeiboLoaderException",
    "AuthError",
    "RateLimitError",
    "CheckpointError",
    "TargetError",
    "APISchemaError",
    "InitError",
    # structures
    "User",
    "SuperTopic",
    "MediaItem",
    "Post",
    "CursorState",
    # targets
    "UserTarget",
    "SuperTopicTarget",
    "MidTarget",
    "SearchTarget",
    "TargetSpec",
    # orchestrator
    "WeiboLoader",
]


def get_config_dir() -> Path:
    path = Path.home() / ".config" / "weiboloader"
    path.mkdir(parents=True, exist_ok=True)
    return path
