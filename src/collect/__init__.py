"""M1 수집 모듈: 네이버 뉴스 + 한경 컨센서스."""
from .consensus import fetch_consensus
from .models import Article, RawArticles, dedup_and_sort
from .naver_news import fetch_naver_news

__all__ = [
    "Article",
    "RawArticles",
    "dedup_and_sort",
    "fetch_naver_news",
    "fetch_consensus",
]
