"""collect_kapt_maint_fees.py — K-apt 공용관리비 배치수집 → universe JSON maint_fee_won 필드 추가.

사용:
    python3 collect_kapt_maint_fees.py        # .env 의 MOLIT_API_KEY 사용

전제:
    data.go.kr 서비스 ID 15057937 (공동주택공용관리비) 별도 활용신청 필요.
    미승인 키 → maint_fee_won=None 으로 저장(중단 없음, 승인 후 재실행).

포함 항목: 경비비·청소비·수선비·승강기유지비·위탁관리수수료·재해예방비·제사무비(확정).
미포함(엔드포인트 미확정): 인건비·교육훈련비·소독비·시설유지비 등.

동작(2026-07-07 재작성):
    1. kaptCode = collect_universe_enrich 가 세대수·준공 교차검증으로 확정한 kapt_code 재사용
       (이름 substring 첫-매치는 타 단지 오매칭 5건 실측 — 적대리뷰 critical — 으로 폐기).
       kapt_verified 없는 단지는 관리비도 미기재(확인된 사실만).
    2. fetch_maint_fee(kaptCode, searchDate, key) 최근 MONTHS_BACK 개월 평균.
    3. 세대당 관리비(원) = 단지총액 ÷ units → maint_fee_won (1만원 미만은 부분응답 의심 → None).
    4. 최신 universe JSON(glob 자동 선택) in-place 갱신.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

from agent_realestate import config
config.load_env_file()

from agent_realestate.collectors.kapt import fetch_maint_fee

# ── 설정 ──────────────────────────────────────────────────────────────────────
EX = Path("examples")
# 최신 universe 자동 선택(run_daily._latest_or 와 동일 glob) — 구 파일 하드코딩 시
# 백필 결과가 발행에 무시되는 사고 방지 (2026-07-07).


def latest_universe_path() -> Path:
    """최신 candidates_universe 파일 — 호출 시점 해소(import 시점 [-1] 은 데이터 파일 없는 CI 수집을 깬다, 2026-09-05)."""
    files = sorted(EX.glob("candidates_universe[0-9][0-9][0-9]_*.json"))
    if not files:
        raise SystemExit("examples/candidates_universe*.json 없음 — enumerate_25gu.py 산출물이 필요하다")
    return files[-1]
MONTHS_BACK = 3   # 최근 N개월 평균 (API 보고 지연 1~2개월 감안)
SLEEP_SEC   = 0.2  # API 호출 간격


def _recent_ym_list(n: int) -> list[str]:
    """오늘 기준 최근 n개월 YYYYMM 리스트 (최신 순)."""
    y, m = date.today().year, date.today().month
    result: list[str] = []
    for _ in range(n):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        result.append(f"{y}{m:02d}")
    return result


def _avg_fee_total(kapt_code: str, key: str, months: list[str]) -> int | None:
    """최근 months 월 단지 총 공용관리비(원/월) 평균. 데이터 없으면 None."""
    totals: list[int] = []
    for ym in months:
        result = fetch_maint_fee(kapt_code, ym, key)
        if result is not None:
            totals.append(result)
        time.sleep(SLEEP_SEC)
    return round(sum(totals) / len(totals)) if totals else None


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    universe_path = latest_universe_path()
    key = os.environ.get("MOLIT_API_KEY", "")
    if not key:
        raise SystemExit("MOLIT_API_KEY 미설정 (.env)")

    universe: list[dict] = json.load(open(universe_path, encoding="utf-8"))

    ym_list = _recent_ym_list(MONTHS_BACK)
    print(f"universe: {universe_path.name} ({len(universe)}개) · 조회 월 {ym_list} 평균\n")

    cnt_ok = cnt_no_code = cnt_no_data = 0

    for c in universe:
        name  = c.get("complex_name", "")
        units = c.get("units") or 0

        # 교차검증 확정 kapt_code 만 사용 — 미검증 단지는 미기재(확인된 사실만).
        kapt_code = c.get("kapt_code") if c.get("kapt_verified") else None
        if not kapt_code:
            c["maint_fee_won"] = None
            cnt_no_code += 1
            continue

        total = _avg_fee_total(kapt_code, key, ym_list)
        if total is not None and units > 0:
            per_hh = total // units
            # 세대당 1만원 미만 = K-apt 부분응답(7개 세부항목 중 일부만 성공) 의심 —
            # '관리비 0만원/월' 오발행 방지, 데이터없음 처리 (2026-07-07 리뷰).
            if per_hh < 10_000:
                c["maint_fee_won"] = None
                cnt_no_data += 1
                print(f"  [부분응답의심] {name}: 세대당 {per_hh}원 → None ({kapt_code})")
                continue
            c["maint_fee_won"] = per_hh
            cnt_ok += 1
            print(f"  [OK] {name}: 총{total//10000}만→세대당{per_hh//10000}만원 ({kapt_code})")
        else:
            c["maint_fee_won"] = None
            cnt_no_data += 1

    json.dump(universe, open(universe_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n✓ {universe_path} 갱신")
    print(f"  OK:{cnt_ok}  미검증코드:{cnt_no_code}  데이터없음:{cnt_no_data}")
    if cnt_ok == 0:
        print("  ⚠ 전건 실패 = 15057937 활용신청 미승인 가능성 — data.go.kr 에서 신청 후 재실행")


if __name__ == "__main__":
    main()
