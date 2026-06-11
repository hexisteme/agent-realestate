"""데모 픽스처 — 합성 단지로 전체 파이프라인 1분 재현 (`agent-realestate report --demo`).

목적(슈퍼샘플, 2026-06-11): 실데이터(네이버 호가)는 DB권 보호로 repo 에 없으므로, 방문자가
클론 직후 §0~11 리포트가 결정론으로 재현되는 것을 보여주는 **명시적 가짜** 단지 6곳.
단지명에 '데모' 를 박아 실존 단지 오인을 차단한다. confirmed_date 는 실행일로 생성되어
F_STALE 게이트에 걸리지 않는다(단, '데모-전세미확보' 는 F_NORENT, '데모-예산초과' 는
F_OVERBUDGET 을 *의도적으로* 시연한다 — 게이트·partition 이 동작하는 모습 자체가 데모).
"""
from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

_INFRA = {"subway_m": 450, "mart_800": 4, "hosp_800": 80, "park_1k": 30, "dept_1500": 40}


def _cand(name, dong, area, floor, facing, price, units, built, far, land_py,
          jeonse=None, sg="데모-강북", gu="서울 데모북구", slope=4.0, trade=30.0,
          tukmokgo=8.0, achieve=72.0, academy=3, bcr=18.0, cbd=7.0):
    return {
        "complex_name": name, "dong_ho": dong, "area_exclusive_m2": area,
        "floor": floor, "facing": facing, "price_krw": price,
        "agent_name": "데모공인중개사사무소", "confirmed_date": date.today().isoformat(),
        "units": units, "built_year": built, "far_pct": far,
        "land_share_pyeong": land_py, "land_share_is_estimate": True,
        "jeonse_krw": jeonse, "transit": f"{sg} ({gu})", "district": gu,
        "cbd_km": cbd, "cbd_name": "여의도", "saenghwalgwon": sg,
        "slope_pct": slope, "broker_count": 2, "regulated": True,
        "redev_stage": "NONE", "academy_exam": academy, "infra": _INFRA,
        "trade_annual": trade, "gu_jeonse_ratio": 0.58, "gu_cagr": 4.2,
        "tukmokgo_pct": tukmokgo, "school_achievement": achieve, "bcr_pct": bcr,
    }


DEMO_CANDIDATES = [
    # 균형형 — 대단지·완경사·전세 확보 (1위 후보)
    _cand("데모-강변타운", "101동", 84.0, "10/15층", "남향", 830_000_000,
          units=1400, built=1998, far=210, land_py=12.0, jeonse=520_000_000),
    # 재건축 결: 저용적률·노후 (토지지분 강점, 거래 적음)
    _cand("데모-저밀주공", "3동", 59.0, "4/5층", "남동향", 820_000_000,
          units=900, built=1989, far=120, land_py=17.5, jeonse=380_000_000, trade=9.0),
    # 신축 비아파트 — 주상복합 가드(_is_non_apt: 토지 2.0캡·신축가점 박탈) 시연
    _cand("데모-리버뷰[주상복합]", "1동", 72.0, "20/35층", "남서향", 850_000_000,
          units=420, built=2019, far=580, land_py=3.2, jeonse=560_000_000, bcr=45.0),
    # 전세 미확보 — F_NORENT 플래그(점수 비차감·신뢰도 반영) 시연
    _cand("데모-전세미확보", "205동", 84.0, "7/12층", "동향", 790_000_000,
          units=600, built=2002, far=240, land_py=9.5, jeonse=None, sg="데모-강남",
          gu="서울 데모남구", slope=7.0),
    # 예산 초과 — F_OVERBUDGET 하드페일 partition(§A 하단 강등) 시연
    _cand("데모-한강프리미엄", "302동", 110.0, "15/20층", "남향", 1_950_000_000,
          units=800, built=2008, far=260, land_py=11.0, jeonse=1_050_000_000,
          sg="데모-강남", gu="서울 데모남구", tukmokgo=14.0, achieve=85.0, academy=9, cbd=3.0),
    # 소형·급경사 — 약점축(학군 ramp·경사) 시연
    _cand("데모-언덕마을", "7동", 49.0, "2/14층", "북동향", 610_000_000,
          units=350, built=1995, far=230, land_py=7.0, jeonse=300_000_000,
          slope=14.0, trade=18.0),
]

DEMO_PROFILE = {
    "exit_strategy": "HOLD_AND_RENT",
    "first_time": True,
    "annual_income_krw": 100_000_000,
    "own_capital_krw": 400_000_000,
    "regulated": True,
    "mortgage_rate": 0.043,
    "term_years": 30,
    "dsr_limit": 0.40,
    "existing_annual_debt_krw": 0,
    "_note": "합성 데모 프로필 — 실존 인물·단지와 무관",
}


def write_demo_inputs(outdir: str | None = None) -> tuple[str, str]:
    """데모 profile/candidates JSON 을 임시 디렉토리에 생성하고 경로 반환."""
    d = Path(outdir) if outdir else Path(tempfile.mkdtemp(prefix="re-demo-"))
    d.mkdir(parents=True, exist_ok=True)
    prof = d / "profile_demo.json"
    cand = d / "candidates_demo.json"
    prof.write_text(json.dumps(DEMO_PROFILE, ensure_ascii=False, indent=1), encoding="utf-8")
    cand.write_text(json.dumps(DEMO_CANDIDATES, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(prof), str(cand)
