"""단지명 canonical 매칭 단위테스트 (2026-09-05 P0 매칭결함 수정) — 부분일치/4자 prefix/빈 이름
전부일치 결함 재발방지 회귀테스트.
대상: blog.build_explorer.canonical_complex_name / match_molit_names / assert_no_duplicate_signatures /
      build_dataset_public(합성 frame+MOLIT 종단)."""
import glob
import json
import os

import pytest

import blog.build_explorer as be
from blog.build_explorer import (
    assert_no_duplicate_signatures,
    build_dataset_public,
    canonical_complex_name,
    match_molit_names,
)


# ── canonical_complex_name ──────────────────────────────────────────────

def test_canonical_strips_bracket_tag_and_keeps_number():
    assert canonical_complex_name("[재발견] 상계주공2단지") == "상계주공2단지"


def test_canonical_strips_allowlisted_paren_qualifier():
    assert canonical_complex_name("상계주공2(고층)") == "상계주공2"


def test_canonical_fully_parenthesized_name_is_none():
    # MOLIT 지번코드 placeholder(예: 구로 "(791-16)") — 바깥 텍스트 없음 → 식별자 없음(무엇과도 매칭 금지)
    assert canonical_complex_name("(791-16)") is None


def test_canonical_strips_trailing_apt_suffix():
    assert canonical_complex_name("구일우성아파트") == "구일우성"


def test_canonical_removes_internal_whitespace():
    assert canonical_complex_name("래미안 아름숲") == "래미안아름숲"


def test_canonical_keeps_non_identity_parens():
    # 동수 등 비allowlist 괄호는 식별자로 보존 — "현대(982)" ≠ "현대(209)"
    assert canonical_complex_name("현대(982)") == "현대(982)"
    assert canonical_complex_name("현대(209)") == "현대(209)"
    assert canonical_complex_name("현대(982)") != canonical_complex_name("현대(209)")


def test_canonical_empty_string_is_none():
    assert canonical_complex_name("") is None


# ── 근접매칭(item 3, 2026-09-05) 신규 규칙 — 각 규칙이 두 표기를 한 키로 접는지 ──────────

def test_ipark_latin_korean_variant_folds_to_one_key():
    assert canonical_complex_name("노원IPARK") == canonical_complex_name("노원아이파크")
    assert canonical_complex_name("DMCSKVIEW".replace("SKVIEW", "IPARK")) == \
        canonical_complex_name("DMC아이파크")


def test_epyeonhansesang_latin_korean_variant_folds_to_one_key():
    assert canonical_complex_name("e편한세상강동에코포레") == canonical_complex_name("이편한세상강동에코포레")


def test_skview_three_way_variant_folds_to_one_key():
    # SKVIEW(라틴)·에스케이뷰(완전풀어쓰기)·SK뷰(공식 축약) 3표기가 전부 한 키로 접혀야 함
    assert canonical_complex_name("DMCSKVIEW") == canonical_complex_name("DMCSK뷰")
    assert canonical_complex_name("강변에스케이뷰") == canonical_complex_name("강변SK뷰")


def test_xi_jai_variant_folds_to_one_key():
    # 자이(XI) 표기 — 현재 실데이터엔 XI 표기가 0건이라 합성 사례로만 검증(방어적 규칙)
    assert canonical_complex_name("래미안XI") == canonical_complex_name("래미안자이")


def test_new_paren_qualifier_tokens_are_non_identity():
    # item3 근접매칭 스캔에서 발견된 4건 — 전부 같은 물리단지의 MOLIT측 qualifier 괄호(실측: 동일-lawd
    # 충돌 0건, test_new_paren_qualifiers_introduce_no_molit_collision 참고)
    assert canonical_complex_name("강남브리즈힐(토지임대부아파트)") == canonical_complex_name("강남브리즈힐")
    assert canonical_complex_name("마곡서광(치현마을)") == canonical_complex_name("마곡서광")
    assert canonical_complex_name("인왕산현대(인왕산힐스테이트)") == canonical_complex_name("인왕산현대")
    assert canonical_complex_name("태영으뜸(데시앙)") == canonical_complex_name("태영으뜸")


