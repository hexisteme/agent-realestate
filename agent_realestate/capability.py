"""역량 manifest 자동생성 (코드→문서 단일소스, drift 방지).

배경(REVIEW_GAPS.md #1): AGENT_CAPABILITIES.md 가 "입력 진위검증"을 정적 산문으로
주장해 *실제 배선 상태*(매물 4요소 자동수집은 article 토큰 차단으로 STALLED)와 어긋날
위험이 있었다. 직교검토 판정: 이건 부정직(REVIEW_GAPS 가 투명)이 아니라 **문서가 코드가
아닌 산문이라 overstatement 가 구조적으로 가능**한 게 결함이다.

대응(agent_newTech gates.scoring_reference_md 패턴 이식): 금융공식·10평가축·신뢰등급·
**데이터소스 실상태**를 *코드 상수에서* 마크다운으로 생성한다. AGENT_CAPABILITIES.md 의
auto-capabilities 마커 사이를 이 출력으로 채운다(`agent-realestate doc-sync`). 코드 배선
상태가 바뀌면 문서가 따라오므로 손정합 drift 가 구조적으로 불가능 — DataSourceStatus 가
단일 소스다. 상태값은 REVIEW_GAPS.md / AGENTS.md 의 실제 진단에서 도출한다.

Provenance 태그 [사실]/[추론]/[가정] 는 코드가 이미 쓰는 규약을 따른다.
"""

from __future__ import annotations

from enum import Enum

from .analysts.scoring import AXES, WEIGHTS
from .analysts.trust import _LEVEL_VALUE, _WEIGHTS
from .domain import ExitStrategy
from .policy_params import PolicyParams


class SourceStatus(Enum):
    """데이터소스 배선 상태 — 산문이 아니라 enum 으로 못박아 overstatement 차단.

    LIVE     = 실수집 검증됨(키/세션 충족 시 자동)
    PARTIAL  = 일부만 자동 — 나머지는 수동 보조
    MANUAL   = 인증 Chrome 등 수동 개입 필수(코어 미검증)
    STALLED  = 자동화 막힘(토큰/인가 차단) — 미완 과제
    PENDING  = 키/인가 승인 대기(엔드포인트는 정상)
    STUB     = placeholder/미구현 — 다른 경로로 우회
    """

    LIVE = "live"
    PARTIAL = "partial"
    MANUAL = "manual"
    STALLED = "stalled"
    PENDING = "pending"
    STUB = "stub"


# ── 데이터소스 실상태 (single source — REVIEW_GAPS.md / AGENTS.md 실진단에서 도출) ──
#   (source, status, note). note 에 차단원인·우회경로를 정직 표기. 이 구조가 문서의
#   "입력 진위검증" 줄을 *생성* 하므로, 산문으로 과대주장하는 것이 구조적으로 불가능.
DATA_SOURCE_STATUS: tuple[tuple[str, SourceStatus, str], ...] = (
    ("MOLIT 실거래(fetch-molit)", SourceStatus.LIVE,
     "data.go.kr 디코딩키(.env) 필요 — 키 있으면 자동. 호가↔실거래 cross-check 의 진위 앵커."),
    ("네이버 단지 overview(fetch-overview)", SourceStatus.MANUAL,
     "인증 Chrome 경유(search+overview 내부 JSON API, 세션쿠키 200). 헤드리스는 429 봇차단 — 수동 개입 필수."),
    ("네이버 매물 4요소(개별 article)", SourceStatus.STALLED,
     "per-매물 동·호/층/향/중개사 자동수집은 /api/articles 가 401 bearer 토큰 요구로 STALLED. "
     "현재 매물탭 DOM 수동 읽기(수작업)로 보조 — 자동화 미완(REVIEW_GAPS P0-1·AGENTS 2026-05-29)."),
    ("K-apt 기본정보 15058453(fetch-meta)", SourceStatus.LIVE,
     "공동주택기본정보 data.go.kr 15058453, MOLIT_API_KEY 재사용. 세대수·준공·동수."),
    ("K-apt 단지목록 15057332", SourceStatus.PENDING,
     "단지목록 호출 403 — 활용신청 미승인(엔드포인트·코드 정상, 키 미인가=승인대기)."),
    ("서울 정비사업 API(fetch-redev-seoul)", SourceStatus.STALLED,
     "OA-2253 서비스명 미해결(SPA). 재건축 단계는 update-redev 수동 경로 권장(seoul_redev.py 구현됨, 라이브 미확인)."),
    ("KB시세(fetch-kb-sise)", SourceStatus.PARTIAL,
     "로그인 Chrome 탭 DOM 경유(KB API 는 회원토큰 요구) — 은행 LTV 기준가. 수동 세션 필요, 후보별 1회."),
    ("카카오 입지(fetch-location)", SourceStatus.LIVE,
     "카카오 로컬 REST(KAKAO_REST_KEY) — 좌표·도보·노선. 라이브 검증됨(2026-05-29)."),
    ("실거래 backfill(backfill)", SourceStatus.STUB,
     "placeholder — 실거래 적재는 update-prices(JSON 주입) 또는 fetch-molit(API 직접)로 우회."),
)


def _weights_table() -> list[str]:
    """10평가축 × ExitStrategy 가중치 표를 scoring.WEIGHTS 상수에서 생성."""
    header = "| 평가축 | " + " | ".join(s.name for s in ExitStrategy) + " |"
    sep = "|---|" + "---|" * len(ExitStrategy)
    rows = [header, sep]
    for axis in AXES:
        cells = " | ".join(f"{WEIGHTS[s][axis]:.2f}" for s in ExitStrategy)
        rows.append(f"| {axis} | {cells} |")
    return rows


