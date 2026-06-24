"""M3 propose — 멀티 LLM 종목 제안 + KRX 검증 + 병합/합의 + judge.

입력:  data/themes.json        (M2 산출)
출력:  data/candidates.json    (PRD §6 Step 2 스키마)

파이프라인(테마별):
  1) 활성 제안자(Claude/GPT/Gemini)가 같은 프롬프트로 종목 제안 — 한 모델 실패는 격리(NF8)
  2) 모델별·필수 KRX 검증 → 실제 코드로 정규화, 실패분은 dropped (환각 차단, NF1)
  3) 코드 기준 병합 → proposed_by / agreement_score → 키워드별 top-K
  4) judge(Claude)가 최종 relation_reason / relevance 작성
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

from .judge import judge_theme
from .krx import KrxValidator
from .merge import merge_theme
from .models import (
    Candidate,
    CandidatesResult,
    DroppedProposal,
    Proposal,
    ValidatedProposal,
)
from .proposers import build_proposers

__all__ = [
    "run_propose",
    "ProposeError",
    "Candidate",
    "CandidatesResult",
    "DroppedProposal",
]

logger = logging.getLogger(__name__)


class ProposeError(RuntimeError):
    """M3 제안 단계 실패(제안자 0개·KRX 로드 실패 등)."""


def _validate_proposals(
    proposals: List[Proposal],
    validator: KrxValidator,
    keyword: str,
) -> tuple[List[ValidatedProposal], List[DroppedProposal]]:
    validated: List[ValidatedProposal] = []
    dropped: List[DroppedProposal] = []
    for p in proposals:
        hit = validator.validate(p.name, p.code)
        if hit is None:
            dropped.append(
                DroppedProposal(
                    keyword=keyword, model=p.model, proposed_name=p.name,
                    reason="KRX 미상장/모호",
                )
            )
            continue
        validated.append(
            ValidatedProposal(
                model=p.model, code=hit.code, name=hit.name, market=hit.market,
                reason=p.reason, relevance=p.relevance,
            )
        )
    return validated, dropped


def _reasons_by_code(validated: List[ValidatedProposal]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for v in validated:
        if v.reason:
            out.setdefault(v.code, []).append(f"{v.model}: {v.reason}")
    return out


def run_propose(
    themes: dict,
    *,
    settings: Optional[dict] = None,
    judge_api_key: Optional[str] = None,
    validator: Optional[KrxValidator] = None,
) -> CandidatesResult:
    """themes dict → CandidatesResult. 단계 실패는 ProposeError로 묶어 올린다."""
    settings = settings or {}
    propose_cfg = settings.get("propose", {}) or {}
    judge_model = propose_cfg.get("judge_model", "claude-opus-4-8")
    top_k = int(settings.get("top_k_per_keyword", 5))

    proposers = build_proposers(settings)
    if not proposers:
        raise ProposeError(
            "활성 제안자가 없습니다. proposers on/off, propose.models, API 키를 확인하세요."
        )

    if validator is None:
        try:
            validator = KrxValidator.from_fdr()
        except Exception as e:  # noqa: BLE001
            raise ProposeError(f"KRX 상장리스트 로드 실패: {e}") from e

    judge_api_key = judge_api_key or os.getenv("ANTHROPIC_API_KEY")

    theme_list = themes.get("themes", []) or []
    all_candidates: List[Candidate] = []
    all_dropped: List[DroppedProposal] = []

    for theme in theme_list:
        keyword = theme.get("keyword", "")
        # 1) 제안 수집 (모델별 격리)
        proposals: List[Proposal] = []
        for proposer in proposers:
            try:
                got = proposer.propose(theme)
                logger.info("[%s] '%s' 제안 %d건", proposer.name, keyword, len(got))
                proposals.extend(got)
            except Exception as e:  # noqa: BLE001 — 한 모델 실패는 격리(NF8)
                logger.warning("[%s] '%s' 제안 실패 — 건너뜀: %s", proposer.name, keyword, e)

        # 2) KRX 검증
        validated, dropped = _validate_proposals(proposals, validator, keyword)
        all_dropped.extend(dropped)
        if not validated:
            logger.info("'%s' 검증 통과 종목 없음", keyword)
            continue

        # 3) 병합 → 합의도 → top-K
        candidates = merge_theme(theme, validated, top_k=top_k)

        # 4) judge 최종 사유/relevance (실패해도 드래프트 유지)
        if judge_api_key:
            items = [
                {
                    "code": c.code, "name": c.name,
                    "agreement_score": c.agreement_score, "proposed_by": c.proposed_by,
                    "reasons": _reasons_by_code(validated).get(c.code, []),
                }
                for c in candidates
            ]
            try:
                judged = judge_theme(theme, items, model=judge_model, api_key=judge_api_key)
                for c in candidates:
                    j = judged.get(c.code)
                    if j:
                        c.relation_reason = j["relation_reason"] or c.relation_reason
                        c.relevance = j["relevance"]
            except Exception as e:  # noqa: BLE001
                logger.warning("'%s' judge 실패 — 드래프트 사유 유지: %s", keyword, e)
        else:
            logger.warning("judge용 ANTHROPIC_API_KEY 없음 — 드래프트 사유 유지")

        all_candidates.extend(candidates)

    scan_date = themes.get("scan_date", "")
    window_days = themes.get("window_days", 7)
    logger.info(
        "후보 %d종목 / 드롭 %d건 (테마 %d개)",
        len(all_candidates), len(all_dropped), len(theme_list),
    )
    return CandidatesResult(
        scan_date=scan_date, window_days=window_days,
        candidates=all_candidates, dropped=all_dropped,
    )


def load_themes(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_candidates(result: CandidatesResult, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
