"""도착역 통근 모형(commute_reach) — reach.html 의 도착역 입력. DIKW 검토 2026-10-30 P2(DESIGN-r3.md).

합성 노선으로 규칙을 고정하고, 실제 판(report/blog/snapshots/commute)이 있으면 연결 스캔(CSA) 대조·원천 좌표 오류 고정을 더 돈다.
실제 판 경로: RE_COMMUTE_SNAPSHOT_DIR · RE_MOLIT_SNAPSHOT_DIR · RE_REACH_DATASET · RE_KAPT_FACILITIES(없으면 건너뜀).
"""
import glob
import gzip
import json
import os
import random
import re
import shutil
import subprocess
from collections import defaultdict

import pytest

from agent_realestate.collectors import commute_inputs as ci
from blog import build_explorer as be
from blog import commute_reach as cr
from blog import reach_calculator as rc
from blog.wording_guard import assert_wording_ok


def _hms(s):
    return "" if s is None else f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _trip(line, tag, train, stops):
    """stops = [(역ID, 역명, 도착 초|None, 출발 초|None)] → 판의 운행시각표 행."""
    return [[line, sid, name, tag, train, _hms(a), _hms(d)] for sid, name, a, d in stops]


T7 = 7 * 3600


def _network_rows():
    """1호선 가(101)→나(102)→다(103) 10분 간격 12편 + 2호선 다(201)→라(202) 5분 간격 24편."""
    rows = []
    for k in range(12):
        t = T7 + 600 * k
        rows += _trip("1", "UP", f"1{k:03d}", [("101", "가", None, t), ("102", "나", t + 120, t + 150), ("103", "다", t + 300, None)])
    for k in range(24):
        t = T7 + 120 + 300 * k
        rows += _trip("2", "OUT", f"2{k:03d}", [("201", "다", None, t), ("202", "라", t + 240, None)])
    return rows


def _vintage(rows=None, transfers=None, stations=None, apts=None):
    return {"asof": "2026-10-30",
            "timetable": {"file": "서울교통공사_도시철도열차운행시각표(250930).csv", "rows": rows if rows is not None else _network_rows()},
            "transfers": transfers if transfers is not None else [{"file": "new.csv", "rows": [["1", "다", "2호선", 100, 180]]}],
            "stations": stations if stations is not None else [
                ["101", "가", "1호선", 37.500, 127.000], ["102", "나", "1호선", 37.510, 127.000],
                ["103", "다", "1호선", 37.520, 127.000], ["201", "다", "2호선", 37.5201, 127.0001],
                ["202", "라", "2호선", 37.540, 127.000], ["901", "외부", "신분당선", 37.600, 127.100]],
            "apts": apts if apts is not None else []}


# ── 운행시각표 → 운행 ─────────────────────────────────────────────────────

def test_time_parsing_and_whole_trip_exclusion():
    assert cr._time("07:05:30") == 25530 and cr._time("") is None and cr._time("7:5") is None
    good = _trip("1", "UP", "G", [("101", "가", None, T7), ("102", "나", T7 + 60, T7 + 90), ("103", "다", T7 + 200, None)])
    bad = {
        "middle_missing": _trip("1", "UP", "B1", [("101", "가", None, T7), ("102", "나", T7 + 60, None), ("103", "다", T7 + 200, None)]),
        "midnight": [["1", "101", "가", "UP", "B2", "00:00:00", "07:00:00"], ["1", "102", "나", "UP", "B2", "07:02:00", ""]],
        "dep_before_arr": _trip("1", "UP", "B3", [("101", "가", None, T7), ("102", "나", T7 + 60, T7 + 30), ("103", "다", T7 + 200, None)]),
        "long_dwell": _trip("1", "UP", "B4", [("101", "가", None, T7), ("102", "나", T7 + 60, T7 + 60 + 1801), ("103", "다", T7 + 2000, None)]),
        "overlap": _trip("1", "UP", "B5", [("101", "가", T7, T7 + 300), ("102", "나", T7 + 180, None)]),
        "terminal_no_arr": _trip("1", "UP", "B6", [("101", "가", None, T7), ("102", "나", None, T7 + 60)]),
    }
    trips, n_bad = cr.parse_trips(good + [r for rows in bad.values() for r in rows])
    assert [t.train for t in trips] == ["G"] and n_bad == len(bad)
    ok_dwell = _trip("1", "UP", "D", [("101", "가", None, T7), ("102", "나", T7 + 60, T7 + 60 + 1800), ("103", "다", T7 + 2000, None)])
    assert len(cr.parse_trips(ok_dwell)[0]) == 1                       # 정차 30분 정각은 남긴다


def test_unsupported_lines_are_ignored():
    rows = _trip("A", "UP", "X", [("A1", "공항", None, T7), ("A2", "항공", T7 + 60, None)]) + _network_rows()
    trips, bad = cr.parse_trips(rows)
    assert bad == 0 and {t.line for t in trips} == {"1", "2"}


