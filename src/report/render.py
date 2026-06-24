"""M5 report — enriched.json → HTML/Markdown 리포트 렌더링.

후보 종목을 **테마(keyword)별로 묶어** 표로 보여준다. 각 테마 안에서는
합의 점수(agreement_score)↓ → 관련도(relevance)↓ → 시가총액↓ 순으로 정렬한다.

면책 고지(DISCLAIMER)는 PRD의 상품 원칙이라 **항상** 상단에 들어가며 설정으로
끌 수 없다 — 출력은 검증된 스크리닝 가설이지 투자 추천이 아니다.

수치 포맷은 두 필터로 통일한다: 억원→`eok`(1조 이상은 '조'), PER→`per`.
값이 없으면(None) 'N/A'. 기준일(data_asof)은 시총(거래일)과 재무(보고서 분기말)가
다르므로 종목 집합에서 distinct 날짜를 모아 상단 메타에 함께 표기한다(NF7).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# 면책 고지 — PRD 상품 원칙: 모든 리포트에 항상 포함, 설정으로 제거 불가.
DISCLAIMER = (
    "본 리포트는 뉴스·브로커리지 컨센서스에서 자동 추출한 "
    "‘검증된 스크리닝 후보(가설)’이며, 투자 추천이나 자문이 아닙니다. "
    "수치는 공개 데이터(KRX·OpenDART) 기준이며 지연·오류가 있을 수 있습니다. "
    "모든 투자 판단과 그 책임은 이용자 본인에게 있습니다."
)

# relevance 정렬/표기
_RELEVANCE_ORDER = {"high": 0, "medium": 1, "low": 2}
_RELEVANCE_LABEL = {"high": "높음", "medium": "중간", "low": "낮음"}


def format_eok(value: Optional[float]) -> str:
    """억원 수치 → 사람이 읽기 쉬운 문자열. 1조(=1만억) 이상은 '조' 단위."""
    if value is None:
        return "N/A"
    if abs(value) >= 10_000:
        return f"{value / 10_000:,.1f}조원"
    return f"{value:,.0f}억원"


def format_per(value: Optional[float]) -> str:
    """PER → 'NN.N배'. None(적자/결측)은 'N/A'."""
    return "N/A" if value is None else f"{value:,.1f}배"


def _relevance_label(relevance: Optional[str]) -> str:
    return _RELEVANCE_LABEL.get(relevance or "", relevance or "")


def _sort_key(stock: dict):
    return (
        -int(stock.get("agreement_score") or 0),
        _RELEVANCE_ORDER.get(stock.get("relevance"), 9),
        -(stock.get("market_cap_eokwon") or 0),
    )


def _asof(stocks, field: str):
    """종목 집합에서 특정 필드의 기준일들을 distinct·정렬해 ', '로 결합."""
    dates = {
        (s.get("data_asof") or {}).get(field)
        for s in stocks
        if (s.get("data_asof") or {}).get(field)
    }
    return ", ".join(sorted(dates))


def build_context(
    enriched: dict, *, generated_at: str, settings: Optional[dict] = None
) -> dict:
    """enriched dict → 템플릿 컨텍스트. 테마별 묶음·정렬·메타 집계."""
    settings = settings or {}
    report_cfg = settings.get("report", {}) or {}
    title = report_cfg.get("title", "쇼티지·병목 수혜주 스캐너 주간 리포트")

    stocks = enriched.get("enriched") or []

    # 테마(keyword)별 묶음 — 첫 등장 순서(=추출 순서) 유지
    themes: list[dict] = []
    index: dict[str, dict] = {}
    for s in stocks:
        kw = s.get("keyword", "")
        bucket = index.get(kw)
        if bucket is None:
            bucket = {"keyword": kw, "category": s.get("category", ""), "stocks": []}
            index[kw] = bucket
            themes.append(bucket)
        bucket["stocks"].append(s)
    for t in themes:
        t["stocks"].sort(key=_sort_key)

    return {
        "title": title,
        "scan_date": enriched.get("scan_date", ""),
        "window_days": enriched.get("window_days", 7),
        "generated_at": generated_at,
        "disclaimer": DISCLAIMER,
        "themes": themes,
        "n_themes": len(themes),
        "n_stocks": len(stocks),
        "n_dropped": len(enriched.get("dropped") or []),
        "asof_market_cap": _asof(stocks, "market_cap"),
        "asof_financials": _asof(stocks, "revenue_ttm"),
    }


def _env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "htm", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["eok"] = format_eok
    env.filters["per"] = format_per
    env.filters["relevance_label"] = _relevance_label
    return env


def render_html(context: dict) -> str:
    return _env().get_template("report.html.j2").render(**context)


def render_markdown(context: dict) -> str:
    return _env().get_template("report.md.j2").render(**context)
