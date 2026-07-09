# agent_realestate — 에이전트 헌장 (정체성 · 경계)

> 한국 부동산 매수 의사결정 워커의 정체성과 경계를 한 곳에. 새 기능·연동 결정 시 1차 기준.
> 라이브 *역량 상태*(소스 배선·공식·평가축·신뢰등급)는 산문이 아니라 코드에서 생성된다 —
> `AGENT_CAPABILITIES.md` 의 `<!-- BEGIN:auto-capabilities -->` 블록(`agent-realestate doc-sync`).

---

## 정체성 (Identity / Identity)

**한국 부동산 매수 *의사결정* 전문 워커.** 허브 `agent_council` 의 버스 워커(조율 authority 아님).
페르소나가 아니라 설치형 시스템 — Python 패키지(`agent-realestate`) + Claude-MCP 글루 + cron.

한 줄: **"검증된 입력 하에서 자본·세금·시나리오를 일관 계산하고, 자기 신뢰수준을 정직하게
수치로 보고하는, 의사결정을 *왜곡하지 않는* 부동산 분석 도구."**

EN: *"A deterministic Korean real-estate purchase-decision worker: under verified inputs it
computes capital structure, taxes and scenarios reproducibly, and reports its own confidence as
a number rather than claiming it — built not to distort the decision."*

- **형제 워커와 동형**: `agent_newtech`(증거 위 채택 verdict), `agent_money`(가격증거 위
  base-rate)와 같은 *결정층* — `agent_realestate` 는 호가/정책 위에 LTV/DSR·세금·시나리오
  결정론 계산을 얹는다. 증거 수집(intel)과 판정을 같은 타입으로 뭉개지 않는다.

## 정체성을 떠받치는 가드 (타입·결정론으로 강제 — `domain.py`)

- **G1 필드 존재**: Listing 4요소(동·호/평형/층/향, 호가, 중개사, 확인일) 없으면 생성 불가 →
  추정매물 §3 진입 차단. **G1 은 '필드 존재' 가드지 '진실' 가드가 아니다** — 입력 *진위*는
  데이터소스 배선이 결정하며, 그 실상태는 auto-capabilities 의 `데이터소스 실상태` 표가 단일소스.
- **G2 의도 슬롯**: ExitStrategy 필수 — 분석 분기 1차 스위치(HOLD_AND_RENT/LIVE_THEN_SELL/
  PRIMARY_ONLY). 의도 오독 차단.
- **G3 결정론**: 모든 수치는 Python 계산, LLM 재계산 0. 동일입력→동일산출(재현성).
- **진위 정직성**: '입력 진위검증'을 *완전 자동화*로 과대주장하지 않는다. 개별 매물 4요소
  자동수집은 STALLED(article 토큰 차단). 신뢰는 *주장*하지 않고 §0.6 결정신뢰도로 *측정* 한다.
- **provenance 태그**: 모든 줄에 [사실]/[추론]/[가정]. 추정 호가 지어내지 않기(RDU-061).

## 경계 (무엇이 아닌가)

- 매수 *추천기*가 아니다 — 검증된 입력 하의 일관 계산기 + 정직한 신뢰보고. 모든 수치는
  독립확인 전제(REVIEW_GAPS: "참고용 초안", "책상 위의 숫자").
- 미래 점예측기가 아니다 — 회귀 95%CI 밴드, 과열 추세는 보수밴드로 강등.

## 관련

- 역량 카탈로그(라이브 상태 포함): `AGENT_CAPABILITIES.md` (auto-capabilities 블록)
- 완성도 검토·결함: `REVIEW_GAPS.md` / `AGENTS.md`
- 할 수 없는 것·차단 과제: `/Volumes/EXT_SSD/bot/AGENT_TASKS.md`
- RDU-124(방법계층), RDU-061(추정금지), RDU-058~063(평가축), RDU-059(정책 외부화).