def _two_halves(gap_s, second_next="Z"):
    a = _trip("2", "OUT", "A", [("1", "P", None, T7), ("2", "X", T7 + 100, None)])
    b = _trip("2", "OUT", "B", [("2", "X", None, T7 + 100 + gap_s), (second_next, "Q" if second_next != "1" else "P", T7 + 250 + gap_s, None)])
    return cr.parse_trips(a + b)[0]


def test_stitch_joins_split_trips_within_90s_only():
    runs, n = cr.stitch_train_runs(_two_halves(90))
    assert n == 1 and len(runs) == 1 and [s.sid for s in runs[0][2]] == ["1", "2", "Z"]
    joint = runs[0][2][1]
    assert joint.arr == T7 + 100 and joint.dep == T7 + 190            # 이음 역은 앞 편 도착 · 뒤 편 출발
    runs, n = cr.stitch_train_runs(_two_halves(91))
    assert n == 0 and len(runs) == 2


def test_stitch_refuses_reversal():
    runs, n = cr.stitch_train_runs(_two_halves(30, second_next="1"))  # 왔던 역으로 되돌아가는 편은 다른 열차
    assert n == 0 and len(runs) == 2


def test_stitch_is_one_to_one_by_time():
    a1 = _trip("2", "OUT", "A1", [("1", "P", None, T7), ("2", "X", T7 + 100, None)])
    a2 = _trip("2", "OUT", "A2", [("1", "P", None, T7 + 20), ("2", "X", T7 + 120, None)])
    b = _trip("2", "OUT", "B", [("2", "X", None, T7 + 130), ("3", "Q", T7 + 250, None)])
    runs, n = cr.stitch_train_runs(cr.parse_trips(a1 + a2 + b)[0])
    assert n == 1 and len(runs) == 2
    assert [s.sid for s in next(stops for _, _, stops in runs if stops[0].dep == T7)] == ["1", "2", "3"]


def test_ride_segments_window_median_and_loop_cut():
    trips, _ = cr.parse_trips(_network_rows())
    runs, _ = cr.stitch_train_runs(trips)
    seg = cr.build_ride_segments(runs)
    assert seg[("1", "UP", "101", "103")] == (12, 300) and seg[("1", "UP", "101", "102")] == (12, 120)
    assert seg[("2", "OUT", "201", "202")] == (24, 240)
    late = _trip("1", "UP", "L", [("101", "가", None, 9 * 3600), ("102", "나", 9 * 3600 + 60, None)])
    runs, _ = cr.stitch_train_runs(cr.parse_trips(late)[0])
    assert cr.build_ride_segments(runs) == {}                           # 09:00 출발은 창 밖
    loop = _trip("2", "IN", "C", [("1", "P", None, T7), ("2", "Q", T7 + 60, T7 + 90), ("1", "P", T7 + 200, T7 + 230), ("3", "R", T7 + 300, None)])
    runs, _ = cr.stitch_train_runs(cr.parse_trips(loop)[0])
    seg = cr.build_ride_segments(runs)
    assert seg[("2", "IN", "1", "3")] == (1, 70) and seg[("2", "IN", "2", "3")] == (1, 210)   # P→Q→P 에서 끊고 둘째 P 에서 다시 센다
    assert seg[("2", "IN", "2", "1")] == (1, 110)


def test_csa_oracle_circle_line_directions_are_not_mixed():
    # 실측(2026-10-30): 2호선 왕십리→잠실 구간에 내선(짧은 쪽)과 외선(한 바퀴) 승차가 한 중위로 섞여 1521초 — 연결 스캔 1115초
    rows = []
    for k in range(10):
        t = T7 + 600 * k
        rows += _trip("2", "IN", f"I{k}", [("1", "가", None, t), ("2", "나", t + 120, t + 150), ("3", "다", t + 300, None)])
    for k in range(12):
        t = T7 + 500 * k
        rows += _trip("2", "OUT", f"O{k}", [("1", "가", None, t), ("4", "라", t + 2000, t + 2030), ("3", "다", t + 4000, None)])
    v = _vintage(rows=rows, transfers=[{"rows": []}],
                 stations=[["1", "가", "2호선", 37.5, 127.0], ["2", "나", "2호선", 37.51, 127.0], ["3", "다", "2호선", 37.52, 127.0],
                           ["4", "라", "2호선", 37.4, 127.0]])
    g = cr.build_station_graph(v)
    assert g.segments[("2", "IN", "1", "3")] == (10, 300) and g.segments[("2", "OUT", "1", "3")] == (12, 4000)
    assert cr.estimate_station_seconds(g, "가", "다") == 360 + 300


def test_segment_wait_is_half_headway():
    assert cr.segment_wait(12) == 300 and cr.segment_wait(24) == 150 and cr.segment_wait(1) == 3600


# ── 환승표 ────────────────────────────────────────────────────────────────

