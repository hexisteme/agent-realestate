"""collect_public_enrich.py — public 유입 신규 단지 overlay enrichment (complexNo 키).

배경(2026-07-10): blog/build_explorer.build_dataset_public 의 (B) 공공 유입 파트가 만든 신규 단지는
kapt_code 가 없어 heating·corridor_type·parking_per_unit·builder·nearest_elem_school·academy_exam·
gongsi_man·maint_fee_won 이 전량 None(호가/enrichment 배선 없음). 이 배치가 그 단지들만 골라
(= 기존 universe complex_no 에 없는 행) 아래를 채운 overlay JSON 을 만든다:

    K-apt V4 교차검증(난방·복도·세대당주차·시공사) + VWorld 공시가 + 관리비 + 카카오 초등/입시학원.

기존 배치 3종의 로직을 그대로 재사용(import) — 새로 설계하지 않는다:
    · collect_universe_enrich._resolve_kapt_basis  — 세대수(±15%)·준공(±2년) 교차검증 + 강한일치 요구
      (이름 substring 첫-매치 오매칭 5건 실측[2026-07-07] 재발 방지).
    · collect_gongsi.*                             — bjdCode+kaptAddr→pnu 조립, 이름게이트, 타당성가드.
    · collect_kapt_maint_fees.*                    — 최근 3개월 평균, 세대당 1만원 미만 부분응답 가드.

확인된 사실만(RDU-061) — 검증 실패/무자료는 해당 키 생략. kapt_verified 는 항상 bool 로 기록해
resume 스킵 신호로 쓴다(값 없어도 '처리됨' 표식). 기존 파일(collect_*.py 등)은 수정하지 않는다.

사용:
    python3 collect_public_enrich.py     # .env 의 MOLIT_API_KEY·KAKAO_REST_KEY·VWORLD_API_KEY 사용
"""
from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

from agent_realestate import config
config.load_env_file()

import blog.build_explorer as be
from collect_universe_enrich import _resolve_kapt_basis, _live_candidates
from collect_gongsi import (
    _pnu_from_basis, _fetch_vworld_all, verify_parcel_identity, _identity_fail_reason, _molit_median_won,
    AREA_TOL, RATIO_LO, RATIO_HI,
)
from collect_kapt_maint_fees import _avg_fee_total, _recent_ym_list, MONTHS_BACK
from agent_realestate.collectors.kapt import BASIS_EP_V5, _get_json_item
from agent_realestate.collectors.kakao import nearest_schools, academy_exam_count

# ── 설정 ──────────────────────────────────────────────────────────────────────
EX        = Path("examples")
FRAME     = EX / "frame_25gu_20260710.json"
MOLIT     = EX / "molit_recent_25gu_20260710.json"
SURVIVORS = EX / "screen_25gu_survivors_20260710.json"
ANCHOR    = EX / "candidates_universe159_20260707.json"
OUT       = EX / "enrich_overlay_25gu_20260710.json"
ASOF, TODAY = "2026-07-09", "2026-07-10"
SLEEP      = 0.18          # 자체 top-level 호출(카카오·raw basis) 간격
SAVE_EVERY = 20            # 중간저장 주기(장시간 실행 중단 대비)


def _safe(fn, *a, **k):
    """카카오 호출 안전 래퍼 — kakao._req 는 실패 시 SystemExit 을 raise 하므로(배치 전체 중단 위험)
    SystemExit/Exception 을 잡아 1회 재시도(2초) 후 None. (task 4: 실패 시 1회 재시도 후 포기)."""
    last: BaseException | None = None
    for _ in (1, 2):
        try:
            return fn(*a, **k)
        except SystemExit as e:
            last = e
        except Exception as e:
            last = e
        time.sleep(2.0)
    print(f"  [kakao실패] {getattr(fn, '__name__', fn)}: {last}")
    return None


