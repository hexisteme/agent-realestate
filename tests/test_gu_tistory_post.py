"""구 단위 티스토리 포스트(2026-10-20 분할 실험) — 회전·제목·본문·게이트 회귀방지.

배경: 일간 다이제스트 1편이 25구를 모두 담아 제목에 지역·단지 키워드가 없었다(_build_digest_title).
구 단위로 쪼갠 포스트를 하루 N개(기본 5)만 돌려 발행해 구·단지 롱테일 쿼리를 점유하는지 재는 실험.
사이트측 구 포스트는 이미 주 1회(should_write_weekly_posts)라 중복을 늘리지 않는다 — 티스토리 표면만 늘린다.
"""
from pathlib import Path

import pytest

from blog.gu_tistory_post import (
    GU_POSTS_PER_DAY,
    build_gu_post,
    enabled,
    select_rotation_gus,
    write_gu_posts,
)
from blog.tistory_contract import parse_helper
from blog.wording_guard import FORBIDDEN_WORDS

_GUS = [f"구{i:02d}" for i in range(25)]


def _row(gu: str, name: str, **kw) -> dict:
    base = dict(gu=gu, name=name, saeng=f"{gu}구 어딘가", product_type="아파트",
                area_m2=84.0, pyeong=25, molit_n=15, molit_recent_eok=10.0,
                pyeong_price_man=4000, units_band="500세대+", decade="2000년대",
                molit_p25_eok=9.0, molit_p75_eok=11.0,
                molit_trend_dir=None, molit_trend_pct=None, molit_pos_52w=None)
    base.update(kw)
    return base


def _ds(gus=("강남", "서초", "노원", "마포", "송파", "중랑")) -> dict:
    rows = []
    for i, gu in enumerate(gus):
        rows.append(_row(gu, f"{gu}첫단지", molit_recent_eok=20.0 + i))
        rows.append(_row(gu, f"{gu}둘째단지", molit_recent_eok=10.0 + i))
    return {"complexes": rows, "count": len(rows), "data_asof": "2026-10-19", "generated": "2026-10-20"}


# ── 회전(rotation) ──────────────────────────────────────────────────────────
# 2026-10-19 = 월요일. 평일 5일 × 5구 = 25구 → 구마다 정확히 주 1회(사이트 주간 구 포스트와 같은 주기).
_WEEK = ("2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23")


def test_평일_하루_발행수는_5개다():
    assert GU_POSTS_PER_DAY == 5
    for day in _WEEK:
        assert len(select_rotation_gus(_GUS, day)) == 5, day


def test_평일_5일이면_25구를_중복없이_완주한다():
    seen: list[str] = []
    for day in _WEEK:
        seen += select_rotation_gus(_GUS, day)
    assert sorted(seen) == sorted(_GUS), "월~금 × 5구 = 25구 정확히 1회씩"


def test_주말에는_구_포스트가_없다():
    assert select_rotation_gus(_GUS, "2026-10-24") == []   # 토
    assert select_rotation_gus(_GUS, "2026-10-25") == []   # 일


def test_7일_동안_구마다_정확히_1회다():
    from collections import Counter
    from datetime import date, timedelta
    d0 = date.fromisoformat("2026-10-19")
    c = Counter(g for i in range(7) for g in select_rotation_gus(_GUS, (d0 + timedelta(days=i)).isoformat()))
    assert set(c.values()) == {1} and len(c) == 25


def test_같은_날짜는_같은_구를_준다():
    assert select_rotation_gus(_GUS, "2026-10-20") == select_rotation_gus(_GUS, "2026-10-20")


def test_구가_5개_이하면_월요일에_전부_나간다():
    assert select_rotation_gus(["강남", "서초"], "2026-10-19") == ["강남", "서초"]
    assert select_rotation_gus(["강남", "서초"], "2026-10-20") == []


def test_구가_25개를_넘어도_평일_5일에_전부_돈다():
    many = [f"구{i:02d}" for i in range(27)]
    seen = [g for day in _WEEK for g in select_rotation_gus(many, day)]
    assert sorted(seen) == sorted(many)


