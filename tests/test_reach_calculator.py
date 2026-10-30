"""예산닿는곳계산기(ReachCalculator) — reach.html. DIKW 검토 2026-10-30 P1(케이스1 v1: 통근 없음)."""
import json
import re
import shutil
import subprocess

import pytest

from blog import gu_dikw as gd
from blog import reach_calculator as rc
from blog.wording_guard import assert_wording_ok


def _row(gu, name, eok, n=12, area=84.0, ptype="아파트"):
    return dict(gu=gu, name=name, area_m2=area, product_type=ptype, molit_recent_eok=eok, molit_n=n,
                molit_p25_eok=eok * 0.95 if eok else None, molit_p75_eok=eok * 1.05 if eok else None)


def _ds():
    rows = [_row("노원", f"노원{i}", 4.0 + i) for i in range(6)] + [_row("서초", f"서초{i}", 15.0 + 5 * i) for i in range(5)]
    rows += [_row("서초", "가격없음", None), _row("강남", "강남0", 30.0, n=2)]
    return {"complexes": rows}


def test_grid_is_the_gu_engine_at_every_sampled_point():
    grid = rc.build_reach_grid()
    assert grid["equity_eok"][0] == 0 and grid["equity_eok"][-1] == rc.EQUITY_MAX_EOK
    assert grid["income_man"][0] == 0 and grid["income_man"][-1] == rc.INCOME_MAX_MAN
    for ei in (0, 3, 10, 29, 60, len(grid["equity_eok"]) - 1):
        for ii in (0, 5, 10, 15, len(grid["income_man"]) - 1):
            e, inc = grid["equity_eok"][ei], grid["income_man"][ii]
            want = gd.compute_reachable_price(int(round(e * gd.EOK)), inc * 10_000)
            price_m, loan_man, reason, _ = grid["cells"][ei][ii]
            assert price_m * 1_000_000 == want.price_krw
            assert loan_man == want.loan_krw // 10_000
            assert grid["reasons"][reason] == gd._plain_binding_reason(want.binding, want.price_krw, polite=True)


def test_rows_are_priced_complexes_with_page_links_only_where_pages_exist():
    rows = rc.select_reach_rows(_ds(), written_slugs={"서초-서초0"})
    names = [r["name"] for r in rows]
    assert "가격없음" not in names and len(rows) == 12
    linked = [r for r in rows if r.get("href")]
    assert [r["name"] for r in linked] == ["서초0"] and linked[0]["href"] == "complex/%EC%84%9C%EC%B4%88-%EC%84%9C%EC%B4%880.html"


def test_static_matrix_counts_match_the_engine():
    ds = _ds()
    mat = rc.build_static_matrix(ds["complexes"])
    priced = [r for r in ds["complexes"] if r.get("molit_recent_eok") is not None]
    for (e, inc), (price, k) in mat.items():
        want = gd.compute_reachable_price(e * gd.EOK, inc * 10_000).price_krw
        assert price == want and k == gd.count_reachable_complexes(priced, want)


def test_page_is_plain_guarded_and_states_assumptions():
    html = rc.render_reach_page(_ds(), "2026-10-30", written_slugs=set())
    assert "<title>내 예산으로 닿는 서울 아파트" in html
    for word in ("모아둔 돈", "연소득", "기존 대출", "은행 심사", "토지거래허가구역", gd.POLICY_CHECKED, "15억", "25억"):
        assert word in html, word
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S)
    assert_wording_ok(text, "reach_page")
    for word in ("추천", "매수 권유", "전망", "사도 된다", "살 수 있습니다"):
        assert word not in text, word
    data = json.loads(re.search(r'<script id=reach-data type="application/json">(.*?)</script>', html, re.S).group(1))
    assert set(data) == {"grid", "rows", "dsr_limit", "gus", "farm_edge_m2"}
    assert "<noscript>" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음 — JS 조회 대조 생략")
def test_page_js_lookup_matches_python_engine(tmp_path):
    html = rc.render_reach_page(_ds(), "2026-10-30", written_slugs=set())
    data = re.search(r'<script id=reach-data type="application/json">(.*?)</script>', html, re.S).group(1)
    js = re.search(r"<script id=reach-js>(.*?)</script>", html, re.S).group(1)
    cases = [(3.0, 7000, 0), (15.0, 10000, 0), (5.2, 8300, 1200), (50.0, 40000, 0), (0.3, 500, 0), (12.0, 6000, 2400)]
    harness = (f"const DATA={data};\n{js}\n"
               f"console.log(JSON.stringify({json.dumps(cases)}.map(c=>reachLookup(DATA,c[0],c[1],c[2]))));")
    (tmp_path / "h.js").write_text(harness, encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(tmp_path / "h.js")], capture_output=True, text=True, check=True).stdout)
    for (eq, inc_man, debt_man), got in zip(cases, out):
        e_eok, i_man = rc.snap_inputs(eq, inc_man, debt_man)
        want = gd.compute_reachable_price(int(round(e_eok * gd.EOK)), i_man * 10_000)
        assert got["price_m"] * 1_000_000 == want.price_krw, (eq, inc_man, debt_man)
        assert got["equity_eok"] == e_eok and got["income_man"] == i_man


