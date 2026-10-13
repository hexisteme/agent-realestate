"""외부 리뷰(2026-10-13) UI — U2·U3·U4·U8·U9·U10 (R1 합의: 04-decisions-r1.md).

U9: 관측 요약과 30초 브리핑이 떨어져 중복됐다 → 상단 한 블록(S1 중복 제외).
U2: 6열 지리타일이 390px에서 57px 칸·4줄 → 티스토리는 구·중위·▲·▼ 목록 표, 사이트는 지도 유지.
U3: 티스토리가 이미지 alt 를 지운다 → 이미지 아래 보이는 설명(기간·시작→최근 값).
U4: 값 표 월 칸 줄바꿈 방지(월 칸만 — 다른 칸 nowrap 은 좁은 화면 넘침 위험, Codex).
U8: 결산 제목이 모바일 첫 화면을 덮는다 → 짧게.
U10: 티스토리 본문 구성 한 줄(앵커 없음 — id 보존 미검증).
"""
import re
from datetime import date

import pytest

from blog import period_delta as pd
from blog import visual_first as vf
from blog.daily_digest import build_daily_digest
from test_daily_digest import _inventory, _sample_ds
from test_market_charts import snapshot

TODAY, ASOF = "2026-09-05", "2026-09-04"


def _digest(tmp_path):
    pytest.importorskip("PIL")
    visual = vf.prepare_visual_first(snapshot("2026-09-05"), TODAY, tmp_path / "images")
    ds = _sample_ds()
    ds["listing_inventory"] = _inventory()
    return build_daily_digest(ds, TODAY, ASOF, visual_first=visual)


def test_u9_briefing_is_merged_into_the_top_summary(tmp_path):
    d = _digest(tmp_path)
    for surface in ("tistory_html", "site_html"):
        body = d[surface]
        assert "30초 브리핑" not in body and "이 페이지에서만 알 수 있는 것" not in body
        assert "추세 판정 가능한" not in body          # S1 은 관측 요약과 같은 숫자 — 중복 제외
        top = body.find("오늘의 관측 요약") if surface == "tistory_html" else body.find("vf-summary")
        img = body.find("<img")
        assert top != -1 and -1 < top < body.find("• ") < img


def test_u2_tistory_uses_a_district_list_not_the_six_column_grid(tmp_path):
    body = _digest(tmp_path)["tistory_html"]
    assert 'data-geo-tiles="districts"' not in body
    table = re.search(r"<table data-district-list.*?</table>", body, flags=re.S).group(0)
    rows = re.findall(r"<tr>(.*?)</tr>", table, flags=re.S)
    assert len(rows) == 26                              # 머리행 + 25개 구
    assert all(len(re.findall(r"<td", r)) == 4 for r in rows)
    site = _digest(tmp_path)["site_html"]
    assert 'data-geo-tiles="districts"' in site       # 사이트는 지도 유지


def test_u3_dashboard_has_visible_caption_with_period_and_values(tmp_path):
    body = _digest(tmp_path)["tistory_html"]
    img_end = body.index(">", body.index("<img"))
    after = body[img_end:img_end + 900]
    assert "data-dashboard-caption" in after
    assert re.search(r"\d{4}-\d{2}~\d{4}-\d{2}", after)
    assert "→" in after


def test_u4_only_month_cells_are_nowrap(tmp_path):
    body = _digest(tmp_path)["tistory_html"]
    table = re.search(r"<table data-dashboard-table.*?</table>", body, flags=re.S).group(0)
    rows = re.findall(r"<tr>(.*?)</tr>", table, flags=re.S)[1:]
    for r in rows:
        cells = re.findall(r'<td style="([^"]*)"', r)
        assert "white-space:nowrap" in cells[0]
        assert all("white-space:nowrap" not in c for c in cells[1:])


def test_u10_tistory_has_an_anchorless_outline_line(tmp_path):
    body = _digest(tmp_path)["tistory_html"]
    m = re.search(r"<p data-outline[^>]*>(.*?)</p>", body, flags=re.S)
    assert m and "→" in m.group(1) and "href=" not in m.group(1)
    assert body.find("data-outline") < body.find("오늘의 관측 요약")


def _r(name, area, price=10.0, gu="송파", n=12):
    return dict(gu=gu, name=name, area_m2=area, molit_recent_eok=price, molit_n=n, product_type="아파트",
                molit_pos_52w=50)


def test_u8_periodic_title_is_short():
    base = {"complexes": [_r("가", 59.0)], "count": 1, "data_asof": "2026-10-04"}
    cur = {"complexes": [_r("가", 59.0)], "count": 1, "data_asof": "2026-10-11"}
    d = pd.build_period_delta(cur, base, "weekly", "2026-10-11", date(2026, 10, 4))
    title = pd.render_period_post(d)["title"]
    assert title == "서울 아파트 주간결산 — 2026-10-11 (10-04 대비)"
