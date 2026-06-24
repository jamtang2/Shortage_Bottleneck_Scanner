"""OpenDART 재무 — 매출/순이익의 TTM(최근 4분기 합) 산출.

한국 공시는 분기/반기/연간 보고서가 **누적(YTD)** 으로 보고된다. 단순히 4개
분기 보고서를 더하면 중복 계산되므로, 표준 **롤링 TTM 공식**을 쓴다:

    TTM = (올해 최신 누적) + (작년 연간) − (작년 동기 누적)

예) 올해 최신 = 1분기(누적 3M)면  TTM = Q1_올해 + FY_작년 − Q1_작년.
'작년 동기 누적'은 같은 보고서의 `frmtrm_amount`(전기)에 이미 들어 있어
호출 한 번을 아낀다(올해 최신 보고서 + 작년 사업보고서, 2콜이면 충분).

최신 분기 단독값(분기연율 PER용)은 올해 직전 누적과의 차이로 구한다:
  Q1→Q1자체, 반기→H1−Q1, 3분기→9M−H1, 연간→FY−9M.

모든 금액은 '원' 단위 문자열("1,234,567" / "-1,234" / "")로 오므로 정수로 정규화.
실패·결측은 전부 None으로 degrade(NF4) — 상위에서 'N/A' 처리.
"""
from __future__ import annotations

import contextlib
import io
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# reprt_code → 회계기간 끝 분기(1=Q1누적3M … 4=연간12M)
_REPORT_QUARTER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
# 같은 해에서 '최신' 보고서를 찾을 때 새 것부터 탐침하는 순서
_PROBE_ORDER = ["11011", "11014", "11012", "11013"]
# 단독분기 계산 시 '직전 누적'에 해당하는 보고서(없으면 그 보고서가 곧 단독분기)
_PREV_CUMULATIVE = {"11012": "11013", "11014": "11012", "11011": "11014"}

# 계정명 매칭(우선순위) — 연결 손익계산서 기준
_REVENUE_NAMES = ["매출액", "수익(매출액)", "영업수익", "매출"]


