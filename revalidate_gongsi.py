"""revalidate_gongsi.py — 신원게이트(verify_parcel_identity, 2026-09-05 수정) 교체 후 이미 발행된
enrich overlay 의 gongsi_man 을 재검증한다(gongsi-gate-fix item 4).

배경: collect_gongsi.py/collect_public_enrich.py 의 이름게이트가 상호포함(_name_gate)에서
verify_parcel_identity(canonical 완전일치 또는 ≥5자 단방향포함+레코드수/세대수 일치)로 바뀌었다.
이미 examples/enrich_overlay_25gu_20260710.json 에 박제된 gongsi_man 값 중 새 게이트를 통과 못하는
것이 있는지 라이브로 재확인해 새 overlay 를 만든다. 다른 필드는 절대 건드리지 않는다 — 이 스크립트가
바꾸는 건 gongsi_man 하나뿐이고, 그것도 "신원게이트 실패"일 때만 None 으로 되돌린다(다른 사유
— API 일시장애·평형무매칭·타당성가드 등 — 로 None 이 나와도 원래 값을 유지한다. 이 재검증의
목적은 새 게이트 하나의 효과만 보는 것이지 전체 파이프라인 재실행이 아니다).

사용:
    python3 revalidate_gongsi.py                      # 전량, 40분 예산
    python3 revalidate_gongsi.py --budget-min 5        # 예산 축소(테스트용)
    python3 revalidate_gongsi.py --sample 60 --sample-min-gu 10   # 층화표본만(overlay 미기록)
    python3 revalidate_gongsi.py --universe --sample 40   # universe 모드 층화표본(파일 미기록)
    python3 revalidate_gongsi.py --universe               # universe 전량 재검증, in-place 갱신+백업

멱등·재개가능: --cache(raw basis·VWorld 페치 캐시)·--decisions(완료된 cno) 를 스크래치패드에 저장,
재실행 시 이미 처리된 cno 는 건너뛴다. rate-limited(SLEEP, 기존 배치와 동일 간격)."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

from agent_realestate import config
config.load_env_file()

from agent_realestate.collectors.kapt import BASIS_EP_V5, _get_json_item
import collect_gongsi
from collect_gongsi import (count_households,
    _pnu_from_basis, _fetch_vworld_all, _identity_fail_reason, _molit_median_won,
    AREA_TOL, RATIO_LO, RATIO_HI,
)
import collect_public_enrich as cpe

SCRATCH = Path(os.environ.get("RE_SCRATCH", str(Path.home() / ".cache" / "agent_realestate" / "revalidate")))   # basis·VWorld 원문 캐시(세션 무관 안정 경로, 2026-09-05)
SLEEP = 0.18   # collect_public_enrich.SLEEP 와 동일 간격


def _district_of(gu: str) -> str:
    return cpe._district_of(gu)


def _revalidate_one(kapt_code: str, name: str, district: str, area: float, units: int,
                    molit: dict, vworld_key: str, molit_key: str,
                    raw_cache: dict, vworld_cache: dict) -> tuple[bool, str, int | None]:
    """단일 단지 재검증 — collect_public_enrich._gongsi_man 과 동일 파이프라인이되 실패 사유를
    세분화해 반환한다: (kept_or_pass, reason, new_value).
    reason ∈ no-basis|no-pnu|no-vworld-records|identity:name-mismatch|identity:count-mismatch|
    identity-ok-no-area|no-area-match|ratio-guard|pass. gongsi_man 을 None 으로 되돌려야 하는 건
    identity:* 뿐 — 그 외 실패는 이 재검증의 관심사가 아니므로(구 값 유지) reason 만 기록하고
    kept=False 로 표시하지 않는다(호출측이 identity:* 만 보고 판단). identity-ok-no-area 는 신원게이트는
    통과했으나 area 가 없는 경우(frame 폴백 후보 — derive_frame_candidates 는 area=0.0 만 채운다)로,
    콜론 없는 이름이라 identity:* 집계에 잡히지 않고 구 값이 유지된다."""
    if not raw_cache.get(kapt_code):   # 빈 dict 는 '미조회'로 취급 — 폐기 API 시절 빈 응답이 캐시에 남아 V5 전환 뒤에도 no-basis 로 오판하던 결함(2026-09-05)
        b = _get_json_item(BASIS_EP_V5, {"kaptCode": kapt_code}, molit_key)
        if not b:
            time.sleep(2.0)
            b = _get_json_item(BASIS_EP_V5, {"kaptCode": kapt_code}, molit_key)
        if b:
            raw_cache[kapt_code] = b   # 성공 응답만 캐시(실패는 다음 실행에서 재시도)
        time.sleep(SLEEP)
    b = raw_cache.get(kapt_code) or {}
    if not b:
        return False, "no-basis", None
    pnu = _pnu_from_basis(str(b.get("bjdCode") or ""), str(b.get("kaptAddr") or ""))
    if not pnu:
        return False, "no-pnu", None
    if pnu not in vworld_cache:
        vworld_cache[pnu] = _fetch_vworld_all(pnu, vworld_key)
        time.sleep(SLEEP)
    recs = vworld_cache[pnu]
    if not recs:
        return False, "no-vworld-records", None
    aphus_nm = recs[0].get("aphusNm", "")
    kapt_name = str(b.get("kaptName") or "")
    reason = _identity_fail_reason(aphus_nm, kapt_name, count_households(recs), units,
                                   kapt_addr=str(b.get("kaptAddr") or ""), frame_gu=district)
    if reason is not None:
        return False, f"identity:{reason}", None
    if not area:
        return False, "identity-ok-no-area", None
    prices = []
    for r in recs:
        try:
            if abs(float(r.get("prvuseAr") or 0) - area) <= AREA_TOL:
                prices.append(int(r["pblntfPc"]))
        except (TypeError, ValueError):
            continue
    if not prices:
        return False, "no-area-match", None
    gongsi_won = int(statistics.median(prices))
    molit_med = _molit_median_won(district, name, area, molit)
    if molit_med is not None:
        ratio = gongsi_won / molit_med
        if not (RATIO_LO <= ratio <= RATIO_HI):
            return False, "ratio-guard", None
    return True, "pass", round(gongsi_won / 10000)


def _stratified_sample(candidates: list[dict], n: int, min_gu: int) -> list[dict]:
    """gu 라운드로빈으로 최소 min_gu 개 구를 포괄하는 결정론 표본 n개(부족하면 있는 만큼)."""
    by_gu: dict[str, list[dict]] = {}
    for c in candidates:
        by_gu.setdefault(c["gu"], []).append(c)
    for lst in by_gu.values():
        lst.sort(key=lambda c: c["complex_no"])
    gus = sorted(by_gu)
    out: list[dict] = []
    i = 0
    while len(out) < n and any(by_gu.values()):
        gu = gus[i % len(gus)]
        if by_gu[gu]:
            out.append(by_gu[gu].pop(0))
        i += 1
        if i > n * len(gus) + len(gus):   # 안전판(모든 gu 소진)
            break
    covered_gu = len({c["gu"] for c in out})
    if covered_gu < min_gu:
        print(f"⚠ 표본 gu 커버리지 {covered_gu} < 요청 {min_gu}(전체 후보의 gu 다양성 한계)")
    return out


RETRYABLE_REASONS = {"no-basis"}   # API 실패(폐기 엔드포인트·일시 장애) — 데이터 판정이 아니므로 재개 시 재시도


def load_resume_decisions(path: Path, sample_mode: bool) -> dict[str, dict]:
    """재개용 판정 로드(2026-09-05 결함 수정). 이전 실행이 폐기 API 로 전건 no-basis 를 남기면 재개 시
    '완료'로 건너뛰어 V5 전환 뒤에도 65/65 no-basis 를 그대로 보고하던 문제 — RETRYABLE_REASONS 는 제외한다.
    표본 모드는 프로브이므로 이전 판정으로 건너뛰지 않고(빈 dict) 기록도 하지 않는다."""
    if sample_mode or not path.exists():
        return {}
    loaded = json.load(open(path, encoding="utf-8"))
    return {k: v for k, v in loaded.items() if v.get("reason") not in RETRYABLE_REASONS}


def derive_universe_candidates(rows: list[dict]) -> list[dict]:
    """universe(candidates_universe*.json, list[dict]) 에서 gongsi_man 보유 행만 골라 overlay 모드와
    동일 shape 의 재검증 후보로 변환한다. complex_no 없는 행은 건너뛴다(대상 특정 불가)."""
    out: list[dict] = []
    for row in rows:
        if row.get("gongsi_man") is None:
            continue
        cno = row.get("complex_no")
        if not cno:
            continue
        district = row["district"]
        out.append({
            "complex_no": str(cno),
            "name": row["complex_name"],
            "gu": district.split()[-1] if district else "",
            "district": district,
            "units": row.get("units") or 0,
            "area": row.get("area_exclusive_m2") or 0.0,
            "kapt_code": row.get("kapt_code") if row.get("kapt_verified") else None,
        })
    return out


def apply_gongsi_removals(rows: list[dict], removed_cnos: set[str]) -> int:
    """removed_cnos(identity:* 탈락 cno)에 속한 행만 gongsi_man=None 으로 되돌린다 — 다른 필드는
    절대 건드리지 않는다. 변경된 행 수를 반환."""
    n = 0
    for row in rows:
        if str(row.get("complex_no")) in removed_cnos:
            row["gongsi_man"] = None
            n += 1
    return n


def write_universe_with_backup(path: Path, rows: list[dict], stamp: str) -> Path:
    """path 를 <path>.bak-gongsi-revalidate-<stamp> 로 백업(동명 존재 시 -2,-3... 접미 부여 — 기존
    백업은 절대 덮어쓰지 않는다)한 뒤 rows 를 collect_gongsi.main 과 동일 스타일로 in-place 기록한다.
    백업 경로를 반환."""
    backup = path.with_name(f"{path.name}.bak-gongsi-revalidate-{stamp}")
    suffix = 2
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-gongsi-revalidate-{stamp}-{suffix}")
        suffix += 1
    backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return backup


def derive_frame_candidates(cnos: list[str], overlay: dict,
                            frame_by_cno: dict) -> tuple[list[dict], list[str]]:
    """derive_public_targets 후보에 없는 cno 를 collect_public_enrich.FRAME 원본으로 폴백 재검증
    후보화한다 — area 는 frame 에 없어 0.0(→ _revalidate_one 이 신원게이트까지만 판정하고
    identity-ok-no-area 로 구 값을 유지). frame 에도 없는 cno 는 missing 으로 분리 반환(재검증
    불가, 원값 유지)."""
    candidates: list[dict] = []
    missing: list[str] = []
    for cno in cnos:
        fr = frame_by_cno.get(cno)
        if not fr:
            missing.append(cno)
            continue
        gu = fr["gu"]
        candidates.append({"complex_no": cno, "name": fr["name"], "gu": gu,
                           "district": _district_of(gu), "units": fr.get("households") or 0,
                           "area": 0.0, "kapt_code": overlay[cno].get("kapt_code")})
    return candidates, missing


def _revalidate_candidates(candidates: list[dict], old_values: dict, molit: dict,
                           vkey: str, mkey: str, cache_path: Path, decisions_path: Path,
                           sample_mode: bool, budget_min: float) -> tuple[dict, bool]:
    """overlay·universe 두 모드가 공유하는 budget/resume 루프 — decisions dict 와 partial 플래그를
    반환한다. old_values 는 cno → 재검증 전 gongsi_man(overlay 모드는 overlay[cno], universe 모드는
    universe row 값)."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = json.load(open(cache_path, encoding="utf-8")) if cache_path.exists() else \
        {"raw_basis": {}, "vworld": {}}
    decisions: dict[str, dict] = load_resume_decisions(decisions_path, sample_mode)
    raw_cache, vworld_cache = cache["raw_basis"], cache["vworld"]

    budget_sec = budget_min * 60
    t0 = time.monotonic()
    processed_now = 0
    partial = False
    for i, c in enumerate(candidates, 1):
        cno = c["complex_no"]
        if cno in decisions:            # resume — 이미 처리됨
            continue
        elapsed = time.monotonic() - t0
        if elapsed > budget_sec:
            partial = True
            print(f"⏱ 예산({budget_min}분) 초과 — {i-1}/{len(candidates)} 처리 후 중단")
            break
        if not c["kapt_code"]:
            decisions[cno] = {"name": c["name"], "gu": c["gu"], "reason": "no-kapt",
                              "old_value": old_values.get(cno)}
            continue
        passed, reason, new_val = _revalidate_one(
            c["kapt_code"], c["name"], c["district"], c["area"], c["units"],
            molit, vkey, mkey, raw_cache, vworld_cache)
        decisions[cno] = {"name": c["name"], "gu": c["gu"], "reason": reason,
                          "old_value": old_values.get(cno), "new_probe_value": new_val}
        processed_now += 1
        if processed_now % 20 == 0:
            json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
            if not sample_mode:
                json.dump(decisions, open(decisions_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  진행 {i}/{len(candidates)} (경과 {elapsed:.0f}s)")

    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    if not sample_mode:
        json.dump(decisions, open(decisions_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return decisions, partial


def _summarize_decisions(candidates: list[dict], decisions: dict,
                         partial: bool) -> list[tuple[str, str, "int | None"]]:
    """사유별 집계 + identity:* 제거대상 목록을 출력하고 반환한다(overlay·universe 공통)."""
    done = [cno for cno in (c["complex_no"] for c in candidates) if cno in decisions]
    by_reason: dict[str, int] = {}
    removed: list[tuple[str, str, int | None]] = []
    for cno in done:
        d = decisions[cno]
        by_reason[d["reason"]] = by_reason.get(d["reason"], 0) + 1
        if d["reason"].startswith("identity:"):
            removed.append((cno, d["name"], d["old_value"]))

    print(f"\n처리완료 {len(done)}/{len(candidates)} (partial={partial})")
    print("사유별:", by_reason)
    print(f"신원게이트 탈락(identity:*) → gongsi_man 제거 대상 {len(removed)}건:")
    for cno, name, old in removed:
        print(f"   {cno} {name}: {old} → None")
    return removed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay", default="examples/enrich_overlay_25gu_20260710.json")
    ap.add_argument("--molit", default="examples/molit_recent_25gu_20260710.json")
    ap.add_argument("--out", default="examples/enrich_overlay_25gu_20260905.json",
                    help="run_daily._latest_or('examples/enrich_overlay_*.json') 가 마지막으로 "
                         "고르도록 20260710 뒤에 lexicographic 하게 오는 이름이어야 함(대시 X)")
    ap.add_argument("--cache", default=str(SCRATCH / "cache.json"))
    ap.add_argument("--decisions", default=None,
                    help="미지정시 --universe 는 decisions_universe.json, 아니면 decisions.json")
    ap.add_argument("--budget-min", type=float, default=40.0)
    ap.add_argument("--sample", type=int, default=0, help="0=전량, N>0=층화표본 N개(overlay/universe 미기록)")
    ap.add_argument("--sample-min-gu", type=int, default=10)
    ap.add_argument("--universe", action="store_true",
                    help="overlay 대신 universe(candidates_universe*.json)를 직접 재검증 — "
                         "in-place 갱신+백업, --out 무시")
    ap.add_argument("--universe-path", default=None,
                    help="미지정시 collect_gongsi.latest_universe_path() 지연 해소")
    a = ap.parse_args()
    if a.decisions is None:
        a.decisions = str(SCRATCH / ("decisions_universe.json" if a.universe else "decisions.json"))

    vkey = os.environ.get("VWORLD_API_KEY", "")
    mkey = os.environ.get("MOLIT_API_KEY", "")
    if not vkey or not mkey:
        raise SystemExit("VWORLD_API_KEY / MOLIT_API_KEY 미설정 (.env)")

    molit = json.load(open(a.molit, encoding="utf-8"))
    cache_path = Path(a.cache)
    decisions_path = Path(a.decisions)

    if a.universe:
        universe_path = Path(a.universe_path) if a.universe_path else collect_gongsi.latest_universe_path()
        rows: list[dict] = json.load(open(universe_path, encoding="utf-8"))
        candidates = derive_universe_candidates(rows)
        old_values = {str(r["complex_no"]): r.get("gongsi_man") for r in rows if r.get("complex_no")}
        print(f"universe={universe_path} 총 {len(rows)}개 중 gongsi_man 보유 {len(candidates)}개")
    else:
        overlay = json.load(open(a.overlay, encoding="utf-8"))
        targets = cpe.derive_public_targets()
        by_cno = {t["complex_no"]: t for t in targets}

        need = [cno for cno, e in overlay.items() if e.get("gongsi_man") is not None]
        print(f"overlay={a.overlay} 총 {len(overlay)}개 중 gongsi_man 보유 {len(need)}개")

        candidates = []
        no_target = []
        for cno in sorted(need):
            t = by_cno.get(cno)
            if not t:
                no_target.append(cno)
                continue
            candidates.append({"complex_no": cno, "name": t["name"], "gu": t["gu"],
                               "district": _district_of(t["gu"]), "units": t["units"] or 0,
                               "area": t["area_m2"] or 0.0, "kapt_code": overlay[cno].get("kapt_code")})
        if no_target:
            frame = json.load(open(cpe.FRAME, encoding="utf-8"))
            frame_by_cno: dict[str, dict] = {}
            for r in frame:
                frame_by_cno.setdefault(str(r["complexNo"]), r)
            frame_candidates, missing = derive_frame_candidates(no_target, overlay, frame_by_cno)
            candidates.extend(frame_candidates)
            print(f"  (참고) derive_public_targets 에 없는 cno {len(no_target)}개 중 frame 폴백으로 "
                  f"{len(frame_candidates)}개 추가 재검증(신원게이트만 판정, area 없음)")
            if missing:
                print(f"  (참고) frame 에도 없어 재검증 불가, 원값 유지: "
                      f"{missing[:10]}{'...' if len(missing) > 10 else ''}")
        old_values = {cno: e.get("gongsi_man") for cno, e in overlay.items()}

    sample_mode = a.sample > 0
    if sample_mode:
        candidates = _stratified_sample(candidates, a.sample, a.sample_min_gu)
        print(f"층화표본 모드: {len(candidates)}개 / {len({c['gu'] for c in candidates})}개 구 "
              "— overlay 는 기록하지 않음")

    decisions, partial = _revalidate_candidates(candidates, old_values, molit, vkey, mkey,
                                                cache_path, decisions_path, sample_mode, a.budget_min)
    removed = _summarize_decisions(candidates, decisions, partial)

    if sample_mode:
        print("\n(표본 모드) overlay/universe 미기록 — 위 사유별/제거대상 목록이 산출물입니다.")
        return
    if partial:
        print("\n예산 내 전량 완료 실패 — 파일 미기록(부분결과만 decisions.json 에 저장됨). "
              "재실행하면 decisions 에 없는 cno 부터 이어서 처리(멱등).")
        return

    if a.universe:
        removed_cnos = {cno for cno, _name, _old in removed}
        n_changed = apply_gongsi_removals(rows, removed_cnos)
        done_cnos = {c["complex_no"] for c in candidates}
        n_moved = sum(1 for cno in done_cnos
                     if cno in decisions and decisions[cno]["reason"] == "pass"
                     and decisions[cno].get("new_probe_value") != decisions[cno].get("old_value"))
        stamp = time.strftime("%Y%m%d")
        backup = write_universe_with_backup(universe_path, rows, stamp)
        print(f"\n✓ universe in-place 갱신: {universe_path} (gongsi_man None 처리 {n_changed}건)")
        print(f"  백업: {backup}")
        print(f"  값 변동(참고용, 기록하지 않음): pass 판정 중 새 프로브값 ≠ 기존값 {n_moved}건")
        return

    new_overlay = json.loads(json.dumps(overlay))   # deep copy
    for cno, name, _old in removed:
        new_overlay[cno]["gongsi_man"] = None
    Path(a.out).write_text(json.dumps(new_overlay, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n✓ 신규 overlay 기록: {a.out} (gongsi_man None 처리 {len(removed)}건, 그 외 전부 원본과 동일)")


if __name__ == "__main__":
    main()
