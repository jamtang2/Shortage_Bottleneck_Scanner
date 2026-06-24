# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Current milestone: M1 (collect) + M2 (extract) + M3 (propose) + M4 (enrich) + M5
(report) + M6 (schedule) implemented.** Full design is in the PRD at
`PRD/PRD_shortage_bottleneck_scanner.md.pdf` (Korean). Follow the PRD milestone order
(M1→M7) and build one stage at a time, validating each stage's JSON/report artifact by
eye before moving on — the PRD explicitly forbids implementing the whole pipeline at once.
Next up: M7 (optional Telegram/SMTP notify after report).

**M4 design deviation (pykrx → FinanceDataReader):** the PRD names pykrx for market
cap/PER, but pykrx ≥1.2.x returns empty responses behind KRX data-portal login/bot
walls. M4 instead uses FDR `StockListing('KRX')` `Marcap` column (already a proven M3
dependency, latest-trading-day snapshot → NF3 holiday-correction is automatic) for market
cap, and derives PER from DART net income (`per_ttm = market_cap / net_income_ttm`, which
is financially identical to trailing PER). Revenue/net-income TTM come from OpenDART via a
rolling formula (see enrich section). DART_API_KEY absent → market cap still fills,
revenue/PER degrade to null (NF4).

## Commands

```bash
pip install -r requirements.txt   # M1–M5 deps
python -m src.pipeline            # collect → extract → propose → enrich → report; writes raw_articles/themes/candidates/enriched.json + reports/{scan_date}/
pytest                           # all tests (58); mock network + LLM, pass without real keys
pytest tests/test_collect.py::test_dedup_and_sort   # run a single test
```

M1 needs `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`; M2 + the M3 judge need
`ANTHROPIC_API_KEY`; M3's GPT/Gemini proposers need `OPENAI_API_KEY` / `GOOGLE_API_KEY`
(absent key → that proposer is skipped, ensemble degrades). M4's market cap needs no key
(FDR); its revenue/PER need `DART_API_KEY` (absent → market cap still fills, revenue/PER
null). M5 (report) needs no key. Copy `.env.example`. Each stage logs-and-skips on failure
instead of crashing — a later stage failing never discards an earlier stage's artifact.

To re-run one stage from its input JSON (module-independence rule), call its stage fn,
e.g. `python -c "from dotenv import load_dotenv; load_dotenv(); from src import pipeline as p; p.run_propose_stage(p.load_settings())"`.

## Repository layout

```
config/settings.yaml      keywords, window_days, source on/off, extract model, proposers + propose.models, top_k
src/pipeline.py           orchestrator — runs collect → extract → propose → enrich → report
src/collect/              M1: naver_news.py, consensus.py (DISABLED — robots.txt Disallow:/), models.py, http.py
src/extract/              M2: claude_extract.py (prompt+parse), models.py, __init__.py (run_extract)
src/propose/              M3: proposers/ (claude/gpt/gemini behind BaseProposer), krx.py (validation), merge.py, judge.py, models.py
src/enrich/               M4: market.py (FDR market cap), dart_financials.py (rolling TTM), models.py, __init__.py (run_enrich)
src/report/               M5: render.py (context+format+Jinja2), templates/report.{html,md}.j2, __init__.py (run_report)
.github/workflows/        M6: weekly-scan.yml (scheduled pipeline + commit), ci.yml (pytest on push/PR)
data/                     intermediate JSON artifacts (gitignored)
reports/YYYY-MM-DD/       final reports — report.html + report.md (gitignored)
tests/test_collect.py … test_report.py   network/LLM-mocked unit tests (58 total)
conftest.py               puts repo root on sys.path so `import src...` works under pytest
```

Stage artifacts (the contract between modules): `data/raw_articles.json` (M1) →
`themes.json` (M2) → `candidates.json` (M3) → `enriched.json` (M4) →
`reports/YYYY-MM-DD/` (M5).

