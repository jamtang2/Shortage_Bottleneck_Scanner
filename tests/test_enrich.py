"""M4 enrich 모듈 단위 테스트.

FDR 스냅샷과 DART 호출을 합성 객체로 주입하므로 실제 키·네트워크 없이 통과한다.
DART 롤링 TTM 공식·계정 선택·degrade 경로의 실제 로직을 검증한다.
"""
from __future__ import annotations

import pandas as pd

from src.enrich import _eok, _per, run_enrich
from src.enrich.dart_financials import (
    DartFinancials,
    _period_end,
    _pick_account,
    _rolling,
    _to_won,
)
from src.enrich.market import MarketData
from src.enrich.models import EnrichedStock


# --- 금액/날짜 파싱 ----------------------------------------------------------

def test_to_won_parsing():
    assert _to_won("1,234,567") == 1234567
    assert _to_won("-1,200") == -1200
    assert _to_won("") is None
    assert _to_won("-") is None
    assert _to_won(None) is None


def test_period_end_parsing():
    assert _period_end("2026.01.01 ~ 2026.03.31") == "2026-03-31"
    assert _period_end("2026.03.31") == "2026-03-31"
    assert _period_end(None) is None


def test_rolling_formula():
    # TTM = 올해누적 + 작년연간 − 작년동기누적
    assert _rolling(300, 1000, 250) == 1050
    assert _rolling(None, 1000, 250) is None
    assert _rolling(300, None, 250) is None


def test_per_and_eok():
    assert _per(1_000_000_000_000, 100_000_000_000) == 10.0  # 시총/순이익
    assert _per(1_000, 0) is None         # 순이익 0 → None
    assert _per(1_000, -50) is None       # 적자 → None
    assert _per(None, 100) is None
    assert _eok(150_000_000_000) == 1500.0  # 원 → 억원
    assert _eok(None) is None


# --- 계정 선택(연결 우선, 포괄손익 배제) -------------------------------------

def _df(rows):
    return pd.DataFrame(rows)


def test_pick_account_prefers_cfs_and_excludes_comprehensive():
    df = _df([
        {"account_nm": "당기순이익", "fs_div": "OFS", "thstrm_amount": "100",
         "frmtrm_amount": "90", "thstrm_dt": "2026.01.01 ~ 2026.03.31"},
        {"account_nm": "당기순이익", "fs_div": "CFS", "thstrm_amount": "200",
         "frmtrm_amount": "180", "thstrm_dt": "2026.01.01 ~ 2026.03.31"},
        {"account_nm": "총포괄손익", "fs_div": "CFS", "thstrm_amount": "999",
         "frmtrm_amount": "999", "thstrm_dt": "2026.01.01 ~ 2026.03.31"},
    ])
    row = _pick_account(df, ["당기순이익", "당기순이익(손실)"])
    assert row is not None
    assert _to_won(row["thstrm_amount"]) == 200  # CFS 우선


# --- 합성 DART --------------------------------------------------------------

class FakeDart:
    """(year, reprt_code) → finstate DataFrame 매핑을 흉내."""

    def __init__(self, table):
        self._table = table  # {(year, rc): list[rows]}

    def finstate(self, code, year, reprt_code):
        rows = self._table.get((int(year), str(reprt_code)))
        if rows is None:
            return pd.DataFrame()
        return pd.DataFrame(rows)


def _is_row(account, cur, prev, end):
    return {
        "account_nm": account, "fs_div": "CFS",
        "thstrm_amount": str(cur), "frmtrm_amount": str(prev),
        "thstrm_dt": f"2026.01.01 ~ {end}",
    }


def test_dart_ttm_interim_rolling():
    # 올해 Q1(누적3M): 매출 300/순이익 30, 전년 Q1: 250/25
    # 작년 연간(FY): 매출 1000/순이익 120
    # TTM 매출 = 300 + 1000 − 250 = 1050; 순이익 = 30 + 120 − 25 = 125
    table = {
        (2026, "11013"): [
            _is_row("매출액", 300, 250, "2026.03.31"),
            _is_row("당기순이익", 30, 25, "2026.03.31"),
        ],
        (2025, "11011"): [
            {"account_nm": "매출액", "fs_div": "CFS", "thstrm_amount": "1000",
             "frmtrm_amount": "950", "thstrm_dt": "2025.01.01 ~ 2025.12.31"},
            {"account_nm": "당기순이익", "fs_div": "CFS", "thstrm_amount": "120",
             "frmtrm_amount": "110", "thstrm_dt": "2025.01.01 ~ 2025.12.31"},
        ],
    }
    dart = DartFinancials(FakeDart(table))
    res = dart.revenue_income_ttm("000000", year=2026)
    assert res.revenue_ttm_won == 1050
    assert res.net_income_ttm_won == 125
    assert res.period_end == "2026-03-31"
    # Q1은 누적=단독분기이므로 최신분기 순이익 = 30
    assert res.latest_quarter_net_income_won == 30


