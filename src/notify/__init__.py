"""M7 notify — 주간 리포트를 텔레그램/이메일로 발송(선택 기능).

입력:  data/enriched.json + reports/{scan_date}/ (M4·M5 산출)
동작:  요약 텍스트 + 리포트 파일 첨부를 설정된 채널로 발송.

설계 원칙(기존 단계와 동일):
- **채널은 인터페이스 뒤에** — 텔레그램·SMTP 각각 `from_env`/`send`. 채널 추가 용이.
- **config·키로 degrade(NF4)** — `notify.{telegram,email}` 가 off 거나 키가 없으면
  그 채널만 건너뛴다. 한 채널 실패가 다른 채널·파이프라인을 막지 않는다.
- **키는 env/Secrets 로만(NF6)** — 코드/레포에 하드코딩 금지.
- **모듈 독립 재실행** — enriched.json 만 있으면 요약을 재구성해 단독 발송 가능.
- **면책 고지 포함** — 요약에 짧은 면책, 첨부 리포트에 전체 면책.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from src.report.render import build_context

from .email_smtp import SmtpNotifier
from .models import NotifyResult
from .summary import build_subject, build_summary_text
from .telegram import TelegramNotifier

__all__ = [
    "run_notify",
    "NotifyResult",
    "NotifyError",
    "TelegramNotifier",
    "SmtpNotifier",
]

logger = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    """M7 notify 단계 치명적 실패(거의 쓰이지 않음 — 보통 채널별 degrade)."""


def run_notify(
    context: dict,
    *,
    settings: Optional[dict] = None,
    report_files: Optional[List[Path]] = None,
    env=None,
    telegram: Optional[TelegramNotifier] = None,
    smtp: Optional[SmtpNotifier] = None,
) -> NotifyResult:
    """리포트 컨텍스트 → 설정된 채널로 발송. 채널별 실패는 격리한다(NF4).

    telegram/smtp 를 주입하면 그대로 쓰고(테스트), 아니면 env 에서 구성한다.
    """
    settings = settings or {}
    env = env if env is not None else os.environ
    notify_cfg = settings.get("notify", {}) or {}
    result = NotifyResult()

    want_tg = bool(notify_cfg.get("telegram", False))
    want_email = bool(notify_cfg.get("email", False))
    attach = bool(notify_cfg.get("attach_report", True))
    attachments = list(report_files or []) if attach else None

    if not want_tg and not want_email:
        logger.info("notify 비활성(notify.telegram/email 모두 false) — 발송 생략.")
        return result

    subject = build_subject(context)
    summary = build_summary_text(context)

    # --- 텔레그램 ---
    if want_tg:
        tg = telegram if telegram is not None else TelegramNotifier.from_env(env)
        if tg is None:
            result.skipped.append(("telegram", "TELEGRAM_BOT_TOKEN/CHAT_ID 없음"))
            logger.warning("[notify] telegram 건너뜀 — 키 없음(degrade).")
        else:
            try:
                tg.send(summary, attachments=attachments)
                result.sent.append("telegram")
                logger.info("[notify] telegram 발송 완료.")
            except Exception as e:  # noqa: BLE001 — 채널 격리
                result.errors.append(("telegram", str(e)))
                logger.warning("[notify] telegram 발송 실패: %s", e)

    # --- 이메일(SMTP) ---
    if want_email:
        mailer = smtp if smtp is not None else SmtpNotifier.from_env(env)
        if mailer is None:
            result.skipped.append(("email", "SMTP_HOST/USER/PASSWORD/FROM/TO 불충분"))
            logger.warning("[notify] email 건너뜀 — 설정 부족(degrade).")
        else:
            try:
                mailer.send(subject, summary, attachments=attachments)
                result.sent.append("email")
                logger.info("[notify] email 발송 완료.")
            except Exception as e:  # noqa: BLE001 — 채널 격리
                result.errors.append(("email", str(e)))
                logger.warning("[notify] email 발송 실패: %s", e)

    return result


def build_notify_context(enriched: dict, *, generated_at: str, settings: Optional[dict] = None) -> dict:
    """enriched dict → 요약용 컨텍스트(리포트와 동일 빌더 재사용, 독립 재실행 지원)."""
    return build_context(enriched, generated_at=generated_at, settings=settings or {})