### `data/raw_articles.json` contract (M1 output → M2 input)

`{ collected_at (ISO8601), window_days, keywords[], articles: [{ source
("naver_news"|"consensus"), keyword, title, summary, url, date ("YYYY-MM-DD"),
publisher }] }`. Deduped by `url`, sorted by `date` descending.

## What this builds

A weekly batch tool ("Shortage/Bottleneck Scanner" / 쇼티지·병목 수혜주 스캐너) for a
single user's personal investment research. It scans Korean news + brokerage report
consensus for supply-shortage/bottleneck themes, maps each theme to candidate KRX-listed
stocks via a multi-LLM ensemble, enriches them with financials, and emits a weekly
HTML/Markdown report. **Output is screening hypotheses, not investment advice** — every
report must carry this disclaimer (a product principle, not a nicety).

## Architecture

A linear pipeline of **independent modules**, each reading the previous stage's JSON
artifact and writing its own. This is the core structural rule: any stage must be
re-runnable / debuggable in isolation from its input JSON, and all intermediate JSON is
persisted for reproducibility (NF5).

```
[collect]  Naver News API + Hankyung Consensus crawl   → raw_articles.json
[extract]  Claude extracts themes/keywords             → themes.json
[map]      Multi-LLM stock proposals (Claude+GPT+Gemini)
           → per-model KRX validation → code normalization
           → merge/agreement-score → judge model writes final reason → candidates.json
[enrich]   FDR market cap + DART revenue/net income (PER derived) → enriched.json
[report]   Jinja2 HTML + Markdown (+ optional notify)   → reports/YYYY-MM-DD/
[schedule] GitHub Actions cron (weekly, default Mon 08:00 KST)
```

### Theme extraction (Step 1 / `[extract]` — implemented in `src/extract/`)

`run_extract(raw_articles_dict)` feeds the collected articles to Claude (model from
`config.extract.model`) and returns a `ThemesResult`. Two non-obvious design points:

1. **Anti-hallucination by source-id citation.** Articles are sent with integer ids; the
   LLM cites only those ids in `source_ids`, and we reconstruct each theme's `sources`
   from the *real* articles (`_coerce_themes`). The model can never invent a source URL —
   the same defense philosophy as KRX validation in `[map]`. Themes with no valid source
   id are dropped.
2. **Defensive everything.** "JSON only" is forced; `_parse_json_object` strips markdown
   fences / surrounding prose; `confidence` and `type` are normalized to the allowed sets
   (Korean labels like 높음→high accepted). The whole call is wrapped so an LLM/parse
   failure raises `ExtractError`, which the pipeline logs and skips — M2 failure never
   aborts the run. Noise filtering (traffic '병목', Hormuz, etc.) is instructed in-prompt.

### The multi-LLM ensemble (the non-obvious part — Step 2 / `[map]`)

This stage is where most of the design complexity lives:

1. **Proposers are an interface.** Claude, GPT, and Gemini each receive the *same prompt
   and same output schema* and propose `(name, reason, relevance)`. The set of active
   models is config-driven (on/off/add). One model timing out or failing must not abort
   the stage — proceed with the surviving models (ensemble degrade, NF8). Force "JSON
   only" and parse defensively.
2. **Validation is per-model and mandatory.** Every proposed stock name is matched against
   the live KRX listing (`FinanceDataReader.StockListing('KRX')`) and normalized to a real
   ticker code. Names that don't exist or are ambiguous are dropped/flagged into a separate
   `dropped` array — **never** included in the report (NF1; this is the primary defense
   against LLM ticker hallucination).
3. **Merge by ticker code.** Dedup validated results on code. Record `proposed_by` (only
   models that passed KRX validation) and `agreement_score` (= length of `proposed_by`).
4. **A single `judge` model** (Claude) then writes the final `relation_reason` and
   `relevance`, since per-model reasons differ.
