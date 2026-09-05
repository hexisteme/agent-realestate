"""collect_gongsi.py 신원게이트(verify_parcel_identity)·_molit_median_won 단위/종단 테스트
(2026-09-05 gongsi-gate-fix) — 네트워크 없음(_fetch_vworld_all·K-apt basis·파일경로 전부 monkeypatch).

대상: collect_gongsi.verify_parcel_identity / _identity_fail_reason / _molit_median_won / main().
배경: fc38934 가 build_explorer.py 의 부분일치 매칭결함을 고쳤지만 collect_gongsi.py 는 여전히
구 fuzzy 상호포함 게이트(_name_gate)·구 부분일치 가드중위(_molit_median_won)를 쓰고 있었다 — 이
테스트는 그 대체(verify_parcel_identity = canonical 완전일치 또는 ≥5자 단방향 포함+레코드수/세대수
일치, _molit_median_won = match_molit_names 재사용)가 실제로 오매칭을 막는지 증명한다."""
from __future__ import annotations

import json

import pytest

import collect_gongsi as cg


# ── verify_parcel_identity / _identity_fail_reason ──────────────────────

def test_sanggye_2danji_vs_gochung_qualifier_passes_via_containment():
    # "(고층)"은 build_explorer._PAREN_NON_IDENTITY 로 제거되는 비식별 qualifier — 다만 "단지" 접미가
    # 한쪽에만 있어 완전일치는 아니고 단방향 포함(canonical("상계주공2(고층)")="상계주공2" ⊂
    # canonical("상계주공2단지"))으로 통과한다 — 레코드수/세대수 일치 조건까지 만족해야 함.
    assert cg.verify_parcel_identity("상계주공2단지", "상계주공2(고층)", 54, 54) is True
    assert cg._identity_fail_reason("상계주공2단지", "상계주공2(고층)", 54, 54) is None


def test_equality_raemian_areumsup_space_vs_apt_suffix_passes():
    # 공백·"아파트" 접미 차이만 — canonical_complex_name 이 둘 다 "래미안아름숲"으로 접어 완전일치.
    # 완전일치는 units/record_count 와 무관하게 통과해야 한다(카운트 게이트 이전에 단락).
    assert cg.verify_parcel_identity("래미안 아름숲", "래미안아름숲아파트", 0, 0) is True
    assert cg._identity_fail_reason("래미안 아름숲", "래미안아름숲아파트", 0, 0) is None


def test_equality_ipark_latin_korean_variant_passes():
    # IPARK↔아이파크 — build_explorer._fold_brand_variants 로 완전일치(카운트 무관 통과).
    assert cg.verify_parcel_identity("노원IPARK", "노원아이파크", 1, 0) is True


def test_generic_short_name_fails_even_with_consistent_count():
    # "현대"(2자, <5) — 컨테인먼트 자격 미달로 카운트가 아무리 맞아도 통과 불가(제네릭 흡수 방지).
    assert cg.verify_parcel_identity("현대", "현대힐스테이트2단지", 200, 200) is False
    assert cg._identity_fail_reason("현대", "현대힐스테이트2단지", 200, 200) == "name-mismatch"


def test_containment_passes_only_with_consistent_record_count():
    # "한아름더샵"(정규화 5자) ⊂ "한아름더샵타워"(7자, 접두 포함) — 세대수 대비 레코드수 25% 이내면 통과.
    assert cg.verify_parcel_identity("한아름더샵타워", "한아름더샵", 95, 100) is True
    assert cg._identity_fail_reason("한아름더샵타워", "한아름더샵", 95, 100) is None


def test_containment_fails_with_inconsistent_record_count():
    # 이름은 포함관계(5자 이상)지만 레코드수(호수)가 세대수와 크게 어긋나면(타 필지 오조립 신호) 실패.
    assert cg.verify_parcel_identity("한아름더샵타워", "한아름더샵", 5, 500) is False
    assert cg._identity_fail_reason("한아름더샵타워", "한아름더샵", 5, 500) == "count-mismatch"


def test_containment_fails_when_units_is_zero():
    # units=0(세대수 미보유) 이면 비교 기준이 없으므로 컨테인먼트 경로 자체를 불허.
    assert cg.verify_parcel_identity("한아름더샵타워", "한아름더샵", 95, 0) is False
    assert cg._identity_fail_reason("한아름더샵타워", "한아름더샵", 95, 0) == "count-mismatch"


