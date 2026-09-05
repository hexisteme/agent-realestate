"""collect_gongsi.py — 공동주택 공시가격(VWorld NSDI) 배치수집 → universe gongsi_man 백필.

사용:
    python3 collect_gongsi.py            # .env 의 VWORLD_API_KEY + MOLIT_API_KEY 사용

전제:
    vworld.kr 인증키(VWORLD_API_KEY). data.go.kr 15124003 의 실제 제공처는 VWorld NSDI
    (구 ApartHousingPriceService3 계열은 전 버전 HTTP500 폐기 — collectors/gongsi.py docstring).
    2026-07-09 라이브 검증: getApartHousingPriceAttr(pnu, stdrYear) → 호별 prvuseAr·pblntfPc(원).

동작:
    1. kaptCode = kapt_verified 단지만 (이름 substring 첫-매치는 타 단지 오매칭 5건 실측으로 폐기).
    2. pnu 조립 = basis V4 raw 의 bjdCode(10) + 필지구분(일반1/산2) + kaptAddr 지번 본번4+부번4.
       지오코딩 불필요 (2026-07-08 HANDOFF 설계).
    3. VWorld 공시가 전 페이지 수집(1000행/페이지) → 신원게이트(verify_parcel_identity — canonical
       완전일치 우선, 아니면 ≥5자 단방향 포함 + 레코드수/세대수 25% 이내 일치만 허용. 2026-09-05
       수정 — 구 상호포함(mutual containment) 게이트는 짧은 일반명이 다른 단지를 오매칭할 수 있어 폐기)
       → 동일평형(±3.5㎡, molit 매칭과 동일 톨러런스) 호들의 공시가 중위(원) → 만원 환산 → gongsi_man.
    4. 타당성 가드: 동일평형 MOLIT 실거래 중위(canonical 완전일치 매칭, 2026-09-05 수정 — 구 부분일치/
       4자 prefix 폐지) 대비 20~90% 범위 밖이면 None(공시가율 통상 40~70% — 범위 밖 = 오매칭/단위오류
       신호). 실거래 표본 없으면 가드 불가 로그 후 유지.
    5. 최신 universe JSON(run_daily._latest_or 동일 glob) in-place 갱신.
"""
from __future__ import annotations

import json
import re
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

from agent_realestate import config
config.load_env_file()

import os

from agent_realestate.collectors.kapt import BASIS_EP_V5, _get_json_item
from blog.build_explorer import GU_LAWD, match_molit_names
# 신원(identity) 검증 게이트 — agent_realestate/identity.py 로 이관(2026-09-05, collect_universe_enrich
# 의 K-apt 코드 배정 게이트 verify_kapt_basis_identity 와 로직을 공유하기 위한 패키지화). 이 모듈의
# 기존 호출자(collect_public_enrich.py·revalidate_gongsi.py·audit_kapt_identity.py·테스트)는 그대로
# `from collect_gongsi import ...` 를 쓰므로 이름을 여기서 재노출(re-export)한다 — 로직/독스트링은 그대로.
from agent_realestate.identity import (
    _identity_norm,
    _DONG_TOKEN_RE,
    _GU_TOKEN_RE,
    _addr_gu,
    _dong_prefix_forms,
    _strip_dong_prefix,
    count_households,
    _identity_fail_reason,
    verify_parcel_identity,
)

# ── 설정 ──────────────────────────────────────────────────────────────────────
EX = Path("examples")


def latest_universe_path() -> Path:
    """최신 candidates_universe 파일 — 호출 시점에 해소한다. import 시점에 [-1] 로 풀면 데이터 파일이 없는
    CI(examples/*.json 미커밋)에서 테스트 수집 자체가 IndexError 로 중단된다(2026-09-05 CI 실측)."""
    files = sorted(EX.glob("candidates_universe[0-9][0-9][0-9]_*.json"))
    if not files:
        raise SystemExit("examples/candidates_universe*.json 없음 — enumerate_25gu.py 산출물이 필요하다")
    return files[-1]


def latest_molit_path() -> Path:
    """최신 MOLIT 실거래 파일 — latest_universe_path 와 같은 이유로 호출 시점 해소."""
    files = sorted(EX.glob("molit_recent*.json"))
    if not files:
        raise SystemExit("examples/molit_recent*.json 없음 — fetch_molit_recent_25gu.py 산출물이 필요하다")
    return files[-1]
