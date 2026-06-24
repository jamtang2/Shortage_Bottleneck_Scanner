# 쇼티지·병목 수혜주 스캐너 (Shortage/Bottleneck Scanner)

매주 뉴스·컨센서스에서 '쇼티지/병목' 테마를 자동 탐지하고, 관련 상장사 후보를
재무지표와 함께 리포트로 산출하는 개인 리서치용 주간 배치 도구.

> 출력되는 종목은 **검증된 스크리닝 후보(가설)**이며 투자 추천이 아닙니다.

## 현재 상태

- **M1 (수집 모듈) 구현 완료** — 네이버 뉴스 → `data/raw_articles.json`
- **M2 (추출 모듈) 구현 완료** — Claude가 기사에서 쇼티지/병목 테마 추출 →
  `data/themes.json`. 출처 URL 환각 방지(기사 id 인용 후 실제 기사로 복원),
  공급망 무관 노이즈 필터링, confidence(high/medium/low) 부여.
- **M3 (제안 모듈) 구현 완료** — 멀티 LLM(Claude·GPT·Gemini)이 테마별 수혜 종목 제안
  → **모델별 KRX 상장리스트 검증으로 환각 티커 제거** → 코드 병합/`agreement_score`
  → judge(Claude)가 최종 사유·relevance 작성 → `data/candidates.json`. GPT/Gemini는
  키가 없으면 자동 비활성(앙상블 degrade), 검증 실패 종목은 `dropped`로 기록.
- **M4 (재무결합 모듈) 구현 완료** — 후보 종목에 시가총액·매출 TTM·PER 결합 →
  `data/enriched.json`. 시총은 **FinanceDataReader**(pykrx는 KRX 로그인 장벽으로 비사용),
  매출/순이익은 **OpenDART 롤링 TTM**(올해누적+작년연간−작년동기), PER은 시총÷순이익TTM로
  파생. 각 수치에 기준일(`data_asof`), DART 키 없으면 시총만 채우고 degrade(NF4).
- **M5 (리포트 모듈) 구현 완료** — `enriched.json`을 **테마별로 묶어** Jinja2 HTML +
  Markdown 주간 리포트로 렌더 → `reports/{scan_date}/`. 억원/PER 포맷, 합의 점수·관련도
  정렬, 기준일 표기. **면책 고지는 상품 원칙이라 상·하단에 항상 포함**(설정으로 끌 수 없음).
- **M6 (스케줄 자동화) 구현 완료** — GitHub Actions 워크플로 2종.
  `weekly-scan.yml`은 **주 1회(기본 월 08:00 KST = 일 23:00 UTC) cron + 수동 실행**으로
  전체 파이프라인을 돌려 `reports/{scan_date}/`를 생성·커밋하고 아티팩트로도 업로드.
  키는 **GitHub Secrets**로 주입(NF6), 일부 키가 없어도 단계별 degrade(NF4). `ci.yml`은
  push/PR마다 모킹 테스트를 돌려 코드 건전성을 지킵니다. **배포 절차는 아래 참고.**
- **M7 (알림 모듈, 선택) 구현 완료** — 리포트 요약을 **텔레그램/이메일(SMTP)**로 발송 →
  `src/notify/`. 테마별 상위 종목 요약 + 리포트 파일 첨부, **면책 고지 포함**. 채널은
  `config`로 on/off하고 키(`.env`/Secrets)가 없으면 그 채널만 건너뜁니다(degrade, NF4).
  기본값은 둘 다 off라 키를 넣고 켜야 발송됩니다.
- 한경 컨센서스는 robots.txt(`Disallow: /`)가 자동수집을 금지해 **비활성화**
  (`sources.consensus: false`). 설계상 뉴스만으로 정상 동작.

**→ M1~M7 전 단계 구현 완료.**

전체 설계는 `PRD/PRD_shortage_bottleneck_scanner.md.pdf`, 작업 규칙은 `CLAUDE.md` 참고.

## 설치

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell:  .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

요구: **Python 3.13+** (OpenDartReader 0.3 이 `>=3.13` 요구. 로컬·CI 모두 3.13 사용)

## 환경변수

`.env.example`을 `.env`로 복사한 뒤 값을 채웁니다. **M1에는 네이버 키, M2에는
`ANTHROPIC_API_KEY`가 필요**합니다.

```bash
cp .env.example .env
```

