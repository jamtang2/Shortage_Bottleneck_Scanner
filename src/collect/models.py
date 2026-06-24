"""수집 결과 데이터 스키마 (M1).

`data/raw_articles.json` 데이터 계약을 정의한다. 다운스트림 M2(extract)가
이 파일을 입력으로 받는다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List

# 허용 source 값: "naver_news" | "consensus"


@dataclass
class Article:
    source: str
    keyword: str
    title: str
    summary: str
    url: str
    date: str  # "YYYY-MM-DD"
    publisher: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RawArticles:
    window_days: int
    keywords: List[str]
    articles: List[Article] = field(default_factory=list)
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "collected_at": self.collected_at,
            "window_days": self.window_days,
            "keywords": self.keywords,
            "articles": [a.to_dict() for a in self.articles],
        }


def dedup_and_sort(articles: List[Article]) -> List[Article]:
    """동일 url 기준 dedup 후 date 내림차순 정렬."""
    seen = set()
    unique: List[Article] = []
    for a in articles:
        if a.url in seen:
            continue
        seen.add(a.url)
        unique.append(a)
    unique.sort(key=lambda a: a.date, reverse=True)
    return unique