def _district_of(gu: str) -> str:
    """frame gu stem → 'district' 문자열(기존 universe 스키마 동일). '중구'는 이미 구 접미라 중복 방지
    ('서울 중구'), 그 외 2자 stem 은 '서울 강남구'. lawd_for_district 가 부분매칭으로 둘 다 해결."""
    return f"서울 {gu}" if gu.endswith("구") else f"서울 {gu}구"


def derive_public_targets() -> list[dict]:
    """build_dataset_public 결과 중 기존 universe complex_no 에 *없는* 행(= 공공 유입 신규)만 대상.
    complex_no·name·gu·units·built_year·area_m2 는 dataset 행에서, lat/lng 는 frame 원본에서 조인."""
    ds = be.build_dataset_public(str(FRAME), str(MOLIT), ASOF, TODAY,
                                 survivors_path=str(SURVIVORS), anchor_universe=str(ANCHOR))
    uni = json.load(open(ANCHOR, encoding="utf-8"))
    uni_cno = {str(d["complex_no"]) for d in uni if d.get("complex_no")}

    frame = json.load(open(FRAME, encoding="utf-8"))
    frame_by_cno: dict[str, dict] = {}
    for r in frame:                                   # lat/lng 은 물리단지(complexNo) 속성 — 첫 행 채택
        frame_by_cno.setdefault(str(r["complexNo"]), r)

    targets: list[dict] = []
    for row in ds["complexes"]:
        cno = str(row.get("complex_no") or "")
        if not cno or cno in uni_cno:                 # base_overlap(기존 발행)·무cno 제외
            continue
        fr = frame_by_cno.get(cno) or {}
        targets.append({
            "complex_no": cno,
            "name": row["name"],
            "gu": row["gu"],
            "units": row.get("units") or 0,
            "built_year": row.get("built_year") or 0,
            "area_m2": row.get("area_m2") or 0.0,
            "lat": fr.get("lat"),
            "lng": fr.get("lng"),
        })
    return targets


def build_gu_ipsi_map() -> dict[str, int]:
    """구 입시학원 수(gu_ipsi_academy) — 자치구 단위 확인된 사실(서울 OpenAPI, universe 에 기수집).
    anchor universe 의 district→gu_ipsi_academy 를 frame gu stem 으로 재키잉. 값 있는 구만(11개);
    신규 14구는 소스 미보유라 미기재(지어내지 않음)."""
    m: dict[str, int] = {}
    for d in json.load(open(ANCHOR, encoding="utf-8")):
        v = d.get("gu_ipsi_academy")
        dist = d.get("district") or ""
        if v is None or not dist:
            continue
        gu = dist.replace("서울", "").strip()
        if gu.endswith("구") and len(gu) > 2:         # '강서구'→'강서', '중구'는 유지
            gu = gu[:-1]
        m.setdefault(gu, int(v))
    return m


