"""검증된 제안을 종목코드 기준으로 병합 → 합의도 → 랭킹/top-K (M3).

judge 호출 전 단계. 여기서는 사실(어느 모델이 동의했나, 코드/이름/시장)만 정리하고,
최종 사유·relevance 문구는 judge가 따로 작성한다.
"""
from __future__ import annotations

from typing import Dict, List

from .models import Candidate, ValidatedProposal

_RELEVANCE_RANK = {"high": 3, "medium": 2, "low": 1}


def merge_theme(
    theme: dict,
    validated: List[ValidatedProposal],
    *,
    top_k: int = 5,
) -> List[Candidate]:
    """한 테마의 검증된 제안들을 코드 기준 병합 → 합의도 정렬 → top-K.

    relation_reason/relevance는 임시값(첫 제안 기반)으로 채우고 judge가 덮어쓴다.
    """
    by_code: Dict[str, List[ValidatedProposal]] = {}
    for v in validated:
        by_code.setdefault(v.code, []).append(v)

    keyword = theme.get("keyword", "")
    category = theme.get("category", "")

    candidates: List[Candidate] = []
    for code, group in by_code.items():
        # 모델 중복 제거(같은 모델이 같은 코드 두 번 제안해도 1표)
        proposed_by = sorted({g.model for g in group})
        first = group[0]
        # 임시 relevance: 제안들 중 최댓값(judge가 최종 결정)
        best_rel = max(group, key=lambda g: _RELEVANCE_RANK.get(g.relevance, 0)).relevance
        candidates.append(
            Candidate(
                keyword=keyword,
                category=category,
                name=first.name,
                code=code,
                market=first.market,
                proposed_by=proposed_by,
                agreement_score=len(proposed_by),
                relation_reason=first.reason,  # judge가 덮어씀
                relevance=best_rel,            # judge가 덮어씀
            )
        )

    # 합의도 → relevance 순 내림차순, 동률은 종목명으로 안정 정렬
    candidates.sort(
        key=lambda c: (
            c.agreement_score,
            _RELEVANCE_RANK.get(c.relevance, 0),
        ),
        reverse=True,
    )
    return candidates[:top_k]
