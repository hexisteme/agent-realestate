"""통근 권역 — 예산 계산기(reach.html)의 '도착역' 입력에 쓰는 통근 모형 추정. DIKW 검토 2026-10-30 P2.

설계 정본: report/2026-10-30-dikw-kw-review/p2/DESIGN-r3.md(2판 DESIGN-r2.md 위에 R2 Codex·agy 반영). 사용자 결정 10-30: 1~9호선
일반열차, 좌표 없는 단지는 '계산자료 없음'으로 공개.
- 역 그래프: 운행시각표 편을 이어붙인 운행(TrainRun)에서 구간 간선(RideSegment)을 만든다. 무게 = 대기(창 안 운행 수로 배차 절반) + 승차 중위.
  환승 = 환승표 걷기(반대 방향 행·옛 판·노선 별칭 허용), 같은 호선 열차 바꾸기 = 같은 호선 행 걷기(성수·신도림 2→2, 강동 5→5 지선).
  빠진 환승 쌍은 잇지 않는다.
- 단지 좌표(ComplexCoordinate): 공동주택 아파트 정보 좌표를 K-apt 코드 → 법정동 안 이름 → 지번으로 붙이고, 공간 일치 검사를 통과한 것만 쓴다.
- Python 이 도착역마다 단지별 초(또는 사유 코드)를 끝까지 계산하고, 페이지 JS 는 받아서 세기만 한다(tally_commute 와 같은 규칙).
결과는 '선택역까지의 모형 추정'이며 회사까지 걸음·혼잡·급행은 넣지 않는다. 단지 좌표는 게시하지 않는다.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
import statistics as st
from collections import Counter, defaultdict
from dataclasses import dataclass, field

SUPPORTED_LINES = tuple(str(n) for n in range(1, 10))
WINDOW_FROM_S, WINDOW_TO_S = 7 * 3600, 9 * 3600     # 평일 07:00 이상 09:00 미만 출발
WINDOW_S = WINDOW_TO_S - WINDOW_FROM_S
STITCH_MAX_S = 90          # 앞 편 도착 → 다음 편 출발이 이 안이면 같은 열차(2호선 성수 편명 분리)
MAX_DWELL_S = 1800         # 정차 30분 초과는 원천 이상값
WALK_M_PER_MIN = 72.0      # OA-13290 환승 산정 기준 1.2m/s — 모형 가정
DETOUR = 1.3               # 직선거리 → 걷는 거리 — 모형 가정, 공공 표준 아님
CANDIDATE_RADIUS_M = 1000.0
UNSUPPORTED_MARGIN_M = 200.0
LIMIT_S = 2700             # 45분 — 초 원값으로 판정
UNITS_TOL, YEAR_TOL = 0.15, 1
DONG_RADIUS_M, GU_RADIUS_M, DONG_MIN_SAMPLES = 2000.0, 7000.0, 3
EARTH_R_M = 6371008.8

GAP_NO_COORD, GAP_NO_STATION, GAP_UNSUPPORTED_NEARER, GAP_UNREACHABLE = -1, -2, -3, -4
GAP_TEXT = {GAP_NO_COORD: "좌표 미확인", GAP_NO_STATION: "1km 안 지원 역 없음",
            GAP_UNSUPPORTED_NEARER: "지원 밖 노선이 더 가까움", GAP_UNREACHABLE: "경로 미연결"}
SAME_STATION = {"이수": "총신대입구"}           # 4호선 총신대입구 = 7호선 이수(한 역, 이름 둘)
KORAIL_ALIASES = ("국철", "경원선", "경부선", "경인선")   # 그 역에 1호선이 있을 때만 1호선
_NAME_SUB = (("에스케이", "sk"), ("skview", "sk뷰"), ("sk view", "sk뷰"), ("e편한세상", "이편한세상"), ("e-편한세상", "이편한세상"))


def station_key(name: str) -> str:
    """역 이름 → 물리역 키(공백·괄호 제거, 이름이 둘인 역은 하나로)."""
    n = re.sub(r"\(.*?\)|\s", "", name or "")
    return SAME_STATION.get(n, n)


def _time(text: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", (text or "").strip())
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def walk_seconds(dist_m: float) -> int:
    return int(dist_m * DETOUR / WALK_M_PER_MIN * 60 + 0.5)


# ── 운행시각표 → 운행 ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Stop:
    sid: str
    name: str
    arr: int | None
    dep: int | None


@dataclass(frozen=True)
class Trip:
    line: str
    tag: str
    train: str
    stops: tuple[Stop, ...]


def _trip_ok(stops: list[Stop]) -> bool:
    """편 하나가 쓸 만한지 — 시각 결측·00:00:00·출발<도착·정차 30분 초과·순서 역전이 하나라도 있으면 편 전체를 버린다."""
    n = len(stops)
    if n < 2:
        return False
    for i, s in enumerate(stops):
        if s.arr is None and s.dep is None or 0 in (s.arr, s.dep):
            return False
        if 0 < i < n - 1 and (s.arr is None or s.dep is None):
            return False
        if i == n - 1 and s.arr is None:
            return False
        if s.arr is not None and s.dep is not None and not 0 <= s.dep - s.arr <= MAX_DWELL_S:
            return False
    for a, b in zip(stops, stops[1:]):
        if (b.arr if b.arr is not None else b.dep) < (a.dep if a.dep is not None else a.arr):
            return False
    return True


def parse_trips(rows: list[list[str]]) -> tuple[list[Trip], int]:
    """판의 운행시각표 행 → (쓸 편, 버린 편 수). 행 = [LINE, SI_ID, STATION_NM, INOUTTAG, TRAIN_NO, STT, EDT]."""
    groups: dict[tuple, list[Stop]] = defaultdict(list)
    for line, sid, name, tag, train, stt, edt in rows:
        if line in SUPPORTED_LINES:
            groups[(line, train, tag)].append(Stop(sid, name, _time(stt), _time(edt)))
    trips, bad = [], 0
    for (line, train, tag) in sorted(groups):
        stops = sorted(groups[(line, train, tag)],
                       key=lambda s: (s.arr if s.arr is not None else (s.dep if s.dep is not None else -1), s.sid))
        if _trip_ok(stops):
            trips.append(Trip(line, tag, train, tuple(stops)))
        else:
            bad += 1
    return trips, bad


def stitch_train_runs(trips: list[Trip]) -> tuple[list[tuple[str, str, tuple[Stop, ...]]], int]:
    """편을 실제 열차 운행으로 잇는다 — 같은 호선·방향 표지, X 도착 → X 출발 0~STITCH_MAX_S 초, 되돌아가기 아님. 시각 순 1:1.
    반환 (운행 [(호선, 방향 표지, 정차열)], 이어붙인 수)."""
    ends: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    starts: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for i, t in enumerate(trips):
        last, first = t.stops[-1], t.stops[0]
        ends[(t.line, t.tag, last.sid)].append((last.arr, i))
        if first.dep is not None:
            starts[(t.line, t.tag, first.sid)].append((first.dep, i))
    nxt: dict[int, int] = {}
    used: set[int] = set()
    for key in sorted(ends):
        cands = sorted(starts.get(key, []))
        for arr, i in sorted(ends[key]):
            prev_sid = trips[i].stops[-2].sid
            for dep, j in cands:
                if j in used or j == i or dep < arr:
                    continue
                if dep - arr > STITCH_MAX_S:
                    break
                if trips[j].stops[1].sid == prev_sid:
                    continue
                nxt[i] = j
                used.add(j)
                break
    runs = []
    for i, t in enumerate(trips):
        if i in used:
            continue
        stops = list(t.stops)
        k = i
        while k in nxt:
            k = nxt[k]
            head = trips[k].stops
            stops[-1] = Stop(stops[-1].sid, stops[-1].name, stops[-1].arr, head[0].dep)
            stops.extend(head[1:])
        runs.append((t.line, t.tag, tuple(stops)))
    return runs, len(nxt)


def build_ride_segments(runs: list[tuple[str, str, tuple[Stop, ...]]]) -> dict[tuple[str, str, str, str], tuple[int, int]]:
    """구간 간선 — 창 안에 s 를 출발해 뒤에 t 에 서는 운행마다 (t 도착 − s 출발). {(호선, 방향 표지, s, t): (운행 수, 승차 중위 초)}.
    중위는 짝수 표본이면 가운데 두 값의 평균, 0.5초는 올린다(Codex R3 — 작은 쪽만 쓰던 median_low 가 45분 경계를 바꿨다).
    방향을 나눠 센다 — 2호선 순환은 두 방향 모두 s→t 에 닿아, 섞으면 짧은 쪽과 한 바퀴 도는 쪽의 승차가 한 중위로 뭉친다.
    같은 역으로 돌아오면(순환) 거기서 끊는다."""
    rides: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for line, tag, stops in runs:
        for i, s in enumerate(stops):
            if s.dep is None or not WINDOW_FROM_S <= s.dep < WINDOW_TO_S:
                continue
            seen = set()
            for t in stops[i + 1:]:
                if t.sid == s.sid:
                    break
                if t.sid in seen or t.arr is None:
                    continue
                seen.add(t.sid)
                rides[(line, tag, s.sid, t.sid)].append(t.arr - s.dep)
    return {k: (len(v), int(st.median(v) + 0.5)) for k, v in rides.items()}


def segment_wait(n_runs: int) -> int:
    """배차 간격 절반 — 균등 배차·무작위 도착 가정."""
    return int(WINDOW_S / (2 * n_runs) + 0.5)


# ── 환승표 ─────────────────────────────────────────────────────────────────

def _route_line(route: str, station: str, from_line: str, lines_at: dict[str, set[str]]) -> str | None:
    r = (route or "").strip()
    m = re.fullmatch(r"(\d)호선", r)
    if m:
        return m.group(1) if m.group(1) in SUPPORTED_LINES else None
    if r in KORAIL_ALIASES and from_line != "1" and "1" in lines_at.get(station, ()):
        return "1"
    return None


def resolve_transfer_walks(transfer_files: list[dict], lines_at: dict[str, set[str]]
                           ) -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str], int], list[str]]:
    """환승표(우선순위 순) → (걷기 {(역, A, B): 초}, 지선 걷기 {(역, 호선): 초}, 빠진 쌍 목록).
    우선순위: 최신판 같은 방향 → 최신판 반대 방향 → 옛 판 같은 방향 → 옛 판 반대 방향(같은 통로로 본다).
    출발 호선과 환승 노선이 같은 행(2호선→2호선)은 본선↔지선 승강장 걷기다(Codex R3 — 원천은 '지선'이라 적지 않는다)."""
    found: dict[tuple[str, str, str], dict[tuple[int, int], int]] = defaultdict(dict)
    branch_found: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
    keep = lambda d, k, v: d.__setitem__(k, min(d.get(k, v), v))   # 같은 우선순위 안 중복 행은 짧은 쪽 — 행 순서와 무관하게
    for p, f in enumerate(transfer_files):
        for from_line, name, route, _dist, secs in f["rows"]:
            stn, a = station_key(name), str(from_line).strip()
            b = _route_line(route, stn, a, lines_at)
            if b is None or a not in SUPPORTED_LINES:
                continue
            if a == b:
                keep(branch_found[(stn, a)], p, int(secs))
                continue
            keep(found[(stn, a, b)], (p, 0), int(secs))
            keep(found[(stn, b, a)], (p, 1), int(secs))
    branch = {k: v[min(v)] for k, v in branch_found.items()}
    walks, missing = {}, []
    for stn in sorted(lines_at):
        ls = sorted(lines_at[stn])
        for a in ls:
            for b in ls:
                if a == b:
                    continue
                opts = found.get((stn, a, b))
                if opts:
                    walks[(stn, a, b)] = opts[min(opts)]
                elif a < b:
                    missing.append(f"{stn} {a}-{b}")
    return walks, branch, missing


# ── 역 그래프 ───────────────────────────────────────────────────────────────

@dataclass
class StationGraph:
    """노드: ('B', 호선, 역ID) 승차 / ('A', 호선, 역ID) 도착. 역방향 인접 목록으로 둔다(도착역에서 거꾸로 다익스트라)."""
    rev: dict[tuple, list[tuple[tuple, int]]]
    station_of: dict[tuple[str, str], str]               # (호선, 역ID) → 물리역 키
    sids_at: dict[str, list[tuple[str, str]]]            # 물리역 키 → [(호선, 역ID)]
    segments: dict[tuple[str, str, str, str], tuple[int, int]]   # (호선, 방향 표지, s, t) → (운행 수, 승차 중위)
    meta: dict = field(default_factory=dict)


def build_station_graph(vintage: dict) -> StationGraph:
    trips, bad = parse_trips(vintage["timetable"]["rows"])
    runs, stitched = stitch_train_runs(trips)
    segments = build_ride_segments(runs)
    station_of: dict[tuple[str, str], str] = {}
    for t in trips:
        for s in t.stops:
            station_of[(t.line, s.sid)] = station_key(s.name)
    lines_at: dict[str, set[str]] = defaultdict(set)
    sids_at: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (line, sid), stn in sorted(station_of.items()):
        lines_at[stn].add(line)
        sids_at[stn].append((line, sid))
    walks, branch, missing = resolve_transfer_walks(vintage["transfers"], lines_at)
    rev: dict[tuple, list[tuple[tuple, int]]] = defaultdict(list)
    for (line, _tag, s, t), (n, ride) in sorted(segments.items()):   # 두 방향이 같은 s→t 를 주면 다익스트라가 짧은 쪽을 고른다
        rev[("A", line, t)].append((("B", line, s), segment_wait(n) + ride))
    for (line, sid), stn in sorted(station_of.items()):
        rev[("B", line, sid)].append((("A", line, sid), branch.get((stn, line), 0)))
        for (line2, sid2) in sids_at[stn]:
            w = walks.get((stn, line, line2))
            if line2 != line and w is not None:
                rev[("B", line2, sid2)].append((("A", line, sid), w))
    meta = {"trips": len(trips), "excluded_trips": bad, "runs": len(runs), "stitched": stitched,
            "segments": len(segments), "missing_transfers": missing,
            "timetable_file": vintage["timetable"].get("file", ""),
            "transfer_files": [f.get("file", "") for f in vintage["transfers"]]}
    return StationGraph(dict(rev), station_of, dict(sids_at), segments, meta)


def seconds_to_station(graph: StationGraph, dest: str) -> dict[tuple, int]:
    """도착 물리역까지 남은 최단 초 — 그 역의 도착 노드 전부를 0 으로 두고 역방향 다익스트라. 승차 노드 값만 쓴다."""
    dist: dict[tuple, int] = {}
    heap = [(0, ("A", line, sid)) for line, sid in graph.sids_at.get(dest, [])]
    heapq.heapify(heap)
    while heap:
        d, node = heapq.heappop(heap)
        if node in dist:
            continue
        dist[node] = d
        for prev, w in graph.rev.get(node, ()):
            if prev not in dist:
                heapq.heappush(heap, (d + w, prev))
    return dist


def estimate_station_seconds(graph: StationGraph, origin: str, dest: str) -> int | None:
    """물리역 → 물리역 모형 추정(첫 대기 포함). 같은 역이면 0. 검증·대조용."""
    if origin == dest:
        return 0
    dist = seconds_to_station(graph, dest)
    vals = [dist[("B", line, sid)] for line, sid in graph.sids_at.get(origin, []) if ("B", line, sid) in dist]
    return min(vals) if vals else None


# ── 역 좌표 ────────────────────────────────────────────────────────────────

def station_coordinates(vintage: dict, graph: StationGraph) -> tuple[dict[tuple[str, str], tuple[float, float]], list[tuple[str, float, float]]]:
    """(호선, 역ID) → (위도, 경도). 역사마스터 BLDN_ID = SI_ID, 없으면 같은 물리역 이름의 좌표 중위.
    둘째 값: 지원 밖 물리역 [(이름, 위도, 경도)] — 이름도 ID 도 지원 역과 겹치지 않는 역사마스터 행."""
    by_id = {s[0]: (s[3], s[4]) for s in vintage["stations"]}
    by_name: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s in vintage["stations"]:
        by_name[station_key(s[1])].append((s[3], s[4]))
    coords = {}
    for (line, sid), stn in sorted(graph.station_of.items()):
        if sid in by_id:
            coords[(line, sid)] = by_id[sid]
        elif by_name.get(stn):
            pts = by_name[stn]
            coords[(line, sid)] = (st.median(p[0] for p in pts), st.median(p[1] for p in pts))
    sup_ids = {sid for _, sid in graph.station_of}
    sup_names = set(graph.sids_at)
    unsupported = sorted({(station_key(s[1]), s[3], s[4]) for s in vintage["stations"]
                          if s[0] not in sup_ids and station_key(s[1]) not in sup_names})
    return coords, unsupported


# ── 단지 좌표 ───────────────────────────────────────────────────────────────

def _canon(name: str) -> str:
    from blog import build_explorer as be
    s = (name or "").lower()
    for a, b in _NAME_SUB:
        s = s.replace(a, b)
    return re.sub(r"아파트|apt", "", be.canonical_complex_name(s) or "")


def _name_forms(name: str, gu: str, dong: str) -> set[str]:
    """정규화 이름 + 구·법정동 접두어를 뗀 형태(대치포스코더샵 ↔ 포스코더샵)."""
    c = _canon(name)
    out = {c}
    for p in (re.sub(r"구$", "", gu), re.sub(r"\d*동$|\d+가$", "", dong or ""), (dong or "").replace("동", "")):
        if p and c.startswith(p) and len(c) > len(p) + 1:
            out.add(c[len(p):])
    return {f for f in out if f}


def _ordinals(name: str) -> tuple[str, ...]:
    return tuple(sorted(re.findall(r"(\d+)(?:차|단지)", name or "")))


def _jibun(text: str) -> str | None:
    m = re.fullmatch(r"\s*(\d+)(?:-(\d+))?\s*(?:번지)?\s*", (text or "").strip().rstrip("."))
    return (m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")) if m else None


def _gate(c: dict, apt: list) -> bool:
    """세대수 ±15% 와 사용승인 연도 ±1 — 둘 다 통과해야 이름·지번 매칭을 받는다."""
    u, t = c.get("units") or 0, apt[7] or 0
    y = str(apt[8] or "")[:4]
    return bool(u and t and abs(u - t) / max(u, t) <= UNITS_TOL and y.isdigit() and c.get("built_year")
                and abs(int(y) - int(c["built_year"])) <= YEAR_TOL)


def spatial_outliers(apts: list[list]) -> set[str]:
    """공간 일치 검사 — 법정동 중위(표본 3개 이상)에서 2km 또는 구 중위에서 7km 밖인 APT_CD(원천 좌표 오류: 상암 단지가 청담역 옆 등)."""
    by_dong, by_gu = defaultdict(list), defaultdict(list)
    for a in apts:
        by_dong[(a[2], a[3])].append(a)
        by_gu[a[2]].append(a)
    med = lambda rows: (st.median(r[6] for r in rows), st.median(r[5] for r in rows))   # (위도, 경도)
    dmed = {k: med(v) for k, v in by_dong.items() if len(v) >= DONG_MIN_SAMPLES}
    gmed = {k: med(v) for k, v in by_gu.items()}
    out = set()
    for a in apts:
        dm = dmed.get((a[2], a[3]))
        if dm and haversine_m(a[6], a[5], *dm) > DONG_RADIUS_M or haversine_m(a[6], a[5], *gmed[a[2]]) > GU_RADIUS_M:
            out.add(a[0])
    return out


def _sgg(gu: str) -> str:
    """데이터셋 구 이름('강남'·'중구') → 공동주택 정보 SGG_ADDR('강남구'·'중구')."""
    return gu if gu.endswith("구") else gu + "구"


def match_complex_coordinates(complexes: list[dict], kapt: dict, apts: list[list], molit: dict
                              ) -> tuple[dict[str, tuple[float, float]], dict[str, str], Counter]:
    """단지 → (위도, 경도), 좌표가 붙은 단지 → 법정동, 단계별 집계.
    ① K-apt 검증 코드 ② MOLIT 법정동 안 이름 완전 일치 + 차수 일치 + 게이트 ③ 지번(본번-부번) 완전 일치 + 게이트.
    APT_CD 하나에 단지 둘 이상이면 모두 버리고, 공간 일치 검사에 걸린 좌표도 버린다.
    법정동은 좌표를 준 공동주택 정보 행의 법정동 — 그 구 MOLIT 기록에 없는 이름이면(원천 오기) 이름이 맞은 MOLIT 기록의 최빈 법정동.
    (Codex R3: 흔한 이름 '삼성'이 다른 동 거래에 더 많이 붙어 좌표는 도곡동인데 수서동으로 세던 경로)"""
    from blog import build_explorer as be
    by_cd = {a[0]: a for a in apts}
    by_gd: dict[tuple[str, str], list[list]] = defaultdict(list)
    for a in apts:
        by_gd[(a[2], a[3])].append(a)
    bad_xy = spatial_outliers(apts)
    idx: dict[str, dict] = {}
    picked: dict[str, str] = {}
    molit_dong: dict[str, str] = {}
    known_umd: dict[str, set[str]] = {}
    stats: Counter = Counter()
    for c in complexes:
        cn = str(c["complex_no"])
        lawd = be.GU_LAWD.get(c["gu"])
        recs = molit.get(lawd, []) if lawd else []
        if lawd and lawd not in idx:
            idx[lawd] = be.build_name_index({r["apt"] for r in recs})
            known_umd[lawd] = {r["umd"] for r in recs if r.get("umd")}
        names = be.match_molit_names(c["name"], [], idx[lawd]) if lawd else set()
        mine = [r for r in recs if r["apt"] in names and r.get("umd")]
        if mine:
            cnt = Counter(r["umd"] for r in mine)
            molit_dong[cn] = min(cnt, key=lambda d: (-cnt[d], d))   # 최빈 법정동, 동률이면 가나다순(입력 순서와 무관)
        f = kapt.get(cn) or {}
        code = f.get("kapt_code") if f.get("kapt_verified") else None
        if code and code in by_cd:
            picked[cn] = code
            stats["kapt"] += 1
            continue
        keys = {(r["umd"], r["jibun"]) for r in mine if r.get("jibun")}
        pool = [a for d in sorted({k[0] for k in keys}) for a in by_gd[(_sgg(c["gu"]), d)]]   # Codex R3 이후: '중구'+'구' 로 풀이 비던 경로
        forms = set().union(*(_name_forms(c["name"], c["gu"], d) for d in {k[0] for k in keys})) if keys else set()
        hit = [a for a in pool if _name_forms(a[1], a[2], a[3]) & forms and _ordinals(a[1]) == _ordinals(c["name"]) and _gate(c, a)]
        if len(hit) == 1:
            picked[cn] = hit[0][0]
            stats["name"] += 1
            continue
        full = {k[1] for k in keys}
        hit = [a for a in pool if _jibun(a[4]) in full and _gate(c, a)]
        if len(hit) == 1:
            picked[cn] = hit[0][0]
            stats["jibun"] += 1
            continue
        stats["none"] += 1
    shared = {cd for cd, n in Counter(picked.values()).items() if n > 1}
    lawd_of = {str(c["complex_no"]): be.GU_LAWD.get(c["gu"]) for c in complexes}
    coords, dongs = {}, {}
    for cn, cd in picked.items():
        if cd in shared:
            stats["shared"] += 1
        elif cd in bad_xy:
            stats["spatial"] += 1
        else:
            coords[cn] = (by_cd[cd][6], by_cd[cd][5])
            emd = by_cd[cd][3]
            dong = emd if emd in known_umd.get(lawd_of[cn], ()) else molit_dong.get(cn)
            if dong:
                dongs[cn] = dong
    return coords, dongs, stats


# ── 단지 × 도착역 ───────────────────────────────────────────────────────────

def access_candidates(lat: float, lon: float, station_xy: dict[tuple[str, str], tuple[float, float]]
                      ) -> tuple[list[tuple[str, str, int]], float]:
    """1km 안 지원 역(호선, 역ID, 걷기 초) 전부와 가장 가까운 지원 역까지 직선 m."""
    cands, nearest = [], math.inf
    for (line, sid), (la, lo) in sorted(station_xy.items()):
        d = haversine_m(lat, lon, la, lo)
        nearest = min(nearest, d)
        if d <= CANDIDATE_RADIUS_M:
            cands.append((line, sid, walk_seconds(d)))
    return cands, nearest


def commute_value(cands: list[tuple[str, str, int]], unsupported_nearer: bool, dest: str,
                  dist: dict[tuple, int], station_of: dict[tuple[str, str], str]) -> int:
    """단지 하나 → 도착역 하나의 값: 초(≥0) 또는 사유 코드(음수). 지원 밖 노선이 더 가까운 단지는 45분 초과일 때만 그 사유로 둔다
    (빠진 노선은 시간을 줄일 수만 있으므로 이내 판정은 유지)."""
    if not cands:
        return GAP_UNSUPPORTED_NEARER if unsupported_nearer else GAP_NO_STATION
    best = None
    for line, sid, walk in cands:
        if station_of[(line, sid)] == dest:
            v = walk
        else:
            rest = dist.get(("B", line, sid))
            if rest is None:
                continue
            v = walk + rest
        best = v if best is None or v < best else best
    if best is None:
        return GAP_UNREACHABLE
    if best > LIMIT_S and unsupported_nearer:
        return GAP_UNSUPPORTED_NEARER
    return best


def tally_commute(rows: list[dict], values: dict[str, int], limit: int = LIMIT_S) -> dict:
    """페이지 JS(commuteTally)와 같은 규칙의 집계 — 이내 + 초과 + 없음 = 행 수. 구·법정동은 이내 단지만, 가나다순."""
    out = {"m": len(rows), "within": 0, "over": 0, "gap": {}, "dongs": {}}
    for r in rows:
        v = values.get(r["id"], GAP_NO_COORD)
        if v >= 0 and v <= limit:
            out["within"] += 1
            g = out["dongs"].setdefault(r["gu"], {})
            d = r.get("dong") or "법정동 미상"
            g[d] = g.get(d, 0) + 1
        elif v > limit:
            out["over"] += 1
        else:
            out["gap"][str(v)] = out["gap"].get(str(v), 0) + 1
    out["dongs"] = {g: dict(sorted(ds.items())) for g, ds in sorted(out["dongs"].items())}
    return out


@dataclass(frozen=True)
class CommuteBundle:
    version: str
    row_ids: list[str]            # reach 행 순서(reach_calculator.reach_row_ids)
    stations: list[dict]          # [{"id", "name", "lines"}] 도착역 선택지
    values: dict[str, list[int]]  # 도착역 id → reach 행 순서의 값
    dongs: dict[str, str]
    meta: dict


def build_commute_bundle(complexes: list[dict], row_ids: list[str], vintage: dict, molit: dict, kapt: dict) -> CommuteBundle:
    """reach 행 순서(row_ids)대로 도착역마다 값 배열을 만든다. version = 행 순서·값 전체의 해시(페이지와 파일 결합용)."""
    graph = build_station_graph(vintage)
    station_xy, unsupported = station_coordinates(vintage, graph)
    coords, dongs, cstats = match_complex_coordinates(complexes, kapt, vintage["apts"], molit)
    access: dict[str, tuple[list, bool]] = {}
    for cn in row_ids:
        if cn not in coords:
            continue
        lat, lon = coords[cn]
        cands, nearest_sup = access_candidates(lat, lon, station_xy)
        nearest_uns = min((haversine_m(lat, lon, la, lo) for _, la, lo in unsupported), default=math.inf)
        access[cn] = (cands, nearest_uns + UNSUPPORTED_MARGIN_M <= nearest_sup)
    stations, values = [], {}
    for stn in sorted(graph.sids_at):
        sids = graph.sids_at[stn]
        if not any(("A", line, sid) in graph.rev for line, sid in sids):
            continue
        sid_key = min(sid for _, sid in sids)
        dist = seconds_to_station(graph, stn)
        values[sid_key] = [commute_value(*access[cn], stn, dist, graph.station_of) if cn in access else GAP_NO_COORD
                           for cn in row_ids]
        stations.append({"id": sid_key, "name": stn, "lines": sorted({line for line, _ in sids})})
    h = hashlib.sha256(json.dumps([row_ids, sorted(values.items())], separators=(",", ":")).encode()).hexdigest()[:12]
    meta = dict(graph.meta, coord_stats=dict(sorted(cstats.items())), coords=sum(cn in coords for cn in row_ids), rows=len(row_ids),
                unsupported_stations=len(unsupported), asof=vintage.get("asof"))
    return CommuteBundle(h, list(row_ids), stations, values, {cn: dongs[cn] for cn in row_ids if cn in dongs}, meta)


def commute_file_json(bundle: CommuteBundle, station_id: str) -> str:
    return json.dumps({"v": bundle.version, "t": bundle.values[station_id]}, separators=(",", ":"))