def test_containment_refuses_short_normalized_name_below_5_chars():
    # 정규화 후 4자 이하는 세대수/레코드수가 완벽히 일치해도 컨테인먼트 자격 없음(일반 단지코드
    # 흡수 방지 — "현대"류 제네릭 브랜드명이 다른 단지를 흡수하던 결함의 재발방지).
    assert cg.verify_parcel_identity("한아름더샵타워", "한아름더", 100, 100) is False
    assert cg._identity_fail_reason("한아름더샵타워", "한아름더", 100, 100) == "name-mismatch"


def test_empty_names_never_pass():
    assert cg.verify_parcel_identity("", "", 100, 100) is False
    assert cg.verify_parcel_identity("", "상계주공2단지", 100, 100) is False


# ── _molit_median_won ────────────────────────────────────────────────────

def test_molit_median_won_uses_only_matching_complex_records():
    # 1/2/3단지가 같은 lawd 에 공존 — canonical 완전일치가 아니면 절대 섞이면 안 됨(구 4자 prefix
    # 부분일치 결함의 재발방지 회귀테스트: 상계주공1~3단지 뭉침).
    molit = {"11350": (
        [{"apt": "상계주공1단지", "area": 59.0, "price": int(3.0e8)} for _ in range(5)]
        + [{"apt": "상계주공2단지", "area": 59.0, "price": int(6.0e8)} for _ in range(5)]
        + [{"apt": "상계주공3단지", "area": 59.0, "price": int(9.0e8)} for _ in range(5)]
    )}
    med = cg._molit_median_won("서울 노원구", "상계주공2단지", 59.0, molit)
    assert med == int(6.0e8)


def test_molit_median_won_outlier_cut_matches_median_of_helper():
    # build_explorer._median_of 와 동일한 이상치(<median*0.6) 컷 — 극단 저가 1건은 제외되어야 함.
    molit = {"11350": (
        [{"apt": "상계주공2단지", "area": 59.0, "price": int(6.0e8)} for _ in range(4)]
        + [{"apt": "상계주공2단지", "area": 59.0, "price": int(1.0e8)}]  # median*0.6=3.6억 미만 → 컷
    )}
    med = cg._molit_median_won("서울 노원구", "상계주공2단지", 59.0, molit)
    assert med == int(6.0e8)


def test_molit_median_won_unknown_district_returns_none():
    assert cg._molit_median_won("서울 없는구", "상계주공2단지", 59.0, {"11350": []}) is None


# ── main() 종단 — 3단지 합성 universe, monkeypatch 로 네트워크 완전 차단 ──────────

@pytest.fixture
def _no_sleep(monkeypatch):
    monkeypatch.setattr(cg.time, "sleep", lambda *_a, **_k: None)


