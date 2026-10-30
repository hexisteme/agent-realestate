"""통근 모형 입력 수집 — 서울 열린데이터광장 4개 원천을 한 판(vintage)으로 묶어 보관한다. DIKW 검토 2026-10-30 P2.

원천(전부 공공누리 제1유형, 실측 report/2026-10-30-dikw-kw-review/p2/DATA-PROBE.md):
- OA-22522 서울교통공사 열차운행시각표(파일) — 평일·일반열차·1~9호선, 06~12시에 걸친 편만 통째로 남긴다.
- OA-13290 서울교통공사 환승역거리 소요시간(파일) — 20250331판(seq=8)과 20230831판(seq=5)을 우선순위대로 함께 둔다.
  최신판에 석촌 8→9·올림픽공원 5→9 등이 빠져 있다. seq=1 은 1~4호선 출발만 있는 옛 양식이라 쓰지 않는다.
- OA-21232 역사마스터(OpenAPI subwayStationMaster) — 역 좌표, 수도권 전 노선.
- OA-15818 서울시 공동주택 아파트 정보(OpenAPI OpenAptInfo) — 단지 좌표.
키는 환경변수 SEOUL_OPENAPI_KEY 에서만 읽고, 키가 든 URL 은 오류 문구에도 남기지 않는다. 하나라도 실패하면 판을 만들지 않는다.
"""
from __future__ import annotations

import csv
import glob
import gzip
import http.cookiejar
import io
import json
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date

SNAPSHOT_DIR = "report/blog/snapshots/commute"
REFRESH_DAYS = 30
_FILE_RE = re.compile(r"commute-inputs-(\d{4}-\d{2}-\d{2})\.json\.gz$")
_DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do"
_DATASET_PAGE = "https://data.seoul.go.kr/dataList/{inf}/F/1/datasetView.do"
TIMETABLE_FILE = {"infId": "OA-22522", "seq": "1", "infSeq": "2"}     # 서울교통공사_도시철도열차운행시각표(250930).csv
TRANSFER_FILES = ({"infId": "OA-13290", "seq": "8", "infSeq": "1"},   # 환승역거리 소요시간 정보_20250331.csv(140행)
                  {"infId": "OA-13290", "seq": "5", "infSeq": "1"})   # 환승역거리 및 소요시간정보_20230831.csv(142행)
TIMETABLE_COLUMNS = ("LINE", "SI_ID", "STATION_NM", "WEEKTAG", "INOUTTAG", "GUBHANG", "TRAIN_NO", "STT", "EDT")
TRANSFER_COLUMNS = ("호선", "환승역명", "환승노선", "환승거리", "환승소요시간")   # 판마다 단위 꼬리표("(m)"·"(초)")가 붙거나 빠진다
LINES = tuple(str(n) for n in range(1, 10))
MIN_ROWS = {"timetable": 30_000, "stations": 600, "apts": 2_000}   # 첫 판 실측 59,957·784·2,854 — 이보다 적으면 부분 응답(Codex R3)
COVERAGE_KEEP = {"line_rows": 0.7, "line_stations": 0.95, "stations": 0.9, "apts": 0.9}   # 이전 판 대비 이 비율 아래로 줄면 부분 판
KEEP_FROM_S, KEEP_TO_S = 6 * 3600, 12 * 3600