5. **Rank & cap:** sort by `agreement_score` → `relevance`, keep top K (default 5) per keyword.

### Financial enrichment (Step 2 / `[enrich]` — implemented in `src/enrich/`)

`run_enrich(candidates_dict)` combines financials **once**, after validation/merge, and
returns an `EnrichedResult`. All amounts are **억원** (1e8 KRW). Non-obvious points:

1. **Market cap via FDR, not pykrx** (`market.py`). pykrx is unusable here (KRX
   login/bot wall → empty JSON). `MarketData.from_fdr()` reads `StockListing('KRX')`
   `Marcap` (KRW → 억원); the snapshot is already the latest trading day, so holiday
   correction (NF3) is automatic. The trading date is read once from the KOSPI index
   (`DataReader('KS11')` last index) and shared as every stock's `market_cap` asof.
2. **Rolling TTM, not naive 4-quarter sum** (`dart_financials.py`). Korean filings report
   **cumulative** (YTD) figures, so summing four reports double-counts. M4 uses
   `TTM = current-year cumulative + prior full year − prior-year same-period cumulative`.
   The "prior-year same-period" value is read from the *same* report's `frmtrm_amount`
   (전기), saving a call — so a typical stock needs only 2 DART calls (latest report +
   prior annual). It probes report codes newest-first (`11011→11014→11012→11013`) to find
   the latest available; if no current-year report exists yet it falls back to the prior
   annual report. Account selection prefers consolidated (CFS) over separate (OFS) and
   excludes 총포괄손익 ("포괄") rows when matching 당기순이익.
3. **PER derived from DART, not pykrx fundamental.** `per_ttm = market_cap /
   net_income_ttm` (financially identical to trailing PER; None if net income ≤ 0).
   Optional `per_quarterly_annualized = market_cap / (latest standalone quarter net income
   × 4)` — best-effort/high-variance; the standalone quarter is `latest cumulative −
   prior cumulative within the same year`.
4. **`data_asof` is a dict (NF7).** Market cap's asof (trading day) and revenue/PER's asof
   (report period-end) differ, so each numeric field maps to its own date in `data_asof`.
5. **Degrade, don't crash (NF4).** No DART_API_KEY (or `enrich.use_dart: false`) →
   market cap still fills, revenue/PER stay null. Per-stock failures are isolated; a
   failed stock keeps its M3 fields with empty financials. Loading the FDR snapshot is the
   only hard failure (`EnrichError`), which the pipeline logs and skips.

### Report rendering (Step 3 / `[report]` — implemented in `src/report/`)

`run_report(enriched_dict)` renders `enriched.json` to `reports/{scan_date}/report.html`
+ `report.md` via Jinja2. Non-obvious points:

1. **The disclaimer is a product principle, not config** (`render.DISCLAIMER`). It is a
   code constant rendered at both the top banner and the footer of every report and
   **cannot be turned off** — output is screening hypotheses, never investment advice.
   Even an empty (0-candidate) report still carries it.
2. **Grouped by theme, sorted within.** Stocks are bucketed by `keyword` in first-seen
   order (= extraction order), and within each theme sorted by `agreement_score` ↓ →
   `relevance` (high>medium>low) → `market_cap` ↓.
3. **Two number filters unify formatting** (`eok`, `per`). 억원 ≥ 1조 renders as "N.N조원",
   else "N,NNN억원"; PER as "N.N배"; any None → "N/A" (so adverse/loss stocks show N/A,
   not a crash). The split `data_asof` (NF7) is surfaced as distinct market-cap vs.
   financials dates in the header.
4. **Config-driven formats** (`config.report.formats`, default `["html","markdown"]`);
   output dir is keyed by `scan_date` so re-runs overwrite the same day's folder
   (reproducibility). Render failure raises `ReportError`, which the pipeline logs/skips.

### Scheduling (`[schedule]` — implemented in `.github/workflows/`)

M6 is CI config, not Python — there is no `src/` module. Two non-obvious points:

