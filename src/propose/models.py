"""종목 제안/검증/병합 데이터 스키마 (M3).

`data/candidates.json` 데이터 계약을 정의한다. 다운스트림 M4(enrich)가
이 파일의 각 candidate에 재무지표를 덧붙인다. (PRD §6 Step 2)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

# relevance 허용값: "high" | "medium" | "low"


@dataclass
class Proposal:
    """한 모델이 제안한 원본 종목(검증 전). 코드/이름은 환각 가능."""
    model: str            # "claude" | "gpt" | "gemini"
    name: str             # 모델이 적은 종목명
    reason: str           # 테마와의 관계 설명
    relevance: str        # high | medium | low
    code: Optional[str] = None  # 모델이 적은 코드(있으면 검증 보조용, 신뢰하지 않음)


@dataclass
class ValidatedProposal:
    """KRX 상장리스트로 검증·정규화된 제안. code/name/market은 KRX가 권위."""
    model: str
    code: str             # 6자리 KRX 종목코드
    name: str             # KRX 정규 종목명
    market: str           # KOSPI | KOSDAQ | KONEX
    reason: str
    relevance: str


@dataclass
class DroppedProposal:
    """KRX 검증 실패로 제외된 제안(투명성 위해 기록, 리포트엔 안 나감)."""
    keyword: str
    model: str
    proposed_name: str
    reason: str           # 드롭 사유(예: "KRX 미상장/모호")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    """코드 기준 병합된 최종 후보 종목."""
    keyword: str                      # 출처 테마 키워드
    category: str                     # 출처 테마 산업/섹터
    name: str
    code: str
    market: str
    proposed_by: List[str]            # KRX 검증 통과한 제안 모델들
    agreement_score: int              # = len(proposed_by)
    relation_reason: str              # judge가 작성한 최종 사유
    relevance: str                    # judge가 정한 최종 high|medium|low

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CandidatesResult:
    scan_date: str
    window_days: int
    candidates: List[Candidate] = field(default_factory=list)
    dropped: List[DroppedProposal] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scan_date": self.scan_date,
            "window_days": self.window_days,
            "candidates": [c.to_dict() for c in self.candidates],
            "dropped": [d.to_dict() for d in self.dropped],
        }
