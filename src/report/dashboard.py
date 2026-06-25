"""주차별 리포트 열람용 정적 대시보드(백로그 — 웹 대시보드).

`reports/{scan_date}/` 폴더들을 가로질러 모아 `reports/index.html` 한 장을
만든다. 별도 서버 없이 GitHub Pages(또는 로컬 파일)로 열람할 수 있다.

각 주의 통계는 M5 가 함께 남기는 `summary.json` 에서 읽고, 없으면(과거 리포트)
폴더명(날짜)과 report.html 링크만으로 degrade 해 목록에 싣는다. 면책 고지는 상품
원칙이라 대시보드에도 항상 들어간다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from .render import DISCLAIMER, TEMPLATES_DIR

logger = logging.getLogger(__name__)


def _load_week(folder: Path) -> Optional[dict]:
    """리포트 폴더 1개 → 인덱스 항목. report.html 이 없으면 제외(None)."""
    report_html = folder / "report.html"
    if not report_html.exists():
        return None

    meta = {
        "scan_date": folder.name,
        "n_themes": None,
        "n_stocks": None,
        "generated_at": "",
        "themes": [],
        "href": f"{folder.name}/report.html",
        "has_md": (folder / "report.md").exists(),
    }
    summary = folder / "summary.json"
    if summary.exists():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            meta["scan_date"] = data.get("scan_date") or folder.name
            meta["n_themes"] = data.get("n_themes")
            meta["n_stocks"] = data.get("n_stocks")
            meta["generated_at"] = data.get("generated_at", "")
            meta["themes"] = data.get("themes", [])
        except Exception as e:  # noqa: BLE001 — 손상된 summary 는 무시하고 링크만
            logger.debug("[dashboard] summary.json 파싱 실패(%s): %s", folder, e)
    return meta


def collect_weeks(reports_dir: Path) -> List[dict]:
    """reports/ 하위의 주차 폴더를 모아 scan_date 내림차순으로 정렬."""
    if not reports_dir.exists():
        return []
    weeks = []
    for child in reports_dir.iterdir():
        if child.is_dir():
            item = _load_week(child)
            if item is not None:
                weeks.append(item)
    weeks.sort(key=lambda w: w["scan_date"], reverse=True)
    return weeks


def _env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "htm", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_dashboard(weeks: List[dict], *, settings: Optional[dict] = None) -> str:
    settings = settings or {}
    report_cfg = settings.get("report", {}) or {}
    title = report_cfg.get("title", "쇼티지·병목 수혜주 스캐너")
    template = _env().get_template("dashboard.html.j2")
    return template.render(
        title=title,
        weeks=weeks,
        n_weeks=len(weeks),
        disclaimer=DISCLAIMER,
        latest=weeks[0] if weeks else None,
    )


def build_dashboard(reports_dir: Path, *, settings: Optional[dict] = None) -> Optional[Path]:
    """reports/index.html 생성. 주차 폴더가 하나도 없으면 None."""
    weeks = collect_weeks(Path(reports_dir))
    if not weeks:
        logger.info("[dashboard] 리포트 폴더가 없어 대시보드 생략.")
        return None
    html = render_dashboard(weeks, settings=settings)
    out_path = Path(reports_dir) / "index.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info("대시보드 생성 완료: %s (주차 %d개)", out_path, len(weeks))
    return out_path
