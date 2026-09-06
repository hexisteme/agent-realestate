#!/usr/bin/env python3
"""audit_kapt_identity.py — K-apt 코드 배정 신원 감사(2026-09-05).

배경: collect_universe_enrich._resolve_kapt_basis 는 구내 substring 후보를 세대수·준공연도로만 교차검증해
kapt_code 를 채택했고, 그 결과 타 구 단지 코드가 붙은 단지(성동 '현대' → 강남 청담2차현대 등 14건, 공시가
재검증 gu-mismatch 로 발견)의 관리비·난방·복도·주차·시공사가 다른 단지 값으로 발행됐다. 공시가 게이트는
VWorld 레코드가 있을 때만 돌므로 코드 배정 자체를 같은 신원게이트(구·이름·세대수)로 전수 감사한다.

    python3 audit_kapt_identity.py                 # dry-run: 판정·요약·실패 목록만
    python3 audit_kapt_identity.py --fetch-missing # basis 캐시 없는 코드를 라이브로 채움(캐시 기록)
    python3 audit_kapt_identity.py --apply         # 실패 단지의 K-apt 파생 필드 None 처리(백업 후 in-place)

판정: pass | no-basis | no-frame | gu-mismatch | name-mismatch | count-mismatch. None 처리는 *-mismatch 만.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

from collect_gongsi import BASIS_EP_V5, SLEEP_SEC, _get_json_item, _identity_fail_reason, latest_universe_path

EX = Path("examples")
FRAME_DEFAULT = EX / "frame_25gu_20260710.json"
CACHE_DIR = Path(os.environ.get("RE_SCRATCH", str(Path.home() / ".cache" / "agent_realestate" / "revalidate")))
KAPT_FIELDS = ("kapt_code", "heating", "corridor_type", "parking_per_unit", "builder", "maint_fee_won", "gongsi_man")
MISMATCH = ("gu-mismatch", "name-mismatch", "count-mismatch")


def latest_overlay_path() -> Path:
    files = sorted(EX.glob("enrich_overlay_25gu_*.json"))
    if not files:
        raise SystemExit("examples/enrich_overlay_25gu_*.json 없음")
    return files[-1]


def normalize_frame_gu(gu: str) -> str:
    """frame 의 구 표기('노원'·'노원구'·'서울 노원구'·'서울특별시 중구') → 신원게이트 구 검사 형식 '서울 노원구'."""
    g = gu.replace("서울특별시", "").replace("서울", "").strip()
    if g and not g.endswith("구"):
        g += "구"
    return f"서울 {g}".strip()


def basis_household_count(basis: dict) -> int:
    """K-apt basis 세대수 — kaptdaCnt(주상복합은 0.0 로 오는 실측: 동도센트리움) 없으면 hoCnt."""
    for k in ("kaptdaCnt", "hoCnt"):
        try:
            v = int(float(basis.get(k) or 0))
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            return v
    return 0


def join_frame_gus(gus: list[str]) -> str:
    """단지가 속할 수 있는 구 후보 전부를 '서울 송파구/서울 강동구' 로 — 신원게이트의 구 검사(addr_gu in frame_gu)가
    후보 중 하나라도 맞으면 통과하도록. FRAME 은 (스캔 구, 단지) 행이라 인접 구 중복 행이 있고 첫 행의 gu 는 스캔
    영역이지 단지의 구가 아니다(강남자곡힐스테이트가 '서초' 행으로 먼저 옴 — 2026-09-05 감사에서 208건 오판)."""
    return "/".join(dict.fromkeys(normalize_frame_gu(g) for g in gus if g))


def audit_kapt_basis(frame_name: str, frame_gus: list[str], households: int, basis: dict | None) -> str:
    """frame 단지(이름·구 후보·세대수) ↔ K-apt basis 가 같은 단지인지 — 공시가 신원게이트와 단일 로직.
    frame_gus 는 데이터셋의 구(실거래 LAWD 기준, 있으면 단독) 또는 FRAME 의 스캔 구 전체."""
    if not frame_name:
        return "no-frame"
    if not basis:
        return "no-basis"
    r = _identity_fail_reason(frame_name, str(basis.get("kaptName") or ""), basis_household_count(basis),
                              households or 0, kapt_addr=str(basis.get("kaptAddr") or ""),
                              frame_gu=join_frame_gus(frame_gus))
    return r or "pass"


def derive_overlay_candidates(overlay: dict, frame_by_cno: dict, dataset_gu_by_cno: dict | None = None) -> list[dict]:
    """frame_by_cno: cno → {"name","households","gus":[스캔 구...]} (index_frame). 구 후보는 데이터셋 구가 있으면
    그것만(실거래 LAWD 기준 확정), 없으면 FRAME 스캔 구 전체."""
    out = []
    for cno, e in overlay.items():
        if not e.get("kapt_code"):
            continue
        fr = frame_by_cno.get(str(cno)) or {}
        ds_gu = (dataset_gu_by_cno or {}).get(str(cno))
        out.append({"source": "overlay", "key": str(cno), "name": str(fr.get("name") or ""),
                    "gus": [ds_gu] if ds_gu else list(fr.get("gus") or []), "households": int(fr.get("households") or 0),
                    "kapt_code": e["kapt_code"]})
    return out


def latest_district_map_path() -> str:
    """최신 examples/frame_district_*.json — 없으면 "". 호출 시점 해소(import 시점 금지)."""
    files = sorted(EX.glob("frame_district_*.json"))
    return str(files[-1]) if files else ""


def resolve_gu_by_cno(dataset_path: str, district_map_path: str) -> dict[str, str]:
    """cno → 소재구. 좌표 소재구 맵(collect_frame_district.py)을 깔고 발행 데이터셋 구로 덮는다.

    FRAME 의 gu 는 스캔 구역이지 소재구가 아니다(2026-09-06) — 스캔 구 전체를 후보로 쓰면 소재구가
    후보에 없는 단지의 타 구 K-apt 코드가 통과해버린다(마포 성원 ↔ 서대문 홍제성원 등 3건 실적발).
    발행 데이터셋의 구는 실거래 LAWD 로 확정된 값이라 최우선, 서울 밖·구 미상은 무시한다."""
    out: dict[str, str] = {}
    if district_map_path and Path(district_map_path).exists():
        for cno, v in json.load(open(district_map_path, encoding="utf-8")).items():
            if str(v.get("sido", "")).startswith("서울") and v.get("gu"):
                out[str(cno)] = str(v["gu"])
    if dataset_path and Path(dataset_path).exists():
        out.update({str(r.get("complex_no")): str(r.get("gu") or "")
                    for r in json.load(open(dataset_path, encoding="utf-8")).get("complexes", [])
                    if r.get("complex_no") and r.get("gu")})
    return out


def index_frame(frame_rows: list[dict]) -> dict[str, dict]:
    """FRAME 행(스캔 구별 중복) → cno 별 name·households(첫 행) + gus(모든 행의 구, 순서 유지)."""
    out: dict[str, dict] = {}
    for r in frame_rows:
        cno = str(r["complexNo"])
        d = out.setdefault(cno, {"name": str(r.get("name") or ""), "households": int(r.get("households") or 0), "gus": []})
        if r.get("gu") and r["gu"] not in d["gus"]:
            d["gus"].append(r["gu"])
    return out


def derive_universe_kapt_candidates(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not r.get("kapt_code") or not r.get("complex_no"):
            continue
        out.append({"source": "universe", "key": str(r["complex_no"]), "name": str(r.get("complex_name") or ""),
                    "gus": [str(r.get("district") or "")], "households": int(r.get("units") or 0),
                    "kapt_code": r["kapt_code"]})
    return out


def null_kapt_fields(entry: dict) -> list[str]:
    """K-apt 파생 필드 None + kapt_verified False. 실제로 바뀐 필드명 반환(학군·학교 등 비 K-apt 필드는 불변)."""
    changed = []
    for k in KAPT_FIELDS:
        if entry.get(k) is not None:
            entry[k] = None
            changed.append(k)
    if entry.get("kapt_verified"):
        entry["kapt_verified"] = False
        changed.append("kapt_verified")
    return changed


def write_with_backup(path: Path, obj, tag: str) -> Path:
    backup = path.with_name(f"{path.name}.bak-{tag}")
    n = 2
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-{tag}-{n}")
        n += 1
    backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return backup


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay", default=None, help="미지정시 최신 examples/enrich_overlay_25gu_*.json")
    ap.add_argument("--universe-path", default=None, help="미지정시 collect_gongsi.latest_universe_path()")
    ap.add_argument("--frame", default=str(FRAME_DEFAULT))
    ap.add_argument("--dataset", default="site/dataset.json", help="발행 데이터셋(complexes[].gu = 실거래 LAWD 기준 구) — 있으면 구 판정의 정본")
    ap.add_argument("--cache", default=str(CACHE_DIR / "cache.json"), help="revalidate 캐시(raw_basis 재사용)")
    ap.add_argument("--extra-cache", default=str(CACHE_DIR / "cache_kapt_audit.json"))
    ap.add_argument("--decisions", default=str(CACHE_DIR / "kapt_audit_decisions.json"))
    ap.add_argument("--fetch-missing", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    overlay_path = Path(a.overlay) if a.overlay else latest_overlay_path()
    universe_path = Path(a.universe_path) if a.universe_path else latest_universe_path()
    overlay = json.load(open(overlay_path, encoding="utf-8"))
    rows = json.load(open(universe_path, encoding="utf-8"))
    frame_by_cno = index_frame(json.load(open(a.frame, encoding="utf-8")))
    dataset_gu_by_cno = resolve_gu_by_cno(a.dataset, latest_district_map_path())
    basis: dict[str, dict] = {}
    for p in (a.cache, a.extra_cache):
        if Path(p).exists():
            basis.update({k: v for k, v in (json.load(open(p, encoding="utf-8")).get("raw_basis") or {}).items() if v})

    cands = derive_overlay_candidates(overlay, frame_by_cno, dataset_gu_by_cno) + derive_universe_kapt_candidates(rows)
    missing = sorted({c["kapt_code"] for c in cands if c["kapt_code"] not in basis})
    print(f"overlay={overlay_path.name} universe={universe_path.name} · 후보 {len(cands)} · basis 미보유 {len(missing)}")
    if a.fetch_missing and missing:
        key = os.environ.get("MOLIT_API_KEY", "")
        if not key:
            raise SystemExit("MOLIT_API_KEY 미설정 (.env)")
        extra = json.load(open(a.extra_cache, encoding="utf-8")) if Path(a.extra_cache).exists() else {"raw_basis": {}}
        for i, code in enumerate(missing, 1):
            b = _get_json_item(BASIS_EP_V5, {"kaptCode": code}, key)
            if not b:
                time.sleep(2.0)
                b = _get_json_item(BASIS_EP_V5, {"kaptCode": code}, key)
            if b:
                extra["raw_basis"][code] = b
                basis[code] = b
            time.sleep(SLEEP_SEC)
            if i % 20 == 0:
                print(f"  basis 조회 {i}/{len(missing)}")
        Path(a.extra_cache).parent.mkdir(parents=True, exist_ok=True)
        json.dump(extra, open(a.extra_cache, "w", encoding="utf-8"), ensure_ascii=False)

    decisions: dict[str, dict] = {}
    by_reason: dict[str, dict[str, int]] = {}
    for c in cands:
        b = basis.get(c["kapt_code"])
        reason = audit_kapt_basis(c["name"], c["gus"], c["households"], b)
        decisions[f'{c["source"]}:{c["key"]}'] = {**c, "reason": reason,
                                                    "kapt_name": (b or {}).get("kaptName"), "kapt_addr": (b or {}).get("kaptAddr"),
                                                    "kapt_households": basis_household_count(b or {})}
        by_reason.setdefault(c["source"], {}).setdefault(reason, 0)
        by_reason[c["source"]][reason] += 1
    print("사유별:", by_reason)
    fails = [d for d in decisions.values() if d["reason"] in MISMATCH]
    print(f"신원 불일치 {len(fails)}건:")
    for d in fails:
        print(f'  [{d["source"]}] {d["key"]} {d["name"]}({"/".join(d["gus"])}, {d["households"]}세대) ↔ {d["kapt_code"]} {d["kapt_name"]} '
              f'({d["kapt_addr"]}, {d["kapt_households"]}세대): {d["reason"]}')
    Path(a.decisions).parent.mkdir(parents=True, exist_ok=True)
    json.dump(decisions, open(a.decisions, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if not a.apply:
        print("\n(dry-run) 파일 미기록 — --apply 로 K-apt 파생 필드 None 처리")
        return
    stamp = date.today().strftime("%Y%m%d")
    n_ov = n_uni = 0
    for d in fails:
        if d["source"] == "overlay":
            if null_kapt_fields(overlay[d["key"]]):
                n_ov += 1
        else:
            for r in rows:
                if str(r.get("complex_no")) == d["key"] and null_kapt_fields(r):
                    n_uni += 1
    b1 = write_with_backup(overlay_path, overlay, f"kapt-audit-{stamp}")
    b2 = write_with_backup(universe_path, rows, f"kapt-audit-{stamp}")
    print(f"\n✓ overlay {overlay_path} ({n_ov}건 None 처리, 백업 {b1.name}) · universe {universe_path} ({n_uni}건, 백업 {b2.name})")


if __name__ == "__main__":
    main()