def _gongsi_man(kapt_code: str, name: str, district: str, area: float, units: int, molit: dict,
                vworld_key: str, molit_key: str,
                raw_cache: dict[str, dict], vworld_cache: dict[str, list]) -> int | None:
    """collect_gongsi.main 과 동일 파이프라인 — raw basis(bjdCode+kaptAddr)→pnu→VWorld 전페이지→
    신원게이트(verify_parcel_identity, 2026-09-05 수정 — 구 _name_gate 상호포함 대체)→
    동일평형(±AREA_TOL) 중위(원)→만원, 타당성가드(공시/실거래중위 0.20~0.90)."""
    if not area:
        return None
    if kapt_code not in raw_cache:
        b = _get_json_item(BASIS_EP_V5, {"kaptCode": kapt_code}, molit_key)
        if not b:                                     # 일시적 API 실패 재시도 1회(collect_gongsi 동일)
            time.sleep(2.0)
            b = _get_json_item(BASIS_EP_V5, {"kaptCode": kapt_code}, molit_key)
        raw_cache[kapt_code] = b
        time.sleep(SLEEP)
    b = raw_cache[kapt_code]
    if not b:
        return None
    pnu = _pnu_from_basis(str(b.get("bjdCode") or ""), str(b.get("kaptAddr") or ""))
    if not pnu:
        return None
    if pnu not in vworld_cache:
        vworld_cache[pnu] = _fetch_vworld_all(pnu, vworld_key)
        time.sleep(SLEEP)
    recs = vworld_cache[pnu]
    if not recs:
        return None
    aphus_nm = recs[0].get("aphusNm", "")
    kapt_name = str(b.get("kaptName") or "")
    if not verify_parcel_identity(aphus_nm, kapt_name, len(recs), units):
        reason = _identity_fail_reason(aphus_nm, kapt_name, len(recs), units)
        print(f"  [이름게이트:{reason}] {name}: aphusNm={aphus_nm} ≠ kaptName={kapt_name} "
              f"(pnu={pnu}, records={len(recs)}, units={units})")
        return None
    prices: list[int] = []
    for r in recs:
        try:
            if abs(float(r.get("prvuseAr") or 0) - area) <= AREA_TOL:
                prices.append(int(r["pblntfPc"]))
        except (TypeError, ValueError):
            continue
    if not prices:
        return None
    gongsi_won = int(statistics.median(prices))
    molit_med = _molit_median_won(district, name, area, molit)
    if molit_med is not None:
        ratio = gongsi_won / molit_med
        if not (RATIO_LO <= ratio <= RATIO_HI):       # 타당성가드 — 범위 밖 = 오매칭/단위오류
            return None
    return round(gongsi_won / 10000)


def enrich_complex(t: dict, molit: dict, gu_ipsi_map: dict[str, int],
                   molit_key: str, kakao_key: str, vworld_key: str,
                   raw_cache: dict, vworld_cache: dict) -> tuple[dict, bool]:
    """단지 1개 → overlay entry(확인된 필드만) + '후보는 있었으나 교차검증 실패' 여부."""
    name, gu = t["name"], t["gu"]
    district = _district_of(gu)
    units = t["units"] or 0
    built_year = t["built_year"] or 0
    area = t["area_m2"] or 0.0
    qname = name.split("[")[0].strip()
    entry: dict = {}
    kapt_rejected = False

    # ── (a) K-apt V4 교차검증(난방·복도·세대당주차·시공사) ──
    kapt_code = None
    hit = _resolve_kapt_basis(qname, district, units, built_year, {}, molit_key)
    if hit:
        kapt_code, b = hit
        for k in ("heating", "corridor_type", "parking_per_unit", "builder"):
            if b.get(k) is not None:
                entry[k] = b[k]
        entry["kapt_code"] = kapt_code
        entry["kapt_verified"] = True
    else:
        entry["kapt_verified"] = False
        # 이름 substring 후보는 있었는데 세대수·준공 교차검증에서 탈락했는지(리포트용, 캐시라 무비용)
        if _live_candidates(qname, district, molit_key):
            kapt_rejected = True

    # ── (b) 공시가(VWorld) — kapt_code 확정 시만 ──
    if kapt_code:
        gm = _gongsi_man(kapt_code, name, district, area, units, molit,
                         vworld_key, molit_key, raw_cache, vworld_cache)
        if gm is not None:
            entry["gongsi_man"] = gm

    # ── (c) 관리비 — kapt_code 확정 + 세대수 보유 시만 ──
    if kapt_code and units > 0:
        total = _avg_fee_total(kapt_code, molit_key, _recent_ym_list(MONTHS_BACK))
        if total is not None:
            per_hh = total // units
            if per_hh >= 10_000:                      # 세대당 1만원 미만 = 부분응답 의심 → 미기재
                entry["maint_fee_won"] = per_hh

    # ── (d) 카카오: 인근 초등학교 + 입시학원 수(좌표 직접 사용, geocode 생략) ──
    if kakao_key and t.get("lat") is not None and t.get("lng") is not None:
        x, y = str(t["lng"]), str(t["lat"])           # 카테고리 API: x=경도, y=위도
        sch = _safe(nearest_schools, x, y, kakao_key)
        time.sleep(SLEEP)
        if sch:
            elem = sch.get("초등학교")
            if elem:
                entry["nearest_elem_school"] = f'{elem["name"]}({elem["distance_m"]}m)'
        ax = _safe(academy_exam_count, x, y, kakao_key)   # 입시학원(800m) → academy_exam
        time.sleep(SLEEP)
        if ax is not None:
            entry["academy_exam"] = ax

    # ── 구 입시학원 수(gu_ipsi_academy) — 자치구 단위 확인된 사실 재사용(값 있는 구만) ──
    if gu in gu_ipsi_map:
        entry["gu_ipsi_academy"] = gu_ipsi_map[gu]

    return entry, kapt_rejected


