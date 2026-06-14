"""사이클 국면(regime) 분류 — 현 시스템이 결여한 *매크로 사이클 맥락*.
2026-06-04 사용자 요구(정량+정책·투매심리 연결) + agent_intel 수집 타임라인(BOK 금리 1차·정책 발표일).

핵심 발견(feature_discovery + regime 정렬): 거래량·상대가치 신호는 *정적*으론 비정상이나
국면 조건부로 일관 — 거래량은 **상승진입기**(금리 인하·정책 완화·미분양 감소)에 forward수익 예측(ρ≈+0.3),
**과열기**(긴축전환·저미분양·패닉심리)엔 소멸. intel: 금리·정책=선행 식별자, 심리=후행 확인.

★OOS 한계(정직): MOLIT 기간 독립 사이클 2~3개뿐 → 국면 분류기는 *메커니즘+in-sample 일관* 근거이나
forward 예측력의 OOS 검증은 제한적. 따라서 *예측기*가 아니라 *국면 맥락 + 국면별 활성신호 라벨*로 쓴다.

provenance: agent_intel 2026-06-04(BOK bok.or.kr 1차·정책 molit.go.kr/korea.kr·미분양 보도). 전부 INFERENCE
라벨(버스 서명 미인증)이나 금리 시계열은 4도메인 교차=FACT급. 출처 `/tmp/out_{rate,policy,supply,sentiment}.json`.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass

# 정량 입력 시계열(미분양·소비심리) — agent 수집(e-나라지표 idx_cd=1234 MOLIT 원천·KREMAP 국토연구원), 2026-06-04.
_INPUTS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "examples", "regime_inputs_20260604.json")


def load_regime_inputs() -> dict:
    """미분양(전국 연말 호)·소비심리지수 실데이터. 부재 시 {} (compute_regime 은 _TIMELINE/모멘텀 fallback)."""
    try:
        d = json.load(open(_INPUTS_PATH, encoding="utf-8"))
        return {"unsold": d.get("unsold", {}).get("data", {}),
                "sentiment_seoul": d.get("sentiment_house_sale_seoul", {}).get("data", {})}
    except Exception:
        return {}


def unsold_trend(year: int, inputs: dict | None = None) -> float | None:
    """미분양 전년대비 변화율(+급증/−감소). 실데이터 부재 시 None."""
    us = (inputs or load_regime_inputs()).get("unsold", {})
    a, b = us.get(str(year)), us.get(str(year - 1))
    return (a - b) / b if (a and b) else None

# 연도별 regime (intel 타임라인). 관측가능 매크로만(심리는 후행 확인용 별도).
# phase: BOTTOM 바닥 · ASCENDING 상승진입 · OVERHEATED 과열 · CORRECTING 조정/하락
_TIMELINE = {
    2010: ("BOTTOM", "완화", "과잉", "고", "침체"),
    2011: ("BOTTOM", "완화", "과잉", "고", "침체"),
    2012: ("BOTTOM", "완화", "과잉", "인하", "침체"),
    2013: ("BOTTOM", "완화", "과잉", "인하", "공포"),
    2014: ("ASCENDING", "완화", "감소전환", "인하", "회복초입"),
    2015: ("ASCENDING", "완화", "부족화", "저", "온기"),
    2016: ("ASCENDING", "완화", "부족", "저", "온기"),
    2017: ("OVERHEATED", "긴축전환", "부족", "저", "과열초입"),
    2018: ("OVERHEATED", "긴축", "부족", "상승", "과열"),
    2019: ("OVERHEATED", "긴축", "부족", "인하", "과열고점"),
    2020: ("OVERHEATED", "긴축", "부족", "제로(ZIRP)", "패닉바잉"),
    2021: ("OVERHEATED", "긴축", "부족", "인상시작", "영끌고점"),
    2022: ("CORRECTING", "완화전환", "과잉전환", "급속인상", "거래절벽전환"),
    2023: ("CORRECTING", "완화", "과잉", "고점동결", "공포"),
    2024: ("CORRECTING", "완화", "과잉", "인하전환", "국지반등"),
    2025: ("OVERHEATED", "재긴축", "양극", "인하지속", "재과열"),
    # ★4차 감사 C(2026-06-13): 2026 국면 추가.
    # 근거(agent-council intel 2026-06-13): 강남3구 집값 상승전환(양도세 중과유예 종료 후 거래급감→집값 반등),
    # 토허 강남·송파 재건축 1년 더 연장(2026-05-07). 10.15 재긴축 기조 유지.
    # BOK 기준금리는 공식 데이터 부재 → 전년 2.50% 유지 추정(INFERENCE, scan-regime 갱신 필요).
    # ★5차 개선 J(2026-06-14): rate 필드 "동결추정"→실측 업데이트.
    # 2026-01-15 동결·2026-05-28 동결 8회 연속·매파적 동결(2명 인상 소수의견). 출처: bok.or.kr 2026-06-13 웹검증.
    2026: ("OVERHEATED", "재긴축지속", "양극", "매파동결(8회연속,2.50%)", "강남재과열"),
}
# 국면별 *역사적으로 활성*인 단지-level 신호 (feature_discovery in-sample, OOS 제한).
#   value: (거래량 신호 방향, 상대가치 신호 방향, 한줄)
_ACTIVE = {
    "BOTTOM":     (+1, -1, "바닥: 저평가(싼) 단지 반등여지 우위. 거래량 약신호."),
    "ASCENDING":  (+1, +1, "상승진입: 거래량 상위·구내 프리미엄 단지가 선도(거래량 ρ≈+0.3, in-sample)."),
    "OVERHEATED": (0, -1, "과열: 거래량 변별력 소멸(다 유동). 고평가 단지 반락 위험(저평가 reversion)."),
    "CORRECTING": (-1, -1, "조정/하락: 모멘텀 역전. 저평가·고유동 방어. 진입은 바닥 신호 대기."),
}
KEY_POLICY = {  # regime 스탠스 전환점(intel, 출처 다도메인)
    "2017-08-02": "8·2 긴축전환", "2018-09-13": "9·13 긴축", "2019-12-16": "12·16 긴축",
    "2020-06-17": "6·17 긴축(molit 1차)", "2022": "윤정부 완화전환", "2023-01-03": "1·3 규제대폭해제",
    "2025-10-15": "10·15 재긴축(molit 1차)",
}


# BOK 기준금리 연말값(%) — agent_intel 2026-06-04, bok.or.kr 1차 + 4도메인 교차(FACT급).
BOK_RATE = {2009: 2.00, 2010: 2.50, 2011: 3.25, 2012: 2.75, 2013: 2.50, 2014: 2.00,
            2015: 1.50, 2016: 1.25, 2017: 1.50, 2018: 1.75, 2019: 1.25, 2020: 0.50,
            2021: 1.00, 2022: 3.25, 2023: 3.50, 2024: 3.00, 2025: 2.50,
            2026: 2.50}  # [사실] 2026-01-15 동결·2026-05-28 동결(8회 연속, 매파적 동결 — 2명 인상 소수의견). 출처: bok.or.kr 2026-06-13 웹검증


# 정책 스탠스 연도별 — *관측가능 dated 발표*에서 도출(8·2 2017 긴축전환·윤정부 2022 완화·10·15 2025 재긴축).
POLICY_STANCE = {y: "완화" for y in range(2010, 2017)}
POLICY_STANCE.update({y: "긴축" for y in range(2017, 2022)})      # 8·2(2017)~ 긴축기
POLICY_STANCE.update({y: "완화" for y in range(2022, 2025)})      # 윤정부 완화전환
POLICY_STANCE[2025] = "긴축"                                       # 10·15 재긴축
POLICY_STANCE[2026] = "긴축"                                       # 재긴축 지속(intel 2026-06-13)


def compute_regime(year: int, market_mom: float | None,
                   unsold_trend: float | None = None, prev_mom: float | None = None) -> str:
    """정량+관측가능 입력으로 국면 *도출*(하드코딩 lookup 대체, 2026+ 일반화). 모두 t0 관측가능:
    market_mom=시장 trailing 2yr 연율(MOLIT) · rate_dir/velocity=BOK 전년대비 · policy=dated 발표 스탠스
    · unsold_trend=미분양 전년대비(+급증/−감소, 도착시) · prev_mom=전기 모멘텀.
    로직(feature_discovery 사이클 + intel 선행마커: 정책·금리=선행, 심리=후행):
      과열=고모멘텀 OR (중모멘텀 & 긴축) / 조정=급속인상 OR 미분양급증 OR 고→음전환 / 바닥=저모멘텀+완화 / 상승진입=회복+완화.
    fallback: market_mom 부재 시 _TIMELINE."""
    if market_mom is None:
        t = _TIMELINE.get(year)
        return t[0] if t else "UNKNOWN"
    r, rp = BOK_RATE.get(year), BOK_RATE.get(year - 1)
    rate_dir = (r - rp) if (r is not None and rp is not None) else 0.0   # +인상 −인하(속도=절대값)
    pol = POLICY_STANCE.get(year, "중립")
    # ① 과열: 모멘텀 과도(>15%) OR 중모멘텀(>5%)+긴축(정책 선행 마커가 과열 조기 포착)
    if market_mom > 0.15 or (market_mom > 0.05 and pol == "긴축"):
        return "OVERHEATED"
    # ② 조정/하락: 급속 금리인상(>1.5%p) OR (모멘텀 약[<2%] & [직전 고점 OR 미분양 *상승* 중]).
    #   ★미분양은 *모멘텀 약할 때만* 작동(2015 미분양 급증은 가격상승기=공급추격이라 CORRECTING 아님 — fragile override 방지).
    us_rising = unsold_trend is not None and unsold_trend > 0.1
    if rate_dir > 1.5 or (market_mom < 0.02 and (
            (prev_mom is not None and prev_mom > 0.10) or us_rising)):
        return "CORRECTING"
    # ③ 상승진입: 모멘텀 회복(0~15%) + 완화/저금리 (미분양 급증해도 가격상승 동반이면 회복장)
    if 0.0 <= market_mom <= 0.15 and rate_dir <= 0.0 and pol != "긴축":
        return "ASCENDING"
    # ④ 바닥: 저/음 모멘텀 + 완화 (미분양 *감소*=흡수 중이면 바닥 확인)
    if market_mom < 0.03 and pol != "긴축":
        return "BOTTOM"
    return "ASCENDING" if market_mom >= 0.03 else "BOTTOM"


@dataclass(frozen=True)
class RegimeContext:
    year: int
    phase: str
    policy_stance: str
    supply: str
    rate: str
    sentiment: str
    volume_signal: int      # +1 활성(거래량 상위 우위) / 0 소멸 / -1 역전
    value_signal: int       # +1 프리미엄 우위(momentum) / -1 저평가 우위(reversion)
    note: str


def classify_regime(year: int) -> RegimeContext | None:
    """연도 → 사이클 국면 맥락. intel 타임라인 기반(관측가능 매크로). 미수록 연도 None."""
    t = _TIMELINE.get(year)
    if not t:
        return None
    phase, pol, sup, rate, sent = t
    vsig, valsig, note = _ACTIVE[phase]
    return RegimeContext(year, phase, pol, sup, rate, sent, vsig, valsig, note)


def sentiment_annual(year: int, inputs: dict | None = None) -> float | None:
    """서울 주택매매 소비심리지수 연평균(KREMAP). 부재 시 None. 시계열 범위 ~98~139(100=명목중립)."""
    se = (inputs or load_regime_inputs()).get("sentiment_seoul", {})
    vals = [v for ym, v in se.items() if ym[:4] == str(year)]
    return round(sum(vals) / len(vals), 1) if vals else None


def sentiment_confirms(phase: str, year: int, inputs: dict | None = None) -> tuple[str, float | None]:
    """소비심리(후행 *확인* 신호 — intel: 진입판별 아닌 과열/공포 확인)가 국면을 confirm 하는가.
    이 시계열 상대임계(고>125·저<108). 반환 (status, value): 확인 / 발산(주의) / 중립 / n/a."""
    s = sentiment_annual(year, inputs)
    if s is None:
        return ("n/a", None)
    if phase == "OVERHEATED":
        return ("과열 확인" if s >= 125 else "발산(심리 미과열)", s)
    if phase in ("CORRECTING", "BOTTOM"):
        return ("공포/위축 확인" if s <= 110 else "발산(심리 여전 높음)", s)
    if phase == "ASCENDING":
        return ("회복 심리 확인" if s >= 112 else "중립", s)
    return ("중립", s)


_JEONSE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "examples", "jeonse_ratio_ts_20260604.json")


def _load_jeonse() -> dict:
    try:
        return json.load(open(_JEONSE_PATH, encoding="utf-8")).get("seoul", {})
    except Exception:
        return {}


def jeonse_value_timing(year: int | None = None) -> dict:
    """전세가율(서울) value-timing 컨텍스트 — ★구/단지 선택 신호 아님(횡단면 ρ−0.25 역상관).
    *시장 진입 타이밍* 선행지표: 高=전세지지 강·갭 작음=상방여지(in-sample 강하나 사이클 2~3개 한계),
    低=갭 큼·전세지지 약=value 불리. 현 시계열 범위(서울 ~53~72%) 백분위로 라벨. 부동산원 R-ONE(2026-06-04)."""
    se = _load_jeonse()
    if not se:
        return {}
    yrs = sorted(se)
    y = str(year) if year and str(year) in se else yrs[-1]
    vals = list(se.values())
    lo, hi = min(vals), max(vals)
    cur = se[y]
    pct = (cur - lo) / (hi - lo) if hi > lo else 0.5
    sig = "value 유리(전세지지 강·갭 작음)" if pct >= 0.67 else "value 불리(갭 큼·전세지지 약)" if pct <= 0.33 else "중립"
    return {"year": y, "ratio": cur, "pct": round(pct, 2), "lo": lo, "hi": hi, "signal": sig}


def regime_entry_read(year: int | None = None) -> dict:
    """★전세가율 편입(2026-06-04 사용자 '5번째 입력 검토') = phase-DRIVER 아닌 value-지속가능성 MODIFIER.
    근거: 전세가율 추세가 phase와 *직교*(ASCENDING 2014-16=전세가율 상승=전세지지 / OVERHEATED 2017-21=전세가율
    *하락* 71→57=갭확대=무지지 과열). phase 분류기는 4축 14/14라 오염 금지 → 전세가율은 *진입 환경 quality*로 결합.
    반환: phase + 전세가율 level/trend + supported(전세지지 여부) + 결합 진입환경 read + risk."""
    rc = current_regime() if year is None else classify_regime(year)
    jv = jeonse_value_timing(year)
    if not rc:
        return {}
    se = _load_jeonse()
    y = jv.get("year") if jv else None
    jr = jv.get("ratio") if jv else None
    jrp = se.get(str(int(y) - 2)) if y else None
    trend = "상승" if jr and jrp and jr > jrp + 1 else "하락" if jr and jrp and jr < jrp - 1 else "횡보"
    supported = bool(jv and (jv["pct"] >= 0.5 or trend == "상승"))
    phase = rc.phase
    if phase == "OVERHEATED":
        read = "전세지지 과열(상대적 견조)" if supported else "갭주도·무지지 과열 → 고위험 진입"
        risk = "중" if supported else "고"
    elif phase == "ASCENDING":
        read = "전세지지 상승 → 양호 진입국면" if supported else "무지지 상승(주의)"
        risk = "저" if supported else "중"
    elif phase == "BOTTOM":
        read = "저평가+전세지지 → 가치 진입국면" if supported else "바닥 형성중(전세지지 약, 대기)"
        risk = "저" if supported else "중"
    else:  # CORRECTING
        read = "조정/하락 — 진입은 바닥+전세지지 반등 신호 대기"
        risk = "고"
    return {"phase": phase, "jeonse_ratio": jr, "jeonse_trend": trend, "supported": supported,
            "read": read, "risk": risk}


def regime_evidence() -> dict:
    """현 국면을 뒷받침하는 실데이터 근거(미분양·소비심리) — §0.3 표시용. 출처 e-나라지표·KREMAP(2026-06-04)."""
    inp = load_regime_inputs()
    us = inp.get("unsold", {}); se = inp.get("sentiment_seoul", {})
    yrs = sorted(us, key=int)
    se_yrs = sorted(se)
    return {
        "unsold_latest": (yrs[-1], us[yrs[-1]]) if yrs else None,
        "unsold_peak": (max(us, key=lambda k: us[k]), max(us.values())) if us else None,
        "unsold_trough": (min(us, key=lambda k: us[k]), min(us.values())) if us else None,
        "sentiment_latest": (se_yrs[-1], se[se_yrs[-1]]) if se_yrs else None,
    }


def current_regime() -> RegimeContext:
    """현 시점 최신 국면(타임라인 최종연도). 리포트 맥락용.
    2026: 과열지속·재긴축·강남재과열(intel 2026-06-13). BOK 동결 추정 — scan-regime 으로 갱신 요망."""
    return classify_regime(max(_TIMELINE))


# feature_discovery 국면별 거래량 IC(in-sample, within-구) — 활성신호 강도 라벨 근거
PHASE_VOLUME_IC = {"BOTTOM": 0.28, "ASCENDING": 0.35, "OVERHEATED": 0.08, "CORRECTING": None}


def regime_conditional_guidance(year: int | None = None) -> str:
    """국면별 의사결정 가이드 한 줄(예측기 아님·OOS 제한 라벨)."""
    c = classify_regime(year) if year else current_regime()
    if not c:
        return ""
    ic = PHASE_VOLUME_IC.get(c.phase)
    icstr = f"거래량 신호 in-sample ρ≈{ic:+.2f}" if ic is not None else "거래량 신호 약/역전"
    return f"[{c.year} {c.phase}] {c.note} ({icstr}, OOS 제한=사이클 2~3개)"


def regime_leading_inputs(year: int) -> dict:
    """국면 *선행* 식별자(금리방향·정책스탠스·미분양) = 그 시점 관측가능(intel: 선행).
    심리는 후행 확인용으로 분리(진입 판별 부적합)."""
    c = classify_regime(year)
    if not c:
        return {}
    return {"leading": {"금리": c.rate, "정책": c.policy_stance, "미분양": c.supply},
            "coincident_confirm": {"심리": c.sentiment}, "phase": c.phase}
