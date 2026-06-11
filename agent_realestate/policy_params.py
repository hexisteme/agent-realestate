"""정책 수치 외부화 (P0-2, RDU-059). 세율·공시가율·공제 등을 코드 상수가 아니라
출처·확인일 있는 캐시(policy_param)에서 로드한다. 캐시에 없으면 *기본값 + 미검증 라벨*.

기본값은 '편의를 위한 출발점'일 뿐 정책 사실이 아니다 — report 는 is_default 면
'정책 미검증' 배너로 경고한다 (정책은 월 단위로 변함, RDU-017 Layer 2).
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace

# 캐시 key → PolicyParams 필드. scan-policy 의 param_key 로 주입.
_FLOAT_KEYS = {
    "official_price_ratio", "fair_market_ratio", "acq_low_rate", "acq_high_rate",
    "acq_edu_ratio", "acq_farm_rate",
    "vacancy_pct", "maintenance_pct", "stress_rate_addon",
}
_INT_KEYS = {
    "acq_first_relief", "acq_relief_cap_eok", "acq_low_threshold_eok",
    "acq_high_threshold_eok", "acq_farm_area", "jongbu_deduction_1home", "jongbu_deduction_multi",
    "management_fee_per_pyeong_month",
}


@dataclass(frozen=True)
class PolicyParams:
    # 공시가/과표
    official_price_ratio: float = 0.65       # 공시가 ≈ 매매가 × 이 비율 (가정)
    fair_market_ratio: float = 0.60          # 공정시장가액비율
    # 취득세
    acq_low_threshold_eok: int = 6
    acq_high_threshold_eok: int = 9
    acq_low_rate: float = 0.01
    acq_high_rate: float = 0.03
    acq_edu_ratio: float = 0.1               # 지방교육세 ≈ 본세 × 이 비율
    acq_farm_rate: float = 0.002             # 농특세 (85㎡ 초과)
    acq_farm_area: int = 85
    acq_first_relief: int = 2_000_000        # 생애최초 감면 한도
    acq_relief_cap_eok: int = 12             # 생애최초 감면 적용 상한(억)
    # 종부세
    jongbu_deduction_1home: int = 1_200_000_000
    jongbu_deduction_multi: int = 900_000_000
    # 누진 구간 데이터화 (R11) — 코드 로직 박제 대신 데이터(과표상한, 한계세율) marginal
    jongbu_brackets: tuple = ((300_000_000, 0.005), (600_000_000, 0.007), (1_200_000_000, 0.010),
                              (2_500_000_000, 0.013), (5_000_000_000, 0.015), (float("inf"), 0.027))
    property_tax_brackets: tuple = ((60_000_000, 0.001), (150_000_000, 0.0015),
                                    (300_000_000, 0.0025), (float("inf"), 0.0040))
    # ★1세대1주택 재산세 특례 (3차 감사 D, 2026-06-11) — 미반영 시 타깃 가격대(공시≤9억) 재산세
    #   1.76~1.8배 과대(연 +34~77만). 특례세율=지방세법 111조의2(공시 9억 이하, 표준 −0.05%p),
    #   공정시장가액비율 특례=시행령 109조(공시 3억↓43%·3~6억 44%·6억↑45%).
    #   출처: law.go.kr 지방세법·시행령 · korea.kr 정책브리핑 148941752 · taxtimes.co.kr/274811 (확인 2026-06-11).
    property_special_brackets: tuple = ((60_000_000, 0.0005), (150_000_000, 0.001),
                                        (300_000_000, 0.002), (float("inf"), 0.0035))
    fair_market_1home_tiers: tuple = ((300_000_000, 0.43), (600_000_000, 0.44), (float("inf"), 0.45))
    property_urban_rate: float = 0.0014   # 도시지역분 근사
    # 실보유비용 (P1-5)
    management_fee_per_pyeong_month: int = 5000   # 관리비 평당 월(원). 단지별 편차 큼 — 가정
    maintenance_pct: float = 0.003                # 연 수선충당 ≈ 매매가 × 이 비율 (가정)
    vacancy_pct: float = 0.03                     # 임대 공실률 가정 (전세금 이자손실 환산용)
    # DSR (P2-9) stress 가산금리
    stress_rate_addon: float = 0.015              # 한국 stress DSR 가산금리 (대략)
    # 메타
    confirmed_date: str = "default"
    source: str = "코드 기본값(미검증)"
    is_default: bool = True

    @classmethod
    def from_cache(cls, conn) -> "PolicyParams":
        """policy_param 테이블에서 매칭되는 key 만 override. 하나라도 세금 param 이 있으면
        is_default=False + 가장 최근 confirmed_date 채택."""
        from .cache import store
        overrides: dict = {}
        latest_date = None
        latest_url = None
        for f in fields(cls):
            if f.name in _FLOAT_KEYS or f.name in _INT_KEYS:
                row = store.get_param(conn, f.name)
                if row is not None:
                    overrides[f.name] = (float(row["value"]) if f.name in _FLOAT_KEYS
                                         else int(row["value"]))
                    if latest_date is None or row["confirmed_date"] > latest_date:
                        latest_date, latest_url = row["confirmed_date"], row["url"]
        if not overrides:
            return cls()  # 전부 기본값
        return replace(cls(), is_default=False,
                       confirmed_date=latest_date or "default",
                       source=latest_url or "정책캐시", **overrides)