def capability_reference_md() -> str:
    """금융공식·10평가축·신뢰등급·데이터소스 실상태·G1/G2/G3 가드를 *코드 상수에서* 생성.

    AGENT_CAPABILITIES.md 의 auto-capabilities 마커 사이를 이 출력으로 채운다
    (`agent-realestate doc-sync`). 코드를 바꾸면 문서가 따라오므로 손정합 drift 가 구조적
    으로 불가능 — '입력 진위검증' 같은 역량 주장이 산문이 아니라 DataSourceStatus(코드)에서
    파생되어 overstatement 가 차단된다. 모든 수치는 결정론(G3)."""
    p = PolicyParams()  # 기본 파라미터 — 정책 사실 아님(미검증 라벨), 공식 형태 노출용

    # ── 결정론 금융공식 (stable id — finance.py/policy_params.py 실상수 참조) ──
    fin = [
        "| id | 공식 (전부 결정론·LLM 계산 0, AGENT_CALC [사실]) |",
        "|---|---|",
        "| ltv_loan | 매매가 × LTV율 (호출자가 PolicySnapshot 에서 LTV율 확정) |",
        "| dsr_loan | 월 원리금균등 역산: PV = (소득×DSR − 기존부채)/12 "
        "× [1 − (1+r/12)^−(term×12)] / (r/12) |",
        f"| dsr_stress | DSR 을 r + stress_rate_addon({p.stress_rate_addon:.3f}, 가산금리)로 재계산 — 보수 한도 |",
        "| loan_binding | min(LTV, stress DSR, 절대한도) — 3중 binding 명시 |",
        f"| acquisition_tax | 구간세율(≤{p.acq_low_threshold_eok}억→{p.acq_low_rate:.0%} / "
        f"{p.acq_low_threshold_eok}~{p.acq_high_threshold_eok}억→선형 / "
        f">{p.acq_high_threshold_eok}억→{p.acq_high_rate:.0%}) + 지방교육세×{p.acq_edu_ratio:.0%} "
        f"+ 농특세({p.acq_farm_rate:.1%}, >{p.acq_farm_area}㎡) − 생애최초 감면(≤{p.acq_relief_cap_eok}억) |",
        "| property_tax | 공시가(가격대 구간화 ~69%) × 공정시장가액비율 → marginal 누진 "
        "(1세대1주택 특례세율·특례비율) + 도시지역분 |",
        f"| comprehensive_tax | (공시가 − 공제[1주택 {p.jongbu_deduction_1home // 100_000_000}억]) "
        f"× 공정시장가액비율({p.fair_market_ratio:.0%}) → 종부세 누진(≤공제면 0) |",
        "| capital_gains_tax | 1세대1주택 12억 이하 비과세 / 12억 초과 안분 / 장특공 / "
        "단기중과(<2년) / 2년+ 누진 (LIVE_THEN_SELL) |",
    ]

    # ── 10평가축 + ExitStrategy 가중치 (scoring.WEIGHTS 실상수) ──
    axes = ["10평가축 × 출구전략 가중치 (합=1.00, 운영자 백테스트 보정값 — profile JSON 으로 override):"]
    axes += _weights_table()

    # ── 신뢰등급 컴포넌트 (trust._LEVEL_VALUE / _WEIGHTS 실상수) ──
    tiers = ", ".join(f"{lvl}={val}" for lvl, val in _LEVEL_VALUE.items())
    tw = ", ".join(f"{f}={w:.2f}" for f, w in _WEIGHTS.items())
    trust = [
        "결정신뢰도 §0.6 — 입력 진위(authenticity)를 *측정* (주장 아님, 전부 결정론):",
        f"- 검증수준 기여: {tiers}",
        f"- 의사결정 영향 가중(합 1.0): {tw}",
        "- 등급: ≥85 '의사결정 가능' / ≥60 '조건부(독립확인 후)' / 그 미만 '참고만'.",
        "- 호가는 네이버 라이브여도 *수동 주입*(코어 미검증) — MOLIT 실거래와 괴리 ≤10%면 '교차검증' 승격.",
    ]

    # ── 데이터소스 실상태 (DATA_SOURCE_STATUS 단일소스 — overstatement 차단의 핵심) ──
    ds = [
        "데이터소스 실상태 — '입력 진위검증' 역량은 여기서 *파생* 된다(산문 과대주장 차단). "
        "G1 은 '필드 존재' 가드지 '진실' 가드가 아니다(REVIEW_GAPS):",
        "| 소스 | 상태 | 비고 |",
        "|---|---|---|",
    ]
    ds += [f"| {name} | {st.value} | {note} |" for name, st, note in DATA_SOURCE_STATUS]

    # ── G1/G2/G3 가드 의미 ──
    guards = [
        "가드 3종 (타입·결정론으로 강제):",
        "- **G1 필드 존재**: Listing 4요소(동·호/평형/층/향, 호가, 중개사, 확인일) 없으면 생성 불가 "
        "→ 추정매물 §3 진입 차단. (진실 가드 아님 — 진위는 위 데이터소스 상태가 결정)",
        "- **G2 의도 슬롯**: ExitStrategy 필수 — 분석 분기 1차 스위치(HOLD_AND_RENT 면 양도세 모듈 비활성).",
        "- **G3 결정론**: 모든 수치는 Python 계산, LLM 재계산 0. 동일입력→동일산출(재현성).",
    ]

    return "\n".join(
        fin + [""] + axes + [""] + trust + [""] + ds + [""] + guards
    )