def test_transfer_priority_reverse_older_alias_and_missing():
    lines_at = {"다": {"1", "2", "3"}, "서울역": {"1", "4"}}
    files = [{"rows": [["2", "다", "1호선", None, 150], ["4", "서울역", "경부선", 200, 170], ["1", "서울역", "국철", 10, 5]]},
             {"rows": [["1", "다", "2호선", None, 999], ["2", "다", "3호선", None, 210]]}]
    walks, branch, missing = cr.resolve_transfer_walks(files, lines_at)
    assert walks[("다", "2", "1")] == 150
    assert walks[("다", "1", "2")] == 150                              # 최신판 반대 방향 > 옛 판 같은 방향
    assert walks[("다", "2", "3")] == 210 and walks[("다", "3", "2")] == 210
    assert walks[("서울역", "4", "1")] == 170                           # 경부선 → 그 역의 1호선
    assert ("서울역", "1", "1") not in walks and branch == {}            # 1호선에서 '국철'은 자기 자신
    assert missing == ["다 1-3"]


def test_transfer_duplicates_resolve_independent_of_row_order():
    rows = [["1", "다", "2호선", None, 200], ["1", "다", "2호선", None, 160], ["2", "다", "지선", None, 70], ["2", "다", "지선", None, 50]]
    lines_at = {"다": {"1", "2"}}
    for perm in (rows, rows[::-1]):
        walks, branch, _ = cr.resolve_transfer_walks([{"rows": perm}], lines_at)
        assert walks[("다", "1", "2")] == 160 and branch == {("다", "2"): 50}


def test_branch_walk_charges_same_line_train_change():
    v = _vintage(transfers=[{"rows": [["1", "다", "2호선", None, 180], ["1", "나", "지선", None, 45]]}])
    g = cr.build_station_graph(v)
    assert (("A", "1", "102"), 45) in g.rev[("B", "1", "102")]
    assert (("A", "1", "103"), 0) in g.rev[("B", "1", "103")]


def test_missing_transfer_pair_leaves_no_edge():
    v = _vintage(transfers=[{"rows": []}])
    g = cr.build_station_graph(v)
    assert g.meta["missing_transfers"] == ["다 1-2"]
    assert cr.estimate_station_seconds(g, "가", "라") is None
    assert cr.estimate_station_seconds(g, "가", "다") == 600


# ── 그래프·시간 ───────────────────────────────────────────────────────────

def test_station_seconds_wait_ride_and_transfer():
    g = cr.build_station_graph(_vintage())
    assert cr.estimate_station_seconds(g, "가", "다") == 300 + 300     # 배차 절반 + 승차
    assert cr.estimate_station_seconds(g, "가", "나") == 300 + 120
    assert cr.estimate_station_seconds(g, "가", "라") == 600 + 180 + 150 + 240
    assert cr.estimate_station_seconds(g, "다", "가") is None           # 반대 방향 운행이 없다
    assert cr.estimate_station_seconds(g, "라", "라") == 0
    assert g.meta["trips"] == 36 and g.meta["excluded_trips"] == 0


def test_same_physical_station_with_two_names():
    assert cr.station_key("이수") == "총신대입구" and cr.station_key("총신대입구(이수)") == "총신대입구"
    assert cr.station_key("서울역") == "서울역"


def test_station_coordinates_by_id_then_name_and_unsupported_list():
    v = _vintage(stations=[["101", "가", "1호선", 37.5, 127.0], ["999", "나", "1호선", 37.51, 127.0], ["998", "나", "1호선", 37.53, 127.0],
                           ["901", "외부", "신분당선", 37.6, 127.1], ["902", "다", "신분당선", 37.52, 127.0]])
    g = cr.build_station_graph(v)
    coords, unsupported = cr.station_coordinates(v, g)
    assert coords[("1", "101")] == (37.5, 127.0)
    assert coords[("1", "102")] == pytest.approx((37.52, 127.0))       # ID 가 없으면 같은 이름 좌표 중위
    assert ("1", "103") in coords and ("2", "201") in coords           # '다'는 지원 밖 노선 행이 이름을 대신 준다
    assert unsupported == [("외부", 37.6, 127.1)]                       # 지원 역과 이름이 같은 행은 지원 밖으로 세지 않는다


# ── 단지 × 도착역 값 ──────────────────────────────────────────────────────

def _value(total, unsupported_nearer=False):
    dist = {("B", "1", "101"): total - 100}
    return cr.commute_value([("1", "101", 100)], unsupported_nearer, "다", dist, {("1", "101"): "가"})


def test_limit_boundaries_on_raw_seconds():
    assert [_value(t) for t in (2699, 2700, 2701)] == [2699, 2700, 2701]
    assert [_value(t, True) for t in (2699, 2700, 2701)] == [2699, 2700, cr.GAP_UNSUPPORTED_NEARER]
    rows = [{"id": str(i), "gu": "노원"} for i in range(3)]
    t = cr.tally_commute(rows, {"0": 2699, "1": 2700, "2": 2701})
    assert (t["within"], t["over"]) == (2, 1)


