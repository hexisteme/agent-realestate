"""단지명 canonical 매칭 단위테스트 (2026-09-05 P0 매칭결함 수정) — 부분일치/4자 prefix/빈 이름
전부일치 결함 재발방지 회귀테스트.
대상: blog.build_explorer.canonical_complex_name / match_molit_names / assert_no_duplicate_signatures /
      build_dataset_public(합성 frame+MOLIT 종단)."""
import glob
import json
import os

import pytest

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
