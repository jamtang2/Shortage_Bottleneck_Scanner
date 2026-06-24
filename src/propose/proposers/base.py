"""제안자(Proposer) 인터페이스 + 공통 프롬프트/파싱 (M3).

Claude·GPT·Gemini가 **같은 프롬프트와 같은 출력 스키마**로 종목을 제안한다.
모델별 API 호출부만 다르고 나머지(프롬프트/파싱/검증)는 공유한다.
한 모델이 실패해도 예외를 올려 호출부가 격리하면 나머지로 진행한다(NF8).
"""
from __future__ import annotations

import abc
import json
import logging
import re
from typing import List

from ..models import Proposal

logger = logging.getLogger(__name__)

_RELEVANCE_MAP = {
    "high": "high", "높음": "high", "상": "high",
    "medium": "medium", "중간": "medium", "중": "medium", "보통": "medium",
    "low": "low", "낮음": "low", "하": "low",
}

SYSTEM_PROMPT = (
    "너는 한국 주식 리서치 애널리스트다. 주어진 '공급 부족/병목' 테마의 직접 수혜가 "
    "기대되는 KRX(코스피/코스닥) 상장사를 제안한다. 반드시 유효한 JSON 객체 하나만 "
    "출력하고 그 외 설명·마크다운 펜스는 출력하지 않는다."
)


def build_prompt(theme: dict, *, max_stocks: int = 8) -> str:
    keyword = theme.get("keyword", "")
    category = theme.get("category", "")
    ttype = theme.get("type", "")
    evidence = theme.get("evidence", "")
    return f"""다음은 뉴스에서 추출한 공급 부족/병목 투자 테마다.

- 키워드: {keyword}
- 산업/섹터: {category}
- 유형: {ttype}
- 근거: {evidence}

# 지시
이 테마의 **직접 수혜**가 기대되는 KRX 상장사를 최대 {max_stocks}개 제안하라.
- 실제로 한국거래소(코스피/코스닥)에 상장된 회사만. 존재하지 않는 회사·코드를 만들지 마라.
- name은 **거래소에 등록된 정식 종목명을 정확히** 적어라(약칭·옛 사명 금지).
  예: "금호석유"(X)→"금호석유화학"(O), "현대두산인프라코어"(X)→"HD현대인프라코어"(O).
- 테마와 인과관계가 분명한 회사 위주(소재·부품·장비·증설 수혜 등). 막연한 대형주 나열 금지.
- code는 알면 6자리로 적되, 모르면 비워라(코드는 별도로 검증한다).
- relevance는 테마 연관 강도: high / medium / low.

# 출력 스키마 (JSON 객체 하나만)
{{
  "stocks": [
    {{"name": "정확한 한국어 종목명", "code": "005930", "reason": "테마와의 관계 1문장", "relevance": "high|medium|low"}}
  ]
}}
"""


def parse_proposals(text: str, model: str) -> List[Proposal]:
    """모델 응답에서 종목 제안 리스트를 방어적으로 파싱한다."""
    if not text:
        return []
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            logger.warning("[%s] JSON 파싱 실패 — 빈 제안", model)
            return []
        try:
            parsed = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            logger.warning("[%s] JSON 재파싱 실패 — 빈 제안", model)
            return []

    raw = parsed.get("stocks") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        return []

    proposals: List[Proposal] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        code = item.get("code")
        code = str(code).strip() if code else None
        if code and not re.fullmatch(r"\d{6}", code):
            code = None  # 6자리 아니면 코드 단서 무시
        relevance = _RELEVANCE_MAP.get(
            str(item.get("relevance", "")).strip().lower(), "medium"
        )
        proposals.append(
            Proposal(
                model=model,
                name=name,
                reason=(item.get("reason") or "").strip(),
                relevance=relevance,
                code=code,
            )
        )
    return proposals


class BaseProposer(abc.ABC):
    """제안자 공통 골격. 서브클래스는 _complete/available만 구현한다."""

    name: str = "base"

    def __init__(self, *, model: str, max_stocks: int = 8):
        self.model = model
        self.max_stocks = max_stocks

    @abc.abstractmethod
    def available(self) -> bool:
        """API 키 등 호출 가능 조건."""

    @abc.abstractmethod
    def _complete(self, system: str, prompt: str) -> str:
        """1회 호출 후 응답 텍스트 반환. 실패 시 예외를 올린다."""

    def propose(self, theme: dict) -> List[Proposal]:
        prompt = build_prompt(theme, max_stocks=self.max_stocks)
        text = self._complete(SYSTEM_PROMPT, prompt)
        return parse_proposals(text, self.name)