def test_gap_codes():
    so = {("1", "101"): "가", ("1", "102"): "나"}
    assert cr.commute_value([], False, "다", {}, so) == cr.GAP_NO_STATION
    assert cr.commute_value([], True, "다", {}, so) == cr.GAP_UNSUPPORTED_NEARER
    assert cr.commute_value([("1", "101", 50)], False, "다", {}, so) == cr.GAP_UNREACHABLE
    assert cr.commute_value([("1", "101", 50), ("1", "102", 400)], False, "나", {("B", "1", "101"): 30}, so) == 80
    assert cr.commute_value([("1", "102", 400)], False, "나", {}, so) == 400       # 출발역 = 도착역이면 걷기만


def test_access_candidates_radius_and_walk_rule():
    xy = {("1", "101"): (37.5, 127.0), ("1", "102"): (37.52, 127.0)}
    cands, nearest = cr.access_candidates(37.5 + 900 / 111_195, 127.0, xy)
    assert [c[:2] for c in cands] == [("1", "101")] and nearest == pytest.approx(900, abs=1)
    assert cands[0][2] == cr.walk_seconds(nearest) == int(nearest * 1.3 / 72 * 60 + 0.5)


def test_tally_invariant_and_dong_grouping():
    rng = random.Random(7)
    pool = [-1, -2, -3, -4, 0, 600, 2700, 2701, 4000]
    rows = [{"id": str(i), "gu": rng.choice(["노원", "강서"]), "dong": rng.choice(["상계동", None])} for i in range(300)]
    vals = {r["id"]: rng.choice(pool) for r in rows}
    t = cr.tally_commute(rows, vals)
    assert t["within"] + t["over"] + sum(t["gap"].values()) == t["m"] == 300
    assert sum(n for ds in t["dongs"].values() for n in ds.values()) == t["within"]
    assert all("법정동 미상" in ds or "상계동" in ds for ds in t["dongs"].values())
    assert list(t["dongs"]) == sorted(t["dongs"])
    assert cr.tally_commute([{"id": "x", "gu": "노원"}], {})["gap"] == {"-1": 1}   # 값 없는 행은 좌표 미확인


# ── 단지 좌표 ─────────────────────────────────────────────────────────────

LAWD = be.GU_LAWD["노원"]


def _apt(cd, name, lat, lon, units=500, year="2000", daddr="", dong="상계동"):
    return [cd, name, "노원구", dong, daddr, lon, lat, float(units), f"{year}-01-01 00:00:00.0"]


def _cx(no, name, units=500, year=2000):
    return {"complex_no": no, "gu": "노원", "name": name, "units": units, "built_year": year}


def _filler(n=3):
    return [_apt(f"F{i}", f"채움{i}", 37.65 + 0.001 * i, 127.06) for i in range(n)]


def test_coordinate_match_stages_and_gates():
    apts = _filler() + [
        _apt("K1", "코드단지", 37.651, 127.061),
        _apt("N3", "상계주공3단지", 37.652, 127.062),
        _apt("N5", "상계주공5단지", 37.653, 127.063),
        _apt("J1", "번지로만맞는집", 37.654, 127.064, daddr="123-4"),
        _apt("G1", "세대수다름", 37.655, 127.065, units=700),
    ]
    molit = {LAWD: [{"apt": "코드단지", "umd": "상계동", "jibun": "1"},
                    {"apt": "상계주공3단지", "umd": "상계동", "jibun": "300"},
                    {"apt": "모르는이름", "umd": "상계동", "jibun": "123-4"},
                    {"apt": "세대수다름", "umd": "상계동", "jibun": "777"}]}
    cxs = [_cx(1, "코드단지"), _cx(2, "상계주공3단지"), _cx(3, "모르는이름"), _cx(4, "세대수다름")]
    kapt = {"1": {"kapt_code": "K1", "kapt_verified": True}}
    coords, dongs, stats = cr.match_complex_coordinates(cxs, kapt, apts, molit)
    assert coords == {"1": (37.651, 127.061), "2": (37.652, 127.062), "3": (37.654, 127.064)}
    assert stats == {"kapt": 1, "name": 1, "jibun": 1, "none": 1}
    assert dongs["4"] == "상계동"


def test_coordinate_match_rejects_other_ordinal_and_unverified_kapt():
    apts = _filler() + [_apt("P1", "개봉거성푸르뫼1차", 37.651, 127.061, units=249, year="1993")]
    molit = {LAWD: [{"apt": "거성푸르뫼2차", "umd": "상계동", "jibun": "55"}]}
    kapt = {"1": {"kapt_code": "P1", "kapt_verified": False}}
    coords, _, stats = cr.match_complex_coordinates([_cx(1, "거성푸르뫼2차", 245, 1994)], kapt, apts, molit)
    assert coords == {} and stats == {"none": 1}


