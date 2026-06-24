"""시장 데이터(시가총액·최신 거래일) — FinanceDataReader 기반.

설계 메모: PRD는 pykrx(get_market_cap/get_market_fundamental)를 지정했으나,
pykrx 1.2.8은 KRX 데이터 포털의 로그인/봇차단 장벽으로 이 환경에서 빈 응답을
반환한다. 대신 M3에서 이미 검증된 **FinanceDataReader**의 StockListing('KRX')을
쓴다 — `Marcap`(시가총액, 원)을 직접 제공하고, 최신 거래일 스냅샷이라
휴장일 보정(NF3)이 자동으로 된다. PER은 별도 펀더멘털 호출 대신
DART 순이익으로 파생한다(시총÷순이익TTM; 재무적으로 TTM PER과 동일, 더 투명).

거래일 기준일(data_asof)은 KOSPI 지수의 마지막 인덱스에서 1회 산출해 전 종목에
공유한다(StockListing 스냅샷과 동일한 거래일).
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

EOK = 100_000_000  # 1억 = 1e8원


class MarketData:
    """KRX 상장 스냅샷에서 종목코드→시가총액(원)을 조회."""

    def __init__(self, marcap_by_code: Dict[str, int], asof: str):
        self._marcap = marcap_by_code
        self.asof = asof  # 최신 거래일 "YYYY-MM-DD"

    @classmethod
    def from_fdr(cls) -> "MarketData":
        """FinanceDataReader로 KRX 상장 스냅샷 + 최신 거래일을 로드."""
        import FinanceDataReader as fdr

        listing = fdr.StockListing("KRX")
        marcap: Dict[str, int] = {}
        for code, cap in zip(listing["Code"], listing["Marcap"]):
            if code is None:
                continue
            try:
                if cap is None or cap != cap:  # NaN 방지
                    continue
                marcap[str(code).zfill(6)] = int(cap)
            except (TypeError, ValueError):
                continue

        asof = cls._latest_trading_date(fdr)
        logger.info("시장 스냅샷 로드: %d종목 (기준일 %s)", len(marcap), asof)
        return cls(marcap, asof)

    @staticmethod
    def _latest_trading_date(fdr) -> str:
        """KOSPI 지수의 마지막 거래일을 'YYYY-MM-DD'로 반환(휴장일 자동 보정)."""
        try:
            ks = fdr.DataReader("KS11")
            return ks.index[-1].date().isoformat()
        except Exception as e:  # noqa: BLE001
            logger.warning("최신 거래일 조회 실패(기준일 비움): %s", e)
            return ""

    def market_cap_won(self, code: str) -> Optional[int]:
        return self._marcap.get(str(code).zfill(6))

    def market_cap_eokwon(self, code: str) -> Optional[float]:
        won = self.market_cap_won(code)
        if won is None:
            return None
        return round(won / EOK, 1)
