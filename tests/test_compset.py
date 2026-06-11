"""compset 테이블 조립 + same_name CAGR 결정론 테스트 (재편 A, gen-baserate 코어)."""
from agent_realestate.analysts.compset import assemble_compset, same_name_cagrs


def test_same_name_cagr_realized():
    # 동일단지 A: entry median 5억 → exit median 10억, 10년 → CAGR ≈ 7.18%
    entry = [{"apt": "A동", "price": 500_000_000}] * 3
    exit_ = [{"apt": "A동", "price": 1_000_000_000}] * 3
    out = same_name_cagrs(entry, exit_, years=10.0)
    assert "A동" in out and abs(out["A동"] - 0.0718) < 0.001


def test_same_name_requires_both_windows_and_min_n():
    entry = [{"apt": "A", "price": 5_00_000_000}] * 3 + [{"apt": "B", "price": 4_00_000_000}] * 2
    exit_ = [{"apt": "A", "price": 6_00_000_000}] * 3 + [{"apt": "C", "price": 7_00_000_000}] * 3
    out = same_name_cagrs(entry, exit_, years=5.0)
    assert "A" in out          # 양 구간 n>=3
    assert "B" not in out      # entry 만(2건) → 제외
    assert "C" not in out      # exit 만 → 제외


def test_name_normalization_spaces():
    entry = [{"apt": "상계 주공 3", "price": 5_00_000_000}] * 3
    exit_ = [{"apt": "상계주공3", "price": 6_00_000_000}] * 3
    out = same_name_cagrs(entry, exit_, years=10.0)
    assert "상계주공3" in out   # 공백 정규화로 동일단지 매칭


def test_assemble_structure_and_sorted():
    # band-aware(2026-06-02): sets = {생활권: {bands: {band: {longrun, recent}}}}
    sets = {
        "구로-신도림": {"bands": {
            84: {"longrun": [0.07, 0.05, 0.06], "recent": {"신도림B": 0.06, "신도림A": 0.02}},
            59: {"longrun": [0.04, 0.03], "recent": {"신도림A": -0.01}},
        }},
    }
    out = assemble_compset(sets, meta={"baseline": "테스트"})
    assert out["longrun"]["구로-신도림|84"] == [0.05, 0.06, 0.07]   # 정렬
    assert out["longrun"]["구로-신도림|59"] == [0.03, 0.04]
    assert out["peers_recent"]["구로-신도림|84"] == [0.02, 0.06]
    # recent 키 = "생활권|단지|band" (일반명 충돌 방지)
    assert out["recent"]["구로-신도림|신도림A|84"] == 0.02
    assert out["recent"]["구로-신도림|신도림A|59"] == -0.01
    assert out["assign"]["신도림A"] == "구로-신도림"      # 폴백용 단지 → 생활권
    assert out["_meta"]["baseline"] == "테스트"


def test_assemble_deterministic():
    sets = {"S": {"bands": {84: {"longrun": [0.05, 0.06], "recent": {"X": 0.03}}}}}
    assert assemble_compset(sets) == assemble_compset(sets)


def test_bucket_area():
    from agent_realestate.analysts.compset import bucket_area
    assert bucket_area(59) == 59 and bucket_area(84) == 84 and bucket_area(72) == 72
    assert bucket_area(113) == 110 and bucket_area(126) == 135 and bucket_area(96) == 84