def test_coordinate_shared_apt_code_drops_both():
    apts = _filler() + [_apt("S1", "공유", 37.651, 127.061)]
    kapt = {"1": {"kapt_code": "S1", "kapt_verified": True}, "2": {"kapt_code": "S1", "kapt_verified": True}}
    coords, _, stats = cr.match_complex_coordinates([_cx(1, "가"), _cx(2, "나")], kapt, apts, {})
    assert coords == {} and stats["shared"] == 2


def test_coordinate_spatial_outlier_is_dropped():
    far = _apt("X1", "멀리찍힘", 37.52, 127.05)                        # 상계동 중위에서 약 15km
    apts = _filler(4) + [far]
    assert cr.spatial_outliers(apts) == {"X1"}
    kapt = {"1": {"kapt_code": "X1", "kapt_verified": True}}
    coords, _, stats = cr.match_complex_coordinates([_cx(1, "멀리찍힘")], kapt, apts, {})
    assert coords == {} and stats["spatial"] == 1


def test_dong_choice_is_mode_with_name_tiebreak():
    molit = {LAWD: [{"apt": "가", "umd": "중계동", "jibun": "1"}, {"apt": "가", "umd": "상계동", "jibun": "2"}]}
    for recs in (molit[LAWD], molit[LAWD][::-1]):
        _, dongs, _ = cr.match_complex_coordinates([_cx(1, "가")], {}, _filler(), {LAWD: recs})
        assert dongs == {"1": "상계동"}


# ── 묶음 ──────────────────────────────────────────────────────────────────

def _bundle_inputs():
    apts = [_apt("A1", "가역앞", 37.5005, 127.0, dong="가동"), _apt("A2", "나역앞", 37.5105, 127.0, dong="가동"),
            _apt("A3", "멀리", 37.4, 126.9, dong="가동"), _apt("A4", "외부옆", 37.6005, 127.1, dong="가동")]
    apts = [a[:2] + ["노원구"] + a[3:] for a in apts]
    v = _vintage(apts=apts)
    cxs = [_cx(i + 1, n) for i, n in enumerate(["가역앞", "나역앞", "멀리", "외부옆", "좌표없음"])]
    kapt = {str(i + 1): {"kapt_code": f"A{i + 1}", "kapt_verified": True} for i in range(4)}
    return cxs, ["1", "2", "3", "4", "5"], v, kapt


def test_bundle_values_codes_and_no_coordinates_leak(monkeypatch):
    monkeypatch.setattr(cr, "spatial_outliers", lambda apts: set())
    cxs, ids, v, kapt = _bundle_inputs()
    b = cr.build_commute_bundle(cxs, ids, v, {}, kapt)
    by_name = {s["name"]: s for s in b.stations}
    assert set(by_name) == {"나", "다", "라"} and by_name["다"]["lines"] == ["1", "2"] and by_name["다"]["id"] == "103"
    to_ra = b.values[by_name["라"]["id"]]
    walk_a1 = cr.walk_seconds(cr.haversine_m(37.5005, 127.0, 37.5, 127.0))
    assert to_ra[0] == walk_a1 + 1170
    assert to_ra[2:] == [cr.GAP_NO_STATION, cr.GAP_UNSUPPORTED_NEARER, cr.GAP_NO_COORD]
    assert b.values[by_name["나"]["id"]][1] == cr.walk_seconds(cr.haversine_m(37.5105, 127.0, 37.51, 127.0))
    files = "".join(cr.commute_file_json(b, s["id"]) for s in b.stations)
    assert "37.5" not in files and "127.0" not in files
    assert b.meta["coords"] == 4 and b.meta["rows"] == 5 and b.meta["unsupported_stations"] == 1


def test_bundle_is_deterministic_and_input_order_invariant(monkeypatch):
    monkeypatch.setattr(cr, "spatial_outliers", lambda apts: set())
    cxs, ids, v, kapt = _bundle_inputs()
    a = cr.build_commute_bundle(cxs, ids, v, {}, kapt)
    assert a == cr.build_commute_bundle(cxs, ids, v, {}, kapt)
    rng = random.Random(3)
    v2 = json.loads(json.dumps(v))
    for key in ("stations", "apts"):
        rng.shuffle(v2[key])
    rng.shuffle(v2["timetable"]["rows"])
    cxs2 = cxs[:]
    rng.shuffle(cxs2)
    b = cr.build_commute_bundle(cxs2, ids, v2, {}, kapt)
    assert b.version == a.version and b.values == a.values
    c = cr.build_commute_bundle(cxs, ids[::-1], v, {}, kapt)          # 행 순서가 바뀌면 판이 바뀐다
    assert c.version != a.version and c.row_ids == ids[::-1]


# ── 페이지 결합 ───────────────────────────────────────────────────────────

