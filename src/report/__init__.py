"""M5 report — enriched.json → HTML/Markdown 주간 리포트.

입력:  data/enriched.json   (M4 산출)
출력:  reports/{scan_date}/report.html, report.md

- 리포트 상단·하단에 '검증된 스크리닝 후보(가설)이며 투자 추천이 아님' 고지 필수
  (PRD 상품 원칙 — `render.DISCLAIMER`, 설정으로 끌 수 없음).
- 출력 폴더는 `scan_date` 기준이라 재실행해도 같은 날짜 폴더를 덮어쓴다(재현성).
- `run_report`는 enriched.json만 있으면 단독 재실행 가능하다(독립 모듈 규칙).
- 렌더링 실패는 `ReportError`로 올리고, 파이프라인이 로그-스킵한다(NF4).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .render import build_context, render_html, render_markdown

__all__ = [
    "run_report",
    "ReportResult",
    "ReportError",
    "load_enriched",
    "REPORTS_DIR",
]

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "reports"


class ReportError(RuntimeError):
    """M5 report 단계 치명적 실패(템플릿 로드/렌더 불가 등)."""


@dataclass
class ReportResult:
    out_dir: Path
    files: List[Path] = field(default_factory=list)
    context: dict = field(default_factory=dict)


def run_report(
    enriched: dict,
    *,
    settings: Optional[dict] = None,
    out_dir: Optional[Path] = None,
    generated_at: Optional[str] = None,
    formats: Optional[List[str]] = None,
) -> ReportResult:
    """enriched dict → reports/{scan_date}/ 에 HTML·Markdown 생성.

    formats 미지정 시 `settings.report.formats`(기본 html+markdown)를 따른다.
    generated_at 미지정 시 현재 시각(YYYY-MM-DD HH:MM)을 기록한다.
    """
    settings = settings or {}
    report_cfg = settings.get("report", {}) or {}
    formats = formats or report_cfg.get("formats", ["html", "markdown"])

    if generated_at is None:
        from datetime import datetime

        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    scan_date = enriched.get("scan_date") or generated_at[:10]
    out_dir = Path(out_dir) if out_dir is not None else REPORTS_DIR / scan_date
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        context = build_context(enriched, generated_at=generated_at, settings=settings)
        renderers = {
            "html": ("report.html", render_html),
            "markdown": ("report.md", render_markdown),
        }
        written: List[Path] = []
        for fmt in formats:
            spec = renderers.get(fmt)
            if spec is None:
                logger.warning("알 수 없는 리포트 포맷 무시: %s", fmt)
                continue
            filename, renderer = spec
            path = out_dir / filename
            path.write_text(renderer(context), encoding="utf-8")
            written.append(path)

        # 대시보드(주차별 열람)용 메타데이터. 본 리포트와 한 폴더에 두어
        # build_dashboard 가 주를 가로질러 모은다.
        summary_path = out_dir / "summary.json"
        summary_path.write_text(
            json.dumps(_summary(context), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(summary_path)
    except Exception as e:  # noqa: BLE001
        raise ReportError(f"리포트 렌더 실패: {e}") from e

    logger.info(
        "리포트 생성 완료: %s (포맷 %s · 테마 %d · 종목 %d)",
        out_dir, ", ".join(formats), context["n_themes"], context["n_stocks"],
    )
    return ReportResult(out_dir=out_dir, files=written, context=context)


def _summary(context: dict) -> dict:
    """대시보드 인덱스용 주별 요약 메타(작고 안정적인 필드만)."""
    return {
        "scan_date": context.get("scan_date", ""),
        "generated_at": context.get("generated_at", ""),
        "window_days": context.get("window_days", 7),
        "n_themes": context.get("n_themes", 0),
        "n_stocks": context.get("n_stocks", 0),
        "themes": [
            {
                "keyword": t.get("keyword", ""),
                "category": t.get("category", ""),
                "top": [s.get("name", "") for s in t.get("stocks", [])[:3]],
            }
            for t in context.get("themes", [])
        ],
    }


def load_enriched(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
