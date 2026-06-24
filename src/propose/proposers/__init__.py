"""제안자 패키지 — config로 활성 모델을 켜고 끈다."""
from __future__ import annotations

import logging
from typing import Dict, List

from .base import BaseProposer, build_prompt, parse_proposals
from .claude_proposer import ClaudeProposer
from .gemini_proposer import GeminiProposer
from .gpt_proposer import GptProposer

logger = logging.getLogger(__name__)

_REGISTRY = {
    "claude": ClaudeProposer,
    "gpt": GptProposer,
    "gemini": GeminiProposer,
}

__all__ = [
    "BaseProposer",
    "build_prompt",
    "parse_proposals",
    "ClaudeProposer",
    "GptProposer",
    "GeminiProposer",
    "build_proposers",
]


def build_proposers(settings: dict) -> List[BaseProposer]:
    """config(proposers on/off + propose.models)로 활성·가용 제안자만 만든다.

    키가 없는(available=False) 제안자는 로그를 남기고 제외한다(앙상블 degrade, NF8).
    """
    enabled: Dict[str, bool] = settings.get("proposers", {}) or {}
    propose_cfg = settings.get("propose", {}) or {}
    models: Dict[str, str] = propose_cfg.get("models", {}) or {}
    max_stocks = int(propose_cfg.get("max_stocks_per_theme", 8))
    # thinking 모델(예: gemini flash)은 추론 토큰도 여기서 소비하므로 넉넉히.
    max_tokens = int(propose_cfg.get("max_tokens", 4000))

    proposers: List[BaseProposer] = []
    for key, cls in _REGISTRY.items():
        if not enabled.get(key, False):
            continue
        model = models.get(key)
        if not model:
            logger.warning("proposer '%s' 켜졌지만 propose.models에 모델 ID 없음 — 건너뜀", key)
            continue
        p = cls(model=model, max_stocks=max_stocks, max_tokens=max_tokens)
        if not p.available():
            logger.warning("proposer '%s' API 키 없음 — 앙상블에서 제외", key)
            continue
        proposers.append(p)
    logger.info("활성 제안자: %s", [p.name for p in proposers] or "없음")
    return proposers