def test_snap_inputs_round_down_and_debt_reduces_income_for_dsr_only():
    assert rc.snap_inputs(5.27, 8350, 0) == (5.0, 8000)
    assert rc.snap_inputs(5.0, 10000, 1200) == (5.0, 7000)          # 1200만 ÷ 0.4 = 3000만 소득분을 DSR 에서 뺀다
    assert rc.snap_inputs(80.0, 99999, 0) == (rc.EQUITY_MAX_EOK, rc.INCOME_MAX_MAN)
    assert rc.snap_inputs(-1, -5, 99999) == (0, 0)


def test_polite_reason_keeps_meaning_and_switches_register():
    assert gd._plain_binding_reason("LTV", 5 * gd.EOK, polite=True) == "집값의 40%까지만 빌릴 수 있습니다"
    assert gd._plain_binding_reason("절대한도", 20 * gd.EOK, polite=True) == "15억이 넘는 집은 대출이 4억까지만 나옵니다"
    assert gd._plain_binding_reason("스트레스 DSR", 5 * gd.EOK, polite=True).endswith("빌릴 수 있습니다")


def test_rows_mark_non_apartment_types():
    ds = {"complexes": [_row("노원", "가", 5.0), _row("노원", "나", 4.0, ptype="도생")]}
    assert [r["type"] for r in rc.select_reach_rows(ds, set())] == [None, "도생"]


def test_codex_r2_every_data_string_entering_innerhtml_is_escaped():
    # 2026-10-30 사전 검토: 구 막대 라벨이 esc 없이 innerHTML 로 들어가던 경로를 막는다
    js = rc._JS
    assert js.index("var esc=") < js.index("q('reach-gu').innerHTML")
    assert "'+g+'" not in js and "esc(g)" in js


def test_codex_r3_rows_over_85m2_are_judged_with_the_farm_tax_price():
    # Codex R3(2026-10-30): 85㎡ 초과 단지를 84㎡ 세금으로 판정해 필요 현금을 과소계산하던 경로
    grid = rc.build_reach_grid()
    for ei, ii in ((11, 8), (20, 10), (40, 30)):
        e, inc = grid["equity_eok"][ei], grid["income_man"][ii]
        big = gd.compute_reachable_price(int(round(e * gd.EOK)), inc * 10_000, area_m2=gd.TAX_AREA_LARGE_M2)
        assert grid["cells"][ei][ii][3] * 1_000_000 == big.price_krw
        assert big.price_krw <= grid["cells"][ei][ii][0] * 1_000_000
    small = gd.compute_reachable_price(int(5.5 * gd.EOK), 80_000_000)
    big = gd.compute_reachable_price(int(5.5 * gd.EOK), 80_000_000, area_m2=gd.TAX_AREA_LARGE_M2)
    assert big.price_krw < small.price_krw
    edge = big.price_krw + gd.PRICE_STEP_KRW
    rows = [_row("구로", "큰평형", edge / gd.EOK, area=153.0), _row("구로", "작은평형", edge / gd.EOK, area=84.0),
            _row("구로", "면적모름", edge / gd.EOK, area=None)]
    assert rc.count_reach(rows, small.price_krw, big.price_krw) == 1   # 85㎡ 이하만 닿는다, 면적 모름은 보수적으로 큰 평형 취급


def test_codex_r3_static_tables_default_to_apartments_and_say_so():
    # Codex R3: 제목 '서울 아파트'인데 기본 모집단·정적 표에 도생이 섞이던 불일치
    ds = _ds()
    ds["complexes"].append(_row("노원", "도생0", 3.0, ptype="도생"))
    mat = rc.build_static_matrix(ds["complexes"])
    no_dosaeng = [r for r in ds["complexes"] if r.get("product_type") != "도생"]
    assert mat == rc.build_static_matrix(no_dosaeng)
    html = rc.render_reach_page(ds, "2026-10-30", written_slugs=set())
    assert "아파트·주상복합 기준" in html and "도생(도시형생활주택)도 포함" in html
    assert '<option value=all>' in html and "<option value=all selected" not in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음 — JS 표시 대조 생략")
def test_codex_r3_js_and_python_print_the_same_eok_text_for_every_cell(tmp_path):
    # Codex R3: 9.95억이 정적 표 9.9억·JS 10.0억으로 갈리던 반올림 차이 — 둘 다 0.1억 정수 사사오입
    grid = rc.build_reach_grid()
    vals = sorted({c[0] / 100 for row in grid["cells"] for c in row} | {c[1] / 10_000 for row in grid["cells"] for c in row}
                  | {9.95, 2.25, 0.05, 12.349, 47.0})
    js = re.search(r"function eokTxt\(v\)\{.*?\}", rc._JS).group(0)
    (tmp_path / "e.js").write_text(f"{js}\nconsole.log(JSON.stringify({json.dumps(vals)}.map(eokTxt)));", encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(tmp_path / "e.js")], capture_output=True, text=True, check=True).stdout)
    assert out == [gd._eok(round(v * gd.EOK)) for v in vals]
    assert gd._eok(995_000_000) == "10.0억" and gd._eok(994_999_999) == "9.9억" and gd._eok(225_000_000) == "2.3억"
