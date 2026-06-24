"""M7 notify 모듈 단위 테스트.

발송은 합성 notifier 를 주입해 네트워크·키 없이 검증한다. 요약 텍스트(면책 고지
포함)·채널 degrade·에러 격리·from_env 구성 로직을 다룬다.
"""
from __future__ import annotations

from src.notify import NotifyResult, run_notify
from src.notify.email_smtp import SmtpNotifier
from src.notify.summary import SHORT_DISCLAIMER, build_subject, build_summary_text
from src.notify.telegram import TelegramNotifier


CONTEXT = {
    "title": "쇼티지·병목 수혜주 스캐너 주간 리포트",
    "scan_date": "2026-06-16",
    "n_themes": 2,
    "n_stocks": 3,
    "themes": [
        {
            "keyword": "HBM 공급부족", "category": "반도체",
            "stocks": [
                {"name": "SK하이닉스", "agreement_score": 3, "relevance": "high",
                 "market_cap_eokwon": 19699093.4},
                {"name": "한미반도체", "agreement_score": 3, "relevance": "high",
                 "market_cap_eokwon": 280000.0},
            ],
        },
        {
            "keyword": "석유화학 NCC", "category": "석유화학",
            "stocks": [
                {"name": "롯데케미칼", "agreement_score": 2, "relevance": "low",
                 "market_cap_eokwon": 30000.0},
            ],
        },
    ],
}


# --- 요약(면책 고지 필수) ----------------------------------------------------

def test_summary_contains_disclaimer_and_stocks():
    text = build_summary_text(CONTEXT)
    assert SHORT_DISCLAIMER in text          # 알림에도 면책 고지 포함
    assert "SK하이닉스" in text and "한미반도체" in text
    assert "HBM 공급부족" in text and "석유화학 NCC" in text
    assert "1,969.9조원" in text             # 억원 포맷 적용
    assert "(3/3, 높음)" in text


def test_summary_truncates_long_and_keeps_disclaimer():
    big = {**CONTEXT, "themes": [
        {"keyword": f"테마{i}", "category": "C",
         "stocks": [{"name": f"종목{i}", "agreement_score": 1, "relevance": "low",
                     "market_cap_eokwon": 1000.0}]}
        for i in range(500)
    ]}
    text = build_summary_text(big, max_len=1000)
    assert len(text) <= 1000 + len(SHORT_DISCLAIMER) + 50
    assert SHORT_DISCLAIMER in text          # 잘려도 면책은 남는다
    assert "생략" in text


def test_build_subject():
    assert build_subject(CONTEXT) == "[2026-06-16] 쇼티지·병목 수혜주 스캐너 주간 리포트"


# --- 합성 notifier 주입 발송 -------------------------------------------------

class FakeTelegram:
    def __init__(self):
        self.calls = []

    def send(self, text, attachments=None):
        self.calls.append((text, list(attachments or [])))


class FakeSmtp:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def send(self, subject, body, attachments=None):
        if self.fail:
            raise RuntimeError("smtp down")
        self.calls.append((subject, body, list(attachments or [])))


def test_run_notify_sends_to_enabled_channels():
    tg, mail = FakeTelegram(), FakeSmtp()
    res = run_notify(
        CONTEXT,
        settings={"notify": {"telegram": True, "email": True, "attach_report": True}},
        report_files=["reports/2026-06-16/report.html"],
        telegram=tg, smtp=mail,
    )
    assert set(res.sent) == {"telegram", "email"}
    assert not res.errors and not res.skipped
    # 첨부가 양 채널에 전달됨
    assert tg.calls[0][1] == ["reports/2026-06-16/report.html"]
    assert mail.calls[0][2] == ["reports/2026-06-16/report.html"]
    # 요약 본문에 면책 고지
    assert SHORT_DISCLAIMER in tg.calls[0][0]


def test_run_notify_all_off_sends_nothing():
    tg = FakeTelegram()
    res = run_notify(CONTEXT, settings={"notify": {"telegram": False, "email": False}},
                     telegram=tg)
    assert res.sent == [] and not tg.calls


def test_run_notify_channel_error_is_isolated():
    tg, mail = FakeTelegram(), FakeSmtp(fail=True)
    res = run_notify(
        CONTEXT,
        settings={"notify": {"telegram": True, "email": True}},
        telegram=tg, smtp=mail,
    )
    assert res.sent == ["telegram"]                 # 텔레그램은 성공
    assert res.errors and res.errors[0][0] == "email"  # 이메일만 실패 기록


def test_run_notify_no_attach_when_disabled():
    tg = FakeTelegram()
    run_notify(CONTEXT, settings={"notify": {"telegram": True, "attach_report": False}},
               report_files=["reports/x/report.md"], telegram=tg)
    assert tg.calls[0][1] == []                      # 첨부 안 함


# --- from_env degrade(키 없음 → None) ---------------------------------------

def test_telegram_from_env_requires_both():
    assert TelegramNotifier.from_env({}) is None
    assert TelegramNotifier.from_env({"TELEGRAM_BOT_TOKEN": "t"}) is None
    n = TelegramNotifier.from_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"})
    assert isinstance(n, TelegramNotifier)


def test_smtp_from_env_requires_full_config():
    assert SmtpNotifier.from_env({"SMTP_HOST": "h"}) is None
    full = {
        "SMTP_HOST": "smtp.example.com", "SMTP_USER": "u@example.com",
        "SMTP_PASSWORD": "pw", "SMTP_FROM": "u@example.com",
        "SMTP_TO": "a@example.com, b@example.com",
    }
    n = SmtpNotifier.from_env(full)
    assert isinstance(n, SmtpNotifier)
    # 다중 수신자 파싱 + 본문/첨부 메시지 구성(네트워크 없이 검증)
    msg = n.build_message("제목", "본문", attachments=None)
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Subject"] == "제목" and "본문" in msg.get_content()


def test_run_notify_skips_when_env_missing():
    # 채널은 켜져 있으나 키가 없으면 skipped 로 degrade
    res = run_notify(CONTEXT, settings={"notify": {"telegram": True, "email": True}}, env={})
    assert res.sent == []
    assert {c for c, _ in res.skipped} == {"telegram", "email"}
    assert isinstance(res, NotifyResult)
