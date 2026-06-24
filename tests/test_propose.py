"""M3 제안 모듈 단위 테스트.

LLM 호출/judge/KRX 네트워크를 모킹하므로 실제 키·네트워크 없이 통과한다.
KRX 검증은 합성 상장리스트(KrxValidator)로 실제 로직을 검증한다.
"""
from __future__ import annotations

from unittest import mock

import pytest

from src.propose import run_propose
from src.propose.krx import KrxValidator, Listing, normalize_name
from src.propose.merge import merge_theme
from src.propose.models import Proposal, ValidatedProposal
from src.propose.proposers.base import BaseProposer, parse_proposals


# --- 합성 KRX 리스트 ---------------------------------------------------------

LISTINGS = [
    Listing("005930", "삼성전자", "KOSPI"),
    Listing("005935", "삼성전자우", "KOSPI"),   # 우선주
    Listing("009150", "삼성전기", "KOSPI"),
    Listing("011070", "LG이노텍", "KOSPI"),
    Listing("000660", "SK하이닉스", "KOSPI"),
]
VALIDATOR = KrxValidator(LISTINGS)


# --- KRX 검증 ----------------------------------------------------------------

def test_normalize_name_strips_noise():
    assert normalize_name("(주)삼성 전자") == "삼성전자"
    assert normalize_name("LG이노텍") == "LG이노텍"


def test_validate_exact_match():
    hit = VALIDATOR.validate("삼성전기")
    assert hit is not None and hit.code == "009150" and hit.market == "KOSPI"


def test_validate_prefers_common_over_preferred():
    # '삼성전자'는 보통주(005930)와 우선주(005935 삼성전자우)가 있지만
    # 정규화 이름이 다르므로 보통주만 매칭된다.
    hit = VALIDATOR.validate("삼성전자")
    assert hit is not None and hit.code == "005930"


def test_validate_rejects_hallucinated_name():
    assert VALIDATOR.validate("없는전자") is None


def test_validate_ignores_wrong_code_falls_back_to_name():
    # 모델이 엉뚱한 코드를 줘도 이름이 맞으면 이름 매칭으로 정규화된다.
    hit = VALIDATOR.validate("LG이노텍", code="999999")
    assert hit is not None and hit.code == "011070"


def test_validate_accepts_matching_code():
    hit = VALIDATOR.validate("SK하이닉스", code="000660")
    assert hit is not None and hit.code == "000660"


# --- 제안 파싱 ---------------------------------------------------------------

def test_parse_proposals_defensive():
    text = '```json\n{"stocks": [{"name": "삼성전기", "code": "009150", "reason": "MLCC", "relevance": "높음"}]}\n```'
    props = parse_proposals(text, "claude")
    assert len(props) == 1
    assert props[0].name == "삼성전기" and props[0].relevance == "high"


def test_parse_proposals_drops_bad_code_and_empty_name():
    text = '{"stocks": [{"name": "", "code": "009150"}, {"name": "삼성전기", "code": "abc"}]}'
    props = parse_proposals(text, "gpt")
    assert len(props) == 1
    assert props[0].name == "삼성전기" and props[0].code is None  # 6자리 아닌 코드 무시


# --- 병합/합의도/랭킹 --------------------------------------------------------

def test_merge_dedups_by_code_and_scores_agreement():
    theme = {"keyword": "MLCC 공급부족", "category": "전자부품"}
    validated = [
        ValidatedProposal("claude", "009150", "삼성전기", "KOSPI", "MLCC 수혜", "high"),
        ValidatedProposal("gpt", "009150", "삼성전기", "KOSPI", "MLCC 1위", "high"),
        ValidatedProposal("claude", "011070", "LG이노텍", "KOSPI", "기판", "medium"),
    ]
    cands = merge_theme(theme, validated, top_k=5)
    assert len(cands) == 2
    # 합의도 높은 삼성전기가 먼저
    assert cands[0].code == "009150" and cands[0].agreement_score == 2
    assert cands[0].proposed_by == ["claude", "gpt"]
    assert cands[1].code == "011070" and cands[1].agreement_score == 1


def test_merge_same_model_twice_counts_one():
    theme = {"keyword": "k", "category": "c"}
    validated = [
        ValidatedProposal("claude", "009150", "삼성전기", "KOSPI", "r1", "high"),
        ValidatedProposal("claude", "009150", "삼성전기", "KOSPI", "r2", "low"),
    ]
    cands = merge_theme(theme, validated, top_k=5)
    assert cands[0].agreement_score == 1 and cands[0].proposed_by == ["claude"]


def test_merge_respects_top_k():
    theme = {"keyword": "k", "category": "c"}
    validated = [
        ValidatedProposal("claude", c, n, "KOSPI", "r", "medium")
        for c, n in [("005930", "삼성전자"), ("009150", "삼성전기"),
                     ("011070", "LG이노텍"), ("000660", "SK하이닉스")]
    ]
    assert len(merge_theme(theme, validated, top_k=2)) == 2


# --- 가짜 제안자로 run_propose 통합 -----------------------------------------

class _FakeProposer(BaseProposer):
    name = "fake"

    def __init__(self, stocks):
        super().__init__(model="fake-model")
        self._stocks = stocks

    def available(self):
        return True

    def _complete(self, system, prompt):  # 사용 안 함
        return ""

    def propose(self, theme):
        return [Proposal("fake", s["name"], s.get("reason", ""), s.get("relevance", "medium"))
                for s in self._stocks]


def test_run_propose_end_to_end_mocked():
    themes = {
        "scan_date": "2026-06-16", "window_days": 7,
        "themes": [
            {"keyword": "MLCC 공급부족", "category": "전자부품", "type": "shortage",
             "evidence": "AI 서버 MLCC 쇼티지"},
        ],
    }
    fake = _FakeProposer([
        {"name": "삼성전기", "reason": "MLCC 1위", "relevance": "high"},
        {"name": "없는전자", "reason": "환각", "relevance": "high"},  # 드롭돼야 함
    ])
    judged = {"009150": {"relation_reason": "AI 서버 MLCC 최대 수혜", "relevance": "high"}}

    with mock.patch("src.propose.build_proposers", return_value=[fake]), \
         mock.patch("src.propose.judge_theme", return_value=judged):
        result = run_propose(themes, settings={}, judge_api_key="k", validator=VALIDATOR)

    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.code == "009150" and c.name == "삼성전기"
    assert c.relation_reason == "AI 서버 MLCC 최대 수혜" and c.relevance == "high"
    # 환각 종목은 dropped로
    assert any(d.proposed_name == "없는전자" for d in result.dropped)


def test_run_propose_raises_without_proposers():
    from src.propose import ProposeError
    with mock.patch("src.propose.build_proposers", return_value=[]):
        with pytest.raises(ProposeError):
            run_propose({"themes": []}, settings={}, validator=VALIDATOR)


def test_run_propose_isolates_proposer_failure():
    """한 제안자가 예외를 던져도 전체는 죽지 않는다(NF8)."""
    class _BoomProposer(BaseProposer):
        name = "boom"
        def __init__(self): super().__init__(model="x")
        def available(self): return True
        def _complete(self, s, p): return ""
        def propose(self, theme): raise RuntimeError("boom")

    themes = {"scan_date": "2026-06-16", "window_days": 7,
              "themes": [{"keyword": "k", "category": "c", "type": "shortage", "evidence": "e"}]}
    with mock.patch("src.propose.build_proposers", return_value=[_BoomProposer()]):
        result = run_propose(themes, settings={}, judge_api_key="k", validator=VALIDATOR)
    assert result.candidates == []  # 크래시 없이 빈 결과