def _to_won(amount: Optional[str]) -> Optional[int]:
    """DART 금액 문자열 → 정수(원). 빈값/하이픈/파싱불가는 None."""
    if amount is None:
        return None
    s = str(amount).strip().replace(",", "")
    if s in ("", "-", "—"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _period_end(thstrm_dt: Optional[str]) -> Optional[str]:
    """'2026.01.01 ~ 2026.03.31' 또는 '2026.03.31' → '2026-03-31'."""
    if not thstrm_dt:
        return None
    s = str(thstrm_dt)
    if "~" in s:
        s = s.split("~")[-1]
    s = s.strip().replace(".", "-").rstrip("-")
    return s or None


def _pick_account(df, names) -> Optional["object"]:
    """연결(CFS) 손익계산서에서 names에 해당하는 행 1개를 우선순위로 선택.

    연결이 없으면 개별(OFS)로 폴백. 순이익은 '포괄'(총포괄손익) 행을 배제.
    """
    if df is None or len(df) == 0 or "account_nm" not in df.columns:
        return None

    def _match(sub):
        for target in names:
            for _, row in sub.iterrows():
                nm = str(row.get("account_nm", "")).strip()
                if nm == target:
                    return row
        # 정확일치 실패 시 부분일치(포괄손익 제외)
        for target in names:
            for _, row in sub.iterrows():
                nm = str(row.get("account_nm", "")).strip()
                if target in nm and "포괄" not in nm:
                    return row
        return None

    fs_div = df["fs_div"] if "fs_div" in df.columns else None
    if fs_div is not None:
        cfs = df[df["fs_div"] == "CFS"]
        row = _match(cfs)
        if row is not None:
            return row
        ofs = df[df["fs_div"] == "OFS"]
        row = _match(ofs)
        if row is not None:
            return row
    return _match(df)


@dataclass
class _Statement:
    """한 보고서에서 뽑은 매출/순이익(당기 누적, 전기 누적)과 기준일."""
    revenue_cum: Optional[int]
    revenue_prev_cum: Optional[int]
    net_income_cum: Optional[int]
    net_income_prev_cum: Optional[int]
    period_end: Optional[str]

    @property
    def has_data(self) -> bool:
        return self.revenue_cum is not None or self.net_income_cum is not None


@dataclass
class DartResult:
    revenue_ttm_won: Optional[int] = None
    net_income_ttm_won: Optional[int] = None
    latest_quarter_net_income_won: Optional[int] = None
    period_end: Optional[str] = None  # TTM에 포함된 최신 보고서 기간말(data_asof)


class DartFinancials:
    """OpenDartReader를 감싸 종목코드 단위 TTM 매출/순이익을 제공."""

    # 순이익 계정명은 인스턴스에서 상수로
    _NET_INCOME_NAMES = ["당기순이익", "당기순이익(손실)", "당기순이익(손실)귀속"]

    def __init__(self, dart):
        self._dart = dart

    @classmethod
    def from_api_key(cls, api_key: str) -> "DartFinancials":
        from opendartreader import OpenDartReader

        return cls(OpenDartReader(api_key))

    def _statement(self, code: str, year: int, reprt_code: str) -> Optional[_Statement]:
        """finstate 1회 호출 → _Statement. 실패/빈 결과는 None."""
        try:
            # opendartreader는 빈 응답(미제출 분기 등)에 print(jo)를 찍는다 —
            # 신→구 보고서 탐침 과정의 정상 케이스라 stdout을 삼킨다.
            with contextlib.redirect_stdout(io.StringIO()):
                df = self._dart.finstate(code, year, reprt_code)
        except Exception as e:  # noqa: BLE001
            logger.debug("[DART] finstate(%s,%s,%s) 실패: %s", code, year, reprt_code, e)
            return None
        rev_row = _pick_account(df, _REVENUE_NAMES)
        ni_row = _pick_account(df, self._NET_INCOME_NAMES)
        if rev_row is None and ni_row is None:
            return None
        period_end = None
        for row in (rev_row, ni_row):
            if row is not None:
                period_end = _period_end(row.get("thstrm_dt"))
                if period_end:
                    break
        return _Statement(
            revenue_cum=_to_won(rev_row.get("thstrm_amount")) if rev_row is not None else None,
            revenue_prev_cum=_to_won(rev_row.get("frmtrm_amount")) if rev_row is not None else None,
            net_income_cum=_to_won(ni_row.get("thstrm_amount")) if ni_row is not None else None,
            net_income_prev_cum=_to_won(ni_row.get("frmtrm_amount")) if ni_row is not None else None,
            period_end=period_end,
        )

    def _latest(self, code: str, year: int):
        """올해 최신 보고서를 (reprt_code, _Statement)로. 없으면 None."""
        for rc in _PROBE_ORDER:
            st = self._statement(code, year, rc)
            if st is not None and st.has_data:
                return rc, st
        return None

    def revenue_income_ttm(self, code: str, *, year: int) -> DartResult:
        """종목코드의 TTM 매출/순이익(원) + 최신분기 단독 순이익."""
        result = DartResult()

        latest = self._latest(code, year)
        if latest is None:
            # 올해 보고서가 아직 없으면 작년 사업보고서(연간)를 TTM으로 사용
            prior_fy = self._statement(code, year - 1, "11011")
            if prior_fy is not None:
                result.revenue_ttm_won = prior_fy.revenue_cum
                result.net_income_ttm_won = prior_fy.net_income_cum
                result.period_end = prior_fy.period_end
            return result

        rc, st = latest
        result.period_end = st.period_end

        if rc == "11011":
            # 최신이 연간 = 그 자체가 TTM
            result.revenue_ttm_won = st.revenue_cum
            result.net_income_ttm_won = st.net_income_cum
        else:
            # 롤링: 올해누적 + 작년연간 − 작년동기누적(=frmtrm)
            prior_fy = self._statement(code, year - 1, "11011")
            result.revenue_ttm_won = _rolling(
                st.revenue_cum,
                prior_fy.revenue_cum if prior_fy else None,
                st.revenue_prev_cum,
            )
            result.net_income_ttm_won = _rolling(
                st.net_income_cum,
                prior_fy.net_income_cum if prior_fy else None,
                st.net_income_prev_cum,
            )

        result.latest_quarter_net_income_won = self._latest_quarter_net_income(
            code, year, rc, st
        )
        return result

    def _latest_quarter_net_income(
        self, code: str, year: int, rc: str, st: "_Statement"
    ) -> Optional[int]:
        """최신 단독분기 순이익 = 올해 최신누적 − 올해 직전누적."""
        prev_rc = _PREV_CUMULATIVE.get(rc)
        if prev_rc is None:  # 11013(Q1): 누적=단독
            return st.net_income_cum
        prev = self._statement(code, year, prev_rc)
        if prev is None or st.net_income_cum is None or prev.net_income_cum is None:
            return None
        return st.net_income_cum - prev.net_income_cum


def _rolling(cur_cum, prior_fy, prior_cum) -> Optional[int]:
    """TTM = cur_cum + prior_fy − prior_cum. 하나라도 None이면 None."""
    if cur_cum is None or prior_fy is None or prior_cum is None:
        return None
    return cur_cum + prior_fy - prior_cum
