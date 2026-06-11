# agent_realestate

[![ci](https://github.com/hexisteme/agent-realestate/actions/workflows/ci.yml/badge.svg)](https://github.com/hexisteme/agent-realestate/actions)
![python](https://img.shields.io/badge/python-3.11%2B-blue) ![deps](https://img.shields.io/badge/dependencies-0_(stdlib_only)-brightgreen) ![license](https://img.shields.io/badge/license-MIT-green)

한국 부동산 매수 의사결정 리포트 시스템. 라이브 호가(네이버) + 정책/재건축 캐시 + 결정론 계산으로
§0~11 리포트를 **재현 가능**하게 산출한다. agent_money 와 동일 설계.

> **Deterministic, LLM-free decision-support pipeline for Korean residential real estate.**
> 10-axis price-blind structural scoring · 2026 lending-rule engine (LTV/stress-DSR/absolute cap) ·
> tax & hold-scenario math · hard-constraint partition ranking · every number carries
> FACT/INFERENCE provenance. Zero third-party dependencies; same input → same report, enforced by tests.

## 30초 데모 (실데이터 불필요)
```bash
pip install -e . && agent-realestate report --demo
# → report/scan/scan_demo_<date>.html : 합성 단지 6곳으로 §0~11 풀 리포트 재현
#   (통과 1위 / 재건축 결 / 전세미확보 F_NORENT / 주상복합 가드 / 예산초과 하드페일 강등 시연)
```

## 가드레일 (타입으로 강제 — `domain.py`)
- **G1 추정매물 차단**: `Listing` 은 4요소(동·호/평형/층/향·호가·중개사·확인일) 없으면 생성 불가.
  `ASKING_LIVE` 호가는 `NAVER_LIVE_CHROME` 출처만 허용 → 추정·웹검색 호가는 §3 진입 불가 (RDU-061).
- **G2 의도 슬롯**: `exit_strategy`(HOLD_AND_RENT|LIVE_THEN_SELL|PRIMARY_ONLY) 필수. HOLD_AND_RENT 면
  장기보유 전세수익·재건축 활성, 양도세 비활성.
- **G3 결정론**: 모든 수치는 Python 계산(LLM 재계산 0). 동일 입력 → 동일 산출(테스트로 고정).
- 모든 줄 `[사실]/[추론]/[가정]`. 정책은 URL+확인일. 투자자문 아님.

## 설치
```bash
cd /Volumes/EXT_SSD/bot/agent_realestate
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## 사용 (Claude-MCP 주입 패턴)
```bash
# 1) 정책 캐시 (Claude 가 WebSearch 로 만든 JSON 주입)
.venv/bin/agent-realestate scan-policy --input examples/policies_seed.json
# 2) 재건축 단계 캐시 (선택)
.venv/bin/agent-realestate update-redev --input redev.json
# 3) 리포트 (Claude 가 read-chrome-tab.sh 로 만든 라이브 호가 JSON 주입)
.venv/bin/agent-realestate report --profile examples/profile_user.json --input examples/candidates_sample.json
```
라이브 호가는 순수 standalone 으로 못 긁으므로 Claude 가 `read-chrome-tab.sh`+osascript 로 수집해
candidates.json 으로 주입한다(agent_money 의 `--onchain-json` 과 동형). 운전은 `/agent-realestate` 스킬.

## 아키텍처 (3계층 하이브리드)
| 계층 | 모듈 | 실행 |
|---|---|---|
| Static(캐시) | cache/store.py (정책·재건축·실거래) | cron + Claude 주입 |
| Live | collectors/naver_live.py (read-chrome-tab.sh) | Claude-MCP |
| Compute | analysts/{finance,scoring,redev} synthesis/{scenario,assembler} | standalone |

## 테스트
```bash
.venv/bin/python -m pytest tests/ -q   # 129 passed (CI 자동 실행)
```
작명은 `~/.claude/rules/glossary-real-estate.md`. 결정·백로그는 `AGENTS.md`.

## 토큰-제로 일일 운영 (Phase 2, 2026-06-11)
```bash
agent-realestate daily   # MOLIT fresh → 블로그 생성(신선도 게이트) → site push → 플래그십 리포트 regen(게이트)
```
cron(`blog/cron_daily.sh`, 매일 07:05)이 위 명령의 thin wrapper. **LLM/토큰 0** — 정기 갱신은 전부 프로그램.
Claude 의 잔여 역할: 신규 후보 발굴(scan-region 결과 검토)·정책 해석(scan-policy 주입 JSON 작성)·정기 감사.

## GitHub
- 코드: `github.com/hexisteme/agent-realestate` (PRIVATE) — push 마다 Actions CI(pytest 129).
- **데이터는 repo 제외**(.gitignore): 네이버 호가(DB권)·개인 프로필·report/. 공공 통계 픽스처(regime 2종)만 포함.
- 블로그 산출: `github.com/hexisteme/seoul-re-snapshot` (별도 repo, daily 가 push).

## 다른 사용자를 위한 입력 가이드 (범용화, 2026-06-12)

**환경변수** (전부 선택 — 미설정 시 기본값):
| env | 의미 | 기본 |
|---|---|---|
| `RE_DATA_ROOT` | 데이터/캐시 루트 | `/Volumes/EXT_SSD/bot/agent_realestate` |
| `RE_DISTRICTS` | MOLIT 수집 구 (쉼표, 서울 25구) | 양천,강서,…,종로 (11구) |
| `RE_EMAIL_TO` | 리포트 수신 이메일 | (없음 = 미발송) |
| `MOLIT_API_KEY` | 공공데이터포털 RTMS 키 (.env) | 필수(실거래 수집 시) |

**프로필 JSON** (`--profile`): `agent_realestate/demo.py` 의 `DEMO_PROFILE` 이 살아있는 스키마.
핵심 키: `exit_strategy`(HOLD_AND_RENT|LIVE_THEN_SELL|PRIMARY_ONLY, G2 필수) · `annual_income_krw` ·
`own_capital_krw` · `mortgage_rate` · `term_years` · `first_time` · `regulated`.
**`axis_weights`** (선택): 축 가중 부분 override — 예 `{"학군": 0.30, "출퇴근": 0.15}` →
전략 기본 가중과 병합 후 합=1 재정규화. 기본 가중은 운영자 백테스트 보정값(별지 B)이며
override 시 민감도(별지 A)도 그 실효 가중 기준으로 교란된다.

**후보 JSON** (`--input`): `demo.py` 의 `DEMO_CANDIDATES` 가 살아있는 스키마. G1 가드 필수 필드 =
`complex_name·dong_ho·area_exclusive_m2·floor·facing·price_krw·agent_name·confirmed_date`
(추정 호가 금지 — 실제 매물 페이지에서 확인한 값만). 메타(units·built_year·far_pct·jeonse_krw 등)는
선택이나 빠지면 해당 축이 보수 처리된다. 네이버 자동 수집이 없는 환경에선 이 JSON 을
손으로(또는 스프레드시트 → JSON 변환으로) 채우면 된다.

**블로그 발행 구 변경**: `python3 -m blog.run_daily --districts "강남,서초" …`