def _reach_ds():
    rows = []
    for i in range(5):
        rows.append(dict(complex_no=i + 1, gu="노원", name=["가역앞", "나역앞", "멀리", "외부옆", "좌표없음"][i], area_m2=84.0,
                         product_type="아파트", molit_recent_eok=5.0 + i, molit_n=10, molit_p25_eok=4.8 + i, molit_p75_eok=5.2 + i,
                         units=500, built_year=2000))
    return {"complexes": rows}


def _page_bundle(monkeypatch):
    monkeypatch.setattr(cr, "spatial_outliers", lambda apts: set())
    ds = _reach_ds()
    _, _, v, kapt = _bundle_inputs()
    return ds, cr.build_commute_bundle(ds["complexes"], rc.reach_row_ids(ds), v, {}, kapt)


def _payload(html):
    return json.loads(re.search(r'<script id=reach-data type="application/json">(.*?)</script>', html, re.S).group(1))


def test_page_without_commute_is_unchanged_p1(monkeypatch):
    ds, b = _page_bundle(monkeypatch)
    p1 = rc.render_reach_page(ds, "2026-10-30", set())
    p2 = rc.render_reach_page(ds, "2026-10-30", set(), commute=b)
    assert "commute" not in _payload(p1) and "cline" not in p1 and "commuteTally" not in p1
    d1, d2 = _payload(p1), _payload(p2)
    assert [{k: v for k, v in r.items() if k not in ("id", "dong")} for r in d2["rows"]] == d1["rows"]
    assert {k: v for k, v in d2.items() if k not in ("rows", "commute")} == {k: v for k, v in d1.items() if k != "rows"}


def test_page_commute_payload_and_notice(monkeypatch):
    ds, b = _page_bundle(monkeypatch)
    html = rc.render_reach_page(ds, "2026-10-30", set(), commute=b)
    c = _payload(html)["commute"]
    assert c["v"] == b.version and c["limit"] == 2700 and list(c["lines"]) == ["1", "2"]
    assert c["gap_order"] == ["-1", "-2", "-3", "-4"] and c["gap"]["-1"] == "좌표 미확인"
    assert [r["id"] for r in _payload(html)["rows"]] == b.row_ids
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S)
    assert_wording_ok(text, "reach_commute_page")
    for word in ("모형 추정", "회사까지", "1~9호선", "일반열차", "2025-09-30 시각표", "공공누리", "계산자료 없음"):
        assert word in html, word
    for word in ("추천", "유망", "출퇴근 가능", "통근 가능 지역"):
        assert word not in text, word
    for lat in ("37.5005", "37.5105", "37.6005"):
        assert lat not in html


def test_page_refuses_bundle_with_other_row_order(monkeypatch):
    ds, b = _page_bundle(monkeypatch)
    ds["complexes"] = ds["complexes"][::-1]
    with pytest.raises(ValueError):
        rc.render_reach_page(ds, "2026-10-30", set(), commute=b)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음 — JS 집계 대조 생략")
def test_js_tally_matches_python(tmp_path):
    js = re.search(r"function commuteTally\(hit,vals,limit\)\{.*?return o;\}", rc._JS_COMMUTE, re.S).group(0)
    rng = random.Random(11)
    hit = [{"_i": i, "gu": rng.choice(["노원", "강서", "중구"]), "dong": rng.choice(["상계동", "화곡동", None])} for i in range(400)]
    vals = [rng.choice([-1, -2, -3, -4, 0, 1, 2699, 2700, 2701, 5000]) for _ in range(400)]
    (tmp_path / "t.js").write_text(f"{js}\nconsole.log(JSON.stringify(commuteTally({json.dumps(hit)},{json.dumps(vals)},2700)));",
                                   encoding="utf-8")
    got = json.loads(subprocess.run(["node", str(tmp_path / "t.js")], capture_output=True, text=True, check=True).stdout)
    rows = [{"id": str(h["_i"]), "gu": h["gu"], "dong": h["dong"]} for h in hit]
    want = cr.tally_commute(rows, {str(i): v for i, v in enumerate(vals)})
    got["dongs"] = {g: dict(sorted(ds.items())) for g, ds in sorted(got["dongs"].items())}
    assert got == want


def test_station_option_labels_are_escaped():
    js = rc._JS_COMMUTE
    assert "esc(s[0])" in js and "esc(s[1])" in js and "'+s[1]+'" not in js


# ── 수집기 ────────────────────────────────────────────────────────────────