# ── 제목 ────────────────────────────────────────────────────────────────────
def test_제목에_구명과_대표단지명이_들어간다():
    ds = _ds()
    rows = [r for r in ds["complexes"] if r["gu"] == "노원"]
    post = build_gu_post("노원", rows, "2026-10-20", "2026-10-19")
    assert "노원" in post["title"]
    assert "노원첫단지" in post["title"], "구 안에서 실거래 중위가 가장 큰 단지를 제목에 노출"
    assert "2026-10-20" in post["title"]


def test_제목과_본문에_금칙어가_없다():
    ds = _ds()
    for gu in ("강남", "중랑"):
        rows = [r for r in ds["complexes"] if r["gu"] == gu]
        post = build_gu_post(gu, rows, "2026-10-20", "2026-10-19")
        for w in FORBIDDEN_WORDS:
            assert w not in post["title"], f"제목 금칙어 {w}"
            assert w not in post["body"], f"본문 금칙어 {w}"


def test_제목이_구마다_다르다():
    ds = _ds()
    titles = {
        gu: build_gu_post(gu, [r for r in ds["complexes"] if r["gu"] == gu],
                          "2026-10-20", "2026-10-19")["title"]
        for gu in ("강남", "서초", "노원")
    }
    assert len(set(titles.values())) == 3


# ── 본문 ────────────────────────────────────────────────────────────────────
def test_본문에_면책과_출처와_사이트링크가_있다():
    rows = [r for r in _ds()["complexes"] if r["gu"] == "마포"]
    body = build_gu_post("마포", rows, "2026-10-20", "2026-10-19")["body"]
    assert "투자자문" in body, "면책"
    assert "국토교통부" in body or "국토부" in body, "출처"
    assert "/gu/" in body, "사이트 구 허브로 canonical 유도"
    assert "2026-10-19" in body, "데이터 기준일"


def test_태그에_구_키워드가_들어간다():
    rows = [r for r in _ds()["complexes"] if r["gu"] == "송파"]
    tags = build_gu_post("송파", rows, "2026-10-20", "2026-10-19")["tags"]
    assert "송파" in tags
    assert len([t for t in tags.split(",") if t]) <= 10, "티스토리 태그 상한"


def test_단지가_없는_구는_포스트를_만들지_않는다():
    with pytest.raises(ValueError):
        build_gu_post("없는구", [], "2026-10-20", "2026-10-19")


# ── 출력·플래그 ─────────────────────────────────────────────────────────────
def test_플래그가_꺼지면_아무것도_쓰지_않는다(tmp_path, monkeypatch):
    monkeypatch.delenv("RE_GU_TISTORY_POSTS", raising=False)
    assert enabled() is False
    assert write_gu_posts(_ds(), "2026-10-20", "2026-10-19", str(tmp_path)) == []
    assert not list(tmp_path.rglob("*tistory-draft.html"))