| 변수 | 필요 시점 | 발급처 |
|------|-----------|--------|
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | **M1** | 네이버 개발자센터 검색 API |
| `ANTHROPIC_API_KEY` | **M2 / M3(judge·제안자)** | Anthropic |
| `OPENAI_API_KEY` | M3 GPT 제안자(선택) | OpenAI |
| `GOOGLE_API_KEY` | M3 Gemini 제안자(선택) | Google AI Studio |
| `DART_API_KEY` | M4 | OpenDART |

API 키는 코드/리포지토리에 하드코딩하지 않습니다(`.env`는 gitignore 처리됨).

## 실행

```bash
python -m src.pipeline
```

`config/settings.yaml`의 키워드·기간·소스 on/off·모델·`report.formats` 설정으로 동작을
조정합니다. 실행 결과는 `data/`의 단계별 JSON(M1~M4)과 `reports/{scan_date}/`의
`report.html`·`report.md`(M5)에 저장됩니다.

- 네이버 키가 없거나 한 소스(예: 컨센서스)가 실패해도 크래시 없이 나머지 소스
  결과로 진행됩니다.
- `ANTHROPIC_API_KEY`가 없거나 M2가 실패하면 해당 단계만 로그를 남기고 건너뛰며,
  수집 결과(`raw_articles.json`)는 그대로 저장됩니다.

## 테스트

```bash
pytest
```

외부 API를 모킹하므로 실제 키 없이도 통과합니다.

## 자동 실행(M6 — GitHub Actions)

`.github/workflows/weekly-scan.yml`이 매주 파이프라인을 자동 실행해 리포트를
커밋합니다. **로컬 코드만으로는 동작하지 않고, GitHub에 올린 뒤 Secrets를 등록해야**
합니다. 배포 절차:

1. **저장소 올리기** — `git init && git add . && git commit -m "init"` 후 GitHub에
   **private** 저장소로 push. (`.env`는 `.gitignore`로 빠지므로 키는 올라가지 않습니다.)
2. **Secrets 등록** — 저장소 → Settings → Secrets and variables → Actions →
   *New repository secret*. `.env`의 키들을 **동일한 이름**으로 넣습니다:

   | Secret 이름 | 비고 |
   |-------------|------|
   | `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 뉴스 수집(M1) |
   | `ANTHROPIC_API_KEY` | 추출·judge·제안(M2/M3) |
   | `OPENAI_API_KEY` / `GOOGLE_API_KEY` | GPT·Gemini 제안자(선택) |
   | `DART_API_KEY` | 재무(M4, 없으면 시총만) |
   | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 알림(M7, 선택) |
   | `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM`/`SMTP_TO` | 이메일 알림(M7, 선택) |

3. **수동 실행으로 첫 검증** — Actions 탭 → *Weekly Shortage Scan* → *Run workflow*.
   성공하면 `reports/{날짜}/`가 새 커밋으로 올라오고, 실행 화면에서 아티팩트도 받을 수
   있습니다.
4. 이후 매주 월 08:00 KST 자동 실행. 시간을 바꾸려면 워크플로의 `cron`과
   `config/settings.yaml`의 `schedule` 블록을 함께 수정합니다.

> ⚠️ 이미 채팅·로컬에 노출된 키(Anthropic·OpenAI·Google·DART 등)는 **공개/협업
> 저장소에 올리기 전에 각 콘솔에서 재발급**한 뒤 Secrets에 새 값을 넣으세요.

## 알림(M7 — 선택)

리포트 생성 후 요약을 **텔레그램/이메일**로 받을 수 있습니다. 기본은 꺼져 있으며,
`config/settings.yaml`의 `notify`에서 채널을 켜고 키를 채우면 발송됩니다.

```yaml
notify:
  telegram: true      # TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 필요
  email: false        # SMTP_HOST/PORT/USER/PASSWORD/FROM/TO 필요
  attach_report: true # report.html·report.md 첨부
```

- **텔레그램**: @BotFather로 봇을 만들어 토큰을 받고, 봇과 대화를 시작한 뒤
  `chat_id`를 확인해 `.env`(로컬)/Secrets(CI)에 넣습니다.
- **이메일(SMTP)**: 예) Gmail은 `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`,
  앱 비밀번호를 `SMTP_PASSWORD`로 사용합니다.
- 키가 없으면 그 채널만 건너뛰고(degrade) 파이프라인은 정상 종료됩니다.
- `enriched.json`만 있으면 알림 단계를 단독 재실행할 수 있습니다:
  `python -c "from src import pipeline as p; p.run_notify_stage(p.load_settings())"`

## 상태

**M1~M7 전 단계 구현 완료** — 수집·추출·제안·재무·리포트·스케줄·알림.
