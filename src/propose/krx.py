"""KRX 상장리스트 검증/정규화 (M3의 환각 티커 1차 방어, NF1).

모델이 제안한 종목명을 살아있는 KRX 상장리스트와 대조해 실제 6자리 코드로
정규화한다. 매칭 안 되거나 모호하면 None을 반환 → 호출부가 dropped 처리.

설계 메모:
- code/name/market은 **KRX가 권위**. 모델이 적은 코드는 신뢰하지 않고, 검증
  통과한 경우의 보조 단서로만 쓴다.
- 우선주('삼성전자우' 등)·중복명은 보통주를 우선한다.
- 리스트는 1회 로드 후 모듈 캐시(NF2: 호출/네트워크 절약).
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, NamedTuple, Optional

logger = logging.getLogger(__name__)

# 보통주 우선용 우선주 접미사 패턴
_PREF_SUFFIX = re.compile(r"(우(?:B|C)?|\d?우(?:B|C)?)$")
# 정규화: 공백/(주)/괄호내용/특수문자 제거
_PAREN = re.compile(r"\(.*?\)")
_NONWORD = re.compile(r"[^0-9A-Za-z가-힣]")


class Listing(NamedTuple):
    code: str
    name: str
    market: str


def normalize_name(name: str) -> str:
    """매칭용 정규화: 공백/괄호/특수문자 제거, 대문자화."""
    if not name:
        return ""
    s = _PAREN.sub("", name)
    s = s.replace("(주)", "").replace("㈜", "")
    s = _NONWORD.sub("", s)
    return s.upper()


def _is_preferred(name: str) -> bool:
    return bool(_PREF_SUFFIX.search(name))


class KrxValidator:
    """KRX 상장리스트 기반 종목명→코드 검증기."""

    def __init__(self, listings: List[Listing]):
        self._by_code: Dict[str, Listing] = {l.code: l for l in listings}
        # 정규화 종목명 → 후보 리스트(동명이인/우선주 대비)
        self._by_name: Dict[str, List[Listing]] = {}
        for l in listings:
            self._by_name.setdefault(normalize_name(l.name), []).append(l)

    @classmethod
    def from_fdr(cls) -> "KrxValidator":
        """FinanceDataReader로 KRX 전체 상장리스트를 로드한다."""
        import FinanceDataReader as fdr  # 지연 임포트

        df = fdr.StockListing("KRX")
        listings: List[Listing] = []
        for _, row in df.iterrows():
            code = str(row.get("Code", "")).strip()
            name = str(row.get("Name", "")).strip()
            market = str(row.get("Market", "")).strip()
            if not code or not name or name.lower() == "nan":
                continue
            listings.append(Listing(code=code, name=name, market=market))
        logger.info("KRX 상장리스트 로드: %d종목", len(listings))
        return cls(listings)

    def _pick(self, candidates: List[Listing]) -> Optional[Listing]:
        """후보 중 하나 선택. 보통주 > 우선주, 그 외 모호하면 None."""
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        commons = [c for c in candidates if not _is_preferred(c.name)]
        if len(commons) == 1:
            return commons[0]
        return None  # 보통주가 0개거나 2개 이상 → 모호 → 드롭

    def validate(self, name: str, code: Optional[str] = None) -> Optional[Listing]:
        """제안 종목명(+선택 코드)을 검증해 정규 Listing 반환, 실패 시 None.

        1) 모델이 준 코드가 실제 존재하고 이름이 합치하면 그대로 채택.
        2) 아니면 정규화 종목명 완전일치로 매칭(보통주 우선).
        부분일치/유사일치는 환각 위험이 커서 허용하지 않는다(엄격).
        """
        norm = normalize_name(name)
        if not norm:
            return None

        # 1) 코드 단서가 유효하고 이름이 합치하는가
        if code:
            code = str(code).strip()
            hit = self._by_code.get(code)
            if hit and normalize_name(hit.name) == norm:
                return hit

        # 2) 정규화 이름 완전일치
        return self._pick(self._by_name.get(norm, []))
