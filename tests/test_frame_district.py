"""collect_frame_district.py 단위테스트(2026-09-06 소재구 확정 신규 스크립트).

대상: gu_stem / build_district_map / fetch_region(캐시 동작) / latest_frame_path.
네트워크 완전 차단 — urllib.request.urlopen 을 monkeypatch 로 막고, 실제로 불리면 테스트가
실패하도록(AssertionError raise) 짜여 있다. 카카오 키·examples 데이터 전부 불필요."""
from __future__ import annotations

import importlib
import json

import pytest


def _m():
    return importlib.import_module("collect_frame_district")


# ── gu_stem ────────────────────────────────────────────────────────────────

def test_gu_stem_strips_gu_suffix_but_keeps_two_char_gu():
    m = _m()
    assert m.gu_stem("구로구") == "구로"
    assert m.gu_stem("중구") == "중구"          # 2자는 유지(제거하면 "중"만 남아 의미 소실)
    assert m.gu_stem("노원구") == "노원"


# ── build_district_map ───────────────────────────────────────────────────
# fetch_region 은 build_district_map 이 모듈 전역으로 참조하므로 monkeypatch.setattr(m, ...) 로 교체하면
# 호출 시점에 대체 함수가 쓰인다(바이트코드가 매 호출마다 모듈 전역을 다시 조회).

def test_build_district_map_dedupes_scan_gus_and_skips_coordless_cno(monkeypatch):
    m = _m()
    calls: list[tuple[float, float]] = []

    def fake_fetch_region(lat, lng, key, cache):
        calls.append((lat, lng))
        return {"sido": "서울특별시", "gu": "노원구", "dong": "하계동"}

    monkeypatch.setattr(m, "fetch_region", fake_fetch_region)

    frame_rows = [
        {"complexNo": 1, "gu": "도봉", "lat": 37.65, "lng": 127.05},
        {"complexNo": 1, "gu": "노원", "lat": 37.65, "lng": 127.05},   # 같은 cno, 다른 스캔 구(중복 유입)
        {"complexNo": 1, "gu": "도봉", "lat": 37.65, "lng": 127.05},   # 같은 스캔 구 재등장(중복 제거 확인)
        {"complexNo": 2, "gu": "강남"},                                 # 좌표 없음 → 항목 자체가 없어야 함(빌드 폴백용)
    ]
    out = m.build_district_map(frame_rows, "fake-key", {})

    assert set(out) == {"1"}                          # 좌표 없는 cno(2)는 폴백을 위해 아예 없어야 함
    assert out["1"]["scan_gus"] == ["도봉", "노원"]      # 중복 없이, 최초 등장 순서 보존
    assert out["1"]["gu"] == "노원"                     # gu_stem 적용된 소재구("노원구"→"노원")
    assert out["1"]["sido"] == "서울특별시"
    assert out["1"]["dong"] == "하계동"
    assert len(calls) == 1                              # cno 1개(좌표 1쌍) → fetch_region 정확히 1회만


def test_build_district_map_skips_cno_when_fetch_region_returns_none(monkeypatch):
    # 카카오 응답 실패(일시장애 재시도까지 소진)로 fetch_region 이 None 을 반환하면 그 cno 는
    # 통째로 스킵되어야 한다 — 부분적으로 잘못된 항목을 만들면 안 됨.
    m = _m()
    monkeypatch.setattr(m, "fetch_region", lambda *a, **k: None)
    frame_rows = [{"complexNo": 9, "gu": "노원", "lat": 1.0, "lng": 2.0}]
    assert m.build_district_map(frame_rows, "fake-key", {}) == {}


# ── fetch_region 캐시 동작 ────────────────────────────────────────────────

def test_fetch_region_cache_hit_with_sido_skips_network(monkeypatch):
    m = _m()
    calls = {"n": 0}

    def fake_urlopen(*a, **k):
        calls["n"] += 1
        raise AssertionError("캐시 히트인데 네트워크(urlopen)를 호출함")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    cache = {"37.65000,127.05000": {"sido": "서울특별시", "gu": "노원구", "dong": "하계동"}}
    out = m.fetch_region(37.65, 127.05, "fake-key", cache)

    assert out == {"sido": "서울특별시", "gu": "노원구", "dong": "하계동"}
    assert calls["n"] == 0


def test_fetch_region_legacy_cache_without_sido_triggers_refetch(monkeypatch):
    # 구버전 캐시(마이그레이션 이전 스키마 — sido 키 없음)는 무효 취급하고 재조회해야 한다.
    m = _m()
    calls = {"n": 0}
    payload = json.dumps({"documents": [
        {"region_type": "B", "region_1depth_name": "서울특별시",
         "region_2depth_name": "노원구", "region_3depth_name": "하계동"},
    ]}).encode("utf-8")

    class _FakeResp:
        def __enter__(self):
            calls["n"] += 1
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp())

    ck = "37.65000,127.05000"
    cache = {ck: {"gu": "노원구"}}          # 구버전 캐시 — sido 키 없음(무효)
    out = m.fetch_region(37.65, 127.05, "fake-key", cache)

    assert calls["n"] == 1                  # 무효 캐시라 재조회(urlopen 1회) 했어야 함
    assert out == {"sido": "서울특별시", "gu": "노원구", "dong": "하계동"}
    assert cache[ck] == out                 # 캐시가 재조회 결과로 갱신됨


# ── latest_frame_path ─────────────────────────────────────────────────────
# latest_frame_path 는 모듈 상수 EX 를 호출 시점에 참조 — monkeypatch 로 EX 를 tmp_path 로 바꿔 격리.

def test_latest_frame_path_raises_systemexit_when_no_files(tmp_path, monkeypatch):
    m = _m()
    monkeypatch.setattr(m, "EX", tmp_path)
    with pytest.raises(SystemExit):
        m.latest_frame_path()


def test_latest_frame_path_returns_lexicographically_last_file(tmp_path, monkeypatch):
    m = _m()
    monkeypatch.setattr(m, "EX", tmp_path)
    (tmp_path / "frame_11gu_20260705.json").write_text("[]", encoding="utf-8")
    (tmp_path / "frame_25gu_20260701.json").write_text("[]", encoding="utf-8")
    (tmp_path / "frame_25gu_20260710.json").write_text("[]", encoding="utf-8")
    assert m.latest_frame_path() == tmp_path / "frame_25gu_20260710.json"