VWORLD_EP = "https://api.vworld.kr/ned/data/getApartHousingPriceAttr"
STDR_YEAR = "2026"          # 당해 공시(1/1 기준, 4월 말 공시) — 라이브 존재 확인(2026-07-09)
AREA_TOL  = 3.5             # 동일평형 톨러런스(㎡) — build_explorer._match_records 와 동일
RATIO_LO, RATIO_HI = 0.20, 0.90   # 공시가/실거래 중위 타당성 범위(공시가율 통상 40~70%)
SLEEP_SEC = 0.15

# 끝 대시 허용("324-" = 부번 없음) — K-apt kaptAddr 실측 5건 (2026-07-09 1차 배치)
_JIBUN_RE = re.compile(r"^(산)?(\d+)(?:-(\d*))?$")


def _pnu_from_basis(bjd: str, kapt_addr: str) -> str | None:
    """basis V4 의 bjdCode + kaptAddr 지번 → 19자리 pnu. 파싱 실패 None."""
    if not bjd or len(bjd) != 10:
        return None
    for tok in kapt_addr.split():
        m = _JIBUN_RE.match(tok)
        if m:
            san, bon, bu = m.groups()
            return f"{bjd}{'2' if san else '1'}{int(bon):04d}{int(bu or 0):04d}"
    return None


def _fetch_vworld_all(pnu: str, key: str) -> list[dict]:
    """VWorld 공시가 속성 전 페이지 수집. 오류/무자료는 빈 리스트."""
    out: list[dict] = []
    page = 1
    while True:
        qs = urllib.parse.urlencode({"key": key, "pnu": pnu, "stdrYear": STDR_YEAR,
                                     "format": "json", "numOfRows": 1000, "pageNo": page})
        try:
            with urllib.request.urlopen(f"{VWORLD_EP}?{qs}", timeout=30) as r:
                body = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"  [VWorld오류] pnu={pnu} p{page}: {str(e).replace(key, '***KEY***')}")
            return out
        top = body.get("apartHousingPrices") or {}
        out.extend(top.get("field") or [])
        total = int(top.get("totalCount") or 0)
        if page * 1000 >= total or not top.get("field"):
            return out
        page += 1
        time.sleep(SLEEP_SEC)


def _molit_median_won(district: str, complex_name: str, area: float, molit: dict) -> int | None:
    """동일평형·이름매칭 12개월 실거래 중위(원) — build_explorer._match_records/_median_of 와 동일 규칙
    (canonical 완전일치 + 명확할 때만 번호블록 collapse, 2026-09-05 수정 — 구 부분일치/4자 prefix 폐지)."""
    gu = next((g for g in GU_LAWD if g in district), None)
    if not gu:
        return None
    recs = molit.get(GU_LAWD[gu], [])
    matched = match_molit_names(complex_name, (r.get("apt", "") for r in recs))
    px = [r["price"] for r in recs
          if r.get("price") and abs(r["area"] - area) <= AREA_TOL and r.get("apt") in matched]
    if len(px) < 2:
        return None
    m0 = statistics.median(px)
    px = [p for p in px if p >= m0 * 0.6]
    return int(statistics.median(px)) if px else None


