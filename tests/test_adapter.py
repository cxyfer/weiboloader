from datetime import datetime, timedelta, timezone
import pytest
from weiboloader.adapter import parse_weibo_datetime, parse_user_info, parse_post, CST
from weiboloader.exceptions import APISchemaError

def test_parse_weibo_datetime_standard():
    raw = "Mon Aug 13 10:00:00 +0800 2018"
    dt = parse_weibo_datetime(raw)
    assert dt == datetime(2018, 8, 13, 10, 0, 0, tzinfo=CST)

def test_parse_weibo_datetime_relative():
    now = datetime(2024, 5, 1, 12, 0, 0, tzinfo=CST)
    
    # 10分鐘前
    dt = parse_weibo_datetime("10分鐘前", now=now)
    assert dt == datetime(2024, 5, 1, 11, 50, 0, tzinfo=CST)
    
    # 昨天 10:30
    dt = parse_weibo_datetime("昨天 10:30", now=now)
    assert dt == datetime(2024, 4, 30, 10, 30, 0, tzinfo=CST)

def test_parse_weibo_datetime_short():
    now = datetime(2024, 5, 1, 12, 0, 0, tzinfo=CST)
    dt = parse_weibo_datetime("04-20", now=now)
    assert dt == datetime(2024, 4, 20, 0, 0, 0, tzinfo=CST)

def test_parse_weibo_datetime_full():
    dt = parse_weibo_datetime("2023-12-31")
    assert dt == datetime(2023, 12, 31, 0, 0, 0, tzinfo=CST)

def test_parse_user_info():
    raw = {"id": 12345, "screen_name": "test_user", "avatar_large": "http://avatar"}
    user = parse_user_info(raw)
    assert user.uid == "12345"
    assert user.nickname == "test_user"
    assert user.avatar == "http://avatar"

def test_parse_post_with_media():
    raw_card = {
        "mblog": {
            "mid": "5000000000",
            "bid": "O123456",
            "text": "Hello world",
            "created_at": "Mon Aug 13 10:00:00 +0800 2018",
            "pics": [
                {"large": {"url": "http://pic1_large"}, "url": "http://pic1_small"}
            ],
            "page_info": {
                "type": "video",
                "media_info": {
                    "stream_url_hd": "http://video_hd",
                    "stream_url": "http://video_sd"
                }
            }
        }
    }
    post = parse_post(raw_card)
    assert post.mid == "5000000000"
    assert len(post.media_items) == 2
    assert post.media_items[0].media_type == "picture"
    assert post.media_items[0].url == "http://pic1_large"
    assert post.media_items[1].media_type == "video"
    assert post.media_items[1].url == "http://video_hd"
