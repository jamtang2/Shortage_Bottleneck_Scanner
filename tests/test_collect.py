"""M1 수집 모듈 단위 테스트.

외부 API/네트워크를 모킹하므로 실제 키 없이도 통과해야 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

from src.collect import dedup_and_sort
from src.collect.models import Article, RawArticles
from src.collect import naver_news
from src.collect import consensus


# --- HTML 클린업 -------------------------------------------------------------

def test_clean_html_strips_tags_and_entities():
    assert naver_news.clean_html("<b>삼성</b>전기 &amp; MLCC &quot;쇼티지&quot;") == (
        '삼성전기 & MLCC "쇼티지"'
    )
    assert naver_news.clean_html(None) == ""
    assert naver_news.clean_html("") == ""


# --- pubDate 파싱 & window 필터 ---------------------------------------------

def test_parse_pubdate_rfc822():
    dt = naver_news.parse_pubdate("Sat, 13 Jun 2026 09:00:00 +0900")
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 6, 13)


def test_parse_pubdate_invalid():
    assert naver_news.parse_pubdate("not-a-date") is None
    assert naver_news.parse_pubdate(None) is None


def test_within_window_boundaries():
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    recent = datetime(2026, 6, 13, tzinfo=timezone.utc)
    old = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert naver_news._within_window(recent, 7, now) is True
    assert naver_news._within_window(old, 7, now) is False
    assert naver_news._within_window(None, 7, now) is False


# --- 네이버 뉴스 수집 (모킹) -------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_naver_news_parses_filters_and_schema():
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    payload = {
        "items": [
            {
                "title": "<b>삼성전기</b> MLCC 쇼티지",
                "description": "AI 서버용 고용량 MLCC &quot;공급부족&quot;",
                "originallink": "https://news.example.com/a",
                "link": "https://n.news.naver.com/a",
                "pubDate": "Sat, 13 Jun 2026 09:00:00 +0900",
            },
            {  # window 밖 — 제외돼야 함
                "title": "오래된 기사",
                "description": "...",
                "originallink": "https://news.example.com/old",
                "link": "https://n.news.naver.com/old",
                "pubDate": "Mon, 01 Jun 2026 09:00:00 +0900",
            },
        ]
    }

    with mock.patch.object(naver_news, "get_with_retry", return_value=_FakeResp(payload)):
        articles = naver_news.fetch_naver_news(
            ["쇼티지"],
            window_days=7,
            max_results=30,
            client_id="id",
            client_secret="secret",
            now=now,
        )

    assert len(articles) == 1
    a = articles[0]
    assert a.source == "naver_news"
    assert a.keyword == "쇼티지"
    assert a.title == "삼성전기 MLCC 쇼티지"
    assert a.summary == 'AI 서버용 고용량 MLCC "공급부족"'
    assert a.url == "https://news.example.com/a"
    assert a.date == "2026-06-13"
    assert a.publisher == "news.example.com"


def test_fetch_naver_news_without_keys_returns_empty(monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    assert naver_news.fetch_naver_news(["쇼티지"]) == []


def test_fetch_naver_news_request_failure_is_isolated():
    # get_with_retry가 None을 반환(최종 실패)해도 크래시 없이 빈 리스트.
    with mock.patch.object(naver_news, "get_with_retry", return_value=None):
        assert naver_news.fetch_naver_news(["쇼티지"], client_id="id", client_secret="s") == []


# --- 컨센서스 수집 (robots.txt로 비활성화) -----------------------------------

def test_fetch_consensus_disabled_returns_empty():
    # 한경 컨센서스 robots.txt(Disallow: /)로 자동수집 비활성화 → 항상 빈 리스트.
    assert consensus.fetch_consensus(["공급부족", "병목"], max_results=30) == []


# --- dedup / sort / 스키마 ---------------------------------------------------

def test_dedup_and_sort():
    arts = [
        Article("naver_news", "쇼티지", "A", "", "http://u/1", "2026-06-10", ""),
        Article("naver_news", "쇼티지", "A-dup", "", "http://u/1", "2026-06-11", ""),
        Article("consensus", "병목", "B", "", "http://u/2", "2026-06-14", ""),
    ]
    out = dedup_and_sort(arts)
    assert [a.url for a in out] == ["http://u/2", "http://u/1"]  # date desc, url dedup
    assert out[1].title == "A"  # 첫 등장 보존


def test_raw_articles_schema():
    raw = RawArticles(
        window_days=7,
        keywords=["쇼티지"],
        articles=[Article("naver_news", "쇼티지", "T", "S", "http://u", "2026-06-13", "p")],
    )
    d = raw.to_dict()
    assert set(d) == {"collected_at", "window_days", "keywords", "articles"}
    assert set(d["articles"][0]) == {
        "source", "keyword", "title", "summary", "url", "date", "publisher",
    }
