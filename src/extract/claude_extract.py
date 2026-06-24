"""M2 extract — Claude로 수집 기사에서 쇼티지/병목 테마를 구조화 추출.

설계 원칙:
- "JSON only" 강제 + 방어적 파싱 (마크다운 펜스/잡설 제거).
- **출처 URL 환각 방지**: LLM에는 기사에 부여한 정수 id만 인용하게 하고,
  sources는 우리가 실제 기사에서 복원한다 (M3의 KRX 검증과 같은 철학).
- 공급망 무관 노이즈('병목'=교통정체/호르무즈 등) 필터링을 프롬프트로 지시.
- 호출 실패/파싱 실패는 예외로 올려 상위(run_extract)가 격리 처리한다.
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from .models import Theme, ThemeSource

logger = logging.getLogger(__name__)

# confidence 정규화: 영문/한글 입력을 표준값으로 매핑
_CONFIDENCE_MAP = {
    "high": "high", "높음": "high", "상": "high",
    "medium": "medium", "중간": "medium", "중": "medium", "보통": "medium",
    "low": "low", "낮음": "low", "하": "low",
}
_VALID_TYPES = {
    "shortage", "bottleneck", "capacity_delay", "leadtime", "production_cut", "other",
}

SYSTEM_PROMPT = (
    "너는 한국 주식 리서치를 돕는 애널리스트다. 뉴스 기사 목록에서 '공급 부족·"
    "병목(쇼티지/bottleneck)' 투자 테마만 정확히 식별해 구조화한다. 반드시 "
    "유효한 JSON 객체 하나만 출력하고, 그 외 설명·인사·마크다운 펜스는 출력하지 않는다."
)


def build_prompt(articles: List[dict], *, window_days: int) -> str:
    """기사 목록을 id 부여해 압축 직렬화하고 추출 지시 프롬프트를 만든다."""
    lines = []
    for i, a in enumerate(articles):
        title = (a.get("title") or "").strip()
        summary = (a.get("summary") or "").strip()
        kw = a.get("keyword") or ""
        date = a.get("date") or ""
        pub = a.get("publisher") or ""
        lines.append(f"[{i}] ({date}, kw={kw}, {pub}) {title} :: {summary}")
    article_block = "\n".join(lines)

    return f"""아래는 최근 {window_days}일간 수집한 한국 뉴스 기사 {len(articles)}건이다.
각 줄은 `[id] (날짜, kw=검색키워드, 언론사) 제목 :: 요약` 형식이다.

# 목표
실제 산업의 '공급 부족 / 생산 병목 / 증설 지연 / 리드타임 증가 / 감산' 테마만
골라 종목 발굴에 쓸 수 있게 구조화하라.

# 반드시 지킬 것
1. 공급망과 무관한 노이즈는 **제외**한다. 예: 교통 병목/정체, 호르무즈 해협 등
   지정학 일반론, 통신 트래픽 병목, 단순 주가·환율 기사, 정치/스포츠.
2. 의미가 같거나 매우 유사한 테마는 **하나로 병합**한다(중복 키워드 금지).
   대표 키워드는 가장 구체적인 제품/소재/공정명으로 정한다
   (예: "MLCC 공급부족", "HBM 병목", "전력기기 리드타임").
3. 각 테마는 기사에 실제로 근거가 있어야 한다. 근거가 된 기사 id만 sources에 넣는다.
   **존재하지 않는 id나 URL을 만들지 마라.**
4. confidence는 근거의 강도로 정한다:
   - high: 서로 다른 기사 3건 이상이 같은 테마를 구체적으로 뒷받침
   - medium: 2건 또는 다소 구체적인 단일 근거
   - low: 단일·모호한 근거
5. type은 다음 중 하나: shortage(공급부족/쇼티지), bottleneck(병목),
   capacity_delay(증설 지연), leadtime(리드타임 증가), production_cut(감산), other.