def test_플래그가_켜지면_구별_헬퍼를_쓴다(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_GU_TISTORY_POSTS", "1")
    assert enabled() is True
    paths = write_gu_posts(_ds(), "2026-10-20", "2026-10-19", str(tmp_path))
    assert 1 <= len(paths) <= GU_POSTS_PER_DAY
    for p in paths:
        assert Path(p).exists()
        payload = parse_helper(p)
        assert payload["title"] and payload["tags"] and payload["body"]
    assert len(set(paths)) == len(paths), "구마다 파일명이 달라야 같은 날 여러 편이 공존한다"


# ── 발행 배관(kind=gu-<구명>) ────────────────────────────────────────────────
def test_구_kind_는_발행종류로_인정된다():
    from blog.tistory_delivery import is_publication_kind

    for kind in ("daily", "periodic", "upsize", "gu-노원", "gu-강남"):
        assert is_publication_kind(kind), kind
    for bad in ("gu-", "gu", "gu-../x", "gu-a/b", "unknown", "", None):
        assert not is_publication_kind(bad), bad


def test_구_kind_는_전용_원장경로를_받는다(tmp_path):
    from blog.tistory_delivery import delivery_path

    p = delivery_path(str(tmp_path), "2026-10-20", "gu-노원")
    assert p.name == "2026-10-20-gu-노원.json"
    assert tmp_path in p.parents or str(tmp_path) in str(p)


def test_구_kind_마커는_일간과_분리된다(tmp_path):
    from blog.tistory_publish_pw import marker_paths

    daily = marker_paths(str(tmp_path), "daily")
    gu = marker_paths(str(tmp_path), "gu-노원")
    assert set(daily).isdisjoint(set(gu)), "구 발행이 일간 '오늘 이미 발행' 게이트에 막히지 않아야 한다"


def test_일간_원고선택이_구_원고를_집지_않는다(tmp_path, monkeypatch):
    from blog.tistory_publish_pw import _latest_draft_for_kind

    d = tmp_path / "report/blog/tistory"
    d.mkdir(parents=True)
    (d / "2026-10-20-tistory-draft.html").write_text("x", encoding="utf-8")
    (d / "2026-10-20-gu-노원-tistory-draft.html").write_text("x", encoding="utf-8")
    assert _latest_draft_for_kind(str(tmp_path), "daily").endswith("2026-10-20-tistory-draft.html")
    assert _latest_draft_for_kind(str(tmp_path), "gu-노원").endswith("2026-10-20-gu-노원-tistory-draft.html")
    assert _latest_draft_for_kind(str(tmp_path), "gu-강남") is None



# ── 실데이터 크기(2026-10-20 검증에서 적발: 25구 중 24구가 본문 예산 초과, 최대 134KB) ─────────
def _full_row(gu: str, i: int) -> dict:
    """실제 dataset.json 행과 같은 상세 필드를 채운 행 — 단지당 ~1.2KB 본문을 만든다."""
    return _row(gu, f"{gu}실측단지{i:03d}", molit_recent_eok=30.0 - i * 0.1, molit_n=20 + i,
                molit_trend_dir="▲", molit_trend_pct=1.5, molit_pos_52w=60,
                subway_m=420, cbd_name="강남역", cbd_km=6.3, slope_pct=3.1, land_share_pyeong=12.4,
                floor="15/25", far_pct=249, bcr_pct=18, heating="지역난방", corridor_type="계단식",
                parking_per_unit=1.3, builder="현대건설", tukmokgo_pct=4.2, school_achievement=88.1,
                academy_exam=37, nearest_elem_school="어딘가초", mart_800=2, hosp_800=11, park_1k=3,
                dept_1500=1, gu_jeonse_ratio_pct=52.3, trade_annual=41, review_score=4.1,
                gongsi_man=81200, complex_no=str(100000 + i))


def test_단지_110곳_구도_티스토리_본문_예산에_든다():
    from blog.gu_tistory_post import TISTORY_BUDGET
    rows = [_full_row("노원", i) for i in range(110)]
    post = build_gu_post("노원", rows, "2026-10-20", "2026-10-19")
    assert len(post["body"].encode("utf-8")) <= TISTORY_BUDGET
    assert "110곳" in post["title"], "제목은 표에 담은 수가 아니라 구 전체 수"
    assert "전체 110곳" in post["body"], "표가 일부만 담았다는 사실을 본문에 밝힌다"
    assert "/gu/" in post["body"] and "투자자문" in post["body"], "축약해도 허브 링크·면책은 남는다"


def test_작은_구는_축약하지_않는다():
    rows = [_full_row("종로", i) for i in range(5)]
    body = build_gu_post("종로", rows, "2026-10-20", "2026-10-19")["body"]
    assert "전체 5곳" not in body and body.count("종로실측단지") >= 5


def test_한_단지도_예산에_못_들면_크게_실패한다(monkeypatch):
    import blog.gu_tistory_post as g
    monkeypatch.setattr(g, "TISTORY_BUDGET", 500)
    with pytest.raises(ValueError, match="예산"):
        g.build_gu_post("노원", [_full_row("노원", 0)], "2026-10-20", "2026-10-19")


def test_기준일과_발행일이_같으면_제목에_날짜를_한번만_쓴다():
    rows = [_full_row("마포", i) for i in range(3)]
    title = build_gu_post("마포", rows, "2026-10-20", "2026-10-20")["title"]
    assert title.count("2026-10-20") == 1
