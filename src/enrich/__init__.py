"""M4 enrich — 검증·병합된 후보에 재무지표를 1회 결합.

입력:  data/candidates.json   (M3 산출)
출력:  data/enriched.json

- 시가총액: FinanceDataReader 스냅샷(원→억원). 기준일=최신 거래일(휴장일 자동보정, NF3).
- 매출/순이익 TTM: OpenDART 롤링 TTM(최근 4분기, 원→억원).
- PER(TTM): 시총 ÷ 순이익TTM (재무적으로 trailing PER과 동일).
- PER(분기연율): 시총 ÷ (최신분기 순이익×4) — best-effort/고변동.
- 각 수치의 기준일을 data_asof(필드→날짜)에 기록(NF7).

`run_enrich`는 candidates.json만 있으면 단독 재실행 가능하다(독립 모듈 규칙).
종목별 작업은 try/except로 격리해 한 종목 실패가 전체를 멈추지 않는다(NF4).
DART_API_KEY가 없으면 매출/PER은 None으로 두고 시총만 채운다(graceful degrade).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .dart_financials import DartFinancials
from .market import EOK, MarketData
from .models import EnrichedResult, EnrichedStock

__all__ = [
    "run_enrich",
    "EnrichedStock",
    "EnrichedResult",
    "MarketData",
    "DartFinancials",
    "EnrichError",
    "load_candidates",
    "write_enriched",
]

logger = logging.getLogger(__name__)


class EnrichError(RuntimeError):
    """M4 enrich 단계 치명적 실패(시장 스냅샷 로드 불가 등)."""


def _per(market_cap_won: Optional[int], net_income_won: Optional[int]) -> Optional[float]:
    """PER = 시총 / 순이익. 순이익이 0 이하면 None(스크리닝상 무의미)."""
    if market_cap_won is None or net_income_won is None or net_income_won <= 0:
        return None
    return round(market_cap_won / net_income_won, 1)


def _eok(won: Optional[int]) -> Optional[float]:
    return None if won is None else round(won / EOK, 1)


def _enrich_one(
    cand: dict,
    market: MarketData,
    dart: Optional[DartFinancials],
    *,
    fiscal_year: int,
) -> EnrichedStock:
    """후보 1종목에 재무지표 결합. 종목 단위 실패는 호출자가 격리."""
    stock = EnrichedStock.from_candidate(cand)
    code = stock.code

    # --- 시가총액(FDR) ---
    cap_won = market.market_cap_won(code)
    stock.market_cap_eokwon = _eok(cap_won)
    if stock.market_cap_eokwon is not None and market.asof:
        stock.data_asof["market_cap"] = market.asof

    # --- 매출/순이익 TTM + PER(DART) ---
    if dart is not None:
        fin = dart.revenue_income_ttm(code, year=fiscal_year)
        stock.revenue_ttm_eokwon = _eok(fin.revenue_ttm_won)
        stock.net_income_ttm_eokwon = _eok(fin.net_income_ttm_won)
        stock.per_ttm = _per(cap_won, fin.net_income_ttm_won)
        if fin.latest_quarter_net_income_won is not None:
            stock.per_quarterly_annualized = _per(
                cap_won, fin.latest_quarter_net_income_won * 4
            )
        if fin.period_end:
            for fld, val in (
                ("revenue_ttm", stock.revenue_ttm_eokwon),
                ("net_income_ttm", stock.net_income_ttm_eokwon),
                ("per_ttm", stock.per_ttm),
                ("per_quarterly_annualized", stock.per_quarterly_annualized),
            ):
                if val is not None:
                    stock.data_asof[fld] = fin.period_end
    return stock


def run_enrich(
    candidates: dict,
    *,
    settings: Optional[dict] = None,
    dart_api_key: Optional[str] = None,
    market: Optional[MarketData] = None,
    dart: Optional[DartFinancials] = None,
) -> EnrichedResult:
    """candidates dict → EnrichedResult.

    market/dart 미지정 시 각각 FDR 스냅샷·DART(키 있으면)를 로드한다.
    DART_API_KEY가 없으면 dart=None으로 두고 시총만 채운다(degrade).
    """
    settings = settings or {}
    scan_date = candidates.get("scan_date", "")
    window_days = candidates.get("window_days", 7)
    cand_list = candidates.get("candidates") or []
    dropped = candidates.get("dropped") or []

    # 회계연도: scan_date 연도 우선, 없으면 시장 기준일 연도.
    fiscal_year = _infer_year(scan_date)

    # --- 시장 스냅샷(필수) ---
    if market is None:
        try:
            market = MarketData.from_fdr()
        except Exception as e:  # noqa: BLE001
            raise EnrichError(f"시장 스냅샷 로드 실패(FDR): {e}") from e
    if fiscal_year is None:
        fiscal_year = _infer_year(market.asof) or 0

    # --- DART(선택) ---
    enrich_cfg = settings.get("enrich", {}) or {}
    use_dart = enrich_cfg.get("use_dart", True)
    if dart is None and not use_dart:
        logger.info("enrich.use_dart=false — 매출/PER 생략, 시총만 채움.")
    elif dart is None:
        dart_api_key = dart_api_key or os.getenv("DART_API_KEY")
        if dart_api_key:
            try:
                dart = DartFinancials.from_api_key(dart_api_key)
            except Exception as e:  # noqa: BLE001
                logger.warning("DART 초기화 실패 — 매출/PER 생략하고 진행: %s", e)
                dart = None
        else:
            logger.warning(
                "DART_API_KEY 없음 — 시총만 채우고 매출/PER은 N/A로 진행(degrade)."
            )

    enriched = []
    for cand in cand_list:
        name = cand.get("name", "?")
        try:
            enriched.append(
                _enrich_one(cand, market, dart, fiscal_year=fiscal_year)
            )
        except Exception as e:  # noqa: BLE001 — 종목 격리(NF4)
            logger.warning("[enrich] '%s' 결합 실패 — 재무 비우고 통과: %s", name, e)
            enriched.append(EnrichedStock.from_candidate(cand))

    n_cap = sum(1 for e in enriched if e.market_cap_eokwon is not None)
    n_rev = sum(1 for e in enriched if e.revenue_ttm_eokwon is not None)
    logger.info(
        "enrich 완료: %d종목(시총 %d, 매출TTM %d)", len(enriched), n_cap, n_rev
    )
    return EnrichedResult(
        scan_date=scan_date,
        window_days=window_days,
        enriched=enriched,
        dropped=dropped,
    )


def _infer_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        return int(str(date_str)[:4])
    except ValueError:
        return None


def load_candidates(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_enriched(result: EnrichedResult, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
