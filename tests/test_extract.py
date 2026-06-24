"""M2 추출 모듈 단위 테스트.

Claude 호출(_call_claude)을 모킹하므로 실제 키 없이도 통과해야 한다.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from src.extract import ExtractError, run_extract
from src.extract import claude_extract
from src.extract.claude_extract import (
    _coerce_themes,
    _parse_json_object,
    build_prompt,
    extract_themes,
)


ARTICLES = [
    {"source": "naver_news", "keyword": "쇼티지", "title": "삼성전기 MLCC 공급부족",
     "summary": "AI 서버용 고용량 MLCC 쇼티지 심화", "url": "https://n/a",
     "date": "2026-06-14", "publisher": "한국경제"},
    {"source": "naver_news", "keyword": "공급부족", "title": "MLCC 리드타임 급증",
     "summary": "전장용 MLCC 주문 폭증으로 납기 지연", "url": "https://n/b",
     "date": "2026-06-13", "publisher": "전자신문"},
    {"source": "naver_news", "keyword": "병목", "title": "출근길 도로 병목 정체",
     "summary": "교통 혼잡으로 시민 불편", "url": "https://n/c",
     "date": "2026-06-12", "publisher": "교통뉴스"},
]


# --- 프롬프트 빌드 -----------------------------------------------------------

def test_build_prompt_includes_ids_and_count():
    p = build_prompt(ARTICLES, window_days=7)
    assert "[0]" in p and "[1]" in p and "[2]" in p
    assert "기사 3건" in p
    assert "삼성전기 MLCC 공급부족" in p


# --- JSON 방어 파싱 ----------------------------------------------------------

def test_parse_json_object_strips_fence():
    out = _parse_json_object('```json\n{"themes": []}\n```')
    assert out == {"themes": []}


def test_parse_json_object_extracts_from_noise():
    out = _parse_json_object('설명 좀 하고 {"themes": [1]} 끝.')
    assert out == {"themes": [1]}


def test_parse_json_object_empty_raises():
    with pytest.raises(ValueError):
        _parse_json_object("")


# --- 검증/매핑 (환각 URL 차단 포함) ------------------------------------------

def test_coerce_maps_source_ids_to_real_articles():
    parsed = {"themes": [
        {"keyword": "MLCC 공급부족", "category": "수동소자", "type": "shortage",
         "evidence": "AI 서버 수요로 MLCC 쇼티지", "confidence": "high",
         "source_ids": [0, 1]},
    ]}
    themes = _coerce_themes(parsed, ARTICLES)
    assert len(themes) == 1
    t = themes[0]
    assert t.confidence == "high" and t.type == "shortage"
    # sources는 실제 기사에서 복원 — URL 환각 불가
    assert [s.url for s in t.sources] == ["https://n/a", "https://n/b"]


def test_coerce_drops_out_of_range_ids_and_themes_without_sources():
    parsed = {"themes": [
        {"keyword": "유령테마", "category": "x", "type": "shortage",
         "evidence": "근거", "confidence": "low", "source_ids": [99, -1]},
    ]}
    assert _coerce_themes(parsed, ARTICLES) == []  # 유효 출처 없음 → 드롭


def test_coerce_drops_missing_keyword_or_evidence():
    parsed = {"themes": [
        {"keyword": "", "evidence": "근거", "source_ids": [0]},
        {"keyword": "키워드", "evidence": "", "source_ids": [0]},
    ]}
    assert _coerce_themes(parsed, ARTICLES) == []


def test_coerce_normalizes_confidence_and_type():
    parsed = {"themes": [
        {"keyword": "k", "category": "c", "type": "이상한값",
         "evidence": "e", "confidence": "높음", "source_ids": [0]},
    ]}
    t = _coerce_themes(parsed, ARTICLES)[0]
    assert t.confidence == "high"
    assert t.type == "other"


def test_coerce_dedups_source_urls():
    parsed = {"themes": [
        {"keyword": "k", "category": "c", "type": "shortage",
         "evidence": "e", "confidence": "low", "source_ids": [0, 0]},
    ]}
    t = _coerce_themes(parsed, ARTICLES)[0]
    assert len(t.sources) == 1


# --- extract_themes (Claude 모킹) -------------------------------------------

def test_extract_themes_end_to_end_mocked():
    response = json.dumps({"themes": [
        {"keyword": "MLCC 공급부족", "category": "수동소자", "type": "shortage",
         "evidence": "AI 서버 수요로 MLCC 쇼티지 심화", "confidence": "high",
         "source_ids": [0, 1]},
    ]})
    with mock.patch.object(claude_extract, "_call_claude", return_value=response):
        themes = extract_themes(ARTICLES, window_days=7, model="m", api_key="k")
    assert len(themes) == 1
    assert themes[0].keyword == "MLCC 공급부족"


def test_extract_themes_empty_articles_returns_empty():
    assert extract_themes([], window_days=7, model="m", api_key="k") == []


# --- run_extract 오케스트레이션 ---------------------------------------------

def test_run_extract_without_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    raw = {"collected_at": "2026-06-16T00:00:00+00:00", "window_days": 7,
           "keywords": ["쇼티지"], "articles": ARTICLES}
    with pytest.raises(ExtractError):
        run_extract(raw)


def test_run_extract_builds_result_with_scan_date():
    response = json.dumps({"themes": [
        {"keyword": "MLCC 공급부족", "category": "수동소자", "type": "shortage",
         "evidence": "근거", "confidence": "medium", "source_ids": [0]},
    ]})
    raw = {"collected_at": "2026-06-16T01:02:03+00:00", "window_days": 7,
           "keywords": ["쇼티지"], "articles": ARTICLES}
    with mock.patch.object(claude_extract, "_call_claude", return_value=response):
        result = run_extract(raw, settings={"extract": {"model": "m"}}, api_key="k")
    assert result.scan_date == "2026-06-16"
    assert result.window_days == 7
    d = result.to_dict()
    assert set(d) == {"scan_date", "window_days", "themes"}
    assert set(d["themes"][0]) == {
        "keyword", "category", "type", "evidence", "confidence", "sources",
    }


def test_run_extract_wraps_failure_as_extract_error():
    raw = {"collected_at": "2026-06-16T00:00:00+00:00", "window_days": 7,
           "keywords": [], "articles": ARTICLES}
    with mock.patch.object(claude_extract, "_call_claude", side_effect=RuntimeError("boom")):
        with pytest.raises(ExtractError):
            run_extract(raw, api_key="k")
