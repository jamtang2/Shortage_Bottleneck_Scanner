"""재무 enrich 데이터 스키마 (M4).

`data/enriched.json` 데이터 계약을 정의한다. M3 candidate에 재무지표를 덧붙인
형태로, 다운스트림 M5(report)가 이 파일을 읽어 리포트를 만든다. (PRD §6)

수치는 모두 **억원** 단위(억 = 1e8원). 값을 못 구하면 None(리포트에서 'N/A'로 표기).
NF7: 각 수치 필드의 기준일을 `data_asof`(필드→날짜 dict)에 따로 기록한다 —
시총/PER 기준일(거래일)과 매출 기준일(보고서 분기말)이 다르기 때문.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class EnrichedStock:
    """M3 후보 + M4 재무지표."""
    # --- M3 candidate 필드(그대로 승계) ---
    keyword: str
    category: str
    name: str
    code: str
    market: str
    proposed_by: List[str]
    agreement_score: int
    relation_reason: str
    relevance: str
    # --- M4 재무지표(억원; 못 구하면 None) ---
    market_cap_eokwon: Optional[float] = None       # 시가총액(pykrx)
    revenue_ttm_eokwon: Optional[float] = None       # 최근 4분기 매출 합(DART)
    net_income_ttm_eokwon: Optional[float] = None    # 최근 4분기 순이익 합(DART)
    per_ttm: Optional[float] = None                  # TTM PER(pykrx 펀더멘털)
    per_quarterly_annualized: Optional[float] = None  # 최근분기 순이익×4 기준(best-effort/고변동)
    # NF7: 필드명 → 기준일("YYYY-MM-DD"). 못 구한 필드는 키 없음.
    data_asof: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_candidate(cls, cand: dict) -> "EnrichedStock":
        """M3 candidate dict에서 재무필드는 비운 채 생성."""
        return cls(
            keyword=cand.get("keyword", ""),
            category=cand.get("category", ""),
            name=cand.get("name", ""),
            code=cand.get("code", ""),
            market=cand.get("market", ""),
            proposed_by=list(cand.get("proposed_by", [])),
            agreement_score=int(cand.get("agreement_score", 0)),
            relation_reason=cand.get("relation_reason", ""),
            relevance=cand.get("relevance", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EnrichedResult:
    scan_date: str
    window_days: int
    enriched: List[EnrichedStock] = field(default_factory=list)
    # M3에서 그대로 통과시키는 드롭 목록(투명성 — 리포트엔 안 나감).
    dropped: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scan_date": self.scan_date,
            "window_days": self.window_days,
            "enriched": [e.to_dict() for e in self.enriched],
            "dropped": list(self.dropped),
        }