def test_new_paren_qualifiers_still_keep_unrelated_numeric_parens_as_identity():
    # 회귀 방지 — 새 토큰 4개를 넣었다고 기존 "현대(982)≠현대(209)" 보존 규칙이 깨지면 안 됨
    assert canonical_complex_name("현대(982)") == "현대(982)"
    assert canonical_complex_name("현대(982)") != canonical_complex_name("현대(209)")


@pytest.mark.skipif(not os.path.exists("examples/molit_recent_25gu_20260710.json"),
                     reason="실데이터 example 파일 없음")
def test_new_paren_qualifiers_introduce_no_molit_collision():
    # item3 필수 측정 — 새 토큰(_PAREN_NON_IDENTITY 4종)·브랜드변형(_BRAND_VARIANTS)이 동일 lawd 안에서
    # 서로 다른 MOLIT 원본명 2개를 같은 canonical 키로 새로 뭉치게 하면 안 된다(count 는 0 이어야 함).
    # 방법: 신규 규칙을 뺀 "구버전" 등가물(기존 8토큰만, 브랜드변형 없음)과 비교해 델타가 0인지 확인.
    molit = json.load(open("examples/molit_recent_25gu_20260710.json", encoding="utf-8"))

    def _collision_groups(paren_tokens, fold):
        groups: dict[tuple[str, str], set[str]] = {}
        for lawd in be.GU_LAWD.values():
            recs = molit.get(lawd, [])
            raws = {r["apt"] for r in recs if isinstance(r, dict) and r.get("apt")}
            by_canon: dict[str, set[str]] = {}
            for raw in raws:
                s = __import__("re").sub(r"\[.*?\]", "", raw)
                if __import__("re").sub(r"\([^()]*\)", "", s).strip() == "":
                    continue
                def _drop(m, _toks=paren_tokens):
                    toks = [t for t in __import__("re").split(r"[,\s]+", m.group(1).strip()) if t]
                    return "" if toks and all(t in _toks for t in toks) else m.group(0)
                s = __import__("re").sub(r"\(([^()]*)\)", _drop, s)
                s = __import__("re").sub(r"\s+", "", s)
                if s.endswith("아파트"):
                    s = s[:-3]
                s = fold(s)
                if s:
                    by_canon.setdefault(s, set()).add(raw)
            for c, origs in by_canon.items():
                if len(origs) > 1:
                    groups[(lawd, c)] = frozenset(origs)
        return set(groups.items())

    old_tokens = {"고층", "저층", "임대", "분양", "아파트", "주상복합", "도시형", "민간임대"}
    baseline = _collision_groups(old_tokens, lambda s: s)
    current = _collision_groups(be._PAREN_NON_IDENTITY, be._fold_brand_variants)
    new_collisions = current - baseline
    assert new_collisions == set(), f"신규 규칙이 새 충돌을 만듦: {new_collisions}"


# ── match_molit_names ───────────────────────────────────────────────────

def test_empty_name_records_never_match():
    # apt 명이 빈 문자열인 레코드는 canonical=None → 무엇과도 매칭되지 않는다(전부일치 버그 재발방지)
    names = ["", "", "래미안아름숲"]
    assert match_molit_names("", names) == set()
    assert match_molit_names("아무거나", names) == set()


def test_numbered_blocks_stay_separate():
    # 상계주공1~3단지가 4자 prefix("상계주공")로 뭉치던 결함 — exact 매칭만으로 분리되어야 함
    names = ["상계주공1단지", "상계주공2단지", "상계주공3단지"]
    assert match_molit_names("상계주공2단지", names) == {"상계주공2단지"}
    assert match_molit_names("상계주공1단지", names) == {"상계주공1단지"}
    assert match_molit_names("상계주공3단지", names) == {"상계주공3단지"}


def test_generic_name_does_not_absorb_neighbours():
    # "현대"가 "현대힐스테이트2단지"를 부분일치로 흡수하던 결함
    names = ["현대힐스테이트2단지", "현대14차"]
    assert match_molit_names("현대", names) == set()


def test_collapse_matches_unambiguous_numbered_block():
    # 질의엔 '단지/차' 접미가 없고 MOLIT 쪽에만 있는 경우 — 유일하면 접어서 매칭
    names = ["국화1단지"]
    assert match_molit_names("국화1", names) == {"국화1단지"}


