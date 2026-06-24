"""오케스트레이터.

현재 마일스톤(M1)에서는 collect 단계만 실행해 data/raw_articles.json을 생성한다.
이후 단계(M2~M5)는 아래 TODO로만 남겨둔다.

실행: python -m src.pipeline
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.collect import (
    RawArticles,
    dedup_and_sort,
    fetch_consensus,
    fetch_naver_news,
)
from src.enrich import EnrichError, run_enrich, write_enriched
from src.extract import ExtractError, run_extract, write_themes
from src.notify import build_notify_context, run_notify
from src.propose import ProposeError, run_propose, write_candidates
from src.report import ReportError, run_report

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "settings.yaml"
DATA_DIR = ROOT / "data"
RAW_ARTICLES_PATH = DATA_DIR / "raw_articles.json"
THEMES_PATH = DATA_DIR / "themes.json"
CANDIDATES_PATH = DATA_DIR / "candidates.json"
ENRICHED_PATH = DATA_DIR / "enriched.json"
REPORTS_DIR = ROOT / "reports"


def load_settings(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run_collect(settings: dict) -> RawArticles:
    keywords = settings.get("keywords", [])
    window_days = settings.get("window_days", 7)
    max_results = settings.get("max_results_per_query", 30)
    sources = settings.get("sources", {}) or {}

    articles = []
    if sources.get("naver_news", True):
        articles += fetch_naver_news(
            keywords, window_days=window_days, max_results=max_results
        )
    if sources.get("consensus", True):
        articles += fetch_consensus(
            keywords, max_results=max_results, window_days=window_days
        )

    articles = dedup_and_sort(articles)
    return RawArticles(window_days=window_days, keywords=keywords, articles=articles)


def write_raw_articles(raw: RawArticles, path: Path = RAW_ARTICLES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def run_extract_stage(
    settings: dict,
    raw_path: Path = RAW_ARTICLES_PATH,
    out_path: Path = THEMES_PATH,
) -> bool:
    """raw_articles.json → themes.json. 실패해도 파이프라인을 중단하지 않는다(NF4)."""
    if not raw_path.exists():
        logger.warning("M2 건너뜀: %s 가 없습니다(먼저 collect 실행).", raw_path)
        return False
    with open(raw_path, "r", encoding="utf-8") as f:
        raw_articles = json.load(f)

    try:
        result = run_extract(raw_articles, settings=settings)
    except ExtractError as e:
        logger.error("M2 extract 실패 — 단계 건너뜀: %s", e)
        return False

    write_themes(result, out_path)
    logger.info("themes.json 저장 완료: %s (테마 %d개)", out_path, len(result.themes))
    return True


def run_propose_stage(
    settings: dict,
    themes_path: Path = THEMES_PATH,
    out_path: Path = CANDIDATES_PATH,
) -> bool:
    """themes.json → candidates.json. 실패해도 파이프라인을 중단하지 않는다(NF4)."""
    if not themes_path.exists():
        logger.warning("M3 건너뜀: %s 가 없습니다(먼저 extract 실행).", themes_path)
        return False
    with open(themes_path, "r", encoding="utf-8") as f:
        themes = json.load(f)

    try:
        result = run_propose(themes, settings=settings)
    except ProposeError as e:
        logger.error("M3 propose 실패 — 단계 건너뜀: %s", e)
        return False

    write_candidates(result, out_path)
    logger.info(
        "candidates.json 저장 완료: %s (후보 %d종목, 드롭 %d건)",
        out_path, len(result.candidates), len(result.dropped),
    )
    return True


def run_enrich_stage(
    settings: dict,
    candidates_path: Path = CANDIDATES_PATH,
    out_path: Path = ENRICHED_PATH,
) -> bool:
    """candidates.json → enriched.json. 실패해도 파이프라인을 중단하지 않는다(NF4)."""
    if not candidates_path.exists():
        logger.warning("M4 건너뜀: %s 가 없습니다(먼저 propose 실행).", candidates_path)
        return False
    with open(candidates_path, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    try:
        result = run_enrich(candidates, settings=settings)
    except EnrichError as e:
        logger.error("M4 enrich 실패 — 단계 건너뜀: %s", e)
        return False

    write_enriched(result, out_path)
    logger.info(
        "enriched.json 저장 완료: %s (종목 %d개)", out_path, len(result.enriched)
    )
    return True


def run_report_stage(
    settings: dict,
    enriched_path: Path = ENRICHED_PATH,
    out_dir: Path | None = None,
) -> bool:
    """enriched.json → reports/{scan_date}/. 실패해도 파이프라인을 중단하지 않는다(NF4)."""
    if not enriched_path.exists():
        logger.warning("M5 건너뜀: %s 가 없습니다(먼저 enrich 실행).", enriched_path)
        return False
    with open(enriched_path, "r", encoding="utf-8") as f:
        enriched = json.load(f)

    try:
        result = run_report(enriched, settings=settings, out_dir=out_dir)
    except ReportError as e:
        logger.error("M5 report 실패 — 단계 건너뜀: %s", e)
        return False

    logger.info(
        "리포트 저장 완료: %s (%s)",
        result.out_dir, ", ".join(p.name for p in result.files),
    )
    return True


def run_notify_stage(
    settings: dict,
    enriched_path: Path = ENRICHED_PATH,
    reports_dir: Path = REPORTS_DIR,
) -> bool:
    """enriched.json + 리포트 파일 → 텔레그램/이메일 발송. 실패해도 중단하지 않는다(NF4).

    enriched.json 만 있으면 요약을 재구성해 단독 재실행 가능(모듈 독립).
    """
    if not enriched_path.exists():
        logger.warning("M7 건너뜀: %s 가 없습니다(먼저 enrich/report 실행).", enriched_path)
        return False
    with open(enriched_path, "r", encoding="utf-8") as f:
        enriched = json.load(f)

    from datetime import datetime

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    context = build_notify_context(enriched, generated_at=generated_at, settings=settings)

    scan_date = enriched.get("scan_date") or generated_at[:10]
    out_dir = reports_dir / scan_date
    report_files = [p for p in (out_dir / "report.html", out_dir / "report.md") if p.exists()]

    result = run_notify(context, settings=settings, report_files=report_files)
    if result.sent:
        logger.info("알림 발송 완료: %s", ", ".join(result.sent))
    if result.errors:
        logger.warning("알림 발송 실패: %s", result.errors)
    return bool(result.sent)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    settings = load_settings()

    # M1 collect — Naver News → raw_articles.json
    raw = run_collect(settings)
    path = write_raw_articles(raw)
    logger.info("raw_articles.json 저장 완료: %s (기사 %d건)", path, len(raw.articles))

    # M2 extract — raw_articles.json → themes.json (Claude 테마 추출)
    run_extract_stage(settings)

    # M3 propose — themes.json → candidates.json (멀티 LLM 제안 + KRX 검증)
    run_propose_stage(settings)

    # M4 enrich — candidates.json → enriched.json (FDR 시총 + DART 매출/PER)
    run_enrich_stage(settings)

    # M5 report — enriched.json → reports/{scan_date}/ (Jinja2 HTML + Markdown)
    run_report_stage(settings)

    # M7 notify — 리포트 요약을 텔레그램/이메일로 발송(선택; 키·설정 없으면 생략)
    run_notify_stage(settings)


if __name__ == "__main__":
    main()
