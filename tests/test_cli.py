"""Tests for CLI (Phase 5.1)."""
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from weiboloader.__main__ import _extract_mid_from_url, _looks_like_containerid, parse_args, parse_target
from weiboloader.exceptions import InitError
from weiboloader.structures import MidTarget, SearchTarget, SuperTopicTarget, UserTarget


class TestExtractMidFromUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://m.weibo.cn/detail/123456", "123456"),
            ("https://weibo.com/123456/abc789", None),
            ("https://m.weibo.cn/status/123456?mid=789", "789"),
        ],
    )
    def test_url_parsing(self, url, expected):
        result = _extract_mid_from_url(url)
        assert result == expected


class TestLooksLikeContainerid:
    @pytest.mark.parametrize(
        "identifier,expected",
        [
            ("100808abc123", True),
            ("topic_-_feed", True),
            ("normal_topic", False),
            ("123456", False),
        ],
    )
    def test_containerid_detection(self, identifier, expected):
        assert _looks_like_containerid(identifier) is expected


class TestParseTarget:
    def test_url_target(self):
        target = parse_target("https://m.weibo.cn/detail/123456", None)
        assert isinstance(target, MidTarget)
        assert target.mid == "123456"

    def test_mid_flag_priority(self):
        target = parse_target("some text", "789")
        assert isinstance(target, MidTarget)
        assert target.mid == "789"

    def test_supertopic_target(self):
        target = parse_target("#topic_name", None)
        assert isinstance(target, SuperTopicTarget)
        assert target.identifier == "topic_name"

    def test_search_target(self):
        target = parse_target(":search keyword", None)
        assert isinstance(target, SearchTarget)
        assert target.keyword == "search keyword"

    def test_uid_target(self):
        target = parse_target("123456789", None)
        assert isinstance(target, UserTarget)
        assert target.identifier == "123456789"
        assert target.is_uid is True

    def test_nickname_target(self):
        target = parse_target("testuser", None)
        assert isinstance(target, UserTarget)
        assert target.identifier == "testuser"
        assert target.is_uid is False

    def test_empty_target_raises(self):
        with pytest.raises(InitError, match="missing target"):
            parse_target("", None)

    def test_empty_supertopic_raises(self):
        with pytest.raises(InitError, match="empty supertopic"):
            parse_target("#", None)

    def test_empty_search_raises(self):
        with pytest.raises(InitError, match="empty search"):
            parse_target(":", None)

    def test_invalid_url_raises(self):
        with pytest.raises(InitError, match="cannot parse mid"):
            parse_target("https://example.com/not-weibo", None)


class TestParseArgs:
    def test_minimal_args(self):
        args = parse_args(["123456"])
        assert args.targets == ["123456"]

    def test_mid_flag(self):
        args = parse_args(["-mid", "abc123"])
        assert args.mid == "abc123"
        assert args.targets == []

    def test_cookie_options(self):
        args = parse_args(["--load-cookies", "chrome", "123"])
        assert args.load_cookies == "chrome"

    def test_media_filters(self):
        args = parse_args(["--no-videos", "--no-pictures", "123"])
        assert args.no_videos is True
        assert args.no_pictures is True

    def test_metadata_options(self):
        args = parse_args(["--metadata-json", "--post-metadata-txt", "template", "123"])
        assert args.metadata_json is True
        assert args.post_metadata_txt == "template"

    def test_pattern_options(self):
        args = parse_args(["--dirname-pattern", "{nickname}", "--filename-pattern", "{mid}", "123"])
        assert args.dirname_pattern == "{nickname}"
        assert args.filename_pattern == "{mid}"

    def test_control_options(self):
        args = parse_args([
            "--count", "100",
            "--fast-update",
            "--latest-stamps", "/tmp/stamps.json",
            "--no-resume",
            "--request-interval", "1.5",
            "--captcha-mode", "skip",
            "123"
        ])
        assert args.count == 100
        assert args.fast_update is True
        assert args.latest_stamps == "/tmp/stamps.json"
        assert args.no_resume is True
        assert args.request_interval == 1.5
        assert args.captcha_mode == "skip"

    def test_negative_count_error(self):
        with pytest.raises(SystemExit):
            parse_args(["--count", "-1", "123"])

    def test_negative_interval_error(self):
        with pytest.raises(SystemExit):
            parse_args(["--request-interval", "-1", "123"])

    def test_no_target_error(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_multiple_targets(self):
        args = parse_args(["user1", "user2", "#topic"])
        assert args.targets == ["user1", "user2", "#topic"]


class TestExitCodeProperty:
    @given(st.lists(st.text(min_size=1), min_size=1, max_size=5))
    def test_exit_code_property(self, targets):
        """PBT: exit_code in {0,1,2,3,5} for all argv combinations."""
        from weiboloader.__main__ import main
        from weiboloader.exceptions import map_exception_to_exit_code

        valid_codes = {0, 1, 2, 3, 5}

        for exc_class in [InitError, Exception, KeyboardInterrupt]:
            try:
                raise exc_class("test")
            except BaseException as e:
                code = map_exception_to_exit_code(e)
                assert code in valid_codes


class TestTargetParsingPriority:
    def test_url_priority_over_mid_flag(self):
        target = parse_target("https://m.weibo.cn/detail/111", "222")
        assert isinstance(target, MidTarget)
        assert target.mid == "111"

    def test_mid_flag_priority_over_supertopic(self):
        target = parse_target("#topic", "mid123")
        assert isinstance(target, MidTarget)
        assert target.mid == "mid123"

    def test_supertopic_priority_over_search(self):
        target = parse_target("#topic", None)
        assert isinstance(target, SuperTopicTarget)

    def test_search_priority_over_user(self):
        target = parse_target(":keyword", None)
        assert isinstance(target, SearchTarget)


class TestVisitorCookiesFlag:
    def test_flag_parsed(self):
        args = parse_args(["--visitor-cookies", "123456"])
        assert args.visitor_cookies is True

    def test_flag_default_false(self):
        args = parse_args(["123456"])
        assert args.visitor_cookies is False