def test_dart_ttm_annual_latest():
    # 올해 사업보고서가 이미 나온 경우: 그 자체가 TTM
    table = {
        (2026, "11011"): [
            _is_row("매출액", 2000, 1800, "2026.12.31"),
            _is_row("당기순이익", 250, 200, "2026.12.31"),
        ],
        (2026, "11014"): [  # 9M 누적(단독 Q4 = FY − 9M)
            _is_row("매출액", 1500, 1300, "2026.09.30"),
            _is_row("당기순이익", 180, 150, "2026.09.30"),
        ],
    }
    dart = DartFinancials(FakeDart(table))
    res = dart.revenue_income_ttm("000000", year=2026)
    assert res.revenue_ttm_won == 2000
    assert res.net_income_ttm_won == 250
    # 단독 Q4 = 250 − 180 = 70
    assert res.latest_quarter_net_income_won == 70


def test_dart_fallback_prior_year_when_no_current():
    # 올해 보고서 전무 → 작년 사업보고서를 TTM으로
    table = {
        (2025, "11011"): [
            _is_row("매출액", 800, 700, "2025.12.31"),
            _is_row("당기순이익", 90, 80, "2025.12.31"),
        ],
    }
    dart = DartFinancials(FakeDart(table))
    res = dart.revenue_income_ttm("000000", year=2026)
    assert res.revenue_ttm_won == 800
    assert res.net_income_ttm_won == 90


# --- run_enrich 통합(주입) ---------------------------------------------------

CANDIDATES = {
    "scan_date": "2026-06-16",
    "window_days": 7,
    "candidates": [
        {"keyword": "HBM", "category": "반도체", "name": "SK하이닉스",
         "code": "000660", "market": "KOSPI", "proposed_by": ["claude", "gpt"],
         "agreement_score": 2, "relation_reason": "HBM 수혜", "relevance": "high"},
    ],
    "dropped": [{"keyword": "HBM", "model": "gpt", "proposed_name": "없는회사",
                 "reason": "KRX 미상장/모호"}],
}


def _market():
    return MarketData({"000660": 1_000_000_000_000}, asof="2026-06-19")


def test_run_enrich_with_injected_market_and_dart():
    table = {
        (2026, "11013"): [
            _is_row("매출액", 300_000_000_000, 250_000_000_000, "2026.03.31"),
            _is_row("당기순이익", 30_000_000_000, 25_000_000_000, "2026.03.31"),
        ],
        (2025, "11011"): [
            {"account_nm": "매출액", "fs_div": "CFS",
             "thstrm_amount": "1000000000000", "frmtrm_amount": "950000000000",
             "thstrm_dt": "2025.01.01 ~ 2025.12.31"},
            {"account_nm": "당기순이익", "fs_div": "CFS",
             "thstrm_amount": "120000000000", "frmtrm_amount": "110000000000",
             "thstrm_dt": "2025.01.01 ~ 2025.12.31"},
        ],
    }
    dart = DartFinancials(FakeDart(table))
    res = run_enrich(CANDIDATES, market=_market(), dart=dart)
    assert len(res.enriched) == 1
    e = res.enriched[0]
    assert e.market_cap_eokwon == _eok(1_000_000_000_000)   # 10000.0 억원
    # TTM 순이익 = 30 + 120 − 25 = 125 (×1e9 원) → 1250 억원
    assert e.net_income_ttm_eokwon == 1250.0
    # PER = 시총 / 순이익TTM = 1e12 / 1.25e11 = 8.0
    assert e.per_ttm == 8.0
    # M3 필드 승계
    assert e.proposed_by == ["claude", "gpt"] and e.agreement_score == 2
    # NF7: 기준일 분리
    assert e.data_asof["market_cap"] == "2026-06-19"
    assert e.data_asof["per_ttm"] == "2026-03-31"
    # 드롭 통과
    assert res.dropped and res.dropped[0]["proposed_name"] == "없는회사"


def test_run_enrich_degrades_without_dart():
    res = run_enrich(CANDIDATES, market=_market(), dart=None,
                     settings={"enrich": {"use_dart": False}})
    e = res.enriched[0]
    assert e.market_cap_eokwon is not None     # 시총은 채움
    assert e.revenue_ttm_eokwon is None         # 매출/PER은 N/A
    assert e.per_ttm is None
    assert "market_cap" in e.data_asof


def test_enriched_from_candidate_carries_m3_fields():
    e = EnrichedStock.from_candidate(CANDIDATES["candidates"][0])
    assert e.name == "SK하이닉스" and e.code == "000660"
    assert e.relation_reason == "HBM 수혜" and e.relevance == "high"
    assert e.market_cap_eokwon is None  # 아직 결합 전
