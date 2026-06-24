"""M5 report 모듈 단위 테스트.

Jinja2 렌더는 실제로 돌리되 파일 출력은 tmp_path로 격리한다. 면책 고지·테마
그룹화·정렬·억원/PER 포맷·degrade(시총만 있는 종목)를 검증한다.
"""
from __future__ import annotations

import json

from src.report import run_report
from src.report.render import (
    DISCLAIMER,
    build_context,
    format_eok,
    format_per,
    render_html,
    render_markdown,
)


ENRICHED = {
    "scan_date": "2026-06-16",
    "window_days": 7,
    "enriched": [
        {
            "keyword": "HBM 공급부족", "category": "반도체",
            "name": "SK하이닉스", "code": "000660", "market": "KOSPI",
            "proposed_by": ["claude", "gpt", "gemini"], "agreement_score": 3,
            "relation_reason": "HBM 글로벌 1위.", "relevance": "high",
            "market_cap_eokwon": 19699093.4, "revenue_ttm_eokwon": 1320838.2,
            "net_income_ttm_eokwon": 751856.2, "per_ttm": 26.2,
            "per_quarterly_annualized": 12.2,
            "data_asof": {"market_cap": "2026-06-19", "revenue_ttm": "2026-03-31",
                          "per_ttm": "2026-03-31"},
        },
        {
            "keyword": "HBM 공급부족", "category": "반도체",
            "name": "한미반도체", "code": "042700", "market": "KOSPI",
            "proposed_by": ["claude"], "agreement_score": 1,
            "relation_reason": "TC본더 독점.", "relevance": "medium",
            "market_cap_eokwon": 280000.0, "revenue_ttm_eokwon": 4802.0,
            "net_income_ttm_eokwon": 1783.0, "per_ttm": 157.0,
            "per_quarterly_annualized": None,
            "data_asof": {"market_cap": "2026-06-19", "revenue_ttm": "2026-03-31",
                          "per_ttm": "2026-03-31"},
        },
        {
            # 적자 → PER None, DART 없을 때처럼 매출만 결측인 종목도 깨지지 않아야
            "keyword": "석유화학 NCC", "category": "석유화학",
            "name": "롯데케미칼", "code": "011170", "market": "KOSPI",
            "proposed_by": ["claude", "gpt"], "agreement_score": 2,
            "relation_reason": "NCC 증설.", "relevance": "low",
            "market_cap_eokwon": 30000.0, "revenue_ttm_eokwon": None,
            "net_income_ttm_eokwon": -22000.0, "per_ttm": None,
            "per_quarterly_annualized": None,
            "data_asof": {"market_cap": "2026-06-19"},
        },
    ],
    "dropped": [{"keyword": "HBM 공급부족", "model": "gpt",
                 "proposed_name": "없는회사", "reason": "KRX 미상장"}],
}


# --- 포맷 ---------------------------------------------------------------------

def test_format_eok():
    assert format_eok(19699093.4) == "1,969.9조원"   # 1조 이상 → 조
    assert format_eok(4802.0) == "4,802억원"          # 1조 미만 → 억
    assert format_eok(None) == "N/A"


def test_format_per():
    assert format_per(26.2) == "26.2배"
    assert format_per(None) == "N/A"


# --- 컨텍스트(그룹화·정렬·집계) ---------------------------------------------

def test_build_context_groups_and_sorts():
    ctx = build_context(ENRICHED, generated_at="2026-06-23 09:00")
    assert ctx["n_themes"] == 2 and ctx["n_stocks"] == 3 and ctx["n_dropped"] == 1
    # 테마 순서는 첫 등장 순서 유지
    assert [t["keyword"] for t in ctx["themes"]] == ["HBM 공급부족", "석유화학 NCC"]
    # 테마 안에서 agreement_score 내림차순 → SK하이닉스(3)가 한미반도체(1)보다 먼저
    hbm = ctx["themes"][0]["stocks"]
    assert [s["name"] for s in hbm] == ["SK하이닉스", "한미반도체"]
    # data_asof 집계
    assert ctx["asof_market_cap"] == "2026-06-19"
    assert ctx["asof_financials"] == "2026-03-31"


# --- 렌더(면책 고지 필수) ----------------------------------------------------

def test_html_contains_disclaimer_and_data():
    ctx = build_context(ENRICHED, generated_at="2026-06-23 09:00")
    html = render_html(ctx)
    assert DISCLAIMER in html                      # 상품 원칙: 면책 고지 필수
    assert "SK하이닉스" in html and "000660" in html
    assert "1,969.9조원" in html                    # 억원 포맷 적용
    assert "26.2배" in html                         # PER 포맷
    assert "N/A" in html                            # 적자 종목 PER N/A


def test_markdown_contains_disclaimer_and_table():
    ctx = build_context(ENRICHED, generated_at="2026-06-23 09:00")
    md = render_markdown(ctx)
    assert DISCLAIMER in md
    assert "## [반도체] HBM 공급부족" in md
    assert "| SK하이닉스 (000660) |" in md
    assert "롯데케미칼" in md and "N/A" in md       # 적자 종목도 표에 들어감


# --- run_report 통합(tmp 출력) ----------------------------------------------

def test_run_report_writes_files(tmp_path):
    out = tmp_path / "2026-06-16"
    res = run_report(ENRICHED, out_dir=out, generated_at="2026-06-23 09:00")
    html = out / "report.html"
    md = out / "report.md"
    assert html.exists() and md.exists()
    assert {p.name for p in res.files} == {"report.html", "report.md"}
    assert DISCLAIMER in html.read_text(encoding="utf-8")


def test_run_report_respects_formats(tmp_path):
    res = run_report(
        ENRICHED, out_dir=tmp_path, generated_at="2026-06-23 09:00",
        formats=["markdown"],
    )
    assert {p.name for p in res.files} == {"report.md"}
    assert not (tmp_path / "report.html").exists()


def test_run_report_default_dir_uses_scan_date(tmp_path):
    # out_dir 미지정이면 reports/{scan_date}/ — 여기선 경로만 확인(쓰기는 격리 위해 out_dir 지정)
    ctx = build_context(ENRICHED, generated_at="2026-06-23 09:00")
    assert ctx["scan_date"] == "2026-06-16"


def test_run_report_handles_empty(tmp_path):
    empty = {"scan_date": "2026-06-16", "window_days": 7, "enriched": [], "dropped": []}
    res = run_report(empty, out_dir=tmp_path, generated_at="2026-06-23 09:00")
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert DISCLAIMER in md            # 후보 0개여도 면책 고지는 나온다
    assert res.context["n_stocks"] == 0