def test_main_end_to_end_mismatched_pnu_yields_none_and_matched_yields_median(
    tmp_path, monkeypatch, _no_sleep,
):
    # (1) 정상: kaptName·aphusNm 완전일치, 레코드 50개(평형 59.0, 3억) → gongsi_man=중위/만원,
    #     타당성가드용 MOLIT 중위(6억) 대비 ratio=0.5 로 20~90% 범위 안 → 유지.
    # (2) 오조립(mis-assembled pnu): 이 단지 kaptName="가짜단지" 인데 VWorld aphusNm 은 전혀 다른
    #     "완전히다른단지" 이고 레코드수(3)도 세대수(800)와 무관 → 신원게이트 실패 → gongsi_man=None.
    # (3) 필터: kapt_verified=False → 네트워크 호출 없이 즉시 gongsi_man=None(cnt_no_code 경로).
    universe = [
        {"complex_name": "상계주공2단지", "area_exclusive_m2": 59.0, "kapt_code": "K_GOOD",
         "kapt_verified": True, "district": "서울 노원구", "units": 54},
        {"complex_name": "가짜단지", "area_exclusive_m2": 59.0, "kapt_code": "K_BAD",
         "kapt_verified": True, "district": "서울 노원구", "units": 800},
        {"complex_name": "미검증단지", "area_exclusive_m2": 59.0, "kapt_code": None,
         "kapt_verified": False, "district": "서울 노원구", "units": 300},
    ]
    molit = {"11350": [
        {"apt": "상계주공2단지", "area": 59.0, "price": int(6.0e8)} for _ in range(3)
    ]}

    uni_path = tmp_path / "candidates_universe999_test.json"
    molit_path = tmp_path / "molit_recent_test.json"
    uni_path.write_text(json.dumps(universe, ensure_ascii=False), encoding="utf-8")
    molit_path.write_text(json.dumps(molit, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(cg, "latest_universe_path", lambda: uni_path)   # import 시점 상수 → 지연 해소(2026-09-05 CI 수정)
    monkeypatch.setattr(cg, "latest_molit_path", lambda: molit_path)
    monkeypatch.setenv("VWORLD_API_KEY", "dummy-vkey")
    monkeypatch.setenv("MOLIT_API_KEY", "dummy-mkey")

    basis_by_code = {
        "K_GOOD": {"bjdCode": "1135010200", "kaptAddr": "100", "kaptName": "상계주공2단지"},
        "K_BAD": {"bjdCode": "1135099900", "kaptAddr": "200", "kaptName": "가짜단지"},
    }

    def fake_get_json_item(_ep, params, _key):
        return basis_by_code.get(params.get("kaptCode"))

    pnu_good = cg._pnu_from_basis("1135010200", "100")
    pnu_bad = cg._pnu_from_basis("1135099900", "200")
    assert pnu_good and pnu_bad and pnu_good != pnu_bad

    records_by_pnu = {
        pnu_good: [
            {"aphusNm": "상계주공2단지", "prvuseAr": "59.0", "pblntfPc": "300000000"}
            for _ in range(50)
        ],
        # 오조립: 이 필지는 실제로는 전혀 다른 단지 소유(이름불일치+카운트불일치 이중 신호)
        pnu_bad: [
            {"aphusNm": "완전히다른단지", "prvuseAr": "59.0", "pblntfPc": "999999999"}
            for _ in range(3)
        ],
    }

    def fake_fetch_vworld_all(pnu, _key):
        return records_by_pnu.get(pnu, [])

    monkeypatch.setattr(cg, "_get_json_item", fake_get_json_item)
    monkeypatch.setattr(cg, "_fetch_vworld_all", fake_fetch_vworld_all)

    cg.main()

    out = json.loads(uni_path.read_text(encoding="utf-8"))
    by_name = {r["complex_name"]: r for r in out}
    assert by_name["상계주공2단지"]["gongsi_man"] == 30000        # 3억원 → 30000만원, 타당성가드 통과
    assert by_name["가짜단지"]["gongsi_man"] is None               # 오조립 pnu → 신원게이트 탈락
    assert by_name["미검증단지"]["gongsi_man"] is None             # kapt_verified=False → 즉시 제외


# ── collect_public_enrich import (heavy modules) ─────────────────────────

def test_collect_public_enrich_imports_cleanly():
    """collect_public_enrich 는 blog.build_explorer·collect_universe_enrich·collect_kapt_maint_fees·
    agent_realestate.collectors.{kapt,kakao} 를 모듈 최상단에서 import 한다(네트워크 호출은 아니지만
    무거운 의존성 체인) — 여기서 verify_parcel_identity/_identity_fail_reason 을 collect_gongsi 로부터
    잘 가져오는지(구 _name_gate 제거 후 회귀 없음)만 확인. .env 키 부재 등으로 import 자체가 막히는
    환경(CI 등)에서는 사유를 남기고 skip."""
    try:
        import collect_public_enrich as cpe
    except Exception as e:  # pragma: no cover - 환경 의존
        pytest.skip(f"collect_public_enrich import 불가(환경 의존) — {e}")
    assert cpe._gongsi_man.__code__.co_varnames[:5] == (
        "kapt_code", "name", "district", "area", "units",
    )
    assert cpe.verify_parcel_identity is cg.verify_parcel_identity


def test_revalidate_resume_skips_only_final_decisions(tmp_path):
    """no-basis(API 실패)는 재개 시 재시도 대상, 표본 모드는 이전 판정을 쓰지 않는다(2026-09-05)."""
    import json, importlib
    rv = importlib.import_module("revalidate_gongsi")
    p = tmp_path / "decisions.json"
    p.write_text(json.dumps({"1": {"reason": "no-basis"}, "2": {"reason": "pass"},
                             "3": {"reason": "identity:name-mismatch"}}), encoding="utf-8")
    assert set(rv.load_resume_decisions(p, sample_mode=False)) == {"2", "3"}
    assert rv.load_resume_decisions(p, sample_mode=True) == {}
    assert rv.load_resume_decisions(tmp_path / "missing.json", sample_mode=False) == {}


def test_count_households_dedupes_duplicate_dong_ho():
    import collect_gongsi as cg
    recs = [{"dongNm": "101", "hoNm": "101"}, {"dongNm": "101", "hoNm": "101"},
            {"dongNm": "101", "hoNm": "102"}, {"dongNm": "101", "hoNm": "102"}]
    assert cg.count_households(recs) == 2
    assert cg.count_households([{"x": 1}, {"x": 2}]) == 2          # dong/ho 없으면 len 폴백


def test_identity_gate_strips_address_dong_prefix():
    """2026-09-05 표본 65 재검증: 동명 접두어만 다른 12건은 통과, 타 구 단지(pnu 오조립)는 여전히 거부."""
    import collect_gongsi as cg
    ok = cg._identity_fail_reason
    assert ok("태진아름", "등촌태진아름", 221, 221, "서울특별시 강서구 등촌동 676 등촌태진아름") is None
    assert ok("성원", "당산성원아파트", 205, 205, "서울특별시 영등포구 당산동5가 40 당산성원아파트") is None
    assert ok("미주아파트", "후암미주", 226, 226, "서울특별시 용산구 후암동 423-1 후암미주") is None
    assert ok("삼성", "수서삼성", 680, 680, "서울특별시 강남구 수서동 747 수서삼성") is None
    assert ok("창동신도브래뉴1차", "창동신도브래뉴", 456, 456, "서울특별시 도봉구 창동 820 창동신도브래뉴") is None
    # 성동구 '현대' 에 강남 청담2차현대 kapt_code 가 붙은 오조립 — 프레임 구 ≠ 주소 구 → 거부. 같은 구면 포함매칭+호수 일치로 통과
    addr_cd = "서울특별시 강남구 청담동 23 청담2차현대아파트"
    assert ok("현대아파트", "청담2차현대아파트", 214, 217, addr_cd, frame_gu="서울 성동구") == "gu-mismatch"
    assert ok("현대아파트", "청담2차현대아파트", 214, 217, addr_cd, frame_gu="서울 강남구") is None
    assert ok("코오롱", "코오롱하늘채", 466, 466, "서울특별시 마포구 연남동 573 코오롱하늘채", frame_gu="은평구") == "gu-mismatch"
    # 주소 없으면 종전 규칙 그대로(짧은 이름 포함매칭 불허)
    assert ok("삼성", "수서삼성", 680, 680) == "name-mismatch"
    # 전량 재검증(2026-09-05)에서 드러난 오탐 — 구 어간·짧은 포함매칭·'동' 오절단
    assert ok("천연뜨란채", "서대문천연뜨란채아파트", 1008, 1008, "서울특별시 서대문구 천연동 145 서대문천연뜨란채아파트", frame_gu="서대문구") is None
    assert ok("길동우성", "길동우성2차", 811, 811, "서울특별시 강동구 길동 400 길동우성2차", frame_gu="강동구") is None
    assert ok("두산", "봉천두산1,2단지", 2001, 2001, "서울특별시 관악구 봉천동 1708 봉천두산1,2단지", frame_gu="관악구") is None
    assert ok("동서울한양", "답십리동서울한양", 499, 499, "서울특별시 동대문구 답십리동 41 답십리동서울한양", frame_gu="동대문구") is None
    assert ok("건영2차아파트", "신내건영2차아파트", 716, 1113, "서울특별시 중랑구 상봉동 63 신내건영2차아파트", frame_gu="중랑구") == "count-mismatch"
    assert ok("정릉꿈에그린", "한화포레나정릉아파트", 289, 349, "서울특별시 성북구 정릉동 1037 한화포레나정릉아파트", frame_gu="성북구") == "name-mismatch"
    # 포함매칭 경로의 세대수 규칙은 유지 — distinct 호수 대신 2배 raw 건수를 넘기면 여전히 거부
    addr = "서울특별시 성동구 하왕십리동 1002 서울 왕십리 KCC스위첸아파트"
    assert ok("왕십리KCC스위첸", "서울 왕십리 KCC스위첸아파트", 544, 272, addr) == "count-mismatch"
    assert ok("왕십리KCC스위첸", "서울 왕십리 KCC스위첸아파트", 272, 272, addr) is None
    # 동명 접두어 제거로 완전일치가 되면 exact 경로(count 무관) — 같은 주소·같은 이름
    assert ok("벽산늘푸른", "염창벽산늘푸른", 412, 206, "서울특별시 강서구 염창동 290 염창벽산늘푸른") is None


def test_identity_norm_collapses_numbered_danji_suffix_but_keeps_number():
    import collect_gongsi as cg
    assert cg._identity_norm("도봉파크빌3단지") == cg._identity_norm("도봉파크빌3")
    assert cg._identity_norm("상계주공1단지") != cg._identity_norm("상계주공2단지")
    assert cg._identity_fail_reason("도봉파크빌3", "도봉파크빌3단지", 200, 200,
                                    "서울특별시 도봉구 도봉동 644 도봉파크빌3단지") is None


def test_latest_universe_path_is_lazy_and_fails_loud_when_missing(tmp_path, monkeypatch):
    """import 시점 [-1] 해소가 CI(데이터 파일 없음) 수집을 깨던 회귀 차단(2026-09-05)."""
    import collect_gongsi as cg
    monkeypatch.setattr(cg, "EX", tmp_path)
    with pytest.raises(SystemExit):
        cg.latest_universe_path()
    (tmp_path / "candidates_universe025_20260710.json").write_text("[]", encoding="utf-8")
    (tmp_path / "candidates_universe025_20260905.json").write_text("[]", encoding="utf-8")
    assert cg.latest_universe_path().name == "candidates_universe025_20260905.json"


# ── revalidate_gongsi: universe 모드(universe gongsi_man 직접 재검증, 2026-09-05) ─────
# revalidate_gongsi 는 collect_public_enrich 등 무거운 의존성 체인을 모듈 최상단에서 import 하므로
# (위 test_collect_public_enrich_imports_cleanly 와 동일 이유) 모듈 top-level import 대신 각 테스트
# 안에서 importlib.import_module 로 불러온다 — 체인이 깨져도 이 파일의 다른 테스트 수집까지 막지 않는다.

def test_derive_universe_candidates_maps_fields_and_skips_missing_gongsi():
    import importlib
    rv = importlib.import_module("revalidate_gongsi")
    rows = [
        {"complex_no": 1, "complex_name": "단지A", "district": "서울 노원구", "units": 100,
         "area_exclusive_m2": 59.0, "kapt_code": "K1", "kapt_verified": True, "gongsi_man": 30000},
        {"complex_no": "2", "complex_name": "단지B", "district": "서울 강남구", "units": 200,
         "area_exclusive_m2": 84.0, "kapt_code": "K2", "kapt_verified": False, "gongsi_man": 50000},
        {"complex_no": "3", "complex_name": "단지C", "district": "서울 중구", "units": 50,
         "area_exclusive_m2": 40.0, "kapt_code": "K3", "kapt_verified": True, "gongsi_man": None},
        {"complex_name": "단지D(무cno)", "district": "서울 종로구", "gongsi_man": 1000},
    ]
    out = rv.derive_universe_candidates(rows)
    by_cno = {c["complex_no"]: c for c in out}
    assert set(by_cno) == {"1", "2"}                  # 단지C(gongsi None)·단지D(무cno) 제외
    assert by_cno["1"]["complex_no"] == "1"           # int → str
    assert by_cno["1"]["gu"] == "노원구"               # district 마지막 공백토큰
    assert by_cno["1"]["district"] == "서울 노원구"
    assert by_cno["1"]["units"] == 100
    assert by_cno["1"]["area"] == 59.0
    assert by_cno["1"]["kapt_code"] == "K1"
    assert by_cno["2"]["kapt_code"] is None           # kapt_verified False → kapt_code None
    assert by_cno["2"]["gu"] == "강남구"


def test_apply_gongsi_removals_only_touches_listed_rows():
    import importlib
    rv = importlib.import_module("revalidate_gongsi")
    rows = [
        {"complex_no": "1", "complex_name": "단지A", "gongsi_man": 30000, "other_field": "x"},
        {"complex_no": "2", "complex_name": "단지B", "gongsi_man": 50000, "other_field": "y"},
        {"complex_no": "3", "complex_name": "단지C", "gongsi_man": 10000, "other_field": "z"},
    ]
    n = rv.apply_gongsi_removals(rows, {"1", "3"})
    assert n == 2
    by_cno = {r["complex_no"]: r for r in rows}
    assert by_cno["1"]["gongsi_man"] is None
    assert by_cno["1"]["other_field"] == "x"          # 다른 필드 불변
    assert by_cno["2"]["gongsi_man"] == 50000          # 미대상 불변
    assert by_cno["3"]["gongsi_man"] is None
    assert by_cno["3"]["other_field"] == "z"


def test_write_universe_with_backup_creates_backup_and_never_overwrites_backup(tmp_path):
    import importlib
    rv = importlib.import_module("revalidate_gongsi")
    path = tmp_path / "candidates_universe999_test.json"
    original_rows = [{"complex_no": "1", "gongsi_man": 30000}]
    path.write_text(json.dumps(original_rows, ensure_ascii=False), encoding="utf-8")

    new_rows = [{"complex_no": "1", "gongsi_man": None}]
    b1 = rv.write_universe_with_backup(path, new_rows, "20260905")
    assert b1.name == "candidates_universe999_test.json.bak-gongsi-revalidate-20260905"
    assert json.loads(b1.read_text(encoding="utf-8")) == original_rows
    assert json.loads(path.read_text(encoding="utf-8")) == new_rows

    # 두번째 호출(같은 stamp) — path 의 현재(new_rows) 내용이 새 백업의 '원본'이 됨, 최초 백업은 보존
    newer_rows = [{"complex_no": "1", "gongsi_man": 99999}]
    b2 = rv.write_universe_with_backup(path, newer_rows, "20260905")
    assert b2 != b1
    assert b2.name == "candidates_universe999_test.json.bak-gongsi-revalidate-20260905-2"
    assert json.loads(b1.read_text(encoding="utf-8")) == original_rows   # 최초 백업 그대로
    assert json.loads(b2.read_text(encoding="utf-8")) == new_rows        # 두번째 백업은 직전 상태
    assert json.loads(path.read_text(encoding="utf-8")) == newer_rows


def test_derive_frame_candidates_uses_frame_and_reports_missing():
    import importlib
    rv = importlib.import_module("revalidate_gongsi")
    overlay = {"10": {"kapt_code": "K10"}, "20": {"kapt_code": None}}
    frame_by_cno = {
        "10": {"complexNo": "10", "name": "프레임단지", "gu": "노원", "households": 300},
    }
    candidates, missing = rv.derive_frame_candidates(["10", "20"], overlay, frame_by_cno)
    assert missing == ["20"]
    assert len(candidates) == 1
    c = candidates[0]
    assert c["complex_no"] == "10"
    assert c["name"] == "프레임단지"
    assert c["gu"] == "노원"
    assert c["district"] == "서울 노원구"
    assert c["units"] == 300
    assert c["area"] == 0.0
    assert c["kapt_code"] == "K10"


def test_revalidate_one_identity_only_when_area_missing(monkeypatch):
    """면적 없는 frame 폴백 후보는 신원게이트만 판정(identity-ok-no-area) — 면적이 있으면 정상 pass."""
    import importlib
    rv = importlib.import_module("revalidate_gongsi")
    monkeypatch.setattr(rv.time, "sleep", lambda *_a, **_k: None)
    basis = {"kaptName": "상계주공2단지", "kaptAddr": "100", "bjdCode": "1135010200"}
    monkeypatch.setattr(rv, "_get_json_item", lambda *a, **k: basis)
    records = [{"aphusNm": "상계주공2단지", "dongNm": "101", "hoNm": str(i),
               "prvuseAr": "59.0", "pblntfPc": "300000000"} for i in range(100)]
    monkeypatch.setattr(rv, "_fetch_vworld_all", lambda *a, **k: records)

    passed, reason, val = rv._revalidate_one("K1", "상계주공2단지", "서울 노원구", 0.0, 100,
                                             {}, "vkey", "mkey", {}, {})
    assert (passed, reason, val) == (False, "identity-ok-no-area", None)

    passed2, reason2, val2 = rv._revalidate_one("K1", "상계주공2단지", "서울 노원구", 59.0, 100,
                                                {}, "vkey", "mkey", {}, {})
    assert (passed2, reason2, val2) == (True, "pass", 30000)


def test_universe_mode_cli_writes_in_place_with_backup(tmp_path, monkeypatch):
    """--universe 종단: derive_public_targets() 를 호출하면 안 되고(데이터 파일 불필요),
    identity:* 탈락 cno 만 gongsi_man=None 처리되어 universe 파일에 in-place 반영 + 백업이 남아야 한다."""
    import importlib
    import sys
    rv = importlib.import_module("revalidate_gongsi")

    universe = [
        {"complex_no": "1", "complex_name": "단지A", "district": "서울 노원구", "units": 100,
         "area_exclusive_m2": 59.0, "kapt_code": "K1", "kapt_verified": True, "gongsi_man": 30000},
        {"complex_no": "2", "complex_name": "단지B", "district": "서울 강남구", "units": 200,
         "area_exclusive_m2": 84.0, "kapt_code": "K2", "kapt_verified": True, "gongsi_man": 50000},
    ]
    uni_path = tmp_path / "candidates_universe999_test.json"
    molit_path = tmp_path / "molit_test.json"
    cache_path = tmp_path / "cache.json"
    decisions_path = tmp_path / "decisions.json"
    uni_path.write_text(json.dumps(universe, ensure_ascii=False), encoding="utf-8")
    molit_path.write_text("{}", encoding="utf-8")

    def fake_revalidate_one(kapt_code, *a, **k):
        if kapt_code == "K1":
            return False, "identity:name-mismatch", None
        return True, "pass", 123

    def _no_public_targets():
        raise AssertionError("universe 모드는 derive_public_targets() 를 호출하면 안 됨(데이터 파일 불필요)")

    monkeypatch.setattr(rv, "_revalidate_one", fake_revalidate_one)
    monkeypatch.setattr(rv.cpe, "derive_public_targets", _no_public_targets)
    monkeypatch.setenv("VWORLD_API_KEY", "dummy-vkey")
    monkeypatch.setenv("MOLIT_API_KEY", "dummy-mkey")
    monkeypatch.setattr(sys, "argv", [
        "revalidate_gongsi.py", "--universe",
        "--universe-path", str(uni_path),
        "--molit", str(molit_path),
        "--cache", str(cache_path),
        "--decisions", str(decisions_path),
    ])

    rv.main()

    out = json.loads(uni_path.read_text(encoding="utf-8"))
    by_cno = {r["complex_no"]: r for r in out}
    assert by_cno["1"]["gongsi_man"] is None            # identity:* 탈락 → 제거
    assert by_cno["2"]["gongsi_man"] == 50000           # pass 지만 새값(123)은 기록하지 않음(report-only)

    backups = list(tmp_path.glob(f"{uni_path.name}.bak-gongsi-revalidate-*"))
    assert len(backups) == 1
    original = json.loads(backups[0].read_text(encoding="utf-8"))
    assert {r["complex_no"]: r["gongsi_man"] for r in original} == {"1": 30000, "2": 50000}


def test_identity_norm_strips_jibun_parenthesis_and_inner_apt_token():
    """VWorld aphusNm 의 필지 지번 괄호와 K-apt 이름 중간의 '아파트' 는 신원 식별자가 아니다(동도센트리움 오탐, 2026-09-05).
    단지 번호(N차·N단지)는 종전대로 보존된다."""
    import collect_gongsi as cg
    assert cg._identity_norm("동도센트리움(70-12)") == "동도센트리움"
    assert cg._identity_norm("동도센트리움 아파트 오피스텔") == "동도센트리움오피스텔"
    assert cg._identity_norm("상계주공16단지") == "상계주공16"
    assert cg._identity_norm("현대(1차)아파트") == "현대(1)"


def test_identity_gate_passes_mixed_use_same_parcel_with_jibun_suffix():
    """동도센트리움[주상복합](구로 개봉동 70-12): aphusNm '동도센트리움(70-12)' ↔ kaptName '동도센트리움 아파트 오피스텔',
    distinct 호수 136 = units 136 → 같은 필지. 이름이 다른 단지·호수가 다른 단지는 여전히 거부."""
    import collect_gongsi as cg
    addr = "서울특별시 구로구 개봉동 70-12 동도센트리움 아파트 오피스텔"
    kw = dict(kapt_addr=addr, frame_gu="서울 구로구")
    assert cg._identity_fail_reason("동도센트리움(70-12)", "동도센트리움 아파트 오피스텔", 136, 136, **kw) is None
    assert cg._identity_fail_reason("개봉푸르지오(70-12)", "동도센트리움 아파트 오피스텔", 136, 136, **kw) == "name-mismatch"
    assert cg._identity_fail_reason("동도센트리움(70-12)", "동도센트리움 아파트 오피스텔", 400, 136, **kw) == "count-mismatch"
