"""한경 컨센서스 수집 — robots.txt(Disallow: /)로 비활성화됨.

확인 결과 https://consensus.hankyung.com/robots.txt 가 모든 User-Agent에 대해
전체 경로 자동 수집을 금지(Disallow: /)하고 있다. PRD의 'robots/이용약관 준수'
원칙에 따라 이 소스는 자동 크롤링하지 않는다.

파이프라인은 뉴스만으로 정상 동작하도록 설계되어 있으므로, 이 함수는 항상
빈 리스트를 반환한다. config/settings.yaml에서도 sources.consensus: false 이다.

추후 한경의 공식 RSS/API 등 '허용된' 경로가 확보되면 이 모듈에서 구현한다
(그때 robots/이용약관을 다시 확인하고, beautifulsoup4 등 파서를 추가).
"""
from __future__ import annotations

import logging
from typing import List

from .models import Article

logger = logging.getLogger(__name__)

ROBOTS_URL = "https://consensus.hankyung.com/robots.txt"


def fetch_consensus(
    keywords: List[str],
    *,
    max_results: int = 30,
    window_days: int = 7,
) -> List[Article]:
    """robots.txt Disallow:/ 로 비활성화. 항상 빈 리스트(뉴스만으로 동작)."""
    logger.info(
        "컨센서스 소스 비활성화 — %s 가 Disallow: / 로 자동수집 금지. 건너뜀.",
        ROBOTS_URL,
    )
    return []
