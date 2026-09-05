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
