"""M1: 네이버 뉴스 검색 API 수집.

https://openapi.naver.com/v1/search/news.json
키워드별로 조회 → window_days 이내 기사만 필터 → HTML 태그/엔티티 정리.
"""
from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from urllib.parse import urlparse

from .http import get_with_retry
from .models import Article

logger = logging.getLogger(__name__)

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")


def clean_html(text: Optional[str]) -> str:
    """HTML 태그(<b> 등)를 제거하고 엔티티(&quot; 등)를 디코드."""
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub("", text)).strip()


def parse_pubdate(raw: Optional[str]) -> Optional[datetime]:
    """네이버 pubDate(RFC822, 예: 'Mon, 13 Jun 2026 09:00:00 +0900') 파싱."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def _within_window(dt: Optional[datetime], window_days: int, now: datetime) -> bool:
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return timedelta(0) <= (now - dt) < timedelta(days=window_days)


def _publisher_from(url: str) -> str:
    try:
        return urlparse(url).netloc
    except ValueError:
        return ""


def fetch_naver_news(
    keywords: List[str],
    *,
    window_days: int = 7,
    max_results: int = 30,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[Article]:
    """키워드 목록을 네이버 뉴스 API로 조회해 Article 리스트 반환.

    키가 없으면 경고 후 빈 리스트(파이프라인은 다른 소스로 계속 진행).
    """
    client_id = client_id or os.getenv("NAVER_CLIENT_ID")
    client_secret = client_secret or os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.warning("NAVER_CLIENT_ID/SECRET 미설정 — 네이버 뉴스 수집 건너뜀")
        return []

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    now = now or datetime.now(timezone.utc)
    out: List[Article] = []

    for kw in keywords:
        resp = get_with_retry(
            NAVER_NEWS_URL,
            headers=headers,
            params={"query": kw, "display": max_results, "sort": "date"},
        )
        if resp is None:
            continue
        try:
            items = resp.json().get("items", [])
        except ValueError:
            logger.warning("네이버 응답 JSON 파싱 실패 (keyword=%s)", kw)
            continue

        for item in items:
            dt = parse_pubdate(item.get("pubDate"))
            if not _within_window(dt, window_days, now):
                continue
            url = item.get("originallink") or item.get("link", "")
            out.append(
                Article(
                    source="naver_news",
                    keyword=kw,
                    title=clean_html(item.get("title")),
                    summary=clean_html(item.get("description")),
                    url=url,
                    date=dt.strftime("%Y-%m-%d"),
                    publisher=_publisher_from(url),
                )
            )

    return out
