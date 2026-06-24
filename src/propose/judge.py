"""judge 모델(Claude) — 병합된 후보마다 최종 관계 사유/relevance를 정리 (M3).

모델별 제안 사유가 제각각이라, 단일 judge가 테마–종목 관계를 일관된 한 문장으로
다시 쓰고 relevance(high/medium/low)를 최종 판정한다. 테마당 1회 호출(비용 절약).
실패 시 예외를 올려 호출부가 격리(드래프트 사유 유지).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

_RELEVANCE = {"high", "medium", "low"}

JUDGE_SYSTEM = (
    "너는 한국 주식 리서치 검수자다. 테마와 후보 종목의 관계를 검증해 간결한 "
    "근거 한 문장과 relevance를 최종 판정한다. 유효한 JSON 객체 하나만 출력한다."
)


def build_judge_prompt(theme: dict, items: List[dict]) -> str:
    lines = []
    for it in items:
        reasons = " / ".join(r for r in it.get("reasons", []) if r)
        lines.append(
            f'- code={it["code"]} name={it["name"]} '
            f'(제안모델 {it["agreement_score"]}개: {", ".join(it["proposed_by"])}) '
            f'근거후보: {reasons}'
        )
    block = "\n".join(lines)
    return f"""테마: {theme.get("keyword","")} / 유형 {theme.get("type","")} / {theme.get("category","")}
테마 근거: {theme.get("evidence","")}

아래 후보 종목들에 대해, 이 테마의 수혜와 어떻게 연결되는지 **간결한 한국어 한 문장**
근거를 다시 쓰고 relevance(high|medium|low)를 판정하라. 관계가 약하면 low로 낮춰라.
존재하지 않는 사실을 지어내지 말고 주어진 근거 범위에서만 정리하라.

# 후보
{block}

# 출력 스키마 (JSON 객체 하나만; code는 위 후보의 code 그대로)
{{
  "judgements": [
    {{"code": "005930", "relation_reason": "한 문장 근거", "relevance": "high|medium|low"}}
  ]
}}
"""


def _parse(text: str) -> Dict[str, dict]:
    s = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(s[start : end + 1])

    out: Dict[str, dict] = {}
    for j in parsed.get("judgements", []) if isinstance(parsed, dict) else []:
        if not isinstance(j, dict):
            continue
        code = str(j.get("code", "")).strip()
        if not code:
            continue
        rel = str(j.get("relevance", "")).strip().lower()
        out[code] = {
            "relation_reason": (j.get("relation_reason") or "").strip(),
            "relevance": rel if rel in _RELEVANCE else "medium",
        }
    return out


def _call_claude(*, model: str, system: str, prompt: str, api_key: str,
                 max_tokens: int = 2000) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, max_retries=3)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        getattr(b, "text", "") for b in msg.content
        if getattr(b, "type", None) == "text"
    )


def judge_theme(theme: dict, items: List[dict], *, model: str, api_key: str) -> Dict[str, dict]:
    """code → {relation_reason, relevance} 매핑 반환. 호출/파싱 실패는 예외 전파."""
    if not items:
        return {}
    prompt = build_judge_prompt(theme, items)
    text = _call_claude(model=model, system=JUDGE_SYSTEM, prompt=prompt, api_key=api_key)
    return _parse(text)
