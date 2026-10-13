"""외부 리뷰(2026-10-13) C14 — 일간 글 첫머리 '오늘 새로 확인'(DailyFreshness).

매일 같은 표·문장이 반복돼 그 날 무엇이 새로 들어왔는지 알 수 없었다. 직전 MOLIT 아카이브·거시 스냅샷과 비교해
새로 나타난 실거래 기록(계약월 분포)·사라진 기록·값이 바뀐 지표·같은 값·수집 실패를 적는다.
"""
import gzip
import json

from blog import daily_digest as dd
from blog import daily_freshness as dfr


def _rec(apt, ym, price=100000, area=59.9):
    return {"apt": apt, "area": area, "price": price, "ym": ym}


def _macro(**vals):
    return {"indicators": {code: {"code": code, "label": label, "value": v, "date": d}
                           for code, (label, v, d) in vals.items()}}


_PREV_MACRO = _macro(kr_govt3y=("국고채 3년", 3.983, "2026-10-08"), cd91=("CD 91일", 3.24, "2026-10-08"),
                     seoul_apt_sale=("서울 아파트 매매가격지수", 102.3, "2026-08-01"))
_CUR_MACRO = _macro(kr_govt3y=("국고채 3년", 3.921, "2026-10-12"), cd91=("CD 91일", 3.24, "2026-10-12"),
                    seoul_apt_sale=("서울 아파트 매매가격지수", 102.3, "2026-08-01"))


def test_compare_macro_splits_changed_values_from_unchanged():
    m = dfr.compare_macro(_PREV_MACRO, _CUR_MACRO)
    assert m["changed"] == ["국고채 3년"]
    assert m["n_unchanged"] == 2


def test_latest_molit_archive_before_today_skips_today(tmp_path):
    for day in ("2026-10-11", "2026-10-12", "2026-10-13"):
        with gzip.open(tmp_path / f"molit-{day}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump({"11710": [_rec(day, "202609")]}, fh)
    got = dfr.latest_molit_archive_before("2026-10-13", str(tmp_path))
    assert got[0] == "2026-10-12" and got[1]["11710"][0]["apt"] == "2026-10-12"
    assert dfr.latest_molit_archive_before("2026-10-11", str(tmp_path)) is None


def _freshness():
    prev = {"11710": [_rec("가", "202510"), _rec("나", "202609")]}
    cur = {"11710": [_rec("나", "202609"), _rec("다", "202609"), _rec("라", "202608"), _rec("마", "202605")]}
    return dfr.build_daily_freshness("2026-10-13", cur, ("2026-10-12", prev), _CUR_MACRO, _PREV_MACRO)


def test_freshness_text_reads_as_confirmed_today_not_contracted_today():
    html = dfr.render_freshness_html(_freshness())
    assert "오늘 새로 확인" in html and "2026-10-12 자료 대비" in html
    assert "새로 나타난 기록 3건" in html
    assert "계약월 2026-08~09 2건" in html and "2026-05 이전 1건" in html
    assert "사라진 기록 1건" in html
    assert "값이 바뀐 지표 1개: 국고채 3년" in html and "직전과 같은 값 2개" in html
    assert "체결" not in html


def test_freshness_without_previous_archive_or_macro_says_not_comparable():
    """R2 Codex — 직전 기준이 없어도 블록을 지우지 않고 비교 불가로 적는다. 오늘 자료가 하나도 없을 때만 생략."""
    html = dfr.render_freshness_html(dfr.build_daily_freshness("2026-10-13", {"11710": []}, None, None, None))
    assert "직전 아카이브 없음(비교 불가)" in html
    assert dfr.build_daily_freshness("2026-10-13", None, None, None, None) is None
    assert dfr.render_freshness_html(None) == ""


def test_daily_digest_puts_freshness_before_ranked_and_summary_blocks():
    rows = [dict(gu="강남", name="가", product_type="아파트", area_m2=59.0, molit_n=15, molit_recent_eok=10.0,
                 molit_trend_dir="▲", molit_trend_pct=3.0, molit_trend_n_recent=3, molit_trend_n_prior=12,
                 molit_pos_52w=100)]
    ds = {"complexes": rows, "count": 1, "data_asof": "2026-10-12", "generated": "2026-10-13"}
    d = dd.build_daily_digest(ds, "2026-10-13", "2026-10-12", freshness=_freshness())
    for body in (d["tistory_html"], d["site_html"]):
        i = body.find("오늘 새로 확인")
        assert i != -1 and i < body.find("30초 브리핑") if "30초 브리핑" in body else i != -1