def test_collapse_refuses_when_danji_and_cha_both_exist():
    # 접은 키가 서로 다른 원본 canonical 2개로 갈라지면(1단지/1차 공존) 무매칭
    names = ["장미1단지", "장미1차"]
    assert match_molit_names("장미1", names) == set()


def test_exact_match_wins_over_collapse():
    names = ["소망1차", "소망1단지"]
    assert match_molit_names("소망1차", names) == {"소망1차"}


# ── assert_no_duplicate_signatures ──────────────────────────────────────

def _row(name, gu, n=10, med=8.0, p25=7.5, p75=8.5, trend=1.0, pos=50):
    return {"name": name, "gu": gu, "molit_recent_eok": med, "molit_n": n,
            "molit_p25_eok": p25, "molit_p75_eok": p75, "molit_trend_pct": trend, "molit_pos_52w": pos}


def test_assert_no_duplicate_signatures_passes_on_distinct_rows():
    ds = {"complexes": [_row("A단지", "노원", med=8.0), _row("B단지", "노원", med=9.0)]}
    assert_no_duplicate_signatures(ds)  # 예외 없이 통과해야 함


def test_assert_no_duplicate_signatures_raises_on_duplicate_group():
    ds = {"complexes": [_row("A단지", "노원"), _row("B단지", "노원")]}  # 완전 동일 시그니처
    with pytest.raises(ValueError):
        assert_no_duplicate_signatures(ds)


def test_assert_no_duplicate_signatures_ignores_small_samples():
    ds = {"complexes": [_row("A단지", "노원", n=3), _row("B단지", "노원", n=3)]}  # n<5 는 게이트 대상 아님
    assert_no_duplicate_signatures(ds)


# ── build_dataset_public 종단(합성 frame+MOLIT) ──────────────────────────

def test_build_dataset_public_distinguishes_prefix_sharing_complexes(tmp_path):
    frame = [
        {"complexNo": 1, "name": "상계주공1단지", "gu": "노원", "households": 600,
         "builtYm": "198504", "far": 150, "type": "아파트"},
        {"complexNo": 2, "name": "상계주공2단지", "gu": "노원", "households": 650,
         "builtYm": "198701", "far": 150, "type": "아파트"},
        {"complexNo": 3, "name": "전혀다른단지", "gu": "노원", "households": 300,
         "builtYm": "199001", "far": 150, "type": "아파트"},
    ]
    molit = {"11350": (
        [{"apt": "상계주공1단지", "area": 59.0, "price": int(6.0e8), "ym": "202605"} for _ in range(6)]
        + [{"apt": "상계주공2단지", "area": 59.0, "price": int(9.0e8), "ym": "202605"} for _ in range(6)]
        # "전혀다른단지"는 MOLIT 에 매칭레코드가 없다 — no_molit_match 로 빠져야 함
    )}
    frame_path = tmp_path / "frame.json"
    molit_path = tmp_path / "molit.json"
    frame_path.write_text(json.dumps(frame, ensure_ascii=False), encoding="utf-8")
    molit_path.write_text(json.dumps(molit, ensure_ascii=False), encoding="utf-8")

    ds = build_dataset_public(str(frame_path), str(molit_path), "2026-07-10", "2026-09-05")
    by_name = {r["name"]: r for r in ds["complexes"]}
    assert by_name["상계주공1단지"]["molit_recent_eok"] == 6.0
    assert by_name["상계주공2단지"]["molit_recent_eok"] == 9.0
    assert by_name["상계주공1단지"]["molit_recent_eok"] != by_name["상계주공2단지"]["molit_recent_eok"]
    assert "전혀다른단지" not in by_name
    assert ds["excluded"]["no_molit_match"] == 1
    assert_no_duplicate_signatures(ds)


# ── build_dataset_public: district_map 이 소재구를 스캔 구 후보 위에 고정(2026-09-06) ─────────
# collect_frame_district.py 산출물을 흉내낸 합성 district_map 으로, 소재구가 스캔 구 후보에 없을 때
# district_map_path 미지정(기존 동작)이면 엉뚱한 구로 발행되고, 지정하면 좌표로 확정한 소재구
# 하나로 고정되는지 둘 다 검증한다(실측 결함 재현: 도봉 스캔에 잡힌 노원 하계동 단지).

