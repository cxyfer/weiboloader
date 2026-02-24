from __future__ import annotations

from datetime import datetime, timezone, timedelta

from weiboloader.structures import (
    CursorState,
    MediaItem,
    MidTarget,
    Post,
    SearchTarget,
    SuperTopic,
    SuperTopicTarget,
    User,
    UserTarget,
)

CST = timezone(timedelta(hours=8))


def test_user_defaults() -> None:
    u = User(uid="123", nickname="test")
    assert u.avatar is None
    assert u.raw == {}


def test_user_with_raw() -> None:
    raw = {"extra": "field"}
    u = User(uid="1", nickname="n", avatar="http://a.jpg", raw=raw)
    assert u.raw is raw


def test_supertopic() -> None:
    st = SuperTopic(containerid="100808abc", name="topic")
    assert st.raw == {}


def test_media_item_picture() -> None:
    m = MediaItem(media_type="picture", url="http://img.jpg", index=0)
    assert m.filename_hint is None
    assert m.raw == {}


def test_media_item_video() -> None:
    m = MediaItem(media_type="video", url="http://vid.mp4", index=1, filename_hint="v.mp4")
    assert m.filename_hint == "v.mp4"


def test_post_defaults() -> None:
    now = datetime.now(CST)
    p = Post(mid="100", bid=None, text="hello", created_at=now)
    assert p.user is None
    assert p.media_items == []
    assert p.raw == {}


def test_post_with_media() -> None:
    now = datetime.now(CST)
    m = MediaItem(media_type="picture", url="http://a.jpg", index=0)
    u = User(uid="1", nickname="n")
    p = Post(mid="200", bid="abc", text="txt", created_at=now, user=u, media_items=[m])
    assert len(p.media_items) == 1
    assert p.user is u


def test_cursor_state_defaults() -> None:
    cs = CursorState(page=1)
    assert cs.cursor is None
    assert cs.seen_mids == []
    assert cs.options_hash == ""
    assert cs.timestamp is None


def test_cursor_state_mutable_default_isolation() -> None:
    a = CursorState(page=1)
    b = CursorState(page=2)
    a.seen_mids.append("mid1")
    assert b.seen_mids == []


def test_user_target_uid() -> None:
    t = UserTarget(identifier="12345", is_uid=True)
    assert t.is_uid


def test_user_target_nickname() -> None:
    t = UserTarget(identifier="someone", is_uid=False)
    assert not t.is_uid


def test_supertopic_target_by_name() -> None:
    t = SuperTopicTarget(identifier="topic_name", is_containerid=False)
    assert not t.is_containerid


def test_supertopic_target_by_containerid() -> None:
    t = SuperTopicTarget(identifier="100808abc", is_containerid=True)
    assert t.is_containerid


def test_mid_target() -> None:
    t = MidTarget(mid="512012345")
    assert t.mid == "512012345"


def test_search_target() -> None:
    t = SearchTarget(keyword="some keyword")
    assert t.keyword == "some keyword"


def test_raw_dict_preserved() -> None:
    raw = {"unknown_field": 42, "nested": {"a": 1}}
    u = User(uid="1", nickname="n", raw=raw)
    assert u.raw["unknown_field"] == 42
    assert u.raw["nested"]["a"] == 1
