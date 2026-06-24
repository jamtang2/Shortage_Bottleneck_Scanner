"""테마 추출 결과 데이터 스키마 (M2).

`data/themes.json` 데이터 계약을 정의한다. 다운스트림 M3(propose)가
이 파일을 입력으로 받는다. (PRD §6 Step 1)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List

# 허용 confidence 값: "high" | "medium" | "low"
# 허용 type 값(권장): "shortage" | "bottleneck" | "capacity_delay"
#                    | "leadtime" | "production_cut" | "other"


@dataclass
class ThemeSource:
    title: str
    url: str
    date: str  # "YYYY-MM-DD"
    publisher: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Theme:
    keyword: str         # 대표 키워드(유사어 병합 후)
    category: str        # 산업/섹터 분류 (예: "반도체", "2차전지")
    type: str            # shortage | bottleneck | capacity_delay | leadtime | production_cut | other
    evidence: str        # 한국어 근거 요약
    confidence: str      # high | medium | low
    sources: List[ThemeSource] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sources"] = [s.to_dict() for s in self.sources]
        return d


@dataclass
class ThemesResult:
    scan_date: str       # "YYYY-MM-DD"
    window_days: int
    themes: List[Theme] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scan_date": self.scan_date,
            "window_days": self.window_days,
            "themes": [t.to_dict() for t in self.themes],
        }
