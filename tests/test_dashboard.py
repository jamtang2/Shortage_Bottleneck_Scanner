"""대시보드(주차별 열람) 단위 테스트.

임시 reports 디렉터리에 가짜 주차 폴더를 만들어, 정렬·링크·면책 고지·summary
없는 주의 degrade 를 검증한다. Jinja2 렌더는 실제로 돌린다.
"""
from __future__ import annotations

import json

from src.report.dashboard import build_dashboard, collect_weeks
from src.report.render import DISCLAIMER


def _week(reports_dir, date, *, summary=True, n_themes=3, n_stocks=15):
    d = reports_dir / date
    d.mkdir(parents=True)
    (d / "report.html").write_text(f"<html>{date}</html>", encoding="utf-8")
    (d / "report.md").write_text(f"# {date}", encoding="utf-8")
    if summary:
        (d / "summary.json").write_text(json.dumps({
            "scan_date": date, "generated_at": f"{date} 09:00",
            "n_themes": n_themes, "n_stocks": n_stocks,
            "themes": [{"keyword": f"테마{i}", "category": "C",
                        "top": ["가", "나"]} for i in range(n_themes)],
        }, ensure_ascii=False), encoding="utf-8")
    return d


def test_collect_weeks_sorted_desc_and_skips_empty(tmp_path):
    _week(tmp_path, "2026-06-16")
    _week(tmp_path, "2026-06-24")
    (tmp_path / "2026-06-30").mkdir()  # report.html 없음 → 제외
    weeks = collect_weeks(tmp_path)
    assert [w["scan_date"] for w in weeks] == ["2026-06-24", "2026-06-16"]


def test_build_dashboard_links_and_disclaimer(tmp_path):
    _week(tmp_path, "2026-06-16", n_themes=2, n_stocks=10)
    _week(tmp_path, "2026-06-24", n_themes=13, n_stocks=65)
    out = build_dashboard(tmp_path, settings={"report": {"title": "스캐너"}})
    assert out is not None and out.name == "index.html"
    html = out.read_text(encoding="utf-8")
    assert DISCLAIMER in html                       # 대시보드에도 면책 고지
    assert 'href="2026-06-24/report.html"' in html  # 주차 링크(상대경로)
    assert 'href="2026-06-16/report.html"' in html
    assert "테마 13 · 종목 65" in html              # summary 통계 노출
    assert "스캐너" in html                          # config 제목
    # 최신(2026-06-24)이 먼저 나온다
    assert html.index("2026-06-24") < html.index("2026-06-16")


def test_build_dashboard_degrades_without_summary(tmp_path):
    # summary.json 없는 과거 주 → 링크만으로 목록에 실린다(통계는 '리포트 보기')
    _week(tmp_path, "2026-05-01", summary=False)
    out = build_dashboard(tmp_path)
    html = out.read_text(encoding="utf-8")
    assert 'href="2026-05-01/report.html"' in html
    assert "리포트 보기" in html


def test_build_dashboard_none_when_empty(tmp_path):
    assert build_dashboard(tmp_path) is None         # 주차 폴더 없음