def _secs(text: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", (text or "").strip())
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None


def _download_file(spec: dict) -> tuple[str, bytes]:
    """열린데이터광장 파일 내려받기 — 데이터셋 화면 세션 쿠키가 있어야 한다. (파일명, 바이트)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    page = _DATASET_PAGE.format(inf=spec["infId"])
    opener.open(page, timeout=30).read()
    req = urllib.request.Request(_DOWNLOAD_URL, data=urllib.parse.urlencode(spec).encode(), headers={"Referer": page})
    with opener.open(req, timeout=180) as r:
        disp = r.headers.get("Content-Disposition", "")
        body = r.read()
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disp)
    name = urllib.parse.unquote(m.group(1)) if m else ""
    try:
        name = name.encode("latin-1").decode("utf-8")   # 헤더를 latin-1 로 읽은 UTF-8 파일명
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name, body


def _decode(body: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("인코딩을 알 수 없는 파일")


def parse_timetable(text: str) -> list[list[str]]:
    """운행시각표 CSV → 평일·일반열차·1~9호선 행 중 06~12시에 정차가 하나라도 있는 편의 행 전부.
    행 = [LINE, SI_ID, STATION_NM, INOUTTAG, TRAIN_NO, STT, EDT]. 열이 바뀌었으면 ValueError."""
    rd = csv.DictReader(io.StringIO(text))
    if not set(TIMETABLE_COLUMNS) <= set(rd.fieldnames or ()):
        raise ValueError(f"운행시각표 열 불일치: {rd.fieldnames}")
    trips: dict[tuple, list[list[str]]] = defaultdict(list)
    for r in rd:
        if r["WEEKTAG"] != "DAY" or r["GUBHANG"].strip() != "0" or r["LINE"].strip() not in LINES:
            continue
        trips[(r["LINE"].strip(), r["TRAIN_NO"].strip(), r["INOUTTAG"].strip())].append(
            [r["LINE"].strip(), r["SI_ID"].strip(), r["STATION_NM"].strip(), r["INOUTTAG"].strip(), r["TRAIN_NO"].strip(),
             r["STT"].strip(), r["EDT"].strip()])
    out = []
    for key in sorted(trips):
        rows = trips[key]
        if any(KEEP_FROM_S <= t < KEEP_TO_S for row in rows for t in (_secs(row[5]), _secs(row[6])) if t is not None):
            out.extend(rows)
    if not out:
        raise ValueError("운행시각표에 남는 행이 없음")
    return out


def _transfer_secs(text: str) -> int | None:
    t = (text or "").replace(" ", "")
    m = re.fullmatch(r"(?:(\d+)분)?(?:(\d+)초)?", t)
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def parse_transfers(text: str) -> list[list]:
    """환승역거리 소요시간 CSV → [[출발 호선, 환승역명, 환승노선, 거리 m|None, 소요 초], ...]. 소요 초가 없는 행은 뺀다."""
    rd = csv.DictReader(io.StringIO(text))
    col = {c: next((f for f in (rd.fieldnames or ()) if re.sub(r"\(.*?\)", "", f).strip() == c), None) for c in TRANSFER_COLUMNS}
    if None in col.values():
        raise ValueError(f"환승 파일 열 불일치: {rd.fieldnames}")
    out = []
    for r in rd:
        secs = _transfer_secs(r[col["환승소요시간"]])
        if secs is None:
            continue
        dist = r[col["환승거리"]].strip()
        out.append([r[col["호선"]].strip(), r[col["환승역명"]].strip(), r[col["환승노선"]].strip(),
                    int(dist) if dist.isdigit() else None, secs])
    if len(out) < 100:
        raise ValueError(f"환승 행이 너무 적음({len(out)}) — 옛 양식 파일일 수 있음")
    return out


PAGE_ROWS = 500
PAGE_ATTEMPTS = 3   # 1천 행 응답이 가끔 잘려 와 JSON 이 깨진다(2026-10-30 실측) — 같은 쪽을 최대 3번


def _openapi_page(service: str, key: str, start: int, end: int) -> dict:
    url = f"http://openapi.seoul.go.kr:8088/{key}/json/{service}/{start}/{end}/"
    err = ""
    for _ in range(PAGE_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.loads(r.read(), strict=False)
        except Exception as e:  # noqa: BLE001 — 키가 든 URL 을 문구에 남기지 않는다
            err = type(e).__name__
    raise RuntimeError(f"{service} 요청 실패: {err}")


def _openapi_rows(service: str, key: str) -> list[dict]:
    rows, start = [], 1
    while True:
        end = start + PAGE_ROWS - 1
        body = _openapi_page(service, key, start, end)
        block = body.get(service) or {}
        if (block.get("RESULT") or {}).get("CODE") != "INFO-000":
            raise RuntimeError(f"{service} 응답 코드 {(block.get('RESULT') or {}).get('CODE')}")
        rows += block.get("row") or []
        total = int(block.get("list_total_count") or 0)
        if end >= total:
            break
        start = end + 1
    if len(rows) != total:
        raise RuntimeError(f"{service} 행 수 불일치 {len(rows)}/{total}")
    return rows


def _line_profile(payload: dict) -> dict[str, tuple[int, set[str], set[str]]]:
    out: dict[str, tuple[int, set[str], set[str]]] = {}
    for line, sid, _nm, tag, *_ in payload["timetable"]["rows"]:
        n, tags, sids = out.get(line, (0, set(), set()))
        tags.add(tag)
        sids.add(sid)
        out[line] = (n + 1, tags, sids)
    return out


def check_commute_coverage(payload: dict, prev: dict | None = None) -> None:
    """저장 전 커버리지(Codex R3 — 8·9호선이 빠진 시각표가 행 수 하한을 넘어 새 판이 되던 경로).
    1~9호선마다 방향 표지 2개 이상. 이전 정상 판이 있으면 호선별 행·역 수와 역사마스터·단지 행 수가 COVERAGE_KEEP 아래로 줄면 ValueError."""
    now = _line_profile(payload)
    for line in LINES:
        tags = now.get(line, (0, set(), set()))[1]
        if len(tags) < 2:
            raise ValueError(f"{line}호선 방향 표지 {sorted(tags)} — 부분 시각표")
    if not prev:
        return
    for line, (n0, _, s0) in sorted(_line_profile(prev).items()):
        n, _, s = now.get(line, (0, set(), set()))
        if n < n0 * COVERAGE_KEEP["line_rows"] or len(s) < len(s0) * COVERAGE_KEEP["line_stations"]:
            raise ValueError(f"{line}호선 행 {n}/{n0}·역 {len(s)}/{len(s0)} — 이전 판보다 크게 줄어듦")
    for key in ("stations", "apts"):
        if len(payload[key]) < len(prev[key]) * COVERAGE_KEEP[key]:
            raise ValueError(f"{key} {len(payload[key])}/{len(prev[key])} — 이전 판보다 크게 줄어듦")


def collect_commute_inputs(today: str, download=_download_file, openapi=_openapi_rows, prev: dict | None = None) -> dict:
    """4개 원천 → 판 dict. 원천 하나라도 실패하거나 커버리지(check_commute_coverage)가 모자라면 예외(부분 판 없음).
    prev = 이전 정상 판(있으면 줄어듦 검사)."""
    key = os.environ.get("SEOUL_OPENAPI_KEY", "")
    if not key:
        raise RuntimeError("SEOUL_OPENAPI_KEY 미설정")
    tt_name, tt_body = download(TIMETABLE_FILE)
    transfers = []
    for spec in TRANSFER_FILES:
        name, body = download(spec)
        transfers.append({"file": name, "rows": parse_transfers(_decode(body))})
    stations = [[r["BLDN_ID"], r["BLDN_NM"], r["ROUTE"], float(r["LAT"]), float(r["LOT"])]
                for r in openapi("subwayStationMaster", key) if r.get("LAT") and r.get("LOT")]
    apts = [[r["APT_CD"], r["APT_NM"], r["SGG_ADDR"], r["EMD_ADDR"], r.get("DADDR") or "", float(r["XCRD"]), float(r["YCRD"]),
             r.get("TNOHSH"), str(r.get("USE_APRV_YMD") or "")]
            for r in openapi("OpenAptInfo", key) if r.get("XCRD") and r.get("YCRD")]
    timetable = parse_timetable(_decode(tt_body))
    for name, n in (("timetable", len(timetable)), ("stations", len(stations)), ("apts", len(apts))):
        if n < MIN_ROWS[name]:
            raise RuntimeError(f"{name} 행이 너무 적음({n} < {MIN_ROWS[name]}) — 이전 판 유지")
    payload = {"asof": today,
               "timetable": {"file": tt_name, "rows": timetable},
               "transfers": transfers,                                   # 우선순위 순(최신판 먼저)
               "stations": stations, "apts": apts}
    check_commute_coverage(payload, prev)
    return payload


def commute_inputs_path_latest(dir: str = SNAPSHOT_DIR, today: str | None = None) -> str | None:
    cands = sorted(f for f in glob.glob(os.path.join(dir, "commute-inputs-*.json.gz"))
                   if _FILE_RE.search(f) and (today is None or _FILE_RE.search(f).group(1) <= today))
    return cands[-1] if cands else None


def load_commute_inputs_latest(dir: str = SNAPSHOT_DIR, today: str | None = None) -> dict | None:
    p = commute_inputs_path_latest(dir, today)
    if not p:
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def save_commute_inputs(payload: dict, dir: str = SNAPSHOT_DIR) -> str:
    os.makedirs(dir, exist_ok=True)
    dst = os.path.join(dir, f"commute-inputs-{payload['asof']}.json.gz")
    tmp = dst + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, dst)
    return dst


def is_refresh_due(today: str, dir: str = SNAPSHOT_DIR) -> bool:
    """마지막 판이 REFRESH_DAYS 일보다 오래됐거나 없으면 True — 시각표·환승표는 드물게 바뀐다."""
    p = commute_inputs_path_latest(dir)
    if not p:
        return True
    last = date.fromisoformat(_FILE_RE.search(p).group(1))
    return (date.fromisoformat(today) - last).days >= REFRESH_DAYS