# 출력 스키마 (JSON 객체 하나만)
{{
  "themes": [
    {{
      "keyword": "대표 키워드(한국어)",
      "category": "산업/섹터 (예: 반도체, 2차전지, 전력기기)",
      "type": "shortage|bottleneck|capacity_delay|leadtime|production_cut|other",
      "evidence": "왜 공급 부족/병목인지 1~2문장 한국어 근거 요약",
      "confidence": "high|medium|low",
      "source_ids": [근거가 된 기사 id 정수들]
    }}
  ]
}}

# 기사
{article_block}
"""


def _parse_json_object(text: str) -> dict:
    """모델 출력에서 JSON 객체를 방어적으로 추출/파싱한다."""
    if not text:
        raise ValueError("빈 응답")
    s = text.strip()
    # ```json ... ``` 펜스 제거
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # 첫 '{' ~ 마지막 '}' 구간만 재시도
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(s[start : end + 1])
        raise


def _coerce_themes(parsed: dict, articles: List[dict]) -> List[Theme]:
    """파싱된 dict를 검증해 Theme 리스트로 변환. 무효 항목은 드롭/로깅."""
    raw_themes = parsed.get("themes")
    if not isinstance(raw_themes, list):
        raise ValueError("응답에 'themes' 배열이 없습니다.")

    themes: List[Theme] = []
    n = len(articles)
    for idx, t in enumerate(raw_themes):
        if not isinstance(t, dict):
            continue
        keyword = (t.get("keyword") or "").strip()
        evidence = (t.get("evidence") or "").strip()
        if not keyword or not evidence:
            logger.warning("테마 #%d 드롭: keyword/evidence 누락", idx)
            continue

        # 기사 id → 실제 기사로 sources 복원(환각 URL 차단)
        sources: List[ThemeSource] = []
        seen_urls = set()
        for sid in t.get("source_ids") or []:
            if not isinstance(sid, int) or sid < 0 or sid >= n:
                continue
            a = articles[sid]
            url = a.get("url") or ""
            if url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append(
                ThemeSource(
                    title=a.get("title") or "",
                    url=url,
                    date=a.get("date") or "",
                    publisher=a.get("publisher") or "",
                )
            )
        if not sources:
            logger.warning("테마 '%s' 드롭: 유효한 출처 기사 없음", keyword)
            continue

        conf = _CONFIDENCE_MAP.get(str(t.get("confidence", "")).strip().lower(), "low")
        ttype = str(t.get("type", "other")).strip().lower()
        if ttype not in _VALID_TYPES:
            ttype = "other"

        themes.append(
            Theme(
                keyword=keyword,
                category=(t.get("category") or "기타").strip() or "기타",
                type=ttype,
                evidence=evidence,
                confidence=conf,
                sources=sources,
            )
        )
    return themes


def _call_claude(*, model: str, system: str, prompt: str, api_key: str,
                 max_tokens: int = 8000) -> str:
    """anthropic SDK로 1회 호출하고 텍스트를 합쳐 반환. SDK가 자체 재시도 수행."""
    import anthropic  # 지연 임포트: 패키지 없어도 모듈 임포트는 가능

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


def extract_themes(
    articles: List[dict],
    *,
    window_days: int,
    model: str,
    api_key: str,
    max_articles: int = 200,
    max_tokens: int = 8000,
) -> List[Theme]:
    """기사 리스트(dict) → Theme 리스트. 호출/파싱 실패는 예외로 전파한다."""
    if not articles:
        return []
    if len(articles) > max_articles:
        logger.info("기사 %d건 중 최신 %d건만 사용", len(articles), max_articles)
        articles = articles[:max_articles]

    prompt = build_prompt(articles, window_days=window_days)
    text = _call_claude(model=model, system=SYSTEM_PROMPT, prompt=prompt, api_key=api_key,
                        max_tokens=max_tokens)
    parsed = _parse_json_object(text)
    return _coerce_themes(parsed, articles)
