"""SMTP 이메일 알림 채널.

표준 라이브러리(`smtplib`/`email`)만 쓴다. 본문에 요약을, 리포트 파일을 첨부로
보낸다. 필수 설정(host/user/password/from/to)이 없으면 `from_env` 가 None 을 돌려
상위에서 degrade(건너뜀)한다(NF4/NF6). 메시지 구성(`build_message`)과 전송을
분리해 네트워크 없이 본문/첨부를 검증할 수 있게 했다.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class SmtpNotifier:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
        recipients: List[str],
        *,
        use_tls: bool = True,
        timeout: int = 30,
    ):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._sender = sender
        self._recipients = recipients
        self._use_tls = use_tls
        self._timeout = timeout

    @classmethod
    def from_env(cls, env) -> Optional["SmtpNotifier"]:
        host = (env.get("SMTP_HOST") or "").strip()
        user = (env.get("SMTP_USER") or "").strip()
        password = (env.get("SMTP_PASSWORD") or "").strip()
        sender = (env.get("SMTP_FROM") or user).strip()
        to_raw = (env.get("SMTP_TO") or "").strip()
        if not host or not user or not password or not sender or not to_raw:
            return None
        recipients = [a.strip() for a in to_raw.replace(";", ",").split(",") if a.strip()]
        if not recipients:
            return None
        port = int((env.get("SMTP_PORT") or "587").strip() or 587)
        use_tls = (env.get("SMTP_USE_TLS") or "true").strip().lower() != "false"
        return cls(host, port, user, password, sender, recipients, use_tls=use_tls)

    def build_message(
        self, subject: str, body: str, attachments: Optional[List[Path]] = None
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._sender
        msg["To"] = ", ".join(self._recipients)
        msg.set_content(body)
        for path in attachments or []:
            p = Path(path)
            if not p.exists():
                logger.debug("[smtp] 첨부 없음(건너뜀): %s", p)
                continue
            data = p.read_bytes()
            subtype = "html" if p.suffix.lower() in (".html", ".htm") else "plain"
            maintype = "text" if subtype in ("html", "plain") else "application"
            if maintype == "text":
                msg.add_attachment(
                    data.decode("utf-8", "replace"),
                    subtype=subtype,
                    filename=p.name,
                )
            else:
                msg.add_attachment(
                    data, maintype="application", subtype="octet-stream", filename=p.name
                )
        return msg

    def send(
        self, subject: str, body: str, attachments: Optional[List[Path]] = None
    ) -> None:
        msg = self.build_message(subject, body, attachments)
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
            if self._use_tls:
                server.starttls()
            server.login(self._user, self._password)
            server.send_message(msg)
