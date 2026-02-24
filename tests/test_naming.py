from __future__ import annotations

from datetime import datetime, timezone, timedelta

from hypothesis import given, strategies as st

from weiboloader.naming import (
    ILLEGAL as ILLEGAL_FILENAME_CHARS,
    build_directory,
    build_filename,
    render_template,
    sanitize as sanitize_filename,
)
from weiboloader.structures import SearchTarget, SuperTopicTarget, UserTarget

CST = timezone(timedelta(hours=8))


@given(st.text())
def test_sanitize_idempotent(raw: str) -> None:
    once = sanitize_filename(raw)
    twice = sanitize_filename(once)
    assert twice == once


@given(st.text(min_size=51, max_size=300))
def test_text_truncation_to_50(raw_text: str) -> None:
    rendered = render_template("{text}", text=raw_text)
    assert len(rendered) <= 50


def test_all_illegal_filename_fallback_to_mid() -> None:
    filename = build_filename("{text}", text='\\/:*?"<>|', mid="5123456789")
    assert filename == "5123456789"


def test_index_padding() -> None:
    filename = build_filename("{index:03}", index=5, mid="100")
    assert filename == "005"


def test_date_format_substitution() -> None:
    dt = datetime(2026, 2, 24, 12, 34, 56, tzinfo=CST)
    filename = build_filename("{date:%Y%m%d}_{mid}", date=dt, mid="9988")
    assert filename == "20260224_9988"


def test_default_user_directory_pattern() -> None:
    path = build_directory(target=UserTarget(identifier="Alice", is_uid=False))
    assert path == "./Alice/"


def test_default_supertopic_directory_pattern() -> None:
    path = build_directory(target=SuperTopicTarget(identifier="MyTopic", is_containerid=False))
    assert path == "./topic/MyTopic/"


def test_default_search_directory_pattern() -> None:
    path = build_directory(target=SearchTarget(keyword="NLP"))
    assert path == "./search/NLP/"


@given(
    nickname=st.text(),
    uid=st.text(),
    mid=st.text(min_size=1),
    text=st.text(),
)
def test_generated_filename_contains_no_illegal_chars(
    nickname: str,
    uid: str,
    mid: str,
    text: str,
) -> None:
    filename = build_filename(
        "{nickname}_{uid}_{mid}_{date:%Y%m%d}_{index:02}_{text}_{type}",
        nickname=nickname,
        uid=uid,
        mid=mid,
        text=text,
        index=3,
        type="picture",
    )
    assert filename
    for ch in ILLEGAL_FILENAME_CHARS:
        assert ch not in filename
