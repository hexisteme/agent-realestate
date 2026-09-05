"""collect_universe_enrich.py — universe JSON 에 K-apt V4 4필드 + 인근초등 백필.

배경(2026-07-07): domain/build_explorer 에 heating·corridor_type·parking_per_unit·builder·
nearest_elem_school 필드가 2026-06~07 에 추가됐으나 universe 에 키를 써주는 배치가 없어
발행본이 전량 "—" 였다(0/117). 이 스크립트가 그 write 측을 채운다.

사용:
    python3 collect_universe_enrich.py            # .env 의 MOLIT_API_KEY·KAKAO_REST_KEY 사용

동작:
    1. kaptCode 해소: 오프라인 kapt_master_10gu(1차) → K-apt live 목록(fallback, 양천 등).
    2. K-apt 기본정보 V4(fetch_basis, basis+detail 2콜) → 난방·복도·세대당주차·시공사.
    3. KAKAO geocode+SC4 → 최근접 초등학교 "이름(거리m)".
    4. 신규 파일 candidates_universe159_<오늘>.json 저장 — run_daily 의 _latest_or glob 이
       자동 픽업, 원본(20260606)은 백업으로 보존(MOLIT 캐시 데이터손실 사고의 교훈).

maint_fee_won(15057937)·gongsi_man 은 data.go.kr 활용신청 미승인(2026-07-07 라이브 확인)이라
본 배치에서 제외 — 승인 후 collect_kapt_maint_fees.py / gongsi 배치 별도 실행.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from agent_realestate import config
config.load_env_file()

import os
from agent_realestate.collectors.kapt import LIST_EP, fetch_basis, parse_apt_list
from agent_realestate.collectors.kakao import geocode_keyword, nearest_schools
from agent_realestate.collectors.lawd import lawd_for_district
import urllib.parse as up
import urllib.request

EX = Path("examples")
UNIVERSE = EX / "candidates_universe159_20260606.json"
MASTER = EX / "kapt_master_10gu_20260603.json"
OUT = EX / f"candidates_universe159_{date.today().strftime('%Y%m%d')}.json"
SLEEP_SEC = 0.15


def _gu_from_district(district: str) -> str:
    """'서울 강서구' → '강서' (kapt_master 키 형식)."""
    parts = district.strip().split()
    last = parts[-1] if parts else district
    return last[:-1] if last.endswith("구") else last


def _build_lookup(master: dict) -> dict[str, list[dict]]:
    return {
        gu: [{"kaptCode": it["kaptCode"], "name_norm": it["name"].replace(" ", "")}
             for it in items]
        for gu, items in master.items()
    }


def _offline_candidates(name: str, district: str, lookup: dict) -> list[str]:
    """구내 substring 매칭 후보 '전체' — 첫-매치 채택 금지(오매칭 5건 실측, 2026-07-07 리뷰)."""
    norm = name.replace(" ", "")
    return [it["kaptCode"] for it in lookup.get(_gu_from_district(district), [])
            if norm in it["name_norm"] or it["name_norm"] in norm]


_LIVE_CACHE: dict[str, list[dict]] = {}


def _live_candidates(name: str, district: str, key: str) -> list[str]:
    """K-apt live 목록의 substring 후보 전체 — 구 단위 1회 캐시."""
    sgg = lawd_for_district(district)
    if not sgg:
        return []
    if sgg not in _LIVE_CACHE:
        try:
            url = (f"{LIST_EP}?sigunguCode={sgg}&numOfRows=5000&pageNo=1"
                   f"&serviceKey={up.quote(key)}")
            with urllib.request.urlopen(url, timeout=20) as r:
                body = r.read().decode("utf-8")
            # AptListService3 도 JSON 이 기본이 됨(2026-07-07 실측) — JSON 우선, XML 폴백
            try:
                items = json.loads(body)["response"]["body"]["items"] or []
                _LIVE_CACHE[sgg] = [{"kaptCode": i.get("kaptCode", ""), "kaptName": i.get("kaptName", "")}
                                    for i in items if i.get("kaptCode")]
            except (ValueError, KeyError, TypeError):
                _LIVE_CACHE[sgg] = parse_apt_list(body)
            time.sleep(SLEEP_SEC)
        except Exception as e:
            print(f"  [live 목록 실패] {district}: {e}")
            _LIVE_CACHE[sgg] = []
    norm = name.replace(" ", "")
    return [a["kaptCode"] for a in _LIVE_CACHE[sgg]
            if norm in a["kaptName"].replace(" ", "") or a["kaptName"].replace(" ", "") in norm]


def _resolve_kapt_basis(name: str, district: str, units: int, built_year: int,
                        lookup: dict, key: str) -> tuple[str, dict] | None:
    """substring 후보 전체를 세대수·준공연도로 교차검증해 (kaptCode, basis) 채택.

    첫-매치 채택이 만든 오매칭(노원 '두산'→녹천역두산위브 등 5건, 타 단지의 난방·시공사가
    실명 발행됨 — 2026-07-07 적대리뷰 critical) 재발 방지. 검증 통과 후보가 없거나
    강한 일치(실측 세대수 또는 준공 중 최소 1개 합치)가 없으면 None — 확인된 사실만 원칙."""
    cands = _offline_candidates(name, district, lookup) or _live_candidates(name, district, key)
    verified: list[tuple[int, str, dict]] = []
    for code in cands:
        b = fetch_basis(code, key)
        time.sleep(SLEEP_SEC)
        if not b:
            continue
        u_tol = max(3, int(units * 0.15)) if units else 0
        u_known = bool(units and b["units"])
        y_known = bool(built_year and b["built_year"])
        u_ok = (not u_known) or abs(b["units"] - units) <= u_tol
        y_ok = (not y_known) or abs(b["built_year"] - built_year) <= 2
        strong = (u_known and u_ok) or (y_known and y_ok)
        if u_ok and y_ok and strong:
            verified.append((abs(b["units"] - units) if u_known else 10**6, code, b))
    if not verified:
        return None
    _, code, b = min(verified)
    return code, b


def main() -> None:
    molit_key = os.environ.get("MOLIT_API_KEY", "")
    kakao_key = os.environ.get("KAKAO_REST_KEY", "")
    if not molit_key:
        raise SystemExit("MOLIT_API_KEY 미설정 (.env)")

    universe: list[dict] = json.load(open(UNIVERSE, encoding="utf-8"))
    lookup = _build_lookup(json.load(open(MASTER, encoding="utf-8")))
    print(f"universe {len(universe)}개 → K-apt V4 + KAKAO SC4 백필 시작\n")

    # 재실행(resume): 이전 산출물이 있으면 이어서 — 이미 채워진 레코드는 건너뜀
    if OUT.exists():
        universe = json.load(open(OUT, encoding="utf-8"))
        print(f"(resume) {OUT.name} 에서 이어서 실행")

    n_kapt = n_elem = n_nocode = 0
    for i, c in enumerate(universe, 1):
        name, district = c.get("complex_name", ""), c.get("district", "")
        # '[주상복합]' 류 대괄호 접미사는 K-apt/카카오 검색어에서 제거
        qname = name.split("[")[0].strip()

        # ── K-apt V4: 난방·복도·세대당주차·시공사 (세대수·준공 교차검증 매칭) ──
        if c.get("kapt_verified"):
            n_kapt += 1
        else:
            # 구 first-match 산출물 무효화(오매칭 5건 실측) — 재검증 전 기존 값 제거
            for k in ("heating", "corridor_type", "parking_per_unit", "builder", "kapt_code"):
                c.pop(k, None)
            hit = _resolve_kapt_basis(qname, district, c.get("units") or 0,
                                      c.get("built_year") or 0, lookup, molit_key)
            if hit:
                code, b = hit
                for k in ("heating", "corridor_type", "parking_per_unit", "builder"):
                    if b.get(k) is not None:
                        c[k] = b[k]
                c["kapt_code"] = code
                c["kapt_verified"] = True   # 세대수·준공 교차검증 통과 마커
                n_kapt += 1
            else:
                n_nocode += 1
                print(f"  [미채택] {name} ({district}) — 후보 검증 실패/없음")

        # ── KAKAO SC4: 최근접 초등학교 ──
        if c.get("nearest_elem_school"):
            n_elem += 1
        elif kakao_key:
            geo = geocode_keyword(qname, kakao_key, district)
            time.sleep(SLEEP_SEC)
            if geo:
                sch = nearest_schools(geo[0], geo[1], kakao_key).get("초등학교")
                time.sleep(SLEEP_SEC)
                if sch:
                    c["nearest_elem_school"] = f'{sch["name"]}({sch["distance_m"]}m)'
                    n_elem += 1

        if i % 20 == 0:
            print(f"  진행 {i}/{len(universe)} (kapt {n_kapt} · 초등 {n_elem})")

    json.dump(universe, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ {OUT} 저장 (원본 {UNIVERSE.name} 보존)")
    print(f"  kapt 4필드: {n_kapt}/{len(universe)} · 인근초등: {n_elem}/{len(universe)} · 코드없음: {n_nocode}")


if __name__ == "__main__":
    main()
