"""M2 extract — 수집 기사에서 Claude로 테마/키워드 추출.

입력:  data/raw_articles.json   (M1 산출)
출력:  data/themes.json         (PRD §6 Step 1 스키마)

`run_extract`는 입력 JSON만 있으면 단독 재실행 가능하다(독립 모듈 규칙).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .claude_extract import extract_themes
from .models import Theme, ThemeSource, ThemesResult

__all__ = [
    "run_extract",
    "extract_themes",
    "Theme",
    "ThemeSource",
    "ThemesResult",
    "ExtractError",
]

logger = logging.getLogger(__name__)


class ExtractError(RuntimeError):
    """M2 추출 단계 실패(키 누락·호출 실패·파싱 실패 등)."""


def run_extract(
    raw_articles: dict,
    *,
    settings: Optional[dict] = None,
    api_key: Optional[str] = None,
) -> ThemesResult:
    """raw_articles dict → ThemesResult.

    api_key 미지정 시 환경변수 ANTHROPIC_API_KEY를 사용한다.
    키가 없으면 ExtractError를 올린다(상위 파이프라인이 격리 처리).
    """
    settings = settings or {}
    extract_cfg = settings.get("extract", {}) or {}
    model = extract_cfg.get("model", "claude-opus-4-8")
    max_articles = int(extract_cfg.get("max_articles", 200))
    max_tokens = int(extract_cfg.get("max_tokens", 8000))

    api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractError(
            "ANTHROPIC_API_KEY가 없습니다. .env에 키를 넣고 다시 실행하세요."
        )

    articles = raw_articles.get("articles") or []
    window_days = raw_articles.get("window_days", 7)
    # scan_date: 수집 시각(collected_at)의 날짜 부분, 없으면 빈 문자열.
    scan_date = (raw_articles.get("collected_at") or "")[:10]

    try:
        themes = extract_themes(
            articles,
            window_days=window_days,
            model=model,
            api_key=api_key,
            max_articles=max_articles,
            max_tokens=max_tokens,
        )
    except Exception as e:  # noqa: BLE001 — 단계 경계에서 묶어 올린다
        raise ExtractError(f"테마 추출 실패: {e}") from e

    logger.info("테마 %d개 추출 (기사 %d건 입력)", len(themes), len(articles))
    return ThemesResult(scan_date=scan_date, window_days=window_days, themes=themes)


def load_raw_articles(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_themes(result: ThemesResult, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