1. **cron is UTC; the product spec is KST.** `weekly-scan.yml` runs `0 23 * * 0`
   (Sun 23:00 UTC = **Mon 08:00 KST**, default). GitHub Actions cannot read
   `config/settings.yaml`, so the authoritative trigger is the workflow cron; the
   `schedule:` block in settings.yaml is **human-facing documentation of intent** and must
   be kept in sync by hand when the time changes. `workflow_dispatch` allows manual runs
   for first-time validation.
2. **Secrets via GitHub Secrets, artifacts committed back.** All six API keys are injected
   from repo Secrets as env vars (NF6 — never in code/repo); missing keys degrade per
   stage (NF4/NF8), so a partial secret set still produces a report. After the run, the
   step force-adds (`git add -f`) `reports/` + `data/` — they're gitignored for local-dev
   cleanliness, but CI commits the dated `reports/{scan_date}/` so a weekly history
   accumulates; it's also uploaded as a downloadable artifact. `ci.yml` runs the mocked
   test suite on push/PR (no keys needed). **Deploying M6 is a user action** (git init →
   push to GitHub → add Secrets → Run workflow); it cannot be validated from local alone.

## Output schemas (from PRD §6)

- **`themes.json`** — `{ scan_date, window_days, themes: [{ keyword, category, type
  (shortage|...), evidence, confidence (high|medium|low), sources:[{title,url,date,publisher}] }] }`.
  Similar keywords are merged/deduped (F1.4).
- **`candidates.json`** (M3 output) — `{ scan_date, window_days, candidates: [ stock obj ],
  dropped: [{ keyword, model, proposed_name, reason }] }`. Each **stock obj** carries the
  source theme (`keyword`, `category`) plus `{ name, code, market, proposed_by[],
  agreement_score, relation_reason, relevance }`. Only KRX-validated stocks reach
  `candidates`; hallucinated/ambiguous names go to `dropped` (transparency, never reported).
  Name matching is **strict exact** (normalized) — colloquial/old names (금호석유,
  현대두산인프라코어) are dropped by design; the proposer prompt asks for the exact listed name.
- **enriched stock object** (M4 output, `enriched.json`) — the M3 stock obj plus
  `market_cap_eokwon, revenue_ttm_eokwon, per_ttm, per_quarterly_annualized, data_asof`.

## Planned tech stack (PRD §8)

- Python 3.11+; `requests`/`httpx`; `pandas`
- Finance: `pykrx`, `FinanceDataReader`, `OpenDartReader` (or raw DART API)
- LLM SDKs: `anthropic`, `openai`, `google-genai` — all behind a proposer interface
- Report: `Jinja2` (HTML template) + Markdown
- Secrets: `python-dotenv` locally, GitHub Secrets in CI. **No API keys in code/repo** (NF6).
- Schedule: GitHub Actions `on: schedule: cron`
- Optional notify: Telegram Bot API or SMTP

When choosing LLM models, default to the latest/most capable per provider and keep the
model list in config so it can be tuned without code changes.

## Configuration-driven by design

Keep these in config files (not hardcoded), per the PRD:
- Search keyword set (e.g. 쇼티지, 병목, 공급부족, 증설 지연, 리드타임, 감산)
- Lookback window (default 7 days), top-K per keyword (default 5)
- Active LLM proposer list and model IDs
- Schedule day/time

## Cross-cutting rules (from NF requirements)

- **Error isolation (NF4):** a single keyword/stock failure must not halt the pipeline —
  wrap per-item work in try/except + logging.
- **Rate limits (NF2):** respect Naver / DART call quotas; use retry with backoff and caching.
- **Out of scope (do not build):** parsing full analyst report PDFs, real-time/daily
  monitoring, trade execution, or generating buy signals.

To re-run M5 alone from `enriched.json` (module-independence rule):
`python -c "from src import pipeline as p; p.run_report_stage(p.load_settings())"`.
