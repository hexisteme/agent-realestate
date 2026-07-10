"""오늘(2026-07-10) 기준 서울 25개구 전 단지 라이브 열거 → 세대수≥200 + corridor 제외 프레임.
naver-region-scan.sh(무인증 마커 API)로 구별 전 단지(complexNo·세대수·용적률·준공·활성매물수) 수집.
enumerate_11gu.py 의 25구 확장 복제 (WS-1) — cortar 는 collectors/naver_region.SEOUL_GU 참조."""
import json, subprocess

SCAN = "/Users/kimjonghyun/.claude/scripts/naver-region-scan.sh"
GU = {  # 서울 25개구 (naver_region.SEOUL_GU 참조 — cortarNo = 법정동코드 prefix + 000000)
        # ※ 광진(11215)·금천(11545)은 원본 SEOUL_GU dict 가 5번째자리를 누락(…10/…40)해 무효 cortar →
        #   정확한 1121500000·1154500000 로 정정(라이브 검증: 987·655단지 반환). 원본 dict 수정은 별도.
    "종로": "1111000000", "중구": "1114000000", "용산": "1117000000", "성동": "1120000000",
    "광진": "1121500000", "동대문": "1123000000", "중랑": "1126000000", "성북": "1129000000",
    "강북": "1130500000", "도봉": "1132000000", "노원": "1135000000", "은평": "1138000000",
    "서대문": "1141000000", "마포": "1144000000", "양천": "1147000000", "강서": "1150000000",
    "구로": "1153000000", "금천": "1154500000", "영등포": "1156000000", "동작": "1159000000",
    "관악": "1162000000", "서초": "1165000000", "강남": "1168000000", "송파": "1171000000",
    "강동": "1174000000",
}
CORRIDOR_EXCL = {"구로현대", "구로두산", "두산"}   # 구로구 한정, 대림역~남구로역 안전제외
MIN_UNITS = 200

def base_name(nm): return nm.split("[")[0].strip()

frame, stats, failed = [], {}, []
for gu, cortar in GU.items():
    try:
        raw = subprocess.run(["bash", SCAN, cortar], capture_output=True, text=True, timeout=60).stdout
        rows = json.loads(raw)
    except Exception as e:
        print(f"  ⚠️ {gu}: 열거 실패 {e}"); stats[gu] = (0, 0, 0); failed.append(gu); continue
    if not isinstance(rows, list):   # 스캔이 {error:...} 반환 시 (Chrome 미인증·regionList 결손)
        print(f"  ⚠️ {gu}: 비정상 응답 {str(rows)[:80]}"); stats[gu] = (0, 0, 0); failed.append(gu); continue
    total = len(rows)
    ge200 = [r for r in rows if (r.get("households") or 0) >= MIN_UNITS]
    # corridor 제외 (구로만)
    kept = [r for r in ge200 if not (gu == "구로" and base_name(r["name"]) in CORRIDOR_EXCL)]
    excl = len(ge200) - len(kept)
    active = [r for r in kept if (r.get("dealCount") or 0) > 0]
    stats[gu] = (total, len(kept), len(active))
    for r in kept:
        r["gu"] = gu
    frame.extend(kept)
    print(f"  {gu:5} 전체 {total:>4} → ≥200세대 {len(ge200):>3} → corridor제외 {len(kept):>3} (활성매물 {len(active):>3}){' [corridor -'+str(excl)+']' if excl else ''}")

print(f"\n총 프레임: {len(frame)}단지 (25개구, ≥200세대, corridor 제외)")
active_total = sum(1 for r in frame if (r.get('dealCount') or 0) > 0)
print(f"활성매물(dealCount>0) 보유: {active_total}단지 ← 호가 수집 대상")
json.dump(frame, open("examples/frame_25gu_20260710.json", "w"), ensure_ascii=False)
print("저장: examples/frame_25gu_20260710.json")
if failed:
    print(f"\n⚠️ 열거 실패 구 {len(failed)}/{len(GU)}: {', '.join(failed)}")
else:
    print(f"\n전 25구 열거 성공.")