def main() -> None:
    vkey = os.environ.get("VWORLD_API_KEY", "")
    mkey = os.environ.get("MOLIT_API_KEY", "")
    if not vkey or not mkey:
        raise SystemExit("VWORLD_API_KEY / MOLIT_API_KEY 미설정 (.env)")

    universe_path = latest_universe_path()
    molit_path = latest_molit_path()
    universe: list[dict] = json.load(open(universe_path, encoding="utf-8"))
    molit: dict = json.load(open(molit_path, encoding="utf-8"))
    print(f"universe: {universe_path.name} ({len(universe)}개) · molit: {molit_path.name} · 기준연도 {STDR_YEAR}\n")

    basis_cache: dict[str, dict] = {}     # kapt_code → basis raw item
    rec_cache: dict[str, list[dict]] = {} # pnu → VWorld 호별 레코드
    cnt_ok = cnt_no_code = cnt_no_rec = cnt_gate = cnt_guard = cnt_no_area = 0

    for c in universe:
        name = c.get("complex_name", "")
        area = c.get("area_exclusive_m2") or 0.0
        kapt_code = c.get("kapt_code") if c.get("kapt_verified") else None
        if not kapt_code or not area:
            c["gongsi_man"] = None
            cnt_no_code += 1
            continue

        if kapt_code not in basis_cache:
            b = _get_json_item(BASIS_EP_V5, {"kaptCode": kapt_code}, mkey)
            if not b:   # 일시적 API 실패 재시도 1회 (2026-07-09 2차 배치: 메트로디오빌 실측)
                time.sleep(2.0)
                b = _get_json_item(BASIS_EP_V5, {"kaptCode": kapt_code}, mkey)
            basis_cache[kapt_code] = b
            time.sleep(SLEEP_SEC)
        b = basis_cache[kapt_code]
        pnu = _pnu_from_basis(str(b.get("bjdCode") or ""), str(b.get("kaptAddr") or ""))
        if not pnu:
            c["gongsi_man"] = None
            cnt_no_rec += 1
            print(f"  [pnu실패] {name}: bjd={b.get('bjdCode')} addr={b.get('kaptAddr')}")
            continue

        if pnu not in rec_cache:
            rec_cache[pnu] = _fetch_vworld_all(pnu, vkey)
            time.sleep(SLEEP_SEC)
        recs = rec_cache[pnu]
        if not recs:
            c["gongsi_man"] = None
            cnt_no_rec += 1
            print(f"  [무자료] {name}: pnu={pnu}")
            continue

        units_c = c.get("units") or 0
        aphus_nm = recs[0].get("aphusNm", "")
        kapt_name = str(b.get("kaptName") or "")
        if not verify_parcel_identity(aphus_nm, kapt_name, count_households(recs), units_c, kapt_addr=str(b.get("kaptAddr") or ""), frame_gu=str(c.get("district") or c.get("gu") or "")):
            c["gongsi_man"] = None
            cnt_gate += 1
            reason = _identity_fail_reason(aphus_nm, kapt_name, count_households(recs), units_c, kapt_addr=str(b.get("kaptAddr") or ""), frame_gu=str(c.get("district") or c.get("gu") or ""))
            print(f"  [이름게이트:{reason}] {name}: aphusNm={aphus_nm} ≠ kaptName={kapt_name} "
                  f"(pnu={pnu}, households={count_households(recs)}, units={units_c})")
            continue

        prices = []
        for r in recs:
            try:
                if abs(float(r.get("prvuseAr") or 0) - area) <= AREA_TOL:
                    prices.append(int(r["pblntfPc"]))
            except (TypeError, ValueError):
                continue
        if not prices:
            c["gongsi_man"] = None
            cnt_no_area += 1
            print(f"  [평형무매칭] {name} {area}㎡: 보유평형 {sorted({r.get('prvuseAr') for r in recs})[:8]}")
            continue

        gongsi_won = int(statistics.median(prices))
        molit_med = _molit_median_won(c.get("district", ""), name, area, molit)
        if molit_med is not None:
            ratio = gongsi_won / molit_med
            if not (RATIO_LO <= ratio <= RATIO_HI):
                c["gongsi_man"] = None
                cnt_guard += 1
                print(f"  [타당성가드] {name} {area}㎡: 공시 {gongsi_won//10**8}.{gongsi_won%10**8//10**7}억 "
                      f"/ 실거래중위 {molit_med//10**8}.{molit_med%10**8//10**7}억 = {ratio:.2f} (범위밖)")
                continue
        else:
            print(f"  [가드불가] {name} {area}㎡: 실거래 표본 부족 — 공시가 유지(이름게이트 통과분)")

        c["gongsi_man"] = round(gongsi_won / 10000)
        cnt_ok += 1
        print(f"  [OK] {name} {area}㎡: {c['gongsi_man']:,}만 (n={len(prices)}"
              f"{', 공시가율 ' + format(gongsi_won / molit_med, '.2f') if molit_med else ''})")

    json.dump(universe, open(universe_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ {universe_path} 갱신")
    print(f"  OK:{cnt_ok}  미검증코드:{cnt_no_code}  pnu/무자료:{cnt_no_rec}  "
          f"이름게이트:{cnt_gate}  평형무매칭:{cnt_no_area}  타당성가드:{cnt_guard}")


if __name__ == "__main__":
    main()
