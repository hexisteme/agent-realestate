"""구 단위 티스토리 포스트(2026-10-20 분할 실험) — 회전·제목·본문·게이트 회귀방지.

배경: 일간 다이제스트 1편이 25구를 모두 담아 제목에 지역·단지 키워드가 없었다(_build_digest_title).
구 단위로 쪼갠 포스트를 하루 N개(기본 5)만 돌려 발행해 구·단지 롱테일 쿼리를 점유하는지 재는 실험.
사이트측 구 포스트는 이미 주 1회(should_write_weekly_posts)라 중복을 늘리지 않는다 — 티스토리 표면만 늘린다.
"""
import gzip
import html
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from blog.gu_tistory_post import (
    GU_CYCLE_DAYS,
    GU_POSTS_PER_DAY,
    build_gu_facts,
    build_gu_post,
    enabled,
    load_week_filings,
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
# 2026-10-27 사용자 결정(카카오 DKAPTCHA 대응): 하루 2구, 주말 포함 14일에 25구 한 바퀴.
# 10-26(월)·10-27(화) 주간 회전이 강남~도봉 10구를 냈으므로 10-28 은 동대문·동작부터 잇는다.
_CYCLE = [(date(2026, 10, 28) + timedelta(days=i)).isoformat() for i in range(14)]


def test_하루_발행수는_2개_이하다():
    assert GU_POSTS_PER_DAY == 2 and GU_CYCLE_DAYS == 14
    for day in _CYCLE:
        assert len(select_rotation_gus(_GUS, day)) <= 2, day


def test_14일이면_25구를_중복없이_완주한다():
    seen = [g for day in _CYCLE for g in select_rotation_gus(_GUS, day)]
    assert sorted(seen) == sorted(_GUS), "14일 × 2구 블록 = 25구 정확히 1회씩"


def test_주말에도_구_포스트가_나간다():
    assert len(select_rotation_gus(_GUS, "2026-10-31")) == 2   # 토
    assert len(select_rotation_gus(_GUS, "2026-11-01")) == 2   # 일


def test_같은_구는_14일마다_같은_요일에_돌아온다():
    for day in _CYCLE:
        later = (date.fromisoformat(day) + timedelta(days=GU_CYCLE_DAYS)).isoformat()
        assert select_rotation_gus(_GUS, day) == select_rotation_gus(_GUS, later), day


def test_10월28일은_도봉_다음_동대문_동작부터_잇는다():
    import blog.build_explorer as be
    assert select_rotation_gus(list(be.GU_LAWD), "2026-10-28") == ["동대문", "동작"]


def test_같은_날짜는_같은_구를_준다():
    assert select_rotation_gus(_GUS, "2026-10-20") == select_rotation_gus(_GUS, "2026-10-20")


def test_구가_2개_이하면_바퀴_첫날에_전부_나간다():
    assert select_rotation_gus(["강남", "서초"], "2026-11-06") == ["강남", "서초"]
    assert select_rotation_gus(["강남", "서초"], "2026-11-07") == []


def test_구가_28개를_넘어도_14일에_전부_돈다():
    many = [f"구{i:02d}" for i in range(31)]
    seen = [g for day in _CYCLE for g in select_rotation_gus(many, day)]
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
    assert "/gu/" in body, "사이트 구 페이지 링크(출처)"
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
    paths = write_gu_posts(_ds(), "2026-10-23", "2026-10-22", str(tmp_path))   # 바퀴 첫날 — 강남·노원
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


# ── 구별 고유 사실 블록(2026-10-21 P1) ─────────────────────────────────────────
# /135~/139 는 표 밖 산문이 편끼리 5-gram Jaccard 0.95(숫자 마스킹)였고 "허브가 정본" 문장이 한 곳으로
# 유도했다 — Google doorway 예시와 같은 형태(report/2026-10-21-blogger-benchmark-followup.md §1.4·§1.5).
# 구마다 그 구에서만 나오는 사실(최근 1주 새 기록·서울 대비 비중·짝 단지 중위·사실 리드)을 표 앞에 싣는다.
def _rec(gu, apt, price=1_000_000_000, ym="202610", area=84.97, day="2026-10-15"):
    return {"gu": gu, "lawd": "", "apt": apt, "area": area, "price": price, "ym": ym, "deal_date": day}


def _week(new=(), highs=(), names=None, skipped=0, base="2026-10-14", ref_ym="202610"):
    new = list(new)
    return {"base_date": base, "apt_names": names or {},
            "filings": {"new": new, "n_new": len(new), "record_highs": list(highs),
                        "ref_ym": ref_ym, "n_lawd_skipped": skipped}}


def test_구_사실에_최근_새_기록_건수와_서울_비중이_들어간다():
    new = [_rec("노원", "노원첫단지")] * 2 + [_rec("노원", "노원소규모빌")] + [_rec("강남", "강남첫단지")] * 7
    week = _week(new, names={"노원": ["노원첫단지", "노원소규모빌"], "강남": ["강남첫단지"]})
    text = " ".join(build_gu_facts("노원", _ds(), None, week)["week"])
    assert "노원구 아파트 매매 기록 3건" in text and "서울 전체 10건의 30%" in text
    assert "2026-10-14 이후" in text, "비교 기준 날짜를 밝힌다"
    assert "체결 건수가 아닙니다" in text, "목록 반영 시점 기준이라는 한계(주간결산 C13 과 같은 고지)"
    assert "노원첫단지 2026-10 84.97㎡ 10억 ×2" in text, "집계 대상 단지의 새 기록은 계약월·전용·금액으로 싣는다"
    assert "노원소규모빌" not in text, "표(발행 풀) 밖 단지명은 싣지 않는다 — 세대수·안전제외 기준 유지"
    assert "강남첫단지" not in text


def test_구_사실의_최고가_넘긴_기록은_그_구_표_안_단지만():
    highs = [{"gu": "노원", "apt": "노원첫단지", "area": 84.97, "price": 1_250_000_000,
              "prev_max": 1_200_000_000, "n_prev": 6},
             {"gu": "노원", "apt": "노원소규모빌", "area": 59.9, "price": 700_000_000,
              "prev_max": 650_000_000, "n_prev": 4},
             {"gu": "강남", "apt": "강남첫단지", "area": 84.9, "price": 3_000_000_000,
              "prev_max": 2_900_000_000, "n_prev": 9}]
    week = _week([_rec("노원", "노원첫단지")], highs,
                 names={"노원": ["노원첫단지", "노원소규모빌"], "강남": ["강남첫단지"]})
    text = " ".join(build_gu_facts("노원", _ds(), None, week)["week"])
    assert "노원첫단지 84.97㎡ 12.5억(기준 최고 12억, n6)" in text
    assert "노원소규모빌" not in text and "강남첫단지" not in text


def test_새_기록이_없는_구는_없다고_적는다():
    week = _week([_rec("강남", "강남첫단지")] * 4, names={"강남": ["강남첫단지"]})
    text = " ".join(build_gu_facts("노원", _ds(), None, week)["week"])
    assert "노원구 아파트 매매 기록은 없습니다" in text and "서울 전체 4건" in text


def _ds_paired(n: int, bump: float = 0.0, generated: str = "2026-10-21") -> dict:
    rows = [_row("노원", f"노원짝단지{i}", molit_recent_eok=10.0 + i + bump) for i in range(n)]
    rows += [_row("강남", f"강남짝단지{i}", molit_recent_eok=20.0 + i) for i in range(n)]
    return {"complexes": rows, "count": len(rows), "data_asof": generated, "generated": generated}


def test_짝_단지가_5곳_이상이면_7일전_대비_구_중위를_싣는다():
    prev = _ds_paired(6, generated="2026-10-14")
    text = " ".join(build_gu_facts("노원", _ds_paired(6, bump=0.5), prev, None)["week"])
    assert "2026-10-14 스냅샷" in text
    assert "짝지은 6단지(이 구 발행 6단지 중) 12개월 중위의 중위 12.5억 → 13억(+4.0%)" in text, \
        "짝 수와 구 전체 수를 함께 밝혀 구 전체 통계로 읽히지 않게 한다(agy P3)"


def test_서울_비중이_반올림해_0이면_1퍼센트_미만으로_적는다():
    new = [_rec("노원", "노원첫단지")] + [_rec("강남", "강남첫단지")] * 299
    text = " ".join(build_gu_facts("노원", _ds(), None, _week(new, names={"노원": ["노원첫단지"]}))["week"])
    assert "서울 전체 300건의 1% 미만" in text and "의 0%" not in text


def test_짝_단지가_5곳_미만이면_구_중위_변화를_싣지_않는다():
    prev = _ds_paired(4, generated="2026-10-14")
    assert build_gu_facts("노원", _ds_paired(4, bump=0.5), prev, None)["week"] == []


def test_본문에_정본_유도_문장이_없고_사이트_링크는_출처로_남는다():
    rows = [r for r in _ds()["complexes"] if r["gu"] == "마포"]
    body = build_gu_post("마포", rows, "2026-10-20", "2026-10-19")["body"]
    assert "정본" not in body
    assert body.index("/gu/") > body.index("방법론·출처"), "구 페이지 링크는 출처 목록 안에 둔다"


def test_구_사실_블록은_표보다_앞에_온다():
    ds = _ds()
    rows = [r for r in ds["complexes"] if r["gu"] == "노원"]
    facts = build_gu_facts("노원", ds, None, _week([_rec("노원", "노원첫단지")], names={"노원": ["노원첫단지"]}))
    body = build_gu_post("노원", rows, "2026-10-20", "2026-10-19", facts=facts)["body"]
    assert "노원구 최근 1주 기록" in body and "노원첫단지 2026-10 84.97㎡ 10억" in body
    assert body.index("최근 1주 기록") < body.index("<table")
    assert facts["leads"], "사이트 구 허브와 같은 사실 리드(fact_lead)를 싣는다"
    assert body.index(html.escape(facts["leads"][0])) < body.index("<table")


def _archive(tmp_path, day: str, recs: list[dict], cur_recs: list[dict]) -> Path:
    from blog import build_explorer as be
    lawd = be.GU_LAWD["노원"]
    d = tmp_path / "snapshots/molit"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"molit-{day}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({lawd: recs}, fh)
    cur = tmp_path / "molit.json"
    cur.write_text(json.dumps({lawd: cur_recs}), encoding="utf-8")
    return cur


_OLD = {"apt": "노원첫단지", "area": 84.97, "price": 900_000_000, "ym": "202609", "deal_date": "2026-09-20"}
_NEW = {"apt": "노원첫단지", "area": 84.97, "price": 1_000_000_000, "ym": "202609", "deal_date": "2026-09-25"}   # 10월 중 수집 창의 마지막 월


def test_7일전_원본_아카이브가_없으면_최근_기록을_내지_않는다(tmp_path):
    cur = tmp_path / "molit.json"
    cur.write_text(json.dumps({"11350": [_NEW]}), encoding="utf-8")
    assert load_week_filings("2026-10-21", str(cur), str(tmp_path / "snapshots")) is None


def test_7일전_원본_아카이브가_있으면_새_기록과_기준일을_낸다(tmp_path):
    cur = _archive(tmp_path, "2026-10-14", [_OLD], [_OLD, _NEW])
    week = load_week_filings("2026-10-21", str(cur), str(tmp_path / "snapshots"))
    assert week["base_date"] == "2026-10-14"
    assert week["filings"]["n_new"] == 1 and week["filings"]["new"][0]["gu"] == "노원"
    assert week["apt_names"]["노원"] == ["노원첫단지"]


def test_기준_아카이브가_하루_빠뜨린_기록은_새_기록도_최고가_기준도_아니다(tmp_path):
    """상류 일시 결손(2026-10-15·16 국토부 API 송파 2026-09: 292→127→132→305건) — 기준일까지 1주 아카이브에
    있던 기록은 '새로 나타난' 것이 아니고, 최고가 비교 기준에도 든다."""
    def rec(price, day, ym="202609"):
        return {**_OLD, "price": price, "ym": ym, "deal_date": day}
    hist = [rec(900_000_000, "2026-09-01"), rec(950_000_000, "2026-09-02"), rec(960_000_000, "2026-09-03")]
    dropped = rec(1_100_000_000, "2026-09-04")
    cur = _archive(tmp_path, "2026-10-13", hist + [dropped], hist + [dropped, _NEW])
    _archive(tmp_path, "2026-10-14", hist, hist + [dropped, _NEW])          # 기준일 아카이브만 dropped 결손
    week = load_week_filings("2026-10-21", str(cur), str(tmp_path / "snapshots"))
    assert week["base_date"] == "2026-10-14"
    assert [r["price"] for r in week["filings"]["new"]] == [_NEW["price"]]
    assert week["filings"]["record_highs"] == [], "11억이 1주 안에 있었으므로 10억은 최고가 경신이 아니다"


def test_합집합은_계약일_유무가_섞여도_같은_기록을_두번_세지_않는다(tmp_path):
    """한 아카이브만 계약일이 없으면 diff_filings 가 계약일 없는 키로 비교한다 — 합집합도 같은 키로 묶어야 한다(Codex R1)."""
    no_day = {k: v for k, v in _OLD.items() if k != "deal_date"}
    later = [{**_OLD, "deal_date": d} for d in ("2026-09-21", "2026-09-22")]
    cur = _archive(tmp_path, "2026-10-13", [no_day], [_OLD] + later)
    _archive(tmp_path, "2026-10-14", [_OLD], [_OLD] + later)
    week = load_week_filings("2026-10-21", str(cur), str(tmp_path / "snapshots"))
    assert week["filings"]["n_new"] == 2


def test_합집합은_기준일_수집_창_밖의_월을_최고가_기준에_넣지_않는다(tmp_path):
    """09-30 아카이브(창 2025-09~2026-08)의 2025-09 기록은 10-01 기준일 12개월 표본이 아니다(Codex R1)."""
    def rec(price, ym, day):
        return {**_OLD, "price": price, "ym": ym, "deal_date": day}
    base = [rec(900_000_000, "202609", "2026-09-01"), rec(950_000_000, "202609", "2026-09-02")]
    new = rec(1_000_000_000, "202609", "2026-09-25")
    cur = _archive(tmp_path, "2026-09-30", [rec(800_000_000, "202509", "2025-09-10")], base + [new])
    _archive(tmp_path, "2026-10-01", base, base + [new])
    week = load_week_filings("2026-10-08", str(cur), str(tmp_path / "snapshots"))
    assert week["base_date"] == "2026-10-01"
    assert [r["price"] for r in week["filings"]["new"]] == [1_000_000_000]
    assert week["filings"]["record_highs"] == [], "기준일 12개월 표본은 2건 — 최고가 비교 게이트(3건) 미달"


def test_수집_창에_새로_든_월의_기록은_새_기록에서_빼고_따로_적는다(tmp_path):
    """월이 바뀌면 수집기가 직전 달을 처음 담는다 — 기준일 자료에 없던 월이라 '새로 나타난' 기록이 아니다
    (2026-10-04 주간결산 '새로 확인된 신고 2335건' 중 2239건이 창에 새로 든 2026-09)."""
    entered = {**_OLD, "price": 1_200_000_000, "ym": "202610", "deal_date": "2026-10-05"}
    cur = _archive(tmp_path, "2026-10-26", [_OLD], [_OLD, _NEW, entered])
    week = load_week_filings("2026-11-02", str(cur), str(tmp_path / "snapshots"))
    assert [r["price"] for r in week["filings"]["new"]] == [_NEW["price"]]
    text = " ".join(build_gu_facts("노원", _ds(), None, week)["week"])
    assert "2026-10 계약 1건" in text and "12억" not in text


def test_7일전_아카이브가_잘린_gzip이면_새_기록만_빼고_실행을_막지_않는다(tmp_path, capsys):
    cur = _archive(tmp_path, "2026-10-14", [_OLD] * 50, [_OLD, _NEW])
    gz = tmp_path / "snapshots/molit/molit-2026-10-14.json.gz"
    gz.write_bytes(gz.read_bytes()[:-12])
    assert load_week_filings("2026-10-21", str(cur), str(tmp_path / "snapshots")) is None
    assert "최근 1주 새 기록 생략" in capsys.readouterr().out


def test_오늘_원본이_구별_사전이_아니면_새_기록만_뺀다(tmp_path):
    cur = _archive(tmp_path, "2026-10-14", [_OLD], [_OLD, _NEW])
    cur.write_text(json.dumps([_OLD, _NEW]), encoding="utf-8")
    assert load_week_filings("2026-10-21", str(cur), str(tmp_path / "snapshots")) is None


def test_플래그가_켜지면_구_원고에_사실_블록이_실린다(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_GU_TISTORY_POSTS", "1")
    cur = _archive(tmp_path, "2026-10-16", [_OLD], [_OLD, _NEW])
    paths = write_gu_posts(_ds(), "2026-10-23", "2026-10-23", str(tmp_path), molit_path=str(cur))   # 바퀴 첫날 — 강남·노원
    body = parse_helper(next(p for p in paths if "-gu-노원-" in p))["body"]
    assert "노원구 최근 1주 기록" in body and "노원첫단지 2026-09 84.97㎡ 10억" in body


def test_새_기록은_최근_계약월부터_상한까지_싣고_나머지는_건수로():
    from blog.gu_tistory_post import _RECORD_LIMIT
    new = [_rec("노원", "노원첫단지", price=1_000_000_000 + i * 10_000_000, ym="202609") for i in range(_RECORD_LIMIT)]
    new += [_rec("노원", "노원둘째단지", ym="202610"), _rec("노원", "노원둘째단지", ym="202608")]
    text = " ".join(build_gu_facts("노원", _ds(), None, _week(new, names={"노원": ["노원첫단지", "노원둘째단지"]}))["week"])
    assert text.index("노원둘째단지 2026-10") < text.index("노원첫단지 2026-09"), "최근 계약월 먼저"
    assert "노원둘째단지 2026-08" not in text and "외 2건" in text
