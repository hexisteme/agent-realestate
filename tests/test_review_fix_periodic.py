"""외부 리뷰(2026-10-13) — 결산 글 W1(추가·제외 이중 표기)·U11(절 제목 구조).

W1: 짝 키가 (구, 이름, 전용)이라 대표면적이 바뀐 단지가 '새로 포함'과 '기준에만 있던 단지'에 동시에 실렸다
    (10-11 주간: 거여1단지 50→39.5㎡ 등). 단지 신원(구·이름)이 같으면 '대표면적 변경'으로 따로 센다.
U11: Tistory 결산 본문의 절 제목이 <p><b> 라 제목 구조가 없었다 — 일간(afed177)처럼 h3.
"""
from datetime import date

from blog import period_delta as pd


def _r(name, area, price=10.0, gu="송파", n=12):
    return dict(gu=gu, name=name, area_m2=area, molit_recent_eok=price, molit_n=n, product_type="아파트",
                molit_pos_52w=50)


def _ds(rows, asof):
    return {"complexes": rows, "count": len(rows), "data_asof": asof}


def test_area_change_is_not_listed_as_both_added_and_removed():
    base = _ds([_r("거여1단지", 50.0), _r("유지단지", 84.0), _r("빠진단지", 59.0)], "2026-10-04")
    cur = _ds([_r("거여1단지", 39.5), _r("유지단지", 84.0), _r("새단지", 59.0, gu="은평")], "2026-10-11")
    d = pd.build_period_delta(cur, base, "weekly", "2026-10-11", date(2026, 10, 4))
    assert d["added"] == ["새단지(은평)"]
    assert d["removed"] == ["빠진단지(송파)"]
    assert d["area_changed"] == ["거여1단지(송파) 50→39.5㎡"]


def test_membership_identity_uses_complex_no_before_name():
    """R1 합의 W1 — 신원은 complex_no 우선. 이름이 바뀐 같은 단지는 대표면적 변경이고,
    같은 구의 동명 다른 단지(complex_no 다름)는 대표면적 변경으로 묶지 않는다. complex_no 가 없으면 (구,이름)."""
    renamed_b, renamed_c = _r("래미안(구)", 59.0), _r("래미안", 84.0)
    renamed_b["complex_no"] = renamed_c["complex_no"] = "A100"
    twin_b, twin_c = _r("현대", 59.0), _r("현대", 84.0)
    twin_b["complex_no"], twin_c["complex_no"] = "B1", "B2"
    base = _ds([renamed_b, twin_b, _r("번호없음", 50.0)], "2026-10-04")
    cur = _ds([renamed_c, twin_c, _r("번호없음", 39.5)], "2026-10-11")
    d = pd.build_period_delta(cur, base, "weekly", "2026-10-11", date(2026, 10, 4))
    assert d["area_changed"] == ["래미안(송파) 59→84㎡", "번호없음(송파) 50→39.5㎡"]
    assert d["added"] == ["현대(송파)"] and d["removed"] == ["현대(송파)"]


def test_periodic_tistory_body_uses_h3_section_titles_and_area_change_line():
    base = _ds([_r("거여1단지", 50.0), _r("유지단지", 84.0)], "2026-10-04")
    cur = _ds([_r("거여1단지", 39.5), _r("유지단지", 84.0)], "2026-10-11")
    d = pd.build_period_delta(cur, base, "weekly", "2026-10-11", date(2026, 10, 4))
    body = pd._sections(d, inline=True)
    assert "<h3>" in body
    assert "<p><b>" not in body
    assert "대표면적 변경 1" in body
    assert "기준에만 있던 단지 1" not in body


def _rec(apt, ym, price=100000, area=59.9):
    return {"apt": apt, "area": area, "price": price, "ym": ym}


def test_filing_delta_splits_new_by_contract_month_and_gone_by_window():
    """C13 — '새로 확인된 신고 N건'은 그 주 체결 N건이 아니다. 계약월 분포와 창 이탈을 나눠 센다."""
    lawd = "11710"
    prev = {lawd: [_rec("가", "202510"), _rec("가", "202603"), _rec("나", "202609")]}
    cur = {lawd: [_rec("가", "202603"), _rec("나", "202609"),
                  _rec("다", "202610"), _rec("다", "202609", 90000), _rec("라", "202607"), _rec("마", "202604")]}
    f = pd.diff_filings(prev, cur, window_start="202511")          # 수집 창 = 2025-11~2026-10(R2: 관측 최저월 아님)
    assert f["n_new"] == 4
    assert f["new_by_age"] == {"recent": 2, "mid": 1, "old": 1}
    assert f["ref_ym"] == "202610"
    assert (f["n_gone_window"], f["n_gone_other"]) == (1, 0)
    s = pd.summarize_filings(f)
    assert s["new_by_age"] == {"recent": 2, "mid": 1, "old": 1}
    assert (s["n_gone_window"], s["n_gone_other"]) == (1, 0)