def test_district_map_pins_true_gu_over_scan_gu_and_no_map_falls_back_to_scan_gu(tmp_path):
    frame = [
        {"complexNo": 501, "name": "테스트한신", "gu": "도봉", "households": 1200,
         "builtYm": "198801", "far": 200, "type": "아파트"},
    ]
    molit = {
        "11320": [{"apt": "테스트한신", "area": 59.0, "price": int(5.0e8), "ym": "202605"}
                  for _ in range(6)],   # 도봉 LAWD — 함정(동명 단지 오매칭 가격)
        "11350": [{"apt": "테스트한신", "area": 59.0, "price": int(9.0e8), "ym": "202605"}
                  for _ in range(6)],   # 노원 LAWD — 실제 소재구 가격
    }
    district_map = {"501": {"sido": "서울특별시", "gu": "노원", "dong": "하계동", "scan_gus": ["도봉"]}}

    frame_path = tmp_path / "frame.json"
    molit_path = tmp_path / "molit.json"
    dmap_path = tmp_path / "dmap.json"
    frame_path.write_text(json.dumps(frame, ensure_ascii=False), encoding="utf-8")
    molit_path.write_text(json.dumps(molit, ensure_ascii=False), encoding="utf-8")
    dmap_path.write_text(json.dumps(district_map, ensure_ascii=False), encoding="utf-8")

    # 맵 미지정(기존 동작) — 스캔 구 후보(도봉)로 발행되고 함정 가격(5억)을 그대로 담는다
    ds_no_map = build_dataset_public(str(frame_path), str(molit_path), "2026-07-10", "2026-09-06")
    rows_no_map = [r for r in ds_no_map["complexes"] if r["name"] == "테스트한신"]
    assert len(rows_no_map) == 1
    assert rows_no_map[0]["gu"] == "도봉"
    assert rows_no_map[0]["molit_recent_eok"] == 5.0

    # 맵 지정 — 좌표로 확정한 소재구(노원) 하나로 고정, 노원 실거래 값(9억)을 담는다
    ds_map = build_dataset_public(str(frame_path), str(molit_path), "2026-07-10", "2026-09-06",
                                   district_map_path=str(dmap_path))
    rows_map = [r for r in ds_map["complexes"] if r["name"] == "테스트한신"]
    assert len(rows_map) == 1
    assert rows_map[0]["gu"] == "노원"
    assert rows_map[0]["molit_recent_eok"] == 9.0


def test_district_map_excludes_outside_seoul_cno_as_outside_scope(tmp_path):
    frame = [
        {"complexNo": 601, "name": "경기외곽아파트", "gu": "노원", "households": 500,
         "builtYm": "199001", "far": 180, "type": "아파트"},
    ]
    molit = {"11350": [{"apt": "경기외곽아파트", "area": 59.0, "price": int(7.0e8), "ym": "202605"}
                        for _ in range(6)]}
    # sido 가 서울이 아님(스캔엔 잡혔지만 실제 소재는 경기) — 발행되지 않고 outside_scope 로 빠져야 함
    district_map = {"601": {"sido": "경기도", "gu": "하남", "dong": "덕풍동", "scan_gus": ["노원"]}}

    frame_path = tmp_path / "frame.json"
    molit_path = tmp_path / "molit.json"
    dmap_path = tmp_path / "dmap.json"
    frame_path.write_text(json.dumps(frame, ensure_ascii=False), encoding="utf-8")
    molit_path.write_text(json.dumps(molit, ensure_ascii=False), encoding="utf-8")
    dmap_path.write_text(json.dumps(district_map, ensure_ascii=False), encoding="utf-8")

    ds = build_dataset_public(str(frame_path), str(molit_path), "2026-07-10", "2026-09-06",
                               district_map_path=str(dmap_path))
    assert "경기외곽아파트" not in {r["name"] for r in ds["complexes"]}
    assert ds["excluded"]["outside_scope"] == 1


