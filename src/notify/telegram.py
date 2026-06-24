"""텔레그램 Bot API 알림 채널.

요약 텍스트는 sendMessage 로, 리포트 파일은 sendDocument 로 보낸다. 새 의존성
없이 기존 `requests` 만 쓴다. 토큰/챗ID 가 없으면 `from_env` 가 None 을 돌려
상위에서 degrade(건너뜀) 처리한다(NF4/NF6).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, *, session=None, timeout: int = 30):
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout
        if session is None:
            import requests

            session = requests
        self._session = session

    @classmethod
    def from_env(cls, env) -> Optional["TelegramNotifier"]:
        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
        if not token or not chat_id:
            return None
        return cls(token, chat_id)

    def send(self, text: str, attachments: Optional[List[Path]] = None) -> None:
        """요약 메시지 발송 후, 있으면 리포트 파일을 문서로 첨부."""
        resp = self._session.post(
            f"{_API}/bot{self._token}/sendMessage",
            data={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
            timeout=self._timeout,
        )
        resp.raise_for_status()

        for path in attachments or []:
            p = Path(path)
            if not p.exists():
                logger.debug("[telegram] 첨부 없음(건너뜀): %s", p)
                continue
            with open(p, "rb") as fh:
                r = self._session.post(
                    f"{_API}/bot{self._token}/sendDocument",
                    data={"chat_id": self._chat_id},
                    files={"document": (p.name, fh)},
                    timeout=self._timeout * 2,
                )
            r.raise_for_status()
