"""M7 notify 결과 스키마.

발송 결과를 채널별로 기록한다(성공/건너뜀/실패). 알림은 보조 기능이라
한 채널 실패가 다른 채널·파이프라인을 막지 않는다(NF4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class NotifyResult:
    sent: List[str] = field(default_factory=list)              # 발송 성공 채널
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (채널, 사유)
    errors: List[Tuple[str, str]] = field(default_factory=list)   # (채널, 에러 메시지)

    @property
    def any_sent(self) -> bool:
        return bool(self.sent)

    def to_dict(self) -> dict:
        return {
            "sent": list(self.sent),
            "skipped": [list(s) for s in self.skipped],
            "errors": [list(e) for e in self.errors],
        }