def main() -> None:
    molit_key = os.environ.get("MOLIT_API_KEY", "")
    kakao_key = os.environ.get("KAKAO_REST_KEY", "")
    vworld_key = os.environ.get("VWORLD_API_KEY", "")
    if not molit_key:
        raise SystemExit("MOLIT_API_KEY 미설정 (.env)")
    if not vworld_key:
        print("⚠ VWORLD_API_KEY 미설정 — 공시가 전량 건너뜀")
    if not kakao_key:
        print("⚠ KAKAO_REST_KEY 미설정 — 초등/학원 전량 건너뜀")

    targets = derive_public_targets()
    molit = json.load(open(MOLIT, encoding="utf-8"))
    gu_ipsi_map = build_gu_ipsi_map()
    total = len(targets)
    print(f"public 유입 신규 단지 {total}개 → overlay enrich 시작 "
          f"(구 입시학원 map {len(gu_ipsi_map)}구)\n")

    overlay: dict[str, dict] = {}
    if OUT.exists():
        overlay = json.load(open(OUT, encoding="utf-8"))
        print(f"(resume) {OUT.name} 에서 {len(overlay)}개 로드 — 이미 있는 complexNo 스킵\n")

    raw_cache: dict[str, dict] = {}
    vworld_cache: dict[str, list] = {}

    def _n(pred) -> int:
        return sum(1 for e in overlay.values() if pred(e))

    n_kapt   = _n(lambda e: e.get("kapt_verified"))
    n_gongsi = _n(lambda e: e.get("gongsi_man") is not None)
    n_maint  = _n(lambda e: e.get("maint_fee_won") is not None)
    n_elem   = _n(lambda e: e.get("nearest_elem_school"))
    n_acad   = _n(lambda e: e.get("academy_exam") is not None)
    n_reject = 0

    for i, t in enumerate(targets, 1):
        cno = t["complex_no"]
        if cno in overlay:
            continue
        entry, rejected = enrich_complex(t, molit, gu_ipsi_map,
                                         molit_key, kakao_key, vworld_key, raw_cache, vworld_cache)
        overlay[cno] = entry
        if entry.get("kapt_verified"):       n_kapt += 1
        if entry.get("gongsi_man") is not None:      n_gongsi += 1
        if entry.get("maint_fee_won") is not None:   n_maint += 1
        if entry.get("nearest_elem_school"):         n_elem += 1
        if entry.get("academy_exam") is not None:    n_acad += 1
        if rejected:                                 n_reject += 1

        if len(overlay) % SAVE_EVERY == 0:
            json.dump(overlay, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if i % 20 == 0:
            print(f"  진행 {i}/{total} (kapt {n_kapt} · 공시가 {n_gongsi} · "
                  f"관리비 {n_maint} · 초등 {n_elem})")

    json.dump(overlay, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ {OUT} 저장 ({len(overlay)}개 항목)")
    print(f"  kapt_verified:{n_kapt}  공시가:{n_gongsi}  관리비:{n_maint}  "
          f"초등:{n_elem}  입시학원:{n_acad}  (교차검증탈락:{n_reject})")


if __name__ == "__main__":
    main()