def test_collector_parsers_and_vintage_io(tmp_path, monkeypatch):
    head = "LINE,SI_ID,STATION_NM,WEEKTAG,INOUTTAG,GUBHANG,TRAIN_NO,STT,EDT\n"
    body = ("1,0150,서울역,DAY,UP,0,K1,06:58:00,06:58:30\n1,0151,시청,DAY,UP,0,K1,07:00:00,\n"
            "1,0150,서울역,SAT,UP,0,K2,07:00:00,07:00:30\n1,0150,서울역,DAY,UP,1,K3,07:00:00,07:00:30\n"
            "1,0150,서울역,DAY,UP,0,K4,13:00:00,13:00:30\nA,0150,서울역,DAY,UP,0,K5,07:00:00,07:00:30\n")
    assert [r[4] for r in ci.parse_timetable(head + body)] == ["K1", "K1"]
    with pytest.raises(ValueError):
        ci.parse_timetable("LINE,SI_ID\n1,2\n")
    assert ci._transfer_secs("1분3초") == 63 and ci._transfer_secs("02:13") == 133 and ci._transfer_secs("45초") == 45
    assert ci._transfer_secs("") is None
    rows = "".join(f"1,역{i},2호선,{100 + i},{i}분 5초\n" for i in range(100))
    parsed = ci.parse_transfers("호선,환승역명,환승노선,환승거리(m),환승소요시간(초)\n" + rows)
    assert parsed[1] == ["1", "역1", "2호선", 101, 65]
    with pytest.raises(ValueError):
        ci.parse_transfers("호선,환승역명,환승노선,환승거리,환승소요시간\n1,역,2호선,10,1분\n")
    d = str(tmp_path)
    assert ci.is_refresh_due("2026-10-30", dir=d) is True
    ci.save_commute_inputs({"asof": "2026-10-01", "x": 1}, dir=d)
    assert ci.is_refresh_due("2026-10-30", dir=d) is False and ci.is_refresh_due("2026-10-31", dir=d) is True
    ci.save_commute_inputs({"asof": "2026-11-05", "x": 2}, dir=d)
    assert ci.load_commute_inputs_latest(dir=d, today="2026-10-30") == {"asof": "2026-10-01", "x": 1}
    assert ci.load_commute_inputs_latest(dir=d)["x"] == 2
    monkeypatch.delenv("SEOUL_OPENAPI_KEY", raising=False)
    with pytest.raises(RuntimeError):
        ci.collect_commute_inputs("2026-10-30", download=lambda s: ("", b""), openapi=lambda s, k: [])


def test_collector_assembles_all_sources_without_partial_vintage(monkeypatch):
    monkeypatch.setenv("SEOUL_OPENAPI_KEY", "test-key")
    tt = ("LINE,SI_ID,STATION_NM,WEEKTAG,INOUTTAG,GUBHANG,TRAIN_NO,STT,EDT\n"
          "1,0150,서울역,DAY,UP,0,K1,06:58:00,06:58:30\n1,0151,시청,DAY,UP,0,K1,07:00:00,\n").encode("cp949")
    tr = ("호선,환승역명,환승노선,환승거리,환승소요시간\n" + "".join(f"1,역{i},2호선,10,1분\n" for i in range(100))).encode("utf-8-sig")
    files = {"OA-22522": ("시각표(250930).csv", tt), "OA-13290": ("환승.csv", tr)}
    api = {"subwayStationMaster": [{"BLDN_ID": "0150", "BLDN_NM": "서울역", "ROUTE": "1호선", "LAT": "37.55", "LOT": "126.97"},
                                   {"BLDN_ID": "X", "BLDN_NM": "좌표없음", "ROUTE": "1호선", "LAT": "", "LOT": ""}],
           "OpenAptInfo": [{"APT_CD": "A1", "APT_NM": "가", "SGG_ADDR": "노원구", "EMD_ADDR": "상계동", "XCRD": "127.06", "YCRD": "37.65",
                            "TNOHSH": 500.0, "USE_APRV_YMD": "2000-01-01"}]}
    v = ci.collect_commute_inputs("2026-10-30", download=lambda s: files[s["infId"]], openapi=lambda s, k: api[s])
    assert v["timetable"]["file"] == "시각표(250930).csv" and len(v["timetable"]["rows"]) == 2
    assert len(v["transfers"]) == 2 and v["stations"] == [["0150", "서울역", "1호선", 37.55, 126.97]]
    assert v["apts"] == [["A1", "가", "노원구", "상계동", "", 127.06, 37.65, 500.0, "2000-01-01"]]
    broken = dict(files, **{"OA-13290": ("환승.csv", "호선,환승역명\n".encode())})
    with pytest.raises(ValueError):
        ci.collect_commute_inputs("2026-10-30", download=lambda s: broken[s["infId"]], openapi=lambda s, k: api[s])


# ── 실제 판(없으면 건너뜀) ─────────────────────────────────────────────────

_REAL_DIR = os.environ.get("RE_COMMUTE_SNAPSHOT_DIR", ci.SNAPSHOT_DIR)


def _real_vintage():
    v = ci.load_commute_inputs_latest(dir=_REAL_DIR)
    if v is None:
        pytest.skip("통근 판 없음 — RE_COMMUTE_SNAPSHOT_DIR")
    return v


@pytest.fixture(scope="module")
def real_graph():
    v = _real_vintage()
    return v, cr.build_station_graph(v)