def test_district_map_excludes_gu_not_in_gu_lawd_as_outside_scope(tmp_path):
    frame = [
        {"complexNo": 602, "name": "없는구아파트", "gu": "노원", "households": 500,
         "builtYm": "199001", "far": 180, "type": "아파트"},
    ]
    molit = {"11350": [{"apt": "없는구아파트", "area": 59.0, "price": int(7.0e8), "ym": "202605"}
                        for _ in range(6)]}
    # sido 는 서울이지만 GU_LAWD 커버리지 밖 구명 — 서울 안이라도 outside_scope 로 빠져야 함
    district_map = {"602": {"sido": "서울특별시", "gu": "없는구", "dong": "어딘가동", "scan_gus": ["노원"]}}

    frame_path = tmp_path / "frame.json"
    molit_path = tmp_path / "molit.json"
    dmap_path = tmp_path / "dmap.json"
    frame_path.write_text(json.dumps(frame, ensure_ascii=False), encoding="utf-8")
    molit_path.write_text(json.dumps(molit, ensure_ascii=False), encoding="utf-8")
    dmap_path.write_text(json.dumps(district_map, ensure_ascii=False), encoding="utf-8")

    ds = build_dataset_public(str(frame_path), str(molit_path), "2026-07-10", "2026-09-06",
                               district_map_path=str(dmap_path))
    assert "없는구아파트" not in {r["name"] for r in ds["complexes"]}
    assert ds["excluded"]["outside_scope"] == 1


def test_build_dataset_public_warns_when_district_map_is_stale_or_absent(tmp_path, capsys):
    """맵이 프레임보다 낡으면(새 cno 미커버) 조용히 스캔 구로 폴백하던 게 이 결함의 원래 모습이다 —
    폴백 자체는 유지하되 커버리지 구멍을 크게 알린다."""
    frame = [
        {"complexNo": 701, "name": "맵있는단지", "gu": "노원", "households": 500,
         "builtYm": "199001", "far": 180, "type": "아파트"},
        {"complexNo": 702, "name": "맵없는신규단지", "gu": "노원", "households": 500,
         "builtYm": "199001", "far": 180, "type": "아파트"},
    ]
    molit = {"11350": [{"apt": n, "area": 59.0, "price": int(7.0e8), "ym": "202605"}
                       for n in ("맵있는단지", "맵없는신규단지") for _ in range(6)]}
    dmap = {"701": {"sido": "서울특별시", "gu": "노원", "dong": "하계동", "scan_gus": ["노원"]}}
    frame_path, molit_path, dmap_path = (tmp_path / "f.json", tmp_path / "m.json", tmp_path / "d.json")
    frame_path.write_text(json.dumps(frame, ensure_ascii=False), encoding="utf-8")
    molit_path.write_text(json.dumps(molit, ensure_ascii=False), encoding="utf-8")
    dmap_path.write_text(json.dumps(dmap, ensure_ascii=False), encoding="utf-8")

    build_dataset_public(str(frame_path), str(molit_path), "2026-07-10", "2026-09-06",
                         district_map_path=str(dmap_path))
    out = capsys.readouterr().out
    assert "소재구 맵 미커버 1단지" in out

    build_dataset_public(str(frame_path), str(molit_path), "2026-07-10", "2026-09-06")
    assert "소재구 맵 없음" in capsys.readouterr().out


# ── build_dataset: complex_no 는 (구, 표시명) 으로 배정 ───────────────────────

def _uni_row(name: str, district: str, cno: str, units: int) -> dict:
    """load_candidates 가 요구하는 최소 universe 행."""
    return {"complex_name": name, "dong_ho": "101동", "area_exclusive_m2": 59.0, "floor": "3/15층",
            "facing": "남향", "price_krw": int(8.0e8), "agent_name": "테스트공인", "confirmed_date": "2026-06-03",
            "units": units, "built_year": 1995, "far_pct": 200.0, "land_share_pyeong": 12.0,
            "land_share_is_estimate": False, "jeonse_krw": None, "district": district,
            "saenghwalgwon": "테스트생활권", "cbd_km": 5.0, "cbd_name": "여의도", "slope_pct": 3.0,
            "complex_no": cno, "broker_count": 1, "regulated": False, "redev_stage": "NONE"}


