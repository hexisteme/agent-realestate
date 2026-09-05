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

from agent_realestate.collectors.kapt import BASIS_EP_V4, _get_json_item
from blog.build_explorer import GU_LAWD, canonical_complex_name, match_molit_names

# ── 설정 ──────────────────────────────────────────────────────────────────────
EX = Path("examples")
UNIVERSE = sorted(EX.glob("candidates_universe[0-9][0-9][0-9]_*.json"))[-1]
MOLIT    = sorted(EX.glob("molit_recent*.json"))[-1]
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


def _identity_norm(nm: str) -> str:
    """단지 신원(identity) 검증 전용 느슨한 정규화 — build_explorer.canonical_complex_name(대괄호·비식별
    괄호·브랜드표기[IPARK/아이파크·e편한세상/이편한세상·SK뷰 계열·자이/XI] 을 이미 접는다) 위에 이 모듈
    고유 확장 2종을 더한다: 'N차'→'N'(전위치, 하계1차청구↔하계1청구) · '주상' 삭제(삼창타워프라자↔
    삼창타워주상프라자). match_molit_names(발행 경로)에는 쓰지 않는다 — 전위치 N차 collapse 는
    상계주공1~16단지 뭉침 재발 위험이라 발행 경로엔 부적합하고 이 모듈의 완화된 신원확인(§verify_
    parcel_identity)에서만 쓴다. 숫자 자체는 보존 — 단지 번호 차이는 여전히 불일치."""
    c = canonical_complex_name(nm) or ""
    c = c.replace("주상", "")
    return re.sub(r"(\d)차", r"\1", c)


def _identity_fail_reason(aphus_nm: str, kapt_name: str, record_count: int, units: int) -> str | None:
    """verify_parcel_identity 판정의 실패 사유(로그 구분용) — 통과면 None, 아니면
    'name-mismatch'|'count-mismatch'. 로직은 verify_parcel_identity 와 단일 소스(이 함수에 위임)."""
    a = _identity_norm(aphus_nm)
    k = _identity_norm(kapt_name)
    if not a or not k:
        return "name-mismatch"
    if a == k:
        return None
    shorter, longer = (a, k) if len(a) <= len(k) else (k, a)
    if len(shorter) < 5 or shorter not in longer:
        return "name-mismatch"
    if units <= 0 or abs(record_count - units) > units * 0.25:
        return "count-mismatch"
    return None


def verify_parcel_identity(aphus_nm: str, kapt_name: str, record_count: int, units: int) -> bool:
    """VWorld 공시가 레코드(aphus_nm)가 실제로 이 K-apt 단지(kapt_name)의 것인지 검증 — 타 단지
    pnu 오조립 방어(2026-09-05 수정, 구 _name_gate 대체). ① _identity_norm 정규화 후 완전일치면 통과.
    ② 완전일치가 아니면 단방향 포함(containment)을 딱 하나의 조건에서만 허용 — 포함되는(짧은) 쪽
    정규화 이름이 5자 이상 AND units>0 AND VWorld 레코드 수(호수, 전 페이지 합)가 K-apt 세대수(units)
    와 25% 이내로 일치할 때만(세대수가 다른 단지끼리는 이 경로로도 통과 불가). 그 외 전부 실패.
    실패 사유(name-mismatch/count-mismatch)는 _identity_fail_reason 으로 별도 조회해 로그에 남긴다."""
    return _identity_fail_reason(aphus_nm, kapt_name, record_count, units) is None


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

    universe: list[dict] = json.load(open(UNIVERSE, encoding="utf-8"))
    molit: dict = json.load(open(MOLIT, encoding="utf-8"))
    print(f"universe: {UNIVERSE.name} ({len(universe)}개) · molit: {MOLIT.name} · 기준연도 {STDR_YEAR}\n")

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
            b = _get_json_item(BASIS_EP_V4, {"kaptCode": kapt_code}, mkey)
            if not b:   # 일시적 API 실패 재시도 1회 (2026-07-09 2차 배치: 메트로디오빌 실측)
                time.sleep(2.0)
                b = _get_json_item(BASIS_EP_V4, {"kaptCode": kapt_code}, mkey)
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
        if not verify_parcel_identity(aphus_nm, kapt_name, len(recs), units_c):
            c["gongsi_man"] = None
            cnt_gate += 1
            reason = _identity_fail_reason(aphus_nm, kapt_name, len(recs), units_c)
            print(f"  [이름게이트:{reason}] {name}: aphusNm={aphus_nm} ≠ kaptName={kapt_name} "
                  f"(pnu={pnu}, records={len(recs)}, units={units_c})")
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

    json.dump(universe, open(UNIVERSE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ {UNIVERSE} 갱신")
    print(f"  OK:{cnt_ok}  미검증코드:{cnt_no_code}  pnu/무자료:{cnt_no_rec}  "
          f"이름게이트:{cnt_gate}  평형무매칭:{cnt_no_area}  타당성가드:{cnt_guard}")


if __name__ == "__main__":
    main()
