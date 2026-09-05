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
from collect_gongsi import (count_households,
    _pnu_from_basis, _fetch_vworld_all, _identity_fail_reason, _molit_median_won,
    AREA_TOL, RATIO_LO, RATIO_HI,
)
import collect_public_enrich as cpe

SCRATCH = Path("/private/tmp/claude-501/-Volumes-EXT-SSD-bot-agent-realestate/"
               "c1bb424a-20db-4a60-a30b-82d1ba98b854/scratchpad/revalidate")
SLEEP = 0.18   # collect_public_enrich.SLEEP 와 동일 간격


def _district_of(gu: str) -> str:
    return cpe._district_of(gu)


def _revalidate_one(kapt_code: str, name: str, district: str, area: float, units: int,
                    molit: dict, vworld_key: str, molit_key: str,
                    raw_cache: dict, vworld_cache: dict) -> tuple[bool, str, int | None]:
    """단일 단지 재검증 — collect_public_enrich._gongsi_man 과 동일 파이프라인이되 실패 사유를
    세분화해 반환한다: (kept_or_pass, reason, new_value).
    reason ∈ no-basis|no-pnu|no-vworld-records|identity:name-mismatch|identity:count-mismatch|
    no-area-match|ratio-guard|pass. gongsi_man 을 None 으로 되돌려야 하는 건 identity:* 뿐 —
    그 외 실패는 이 재검증의 관심사가 아니므로(구 값 유지) reason 만 기록하고 kept=False 로 표시하지
    않는다(호출측이 identity:* 만 보고 판단)."""
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
                                   kapt_addr=str(b.get("kaptAddr") or ""))
    if reason is not None:
        return False, f"identity:{reason}", None
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay", default="examples/enrich_overlay_25gu_20260710.json")
    ap.add_argument("--molit", default="examples/molit_recent_25gu_20260710.json")
    ap.add_argument("--out", default="examples/enrich_overlay_25gu_20260905.json",
                    help="run_daily._latest_or('examples/enrich_overlay_*.json') 가 마지막으로 "
                         "고르도록 20260710 뒤에 lexicographic 하게 오는 이름이어야 함(대시 X)")
    ap.add_argument("--cache", default=str(SCRATCH / "cache.json"))
    ap.add_argument("--decisions", default=str(SCRATCH / "decisions.json"))
    ap.add_argument("--budget-min", type=float, default=40.0)
    ap.add_argument("--sample", type=int, default=0, help="0=전량, N>0=층화표본 N개(overlay 미기록)")
    ap.add_argument("--sample-min-gu", type=int, default=10)
    a = ap.parse_args()

    vkey = os.environ.get("VWORLD_API_KEY", "")
    mkey = os.environ.get("MOLIT_API_KEY", "")
    if not vkey or not mkey:
        raise SystemExit("VWORLD_API_KEY / MOLIT_API_KEY 미설정 (.env)")

    overlay = json.load(open(a.overlay, encoding="utf-8"))
    molit = json.load(open(a.molit, encoding="utf-8"))
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
        print(f"  (참고) derive_public_targets 에 없는 cno {len(no_target)}개 — 재검증 불가, 원값 유지: "
              f"{no_target[:10]}{'...' if len(no_target) > 10 else ''}")

    sample_mode = a.sample > 0
    if sample_mode:
        candidates = _stratified_sample(candidates, a.sample, a.sample_min_gu)
        print(f"층화표본 모드: {len(candidates)}개 / {len({c['gu'] for c in candidates})}개 구 "
              "— overlay 는 기록하지 않음")

    cache_path = Path(a.cache)
    decisions_path = Path(a.decisions)
    cache = json.load(open(cache_path, encoding="utf-8")) if cache_path.exists() else \
        {"raw_basis": {}, "vworld": {}}
    decisions: dict[str, dict] = load_resume_decisions(decisions_path, sample_mode)
    raw_cache, vworld_cache = cache["raw_basis"], cache["vworld"]

    budget_sec = a.budget_min * 60
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
            print(f"⏱ 예산({a.budget_min}분) 초과 — {i-1}/{len(candidates)} 처리 후 중단")
            break
        if not c["kapt_code"] or not c["area"]:
            decisions[cno] = {"name": c["name"], "gu": c["gu"], "reason": "no-kapt-or-area",
                              "old_value": overlay[cno].get("gongsi_man")}
            continue
        passed, reason, new_val = _revalidate_one(
            c["kapt_code"], c["name"], c["district"], c["area"], c["units"],
            molit, vkey, mkey, raw_cache, vworld_cache)
        decisions[cno] = {"name": c["name"], "gu": c["gu"], "reason": reason,
                          "old_value": overlay[cno].get("gongsi_man"), "new_probe_value": new_val}
        processed_now += 1
        if processed_now % 20 == 0:
            json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
            if not sample_mode:
                json.dump(decisions, open(decisions_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  진행 {i}/{len(candidates)} (경과 {elapsed:.0f}s)")

    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    if not sample_mode:
        json.dump(decisions, open(decisions_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

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

    if sample_mode:
        print("\n(표본 모드) overlay 미기록 — 위 사유별/제거대상 목록이 산출물입니다.")
        return
    if partial:
        print("\n예산 내 전량 완료 실패 — overlay 미기록(부분결과만 decisions.json 에 저장됨). "
              "재실행하면 decisions 에 없는 cno 부터 이어서 처리(멱등).")
        return

    new_overlay = json.loads(json.dumps(overlay))   # deep copy
    for cno, name, _old in removed:
        new_overlay[cno]["gongsi_man"] = None
    Path(a.out).write_text(json.dumps(new_overlay, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n✓ 신규 overlay 기록: {a.out} (gongsi_man None 처리 {len(removed)}건, 그 외 전부 원본과 동일)")


if __name__ == "__main__":
    main()