def test_build_dataset_assigns_complex_no_per_gu_for_same_named_complexes(tmp_path):
    """동명 단지(구 다름)가 서로의 complex_no 를 가져가지 않아야 한다(2026-09-06 실측 결함).

    complex_no 는 네이버 매물 링크이자 add_enrich_overlay 의 조인 키다 — 이름만으로 매핑하면
    universe 의 '두산' 4·'삼환' 2·'현대' 2 중 마지막 행이 이겨, 발행 데이터셋 4행이 타 구 단지의
    번호를 달고 나갔다(노원 두산 763 ← 동대문 두산 739 의 3271 등). 그 상태로 그 번호에 overlay
    항목이 생기면 타 단지의 K-apt·공시가·관리비가 병합된다."""
    from blog.build_explorer import build_dataset
    uni = [_uni_row("두산", "서울 노원구", "194", 763), _uni_row("두산", "서울 성북구", "1183", 1998)]
    uni_path = tmp_path / "uni.json"
    uni_path.write_text(json.dumps(uni, ensure_ascii=False), encoding="utf-8")
    molit = {"11350": [{"apt": "두산", "area": 59.0, "price": int(8.0e8), "ym": "202605"} for _ in range(6)],
             "11290": [{"apt": "두산", "area": 59.0, "price": int(9.0e8), "ym": "202605"} for _ in range(6)]}
    molit_path = tmp_path / "molit.json"
    molit_path.write_text(json.dumps(molit, ensure_ascii=False), encoding="utf-8")

    ds = build_dataset(str(uni_path), str(molit_path), "2026-09-06", "2026-09-06")
    by_gu = {r["gu"]: r for r in ds["complexes"]}
    assert by_gu["노원"]["complex_no"] == "194"
    assert by_gu["성북"]["complex_no"] == "1183"


# ── 실데이터 종단(examples 파일 있을 때만) ─────────────────────────────────

FRAME_25GU = "examples/frame_25gu_20260710.json"
MOLIT_25GU = "examples/molit_recent_25gu_20260710.json"


@pytest.mark.skipif(not (os.path.exists(FRAME_25GU) and os.path.exists(MOLIT_25GU)),
                     reason="실데이터 example 파일 없음")
def test_real_data_no_duplicate_signatures_and_sanggye_2danji_sample_shrinks():
    uni = sorted(glob.glob("examples/candidates_universe159_*.json"))
    anchor_universe = uni[-1] if uni else None
    ds = build_dataset_public(FRAME_25GU, MOLIT_25GU, asof="2026-07-10", today="2026-09-05",
                              anchor_universe=anchor_universe)
    assert_no_duplicate_signatures(ds)  # 회귀 게이트 — 예외 없이 통과해야 함
    sanggye = [r for r in ds["complexes"] if r["gu"] == "노원" and r["name"] == "상계주공2단지"]
    assert sanggye, "노원 상계주공2단지 행이 존재해야 함"
    # 4자 prefix 로 상계주공1~16단지가 뭉쳐 n≈605-633 이던 결함 수정 확인(그 단지 자신의 표본만 남아야 함)
    assert sanggye[0]["molit_n"] < 100


@pytest.mark.skipif(not os.path.exists(FRAME_25GU), reason="실데이터 example 파일 없음")
def test_real_data_published_complex_no_matches_frame_identity():
    """발행 데이터셋의 complex_no 가 프레임의 그 번호(이름·세대수)와 같은 단지를 가리켜야 한다.
    2026-09-06 실측: 이름만으로 매핑하던 레거시 경로 때문에 4행이 타 구 단지 번호를 달고 발행됐다."""
    uni = sorted(glob.glob("examples/candidates_universe159_*.json"))
    if not (uni and os.path.exists(MOLIT_25GU)):
        pytest.skip("실데이터 example 파일 없음")
    ds = build_dataset_public(FRAME_25GU, MOLIT_25GU, asof="2026-07-10", today="2026-09-06",
                              anchor_universe=uni[-1])
    frame_by_cno = {}
    for r in json.load(open(FRAME_25GU, encoding="utf-8")):
        frame_by_cno.setdefault(str(r["complexNo"]), (r["name"], r.get("households")))
    bad = [(r["gu"], r["name"], r["units"], r["complex_no"], frame_by_cno[str(r["complex_no"])])
           for r in ds["complexes"]
           if str(r.get("complex_no") or "") in frame_by_cno
           and frame_by_cno[str(r["complex_no"])][1] != r["units"]]
    assert not bad, f"프레임 신원과 다른 complex_no 를 단 행: {bad}"
