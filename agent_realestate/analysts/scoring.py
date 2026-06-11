"""RDU-058 5축 평가 + ExitStrategy 별 가중치. 축 점수는 실데이터의 결정론 변환
(전세가율→전세수요, 세대수+역세권→환금성, 재건축점수→상승/방어, 평당가→가격메리트).
LLM 이 점수를 지어내지 않는다 (G3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..domain import Candidate, ExitStrategy
from .location import parse_location
from .redev import RedevScore

AXES = ("전세수요", "환금성", "가격방어", "상승여력", "토지지분", "가격메리트", "출퇴근", "학군", "경사", "후기")

# ★호가 분리(2026-06-04, Wittgenstein/roleaudit): 현재 호가(listing.price)를 분모/잔차로 쓰는 축은
#   '이 가격이 싼가'(가격 매력도)를 재고, 나머지는 '단지 펀더멘털 자체'를 잰다. 한 숫자로 섞으면 많이 빠진
#   단지가 '싸짐→점수↑' 착시(H1 merit-leakage)를 만든다 → fundamental_total(호가무관) ↔ 가격메리트·전세수요(가격대비) 분리.
PRICE_DERIVED_AXES = ("가격메리트", "전세수요")
FUNDAMENTAL_AXES = tuple(a for a in AXES if a not in PRICE_DERIVED_AXES)

# 10축 = '미래 CAGR 예측기'가 아니라 **같은 급지(생활권×전용band) 안에서 어느 단지가 더 적합·안전한가의 다축 종합 검수표**.
# FACT 백테스트(2026-06-01, MOLIT n=552): 점수 단독↔실현CAGR ρ²≈1.2% — 즉 점수만으로 미래수익을 맞히지 못한다.
# → 따라서 점수는 §★ 보조신호(생활권 base-rate=거시 가이드, within-구 mean-reversion=타이밍)와 *함께* 읽는다(리포트가 둘을 병치).
# 이건 점수의 폐기 근거가 아니라 '역할의 정직한 한정'이다 (2026-06-03 사용자 OVERRIDE: 10축 매트릭스 = 본문 주 프레임).
# ★가중치 재검토(2026-06-03, gemma4:31b-cloud 자문): HOLD_AND_RENT(15년 보유·자산증식 70~80%·임대)에서
#   기존 환금성0.20 최고가중은 모순(매도속도 아닌 '수요 두께'로 재정의해 ↓0.10), 상승여력0.07 과소(자산증식 대비 ↑0.20),
#   출퇴근0.18 과대(거주 2년/보유 13년 → ↓0.10). 전세수요는 레버리지·현금흐름 핵심으로 유지(0.15).
# 경사 = opentopodata 실측 slope_pct(객관). 후기 = 커뮤니티 정성 coarse 감성(★편향·단일출처 council 경고)라 경가중 tiebreaker.
# 학군은 사용자 (b) 선택으로 점수 편입 — PRIMARY_ONLY(대구 실거주) 0.23 중시 / HOLD_AND_RENT(서울 임대) 0.08(임대수요 간접). 학원가(입시학원 수)는 학군 축 통합.
WEIGHTS: dict[ExitStrategy, dict[str, float]] = {
    # HOLD_AND_RENT: gemma4 재검토 반영 (자산증식 우선·15년 보유). 합=1.00.
    # ★2026-06-03 v2 사용자 OVERRIDE: 후기·경사 = **각 0.02**(저신호 tiebreaker 하향). 해제된 0.04 는
    #   전세수요 +0.02(임대 leg 핵심)·가격메리트 +0.02(헤도닉 저평가 신호로 격상)로 재배분. 합=1.00.
    # ★2026-06-04 v3 백테스트 반영(roleaudit): 상승여력 0.20→0.10 로 반감. 근거 3중 — (a) H3: 재개발 단계
    #   진입 marginal alpha NULL(Δexcess+0.9%/yr,p=0.51) → '상승여력=재건축 프리미엄' thesis 미실현,
    #   (b) 상승여력↔토지지분 r=+0.80(둘 다 용적률 구동) = 0.25 가중을 용적률에 이중 베팅,
    #   (c) gu_cagr(과거 15년 구 CAGR) 비정상성. 해제한 0.10 은 within-급지 rank-IC 양(+)을 보인 두 축으로
    #   이동(가격방어 0.15→0.20[band×구 IC+0.13]·환금성 0.10→0.15[+0.16]). 합=1.00.
    #   ※ 이전 0.20 은 사용자 OVERRIDE 였으므로 silent 아닌 명시 변경 — 뒤집힐 조건: 코호트B 추적가능률↑로
    #     재개발 alpha 유의(+) 재측정되거나 가격방어·환금성 within-급지 IC가 큰 표본서 0 수렴 시 철회.
    ExitStrategy.HOLD_AND_RENT:  {"전세수요": 0.17, "환금성": 0.15, "가격방어": 0.20, "상승여력": 0.10, "토지지분": 0.05, "가격메리트": 0.12, "출퇴근": 0.09, "학군": 0.08, "경사": 0.02, "후기": 0.02},
    # LIVE_THEN_SELL(매도): 환금성(매도속도)은 매도전략이라 중간 유지, 상승여력↑. 합=1.00.
    ExitStrategy.LIVE_THEN_SELL: {"전세수요": 0.10, "환금성": 0.16, "가격방어": 0.13, "상승여력": 0.16, "토지지분": 0.05, "가격메리트": 0.12, "출퇴근": 0.12, "학군": 0.07, "경사": 0.03, "후기": 0.06},
    # PRIMARY_ONLY(실거주): 거주효용 중심(학군·가격방어·출퇴근). 자산증식 비중 낮아 상승여력 경가중. 합=1.00.
    ExitStrategy.PRIMARY_ONLY:   {"전세수요": 0.05, "환금성": 0.11, "가격방어": 0.15, "상승여력": 0.04, "토지지분": 0.03, "가격메리트": 0.18, "출퇴근": 0.12, "학군": 0.23, "경사": 0.04, "후기": 0.05},
}

def merge_axis_weights(strategy: ExitStrategy, override: dict | None) -> dict | None:
    """profile["axis_weights"] 부분 override 를 전략 기본 가중과 병합 후 합=1 재정규화 (범용화 2026-06-12).
    기본 WEIGHTS 는 운영자(이 repo 사용자)의 백테스트 보정값 — 타 사용자는 profile JSON 만으로
    자기 선호(예: 학군 0.2)를 주입한다. 알 수 없는 축 이름은 시끄럽게 거부(silent 무시 금지)."""
    if not override:
        return None
    unknown = set(override) - set(AXES)
    if unknown:
        raise SystemExit(f"[axis_weights] 알 수 없는 축: {sorted(unknown)} — 유효 축: {AXES}")
    w = dict(WEIGHTS[strategy])
    w.update({k: float(v) for k, v in override.items()})
    s = sum(w.values())
    if s <= 0:
        raise SystemExit("[axis_weights] 가중 합이 0 이하")
    return {k: v / s for k, v in w.items()}


@dataclass(frozen=True)
class AxisScores:
    candidate: Candidate
    scores: dict[str, float]
    weighted_total: float
    fundamental_total: float = 0.0   # 호가무관 축만(가격메리트·전세수요 제외) renorm — Wittgenstein 호가분리(2026-06-04)
    imputed: frozenset = frozenset()  # ★표본평균 대체된 축(전세수요·학군·경사·후기) — 거짓 정밀도 차단용 플래그(2026-06-06 적대분석)


def _jeonse_demand(c: Candidate) -> float | None:
    """전세수요(임대 leg 안전성 proxy, 0~5). ★적대검증(2026-06-06): (a) 0.40 경계 0.001→1.0점 절벽 →
    선형 연속화(학군 ramp 선례 일관), (b) 단조증가로 ≥0.70 만점이나 한국맥락 전세가율 ≥0.80 은
    깡통전세·갭·역전세 *적신호*라 역U 상한 페널티 추가(위험국면 최고보상 차단)."""
    if not c.jeonse_krw:
        return None                          # 공백 → score_candidates 가 표본평균 대체
    ratio = c.jeonse_krw / c.listing.price_krw
    if ratio <= 0.78:                        # 정상대역: 0.35→2.0 … 0.78→5.0 선형(절벽 제거)
        return round(max(2.0, min(5.0, 2.0 + (ratio - 0.35) / (0.78 - 0.35) * 3.0)), 2)
    # ≥0.78 깡통전세 리스크 — 역U 감점(0.80→4.3, 0.85→3.6, 0.90+→3.0)
    return round(max(3.0, 5.0 - (ratio - 0.78) * 14.0), 2)


def _liquidity(c: Candidate) -> float:
    # ★적대검증(2026-06-06) + 백테스트(probe_liquidity_ceiling.py): 구 base 4.0 은 +노선0.5 +도보0.2 가산 후에도
    #   max 4.7 로 clip → 환금성은 형제축(가격방어·토지지분 등)이 5.0 도달 가능한 것과 달리 5.0 불가 = 체계적 과소(실효 max 94%).
    #   최상위 base 4.0→4.3 으로 max=4.3+0.5+0.2=5.0 도달. 백테스트: within-axis Spearman(OLD,NEW)=1.0(순수 monotonic 재척도,
    #   무재정렬) · 복합 랭킹 top-5 불변·최대 8계단(전부 2000+세대 대단지가 정당히 상승) · 실현CAGR 복합 IC Δ=−0.0004(비열화).
    #   ★뒤집힐 조건: 2000+세대 대단지의 환금성 프리미엄이 과대(다른 축 대비 over-credit)로 측정되면 4.3→4.15 로 축소.
    u = c.units
    base = 4.3 if u >= 2000 else 3.7 if u >= 1500 else 3.3 if u >= 1000 else 2.8 if u >= 500 else 2.3
    lp = parse_location(c.transit)                  # R9: 키워드 대신 파싱된 노선수·도보
    base += 0.5 if lp.line_count >= 2 else 0.3 if lp.line_count == 1 else 0.0
    if lp.walk_min is not None and lp.walk_min <= 10:
        base += 0.2
    return min(5.0, base)


def _defense(c: Candidate, r: RedevScore) -> float:
    """가격방어(0~5) — 순수 구조축 3성분 가중합(2026-06-05 적대검증 반영):
    세대수0.25(대단지=환금·관리 안정) + 준공신축0.30(감가 적음·비아파트 cap3.0) + 연간거래수0.45(MOLIT 실거래 유동성=급매 흡수력, 결측→2.4 보수).
    ★전세가율 성분(구판 0.20) 제거 — (a) 전세수요 축과 이중카운트, (b) 호가(jeonse/listing.price)가 분모라
      많이 빠진 단지가 '방어우수'로 둔갑하는 가격 누수(H1 merit-leakage 동형). 가격대비 신호는 전세수요/가격메리트로 분리.
    ★매매급락 보정은 매매 시계열 부재(API 미구독)로 미적용. 가격방어=호가무관(fundamental) 축."""
    u = c.units
    s_unit = (5.0 if u >= 2500 else 4.4 if u >= 1500 else 3.8 if u >= 800
              else 3.2 if u >= 400 else 2.5 if u >= 150 else 2.0)
    age = date.today().year - c.built_year   # ★적대검증(2026-06-06): 하드코딩 2026 → 동적(assembler today.year 와 일관, 2027+ off-by-one·점수-표시 괴리 제거). 2026 현재 출력 불변.
    s_age = (5.0 if age <= 5 else 4.3 if age <= 10 else 3.6 if age <= 20
             else 3.0 if age <= 30 else 2.4)
    t = c.trade_annual
    # ★적대검증(2026-06-06): trade_annual 결측은 MNAR — MOLIT 실거래 0건은 *거래 드문 저유동 단지*라
    #   가격방어가 마땅히 낮춰야 할 대상. 결측을 3.0(중앙급)으로 채우면 t<15 실측단지(2.4/1.8)보다 높아져
    #   '최악 방어 자산을 보상'(누수). 결측→2.4(하위급)로 보수 임퓨트.
    s_trade = (2.4 if t is None else
               5.0 if t >= 80 else 4.3 if t >= 50 else 3.6 if t >= 30
               else 3.0 if t >= 15 else 2.4 if t >= 6 else 1.8)
    # ★적대검증(2026-06-05): 세대수(units) 가 환금성·가격방어·상승여력 3축 진입 → 실효가중 0.33 과대.
    #   가격방어의 units 0.40→0.25 축소, 실거래수(직접 유동성·방어신호, 환금성과 덜 중복) 0.30→0.45.
    # ★★적대검증 2차(2026-06-05, 5 instrument 만장일치+코드검증): 비아파트(주상복합/도생/오피스텔)는
    #   신축 프리미엄 소멸이 빠르고 환금성 약함 → '신축=가격방어' 가점을 박탈(s_age cap 3.0). 신축 도생이
    #   신축만으로 노후 아파트를 역전하던 결함의 143% 직접 제거(신길AK fund 2.946→2.78<목동 2.793 역전).
    if _is_non_apt(c):
        s_age = min(s_age, 3.0)
    return round(0.25 * s_unit + 0.30 * s_age + 0.45 * s_trade, 2)


def _is_non_apt(c: Candidate) -> bool:
    """비-아파트(주상복합·오피스텔·도시형생활주택) 여부 — 단지명 표기 기반. 이들은 고용적·복합용도·
    분산 권리관계로 재건축 사실상 불가(council deep 8인+sage 만장일치, 2026-06-04) + 신축 프리미엄
    소멸 빠르고 환금성 약함(2026-06-05 적대검증, 신길AK 도생+오피스텔 사례). → 재건축 관련 축
    (토지지분 2.0캡·상승여력 재건축잠재 0) + 가격방어 신축가점(s_age) 박탈을 적용한다."""
    nm = c.listing.complex_name or ""
    return any(k in nm for k in ("주상복합", "오피스텔", "도시형"))


_is_jusang = _is_non_apt   # 하위호환 alias(주상복합은 _is_non_apt 의 부분집합 — 2026-06-05 일반화)


def _land_share(c: Candidate) -> float:
    """토지지분(0~5) — 용적률(주동인) + 건폐율(보조) 복합(2026-06-04 사용자 건폐율 편입).
    용적률 낮을수록 세대당 대지지분↑(현재 토지밀도, 주동인). 건폐율 낮을수록 대지 대비 건물 바닥점유↓
    = 개방·녹지 여유·타워형 → 토지 활용가치 소폭↑(보조 ±0.4). 용적률 미수집 시 far_pct, 건폐율 미수집 시 보조 0.
    ★주상복합(2026-06-04 사용자+council+sage): 저용적률이어도 복합용도라 재건축 토지가치 실현 불가 →
    중립 2.0 고정(숭인[주상복합] far158→5.0 허구 만점 차단)."""
    if _is_non_apt(c):
        return 2.0                                # 재건축 토지가치 미실현 → 중립(아파트와 비교 시 정당한 하향)
    far = c.far_pct
    base = (5.0 if far <= 180 else 4.4 if far <= 220 else 3.8 if far <= 280
            else 3.2 if far <= 350 else 2.5 if far <= 450 else 2.0)
    bcr = c.bcr_pct
    if bcr is not None:
        adj = (0.4 if bcr <= 18 else 0.2 if bcr <= 25 else 0.0 if bcr <= 40
               else -0.2 if bcr <= 55 else -0.4)
        base = max(2.0, min(5.0, base + adj))
    # ★재개발난망 차감(2026-06-07 적대감사): 경사 >15%(경사지)는 옹벽·정지비·동배치 제약으로 재건축 사업성 크게↓
    #   → 대지지분의 *실현 경로* 제약 → 토지지분 실현성 haircut(×0.65, floor 2.0). 주상복합 cap(미실현→2.0) 선례의 연속.
    #   ★뒤집힐 조건: 경사지여도 대지지분이 단독개발/리모델링으로 실현되거나 조망 프리미엄이 상쇄하면 철회.
    sp = getattr(c, "slope_pct", None)
    if (sp is not None and sp > 15) or getattr(c, "redev_infeasible", False):
        base = max(2.0, base * 0.65)   # 경사지 OR 재개발난망 플래그 → 대지지분 실현성 차감
    return round(base, 2)


def _infra_score(c: Candidate) -> float:
    """상승여력 인프라성분(0~5) — KAKAO 정량(교통 차등↑↑·생활편의·의료·녹지·상권). 사람들이 통상
    가치를 두는 주변 인프라를 정량화. 역세권/GTX 교통 비중을 최대로(부동산 상승의 1차 동인)."""
    ix = c.infra or {}
    sub = ix.get("subway_m", 9999)
    s = (1.6 if sub <= 250 else 1.2 if sub <= 500 else 0.8 if sub <= 800
         else 0.4 if sub <= 1200 else 0.0)
    if "GTX" in (c.transit or ""):
        s += 0.6
    s += min(0.7, 0.14 * ix.get("mart_800", 0))                 # 대형마트
    h = ix.get("hosp_800", 0)
    s += (0.6 if h >= 120 else 0.4 if h >= 60 else 0.2 if h >= 20 else 0.0)   # 의료접근
    p = ix.get("park_1k", 0)
    s += (0.5 if p >= 50 else 0.35 if p >= 25 else 0.2 if p >= 10 else 0.0)   # 녹지
    d = ix.get("dept_1500", 0)
    s += (0.7 if d >= 100 else 0.45 if d >= 30 else 0.25 if d >= 8 else 0.0)  # 상권/백화점
    return min(5.0, round(0.6 + s, 2))


def _redev_potential(c: Candidate) -> float:
    """상승여력 재건축잠재성분(0~5) — 용적률 상향 Gap(정책상한−현재) + 준공오래될수록 + 세대수클수록.
    정책상한 default 300%(2026 서울 3종 250~300), 역세권/GTX 종상향 500%(brave 확인 2026-06-03).
    far Gap 으로 토지지분(far 단독)과 의미 분리 — 여긴 '상향 여지', 토지지분은 '현재 밀도'."""
    # ★주상복합(2026-06-04 사용자+council+sage): 복합용도·분산 권리관계로 재건축 사실상 불가 →
    #   재건축잠재 = 0 (저용적률·노후여도 재건축 출구 없음. 숭인[주상복합] 허구 1위 차단).
    if _is_non_apt(c) or getattr(c, "redev_infeasible", False):
        return 0.0    # 주상복합 OR 재개발난망 플래그 → 재건축잠재 0(실현 출구 없음)
    far = c.far_pct
    ceiling = 500.0 if ("GTX" in (c.transit or "") or "역세권" in (c.transit or "")) else 300.0
    gap = max(0.0, (ceiling - far) / ceiling)
    # ★용적률 직교화(2026-06-04 roleaudit): far-gap 계수 5.0→2.0 으로 축소. 근거 — 상승여력↔토지지분 r=+0.80
    #   (둘 다 용적률 단조) 이중카운트 해소 + H3(재개발 단계 alpha NULL)로 'gap=미래 증축 upside'는 실현 안 됨.
    #   저용적률의 '현재 대지지분 가치'는 토지지분 축이 단독으로 보유 → 상승여력은 인프라·실단계(stage_adj)로 변별.
    g = 2.0 * gap
    age = date.today().year - c.built_year   # ★적대검증(2026-06-06): 하드코딩 2026 → 동적(assembler today.year 와 일관, 2027+ off-by-one·점수-표시 괴리 제거). 2026 현재 출력 불변.
    age_adj = (1.0 if age >= 35 else 0.6 if age >= 30 else 0.2 if age >= 25
               else -0.3 if age < 15 else 0.0)
    u = c.units
    unit_adj = 0.6 if u >= 2000 else 0.4 if u >= 1000 else 0.2 if u >= 500 else 0.0
    # ★적대검증(2026-06-06): redev_stage 연속 가산 *제거*. 프로젝트 자체 falsifiable 게이트
    #   (redev_factor_gate.py, 사전등록·변경금지)가 passed=False — 동(洞)통제 ρ=−0.606(음의 예측력!),
    #   구통제 −0.149 → verdict '재건축 ranking factor 폐기, 촉매 watchlist[binary]로만'. 반증된 신호를
    #   순위 가산으로 넣으면 변별을 *악화*. 단계는 §0 촉매 watchlist(binary 표시)로만, 점수 미반영.
    stage_adj = 0.0
    # ★용적률 여지(gap) 게이팅(2026-06-04): 노후·세대수·단계는 *증축 여지가 있을 때만* 재건축 신호.
    #   gap≈0(이미 최대밀도)이면 age/units 가 있어도 재건축 사업성 없음 → 재건축 age-credit 오인 차단.
    #   (council/sage: type 플래그가 주효, gap 게이팅은 maxed-out 일반단지 보조 안전장치.)
    if gap <= 0.1:
        age_adj = min(age_adj, 0.0)              # 여지 없으면 노후가 재건축 plus 아님(중립~경미한 노후)
        unit_adj = 0.0
        stage_adj = 0.0
    # 건폐율(2026-06-04 사용자 편입): 낮을수록 증축 여지↑. 단 *용적률 Gap 이 있을 때만* 의미
    #   (고용적률 신축 타워는 gap≈0 → 건폐율 낮아도 재건축 무관, 오인 가산 방지).
    bcr_adj = 0.0
    if c.bcr_pct is not None and gap > 0.1:
        bcr = c.bcr_pct
        bcr_adj = (0.5 if bcr <= 20 else 0.25 if bcr <= 35 else 0.0 if bcr <= 50 else -0.2)
    return max(0.0, min(5.0, round(g + age_adj + unit_adj + stage_adj + bcr_adj, 2)))


def _commute(c: Candidate) -> float:
    """출퇴근접근성 — 주요 업무지구(서울 강남/시청/여의도·대구 동대구/반월당) 직선거리(km) 기반 +
    ★역세권 통근속도 보정(2026-06-07 적대감사): 지하철 초역세권일수록 직선거리보다 *실제 통근이 빠르다*
    (예 가양/증미역 9호선 급행 → 여의도 ~13분, 직선 7km이나 통근은 4km대). subway_m 으로 유효 통근거리 단축.
    ★의미 분리(이중카운트 아님): 여긴 *통근 속도*(역세권→빠른 도달), 상승여력 infra 의 subway 는 *생활 인프라 가치*.
    [추론] 직선거리 proxy 의 보정이며 실제 환승·배차 정밀 시간은 아님. cbd_km 미수집이면 중립 3.0."""
    km = getattr(c, "cbd_km", None)
    if km is None:
        return 3.0
    sub = (c.infra or {}).get("subway_m", 9999) if c.infra else 9999
    f = 0.6 if sub <= 250 else 0.75 if sub <= 500 else 0.9 if sub <= 800 else 1.0   # 초역세권일수록 유효 통근거리↓
    km = km * f
    return (5.0 if km <= 2 else 4.3 if km <= 4 else 3.6 if km <= 6
            else 2.9 if km <= 9 else 2.3 if km <= 12 else 2.0)


def _norm_tukmokgo(pct: float) -> float:
    """특목고 진학률 %(과학고+외고국제고, 아실 기준 0~5 정규화). 계단 임계.
    서울 중학 분포 우편향 — 5%+ 상위, 2% 중위, 0% 하위. (신목4.2→4.3 / 경서2.1→2.8)"""
    return (5.0 if pct >= 5 else 4.3 if pct >= 4 else 3.6 if pct >= 3
            else 2.8 if pct >= 2 else 2.0 if pct >= 1 else 1.2)


def _norm_achievement(pct: float) -> float:
    """국가수준 학업성취도 보통학력이상 평균 %(0~5). 학군 최강 변별(신목95 vs 경서70).
    서울 중학 분포 ~65~97%. 계단 임계."""
    return (5.0 if pct >= 94 else 4.3 if pct >= 89 else 3.6 if pct >= 84
            else 2.9 if pct >= 79 else 2.2 if pct >= 74 else 1.5)


def _norm_academy(ax: int) -> float:
    """입시학원 800m 수(0~5). 학원가 밀집 — 목동117/입시11 → 5, 등촌47/입시0 → 2."""
    return (5.0 if ax >= 11 else 4.3 if ax >= 8 else 3.5 if ax >= 4 else 2.8 if ax >= 1 else 2.0)


def compute_school_district_score(c: Candidate) -> float | None:
    """자체 학군 점수 알고리즘(2026-06-05 · 적대검증 수정 v2). 학교알리미 OpenAPI 진학률 무변별(전부
    99%·성취도 폐지) 확인 후, 배정/최근접 중학교(아실, 원자료 학교알리미 5월공시) 직접신호로 대체.

    ★적대검증(sage gemma4/gpt-oss + agent-council 코드검증, 2026-06-05) 반영:
      (1) gu_ipsi(구 입시학원 *원시카운트*) **제거** — academy_exam(동 학원)과 이중카운트 + 구 인구/면적
          미정규화로 '구 크기'를 학군으로 오인(양천1421). 158단지 모두 tukmokgo/achievement 보유라 불필요.
      (2) achievement↔tukmokgo 공선(r≈0.65) → 단일 composite(acad) 로 de-collineate 후 0.60 가중.
          학원 인프라(academy_exam)는 *독립* 신호라 0.40 분리.
    학군 = 0.60·acad(학업성취 latent) + 0.40·academy(학원 인프라). 미주입은 가용가중 재정규화.

    [추론] 입력(아실 가공·원자료 학교알리미) + [사실] academy(카카오). 학업성취 *측정* 아님(상대 변별
    proxy)·미래예측 아님 — 같은 급지 상대 변별만(council 경고)."""
    av = getattr(c, "school_achievement", None)
    tm = getattr(c, "tukmokgo_pct", None)
    ax = getattr(c, "academy_exam", None)
    hk = getattr(c, "hakgun_score", None)
    # 학업성취 latent: 성취도·특목고는 공선(r≈0.65) → 단일 composite 로 묶어 이중가중 방지.
    acad = None
    if av is not None and tm is not None:
        acad = 0.65 * _norm_achievement(av) + 0.35 * _norm_tukmokgo(tm)
    elif av is not None:
        acad = _norm_achievement(av)
    elif tm is not None:
        acad = _norm_tukmokgo(tm)
    parts: list[tuple[float, float]] = []     # (weight, score0~5)
    if acad is not None:
        parts.append((0.60, acad))                   # 학업성취 latent(성취도+특목고 합성, de-collineated)
    if ax is not None:
        parts.append((0.40, _norm_academy(ax)))      # 학원 인프라(동 800m, 독립 신호)
    if not parts and hk is not None:
        parts.append((1.0, float(hk)))               # 전 신호 부재 시 intel 정성 fallback
    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    return round(min(5.0, sum(w * s for w, s in parts) / wsum), 2)   # 가용가중 재정규화


def _school(c: Candidate) -> float:
    """학군 [추론] proxy(0~5) — compute_school_district_score(특목고 진학률 주신호 + 학원밀도 + intel).
    학교알리미 진학률은 무변별(실측)이라 미사용. 미주입이면 표본평균 대체."""
    return compute_school_district_score(c)


def _slope(c: Candidate) -> float:
    """경사 [사실] — opentopodata 실측 slope_pct(객관). 평탄=5 / 가파름=낮음. 거주·임대 공통 불리.
    미측정이면 중립 3.5."""
    sp = getattr(c, "slope_pct", None)
    if sp is None:
        return None                          # 공백 → 표본평균 대체(2026-06-03 v2)
    return (5.0 if sp <= 3 else 4.3 if sp <= 6 else 3.5 if sp <= 10 else 2.6 if sp <= 15 else 1.8)


def _review(c: Candidate) -> float:
    """후기 정성 coarse 감성(0~5). ★편향·단일출처(council 경고)라 0.05 경가중 tiebreaker 전용.
    review_score 미주입이면 중립 3.0."""
    s = getattr(c, "review_score", None)
    return float(s) if s is not None else None   # 공백 → 표본평균 대체(2026-06-03 v2)


def _solve3(A: list[list[float]], b: list[float]) -> list[float] | None:
    """k×k 정규방정식 가우스 소거(부분 피벗). 특이행렬이면 None."""
    k = len(b)
    M = [A[i][:] + [b[i]] for i in range(k)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None                       # 특이(공선성) — 호출측이 폴백
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col]
        M[col] = [v / d for v in M[col]]
        for r in range(k):
            if r != col:
                f = M[r][col]
                M[r] = [M[r][j] - f * M[col][j] for j in range(k + 1)]
    return [M[i][k] for i in range(k)]


def _feat_rows(cands: list[Candidate], use_land: bool) -> list[list[float]]:
    rows = []
    for c in cands:
        row = [1.0, max(c.listing.pyeong, 0.1), (getattr(c, "cbd_km", None) or 6.0)]
        if use_land:
            row.append(c.land_share_pyeong)
        rows.append(row)
    return rows


def _hedonic_merit(candidates: list[Candidate],
                   reference: list[Candidate] | None = None) -> list[float] | None:
    """가격메리트 = '펀더멘털(크기·토지지분·서울 중심거리) 대비 저평가 정도' (2026-06-03 사용자 OVERRIDE).
    ★단순 평당가(싼 게 무조건 5)는 거리·크기를 무시 → 외곽 소형 저가가 과대평가됨. 백테스트(2026-06-01)도
    '가격메리트=싼값' 역할은 국면반전(예측력 NULL)이라 폐기 — *상대 저평가*로 재정의가 정직.
    방법: 헤도닉 OLS 호가(억) ~ 1 + 평수 + 중심거리(cbd_km) [+ 토지지분(실측 변량 있을 때만)].
    잔차(실호가 − 예측) 음(-)=펀더멘털 대비 저평가 → 메리트↑. 잔차 range-정규화 [2,5](기존 스케일 보존).
    ★reference 주입(2026-06-03 v3): 회귀계수·잔차범위를 reference 집합에서 산출해 candidates 에 적용 →
    동일 단지가 여러 리포트(통합 vs 주상복합단독)에서 *같은 점수*를 갖도록 고정기준집합으로 통일.
    reference=None 이면 candidates 자기참조(기존). 토지지분 상수 placeholder면 공선성→자동 제외.
    표본<5·특이행렬이면 None(호출측 평당가 폴백). 순수 stdlib·결정론(G3)."""
    ref = reference if reference is not None else candidates
    nref = len(ref)
    if nref < 5:
        return None
    land_ref = [c.land_share_pyeong for c in ref]
    lmean = sum(land_ref) / nref
    use_land = sum((v - lmean) ** 2 for v in land_ref) / nref > 1e-6   # 토지지분 실측 변량 있을 때만
    k = 4 if use_land else 3
    Fref = _feat_rows(ref, use_land)
    pref = [c.listing.price_krw / 100_000_000 for c in ref]
    A = [[sum(Fref[m][i] * Fref[m][j] for m in range(nref)) for j in range(k)] for i in range(k)]
    b = [sum(Fref[m][i] * pref[m] for m in range(nref)) for i in range(k)]
    coef = _solve3(A, b)
    if coef is None:
        return None
    resid_ref = [pref[i] - sum(coef[j] * Fref[i][j] for j in range(k)) for i in range(nref)]
    lo, hi = min(resid_ref), max(resid_ref)
    if hi == lo:
        return None
    Fc = _feat_rows(candidates, use_land)
    pc = [c.listing.price_krw / 100_000_000 for c in candidates]
    out = []
    for i in range(len(candidates)):
        r = pc[i] - sum(coef[j] * Fc[i][j] for j in range(k))
        out.append(round(max(2.0, min(5.0, 2.0 + 3.0 * (hi - r) / (hi - lo))), 2))
    return out


def _upside(c: Candidate, r: RedevScore) -> float:
    """상승여력(0~5) — 단지 고유 구조 변별만: (인프라 + 재건축잠재)/2.
    ★2026-06-04 gu_cagr(구 15년 과거 CAGR) 미래 승수 *제거*. 근거 2중 —
     (a) 검증(validate_reversion.py): 구 모멘텀 외삽은 forward 수익과 pooled ρ=−0.69(역방향),
         횡단면(구 선택)으론 regime-불안정(t0별 +0.06~+0.47 진동) → 안정 신호 아님(미래 과적합).
     (b) between-구 거시 신호는 §★① 생활권 base-rate·§★② mean-reversion 타이밍 레이어에 *이미* 존재 →
         점수축에 또 넣으면 거시 이중계상 + 비정상 외삽. 역할 정의(within-급지 검수표, '구 선택 예측기 아님')와 충돌.
    → 상승여력은 이제 '구 선택 베팅'이 아니라 '단지 구조 품질(인프라·재건축여지)'만 잰다.
    구 mean-reversion 은 점수가 아니라 §★② 타이밍 신호(시계열 ρ=+0.68, 횡단면 불안정)로 분리 운용.
    r(RedevScore)·c.gu_cagr 는 시그니처/표시(decision_prior·§★)용으로 보존, 점수엔 미사용."""
    return round(max(0.0, min(5.0, (_infra_score(c) + _redev_potential(c)) / 2.0)), 2)


def score_candidates(candidates: list[Candidate], redevs: list[RedevScore],
                     strategy: ExitStrategy,
                     weights: dict[str, float] | None = None,
                     strategies: list[ExitStrategy] | None = None,
                     reference_candidates: list[Candidate] | None = None) -> list[AxisScores]:
    """가격메리트 = 펀더멘털(크기·토지지분·서울 중심거리) 대비 저평가 정도(헤도닉 잔차, 2026-06-03 사용자
    OVERRIDE). 표본<5·특이행렬이면 평당가 상대순위로 폴백(싼 쪽 5 → 비싼 쪽 2).
    ★공백 데이터 처리(2026-06-03 v2 사용자 OVERRIDE): 전세수요·학군·경사·후기는 입력이 공백이면
    고정 중립이 아니라 **그 축의 표본평균(공백 아닌 후보들의 평균)으로 대체**한다(전부 공백이면 중립 폴백).
    weights override 시 그 가중치로 (민감도 분석의 가중치 교란용).
    strategies(후보별 전략) 주입 시 각 후보의 weighted_total 을 그 후보 전략 가중치로 산출 —
    서울(임대)·대구(실거주) 혼합을 단일 통합 순위로 비교(2026-05-30 사용자 요청)."""
    # ★고정 기준집합(reference) — 가격메리트 헤도닉·공백평균을 이 집합으로 산출(없으면 자기참조).
    #   동일 단지가 통합/단독 리포트에서 같은 점수를 갖도록 통일(2026-06-03 v3 사용자 1번 선택).
    ref = reference_candidates if reference_candidates is not None else candidates
    merits = _hedonic_merit(candidates, reference=ref)   # 헤도닉 잔차 메리트 (None 이면 폴백)
    ppp = [c.listing.price_krw / max(c.listing.pyeong, 0.1) for c in candidates]
    lo, hi = min(ppp), max(ppp)
    # ── 공백→표본평균 대체: 전세수요·학군·경사·후기 — 평균은 reference 집합에서(고정), 값은 candidates ──
    IMPUTABLE = {"전세수요": _jeonse_demand, "학군": _school, "경사": _slope, "후기": _review}
    NEUTRAL = {"전세수요": 3.0, "학군": 3.0, "경사": 3.5, "후기": 3.0}   # 전부 공백일 때만 폴백
    raw = {a: [fn(c) for c in candidates] for a, fn in IMPUTABLE.items()}
    impute = {}
    for a, fn in IMPUTABLE.items():
        present = [v for v in (fn(c) for c in ref) if v is not None]
        impute[a] = round(sum(present) / len(present), 2) if present else NEUTRAL[a]
    out: list[AxisScores] = []
    for i, (c, r, p) in enumerate(zip(candidates, redevs, ppp)):
        merit = (merits[i] if merits is not None
                 else (5.0 if hi == lo else 2.0 + 3.0 * (hi - p) / (hi - lo)))
        s = {
            "전세수요": raw["전세수요"][i] if raw["전세수요"][i] is not None else impute["전세수요"],
            "환금성": round(_liquidity(c), 2),
            "가격방어": round(_defense(c, r), 2),
            "상승여력": round(_upside(c, r), 2),
            "토지지분": _land_share(c),
            "가격메리트": round(merit, 2),
            "출퇴근": round(_commute(c), 2),
            "학군": raw["학군"][i] if raw["학군"][i] is not None else impute["학군"],
            "경사": raw["경사"][i] if raw["경사"][i] is not None else impute["경사"],
            "후기": raw["후기"][i] if raw["후기"][i] is not None else impute["후기"],
        }
        # ★전용 소형 = 가족(부모+아이) 거주 비대상 → 학군 무의미. (2026-06-03 사용자 OVERRIDE)
        #   ★적대검증(2026-06-05): 기존 <59 하드컷은 58.9↔59.1㎡ 사이 0→full 불연속 절벽 → 50~62㎡ ramp 로 smoothing.
        #   ★완화(2026-06-07 사용자): 전용59㎡ = 국민평형(방3)·가족 표준 거주 단위인데 50~62 ramp 는 59㎡를 ×0.75 로
        #     깎아 가족거주 단지(래미안안암 등)를 과소평가. → 가족적합 full 임계를 62→59 로 내리고 ramp 를 44~59 로
        #     (방3 표준 전용59㎡ full / 방2 소형 49㎡ ≈×0.33 / 방1 44㎡↓ 0). 도생·초소형의 학군무의미는 유지.
        a = c.listing.area_exclusive_m2
        if a < 59 and s["학군"]:
            s["학군"] = 0.0 if a < 44 else round(s["학군"] * (a - 44) / 15.0, 2)
        w = weights or WEIGHTS[strategies[i] if strategies else strategy]
        total = round(sum(s[a] * w[a] for a in AXES), 3)
        # ★호가 분리(2026-06-04): fundamental_total = 호가무관 축만(가격메리트·전세수요 제외) renorm.
        #   순위는 이걸로 매겨 '많이 빠짐→싸짐→점수↑' 누수를 차단. 가격대비 매력(가격메리트·전세수요)은 별도 레이어.
        # ★적대검증(2026-06-06): 결측 fundamental 축(학군·경사·후기)을 *평균대체* 대신 *가용가중 드롭*으로 처리.
        #   평균대체는 MNAR(미기재=노후·변두리 상관) 편향으로 우량 미기재 단지를 코호트평균으로 깎고, ref 자기참조로
        #   표본 mix 에 점수가 의존(비멱등). → 결측축은 fwsum 에서 빼고 present 축만으로 재정규화(_school 패턴 일관).
        imputed = {a for a in ("학군", "경사", "후기") if raw[a][i] is None}
        present_fund = [a for a in FUNDAMENTAL_AXES if a not in imputed]
        fwsum = sum(w[a] for a in present_fund)
        ftotal = round(sum(s[a] * w[a] for a in present_fund) / fwsum, 3) if fwsum else total
        # ★임퓨트 플래그(2026-06-06): 4개 IMPUTABLE 축(전세수요 포함) 중 입력 공백→표본평균 대체된 것 — 표시단에서 측정값과 구분.
        imputed_all = frozenset(a for a in IMPUTABLE if raw[a][i] is None)
        out.append(AxisScores(candidate=c, scores=s, weighted_total=total, fundamental_total=ftotal,
                              imputed=imputed_all))
    return out
