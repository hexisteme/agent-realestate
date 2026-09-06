"""프레임 단지의 법정 자치구를 좌표로 확정해 오프라인 맵으로 굳힌다(카카오 coord2regioncode).

배경(2026-09-06 실측): `examples/frame_*.json` 의 `gu` 는 **스캔 구역**이지 단지의 소재구가 아니다.
경계 단지는 이웃 구 스캔에도 잡히고, 서울 밖(하남·부천·광명) 단지까지 들어온다. build_dataset_public
은 이 스캔 구 후보들 중 'MOLIT 이름매칭 표본이 가장 많은 구'를 골라 발행했는데, 소재구가 후보에
아예 없으면 **엉뚱한 구의 동명 단지 거래**로 표본을 채운 채 그 구 이름으로 발행됐다(발행 31행).
좌표는 단지 고유값이라 스캔 구역과 달리 신뢰할 수 있다 — 한 번 역지오코딩해 파일로 굳히고,
일일 빌드는 네트워크 없이 그 파일만 읽는다.

사용: python3 collect_frame_district.py [--frame examples/frame_25gu_20260710.json] [--out examples/frame_district_<오늘>.json]
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

EX = Path(__file__).resolve().parent / "examples"
CACHE = Path(os.environ.get("RE_SCRATCH", str(Path.home() / ".cache" / "agent_realestate" / "revalidate"))) / "coord2gu.json"
COORD_EP = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
SLEEP_SEC = 0.05


def latest_frame_path() -> Path:
    """최신 examples/frame_*.json — 호출 시점 해소(import 시점 glob 금지, CI 트리엔 데이터가 없다)."""
    files = sorted(EX.glob("frame_*gu_*.json"))
    if not files:
        raise SystemExit(f"{EX}/frame_*gu_*.json 없음")
    return files[-1]


def fetch_region(lat: float, lng: float, key: str, cache: dict) -> dict | None:
    """좌표 → {'sido','gu','dong'} (법정동 region_type='B' 우선). 실패·무응답이면 None."""
    ck = f"{lat:.5f},{lng:.5f}"
    if ck in cache:
        v = cache[ck]
        if v is None or "sido" in v:      # 구버전 캐시(sido 없음)는 무효 처리하고 재조회
            return v
    url = f"{COORD_EP}?" + urllib.parse.urlencode({"x": lng, "y": lat})
    req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            docs = json.load(r).get("documents", [])
    except Exception as e:                                  # 일시장애 1회 재시도
        time.sleep(2.0)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                docs = json.load(r).get("documents", [])
        except Exception:
            print(f"  [카카오실패] {ck}: {e}")
            return None
    d = next((x for x in docs if x.get("region_type") == "B"), docs[0] if docs else None)
    cache[ck] = None if not d else {"sido": d.get("region_1depth_name", ""),
                                    "gu": d.get("region_2depth_name", ""),
                                    "dong": d.get("region_3depth_name", "")}
    time.sleep(SLEEP_SEC)
    return cache[ck]


def gu_stem(gu: str) -> str:
    """'구로구'→'구로', '중구'→'중구'(2자는 유지) — frame/GU_LAWD 표기와 맞춘다."""
    return gu[:-1] if gu.endswith("구") and len(gu) > 2 else gu


def build_district_map(frame_rows: list[dict], key: str, cache: dict) -> dict:
    """cno → {'gu'(어간), 'dong', 'sido', 'scan_gus'} — 좌표가 없으면 항목 자체를 만들지 않는다
    (빌드가 기존 스캔구 후보 경로로 폴백)."""
    coords: dict[str, tuple[float, float]] = {}
    scans: dict[str, list[str]] = {}
    for r in frame_rows:
        cno = str(r["complexNo"])
        scans.setdefault(cno, [])
        if r.get("gu") and r["gu"] not in scans[cno]:
            scans[cno].append(r["gu"])
        if cno not in coords and r.get("lat") and r.get("lng"):
            coords[cno] = (float(r["lat"]), float(r["lng"]))
    out: dict[str, dict] = {}
    for i, (cno, (lat, lng)) in enumerate(sorted(coords.items()), 1):
        reg = fetch_region(lat, lng, key, cache)
        if not reg:
            continue
        out[cno] = {"sido": reg["sido"], "gu": gu_stem(reg["gu"]), "dong": reg["dong"],
                    "scan_gus": scans.get(cno, [])}
        if i % 200 == 0:
            print(f"  {i}/{len(coords)} …")
    return out


def main() -> None:
    from agent_realestate import config
    config.load_env_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default=None, help="미지정시 최신 examples/frame_*gu_*.json")
    ap.add_argument("--out", default=None, help="미지정시 examples/frame_district_<오늘 YYYYMMDD>.json")
    a = ap.parse_args()
    key = os.environ.get("KAKAO_REST_KEY", "")
    if not key:
        raise SystemExit("KAKAO_REST_KEY 없음 — .env 확인")
    frame_path = Path(a.frame) if a.frame else latest_frame_path()
    out_path = Path(a.out) if a.out else EX / f"frame_district_{time.strftime('%Y%m%d')}.json"
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = json.load(open(CACHE, encoding="utf-8")) if CACHE.exists() else {}
    rows = json.load(open(frame_path, encoding="utf-8"))
    dmap = build_district_map(rows, key, cache)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(dmap, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    outside = sum(1 for v in dmap.values() if not v["sido"].startswith("서울"))
    moved = sum(1 for v in dmap.values() if v["sido"].startswith("서울") and v["gu"] not in v["scan_gus"])
    print(f"✓ {out_path} — {len(dmap)}단지 · 서울 밖 {outside} · 스캔구에 소재구가 없던 단지 {moved}")


if __name__ == "__main__":
    main()
