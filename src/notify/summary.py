"""리포트 컨텍스트 → 알림용 요약 텍스트.

푸시는 길이 제한(텔레그램 sendMessage ≈ 4096자)이 있으므로 테마별 상위 종목만
간추린다. 전체는 첨부 리포트(report.html/md)로 보내고, 본문은 한눈 요약 + 면책
고지로 구성한다. 면책 고지는 상품 원칙이라 알림에도 반드시 포함한다.
"""
from __future__ import annotations

from src.report.render import _relevance_label, format_eok

# 푸시 1건 안전 상한(텔레그램 4096자 한도 + 첨부 안내 여유분).
_MAX_LEN = 3500
# 테마당 요약에 노출할 상위 종목 수.
_TOP_PER_THEME = 3

# 알림용 짧은 면책(전체 면책은 첨부 리포트에 포함됨).
SHORT_DISCLAIMER = "⚠️ 투자 추천이 아닌 ‘검증된 스크리닝 후보(가설)’입니다. 투자 판단·책임은 본인에게 있습니다."


def build_subject(context: dict) -> str:
    """이메일 제목/알림 헤더용 한 줄."""
    return f"[{context.get('scan_date', '')}] {context.get('title', '주간 리포트')}"


def build_summary_text(context: dict, *, max_len: int = _MAX_LEN) -> str:
    """리포트 컨텍스트 → 플레인 텍스트 요약(면책 고지 포함, 길이 제한 적용)."""
    lines = []
    lines.append(f"📊 {context.get('title', '주간 리포트')}")
    lines.append(
        f"스캔 {context.get('scan_date', '')} · "
        f"테마 {context.get('n_themes', 0)}개 · 후보 {context.get('n_stocks', 0)}종목"
    )

    for t in context.get("themes", []):
        lines.append("")
        lines.append(f"• [{t.get('category', '')}] {t.get('keyword', '')}")
        for s in t.get("stocks", [])[:_TOP_PER_THEME]:
            lines.append(
                f"   - {s.get('name', '')}"
                f" ({s.get('agreement_score', 0)}/3, {_relevance_label(s.get('relevance'))})"
                f" {format_eok(s.get('market_cap_eokwon'))}"
            )

    lines.append("")
    lines.append(SHORT_DISCLAIMER)
    text = "\n".join(lines)

    if len(text) > max_len:
        cut = text[: max_len - 40].rstrip()
        text = cut + "\n…(생략 — 전체는 첨부 리포트 참고)\n\n" + SHORT_DISCLAIMER
    return text