def _csa_seconds(v, g, origin, dest, t0):
    """연결 스캔 — t0 에 출발역에 선 사람이 도착역에 닿는 가장 이른 시각 − t0. 환승은 같은 환승표 걷기, 같은 호선 갈아타기는 0초."""
    trips, _ = cr.parse_trips(v["timetable"]["rows"])
    runs, _ = cr.stitch_train_runs(trips)
    walks, branch, _ = cr.resolve_transfer_walks(v["transfers"], {k: {l for l, _ in s} for k, s in g.sids_at.items()})
    conns = sorted((a.dep, b.arr, line, a.sid, b.sid, ri) for ri, (line, _tag, stops) in enumerate(runs)
                   for a, b in zip(stops, stops[1:]) if a.dep is not None and b.arr is not None)
    best = defaultdict(lambda: float("inf"))
    for line, sid in g.sids_at[origin]:
        best[(line, sid)] = t0
    on = set()
    for dep, arr, line, s, t, ri in conns:
        if dep < t0:
            continue
        if ri in on or best[(line, s)] <= dep:
            on.add(ri)
            if arr < best[(line, t)]:
                best[(line, t)] = arr
                stn = g.station_of[(line, t)]
                for l2, s2 in g.sids_at[stn]:
                    w = walks.get((stn, line, l2)) if l2 != line else branch.get((stn, line), 0)
                    if w is not None and arr + w < best[(l2, s2)]:
                        best[(l2, s2)] = arr + w
    return min(best[k] for k in g.sids_at[dest]) - t0


@pytest.mark.parametrize("origin,dest", [("서울역", "청량리"), ("구로", "서울역"), ("사당", "서울역"), ("왕십리", "잠실"),
                                         ("노원", "강남"), ("잠실", "광화문"), ("시청", "강남"), ("광화문", "둔촌동")])
def test_real_model_agrees_with_connection_scan(real_graph, origin, dest):
    v, g = real_graph
    model = cr.estimate_station_seconds(g, origin, dest)
    scans = [_csa_seconds(v, g, origin, dest, t) for t in range(T7, T7 + 6600, 600)]
    mean = sum(scans) / len(scans)
    assert abs(model - mean) <= max(120, 0.10 * mean), (origin, dest, model, mean)


def test_real_missing_transfers_are_pinned(real_graph):
    _, g = real_graph
    assert g.meta["missing_transfers"] == ["금정 1-4", "노량진 1-9"]
    assert g.meta["excluded_trips"] <= 10


def _real_all():
    v = _real_vintage()
    ds_path = os.environ.get("RE_REACH_DATASET", "site/dataset.json")
    kapt_path = os.environ.get("RE_KAPT_FACILITIES", "report/enrichment/kapt-facilities.json")
    molits = sorted(glob.glob(os.path.join(os.environ.get("RE_MOLIT_SNAPSHOT_DIR", "report/blog/snapshots/molit"), "molit-*.json.gz")))
    if not (os.path.exists(ds_path) and os.path.exists(kapt_path) and molits):
        pytest.skip("dataset·K-apt·MOLIT 아카이브 없음")
    with gzip.open(molits[-1], "rt", encoding="utf-8") as f:
        molit = json.load(f)
    ds = json.load(open(ds_path, encoding="utf-8"))
    return v, ds, json.load(open(kapt_path, encoding="utf-8")).get("complexes") or {}, molit


def test_real_source_coordinate_errors_and_ordinal_mismatches_stay_out():
    # Codex R2: 원천 좌표 오류(상암월드컵파크4단지 → 청담역 옆, 염창무학 → 금호역 옆) / agy R2: 차수 오매칭(거성푸르뫼2차→1차)
    # agy R2 의 '대호프라자→불광대호2차 오매칭'은 반증 — MOLIT 대호프라자아파트 지번 불광동 631-1 = 공동주택 정보 불광대호2차 지번 631-1,
    # 198세대·2000년 모두 같다(같은 필지, 이름 표기만 다름). 지번 단계 일치로 고정한다.
    v, ds, kapt, molit = _real_all()
    assert {"A12127006", "A15786213"} <= cr.spatial_outliers(v["apts"])
    coords, _, _ = cr.match_complex_coordinates(ds["complexes"], kapt, v["apts"], molit)
    xy = {(a[6], a[5]): a[1] for a in v["apts"]}
    used = {xy.get(c) for c in coords.values()}
    assert not {"상암월드컵파크4단지", "염창무학"} & used
    by_name = {c["name"]: str(c["complex_no"]) for c in ds["complexes"]}
    cn = by_name.get("거성푸르뫼2차")
    if cn in coords:
        assert xy.get(coords[cn]) != "개봉거성푸르뫼1차"
    cn = by_name["대호프라자"]
    assert xy.get(coords.get(cn)) == "불광대호2차"
    a = next(a for a in v["apts"] if a[1] == "불광대호2차")
    assert a[4] == "631-1" and a[7] == 198
