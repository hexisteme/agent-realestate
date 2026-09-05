"""agent_realestate/identity.py 단위테스트(2026-09-05 신설) — collect_gongsi.py 에서 이관된 신원게이트
(_identity_fail_reason 등)과 새로 추가된 K-apt 코드 배정 게이트(verify_kapt_basis_identity), 그리고
그 게이트가 실제로 collect_universe_enrich._resolve_kapt_basis 에 배선됐는지를 검증한다.

배경: collect_universe_enrich._resolve_kapt_basis 는 구내 substring 후보를 세대수·준공연도로만
교차검증해 kapt_code 를 채택했다 — 그 결과 성동 '현대' 가 강남 '청담2차현대' 코드를 받는 등 14건이
kapt_verified=True 로 발행됐다(관리비·난방·주차·시공사가 타 단지 값으로 발행). verify_kapt_basis_
identity 는 _identity_fail_reason 의 구·이름 검사를 재사용해 이 오매칭을 코드 배정 시점에 막는다.

2026-09-05 코디네이터 추가지시 — _identity_fail_reason 자체에 두 규칙이 더해졌다:
  rule 1: 이름 비교를 원문(raw)·동접두어제거(stripped) 두 페어 모두로 본다(한쪽만 보면 접두어제거의
          비대칭 절단이 원문에서는 성립하던 포함관계를 깨뜨리는 오탈락이 있었다 — 숭인 사례).
  rule 2: gu 일치 + 세대수 정확 일치(±max(3,2%))면 이름이 서로 무관해도 개명(rebrand)으로 통과시킨다.
verify_kapt_basis_identity 는 _identity_fail_reason 에 위임하므로 두 규칙이 그대로 적용된다."""
from __future__ import annotations

import agent_realestate.identity as identity
import collect_universe_enrich as cue


# ── verify_kapt_basis_identity ────────────────────────────────────────────

def test_verify_kapt_basis_identity_passes_same_gu_containment_with_matching_households():
    """이름은 완전일치가 아니라 포함관계(두산⊂봉천두산1,2단지)지만 구·세대수가 맞으면 통과."""
    basis = {"kaptName": "봉천두산1,2단지", "kaptAddr": "서울특별시 관악구 봉천동 1708 봉천두산1,2단지",
             "kaptdaCnt": 2001}
    assert identity.verify_kapt_basis_identity("두산", "관악구", 2001, basis) is None


def test_verify_kapt_basis_identity_fails_gu_mismatch_for_other_gu_kapt_addr():
    """성동 '현대' 프레임에 강남 '청담2차현대' K-apt 코드가 붙은 실제 감사 사례(2026-09-05, 14건 중 1건)."""
    basis = {"kaptName": "청담2차현대아파트", "kaptAddr": "서울특별시 강남구 청담동 23 청담2차현대아파트",
             "kaptdaCnt": 217}
    assert identity.verify_kapt_basis_identity("현대아파트", "성동구", 214, basis) == "gu-mismatch"


def test_verify_kapt_basis_identity_fails_name_mismatch_same_gu():
    """같은 구지만 세대수가 tolerance 밖(289 vs 330)이라 개명(rule 2)으로도 구제되지 않는 이름불일치."""
    basis = {"kaptName": "한화포레나정릉아파트", "kaptAddr": "서울특별시 성북구 정릉동 1037 한화포레나정릉아파트",
             "kaptdaCnt": 330}
    assert identity.verify_kapt_basis_identity("정릉꿈에그린", "성북구", 289, basis) == "name-mismatch"


def test_verify_kapt_basis_identity_accepts_stem_full_and_city_prefixed_frame_gu():
    """frame_gu 세 형태(어간·완전형·시도접두) 모두 같은 판정을 내야 한다 — 어간만 오면 _identity_fail_
    reason 의 'addr_gu in frame_gu' 포함검사가 '노원구' ⊄ '노원' 로 오탈락하던 결함 수정(2026-09-05)."""
    basis = {"kaptName": "상계주공2단지", "kaptAddr": "서울특별시 노원구 상계동 155 상계주공2단지",
             "kaptdaCnt": 2000}
    for gu in ("노원", "노원구", "서울 노원구"):
        assert identity.verify_kapt_basis_identity("상계주공2단지", gu, 2000, basis) is None, gu


def test_verify_kapt_basis_identity_skips_count_clause_when_households_unknown():
    """frame_households=0(신규 universe 행 등) 이면 count 절만 면제 — 구·이름은 그대로 검사한다."""
    basis = {"kaptName": "길동우성2차", "kaptAddr": "서울특별시 강동구 길동 400 길동우성2차", "kaptdaCnt": 811}
    assert identity.verify_kapt_basis_identity("길동우성", "강동구", 0, basis) is None          # 세대수 미상 — 면제
    assert identity.verify_kapt_basis_identity("길동우성", "강동구", 811, basis) is None         # 세대수 기지+일치 — 통과
    assert identity.verify_kapt_basis_identity("길동우성", "강동구", 300, basis) == "count-mismatch"  # 세대수 기지+불일치 — 거부


# ── _resolve_kapt_basis 배선(collect_universe_enrich.py) ──────────────────

def test_resolve_kapt_basis_rejects_wrong_gu_candidate_then_accepts_correct(monkeypatch):
    """후보 2개(강남 청담2차현대 오매칭·성동 현대 정매칭) 모두 세대수·준공연도 교차검증(u_ok/y_ok/strong)은
    통과하지만(오매칭 재현 조건) 신원게이트가 첫 후보를 걸러 두 번째를 채택해야 한다(2026-09-05 감사)."""
    monkeypatch.setattr(cue, "_offline_candidates", lambda name, district, lookup: ["WRONG", "RIGHT"])
    monkeypatch.setattr(cue.time, "sleep", lambda *_a, **_k: None)
    basis_by_code = {
        "WRONG": {"kaptName": "청담2차현대아파트", "kaptAddr": "서울특별시 강남구 청담동 23 청담2차현대아파트",
                  "units": 214, "built_year": 2000, "kaptdaCnt": 214},
        "RIGHT": {"kaptName": "현대아파트", "kaptAddr": "서울특별시 성동구 금호동 1 현대아파트",
                  "units": 214, "built_year": 2000, "kaptdaCnt": 214},
    }
    monkeypatch.setattr(cue, "fetch_basis", lambda code, key: basis_by_code.get(code))

    hit = cue._resolve_kapt_basis("현대아파트", "서울 성동구", 214, 2000, {}, "dummy-key")
    assert hit is not None
    code, _b = hit
    assert code == "RIGHT"


def test_resolve_kapt_basis_returns_none_when_every_candidate_fails_identity_gate(monkeypatch):
    """후보 전부가 신원게이트 실패면(구도 이름도 다름) kapt_verified 를 세울 후보가 없다 — None
    (호출측 main() 은 이 None 을 기존 [미채택] 분기로 처리 — kapt 필드 미기록·kapt_verified=False 유지)."""
    monkeypatch.setattr(cue, "_offline_candidates", lambda name, district, lookup: ["WRONG1", "WRONG2"])
    monkeypatch.setattr(cue.time, "sleep", lambda *_a, **_k: None)
    basis_by_code = {
        "WRONG1": {"kaptName": "청담2차현대아파트", "kaptAddr": "서울특별시 강남구 청담동 23 청담2차현대아파트",
                   "units": 214, "built_year": 2000, "kaptdaCnt": 214},
        "WRONG2": {"kaptName": "연남코오롱하늘채", "kaptAddr": "서울특별시 마포구 연남동 573 코오롱하늘채",
                   "units": 214, "built_year": 2000, "kaptdaCnt": 214},
    }
    monkeypatch.setattr(cue, "fetch_basis", lambda code, key: basis_by_code.get(code))
    assert cue._resolve_kapt_basis("현대아파트", "서울 성동구", 214, 2000, {}, "dummy-key") is None


# ── _identity_norm: 지번처럼 보이는 괄호만 벗기는 규칙 ─────────────────────

def test_identity_norm_tightened_jibun_parenthesis_rule():
    """지번처럼 보이는 괄호(대시 포함 또는 3자리+ 순수숫자)만 벗긴다 — 1~2자리 순수숫자는 단지번호
    표기일 수 있어 보존한다(2026-09-05 완화, VWorld 실측 괄호내용 고층/저층/101동/70-12/해등마을/276/
    치현마을/래미안/'1,2차' 검토 결과 '276'류만 지번이고 나머지는 비식별qualifier·법정동·단지고유명)."""
    assert identity._identity_norm("동도센트리움(70-12)") == "동도센트리움"   # 대시 포함 지번
    assert identity._identity_norm("성원(276)") == "성원"                     # 3자리+ 순수숫자 지번
    assert identity._identity_norm("현대(1)") == "현대(1)"                    # 1~2자리는 단지번호일 수 있어 보존


# ── _identity_fail_reason rule 1: 원문·접두어제거 이름 페어를 모두 본다 ────

def test_identity_fail_reason_rule1_checks_raw_pair_when_strip_is_asymmetric():
    """숭인[주상복합]↔숭인상가아파트(종로구 숭인동, 109/109): _strip_dong_prefix 가 '숭인'(제거하면
    빈 문자열이 될 길이라 원문 유지)과 '숭인상가'→'상가'(정상 제거)로 비대칭 절단해 포함관계가 깨지던
    결함(2026-09-05 감사) — 원문(raw) 페어는 애초에 '숭인'⊂'숭인상가'라 포함관계가 성립하므로 통과."""
    assert identity._identity_fail_reason(
        "숭인[주상복합]", "숭인상가아파트", 109, 109,
        kapt_addr="서울특별시 종로구 숭인동 204-11 숭인상가아파트", frame_gu="서울 종로구",
    ) is None
    # 기존 케이스 회귀 없음(동명 접두어 제거 경로)
    assert identity._identity_fail_reason(
        "태진아름", "등촌태진아름", 221, 221, "서울특별시 강서구 등촌동 676 등촌태진아름",
    ) is None
    # 진짜 무관한 이름 + 세대수도 다르면 rule 1 도 rule 2(개명허용) 도 구제 못한다
    assert identity._identity_fail_reason(
        "완전히다른단지", "상계주공2단지", 54, 999,
        "서울특별시 노원구 상계동 155 상계주공2단지", frame_gu="노원구",
    ) == "name-mismatch"


# ── _identity_fail_reason rule 2: gu 일치 + 세대수 정확 일치 = 개명(rebrand) ─

def test_identity_fail_reason_rule2_allows_rebrand_on_exact_household_match():
    """이름이 서로 무관해도 gu 일치 + 세대수 정확 일치면 개명(rebrand)으로 통과 — 한화 꿈에그린→포레나
    리브랜딩, 주공1·2단지→휘경리오포레1·2단지 재건축 후 개명(2026-09-05 감사 근거)."""
    addr_jn = "서울특별시 성북구 정릉동 1037 한화포레나정릉아파트"
    assert identity._identity_fail_reason("정릉꿈에그린", "한화포레나정릉아파트", 349, 349, addr_jn, frame_gu="성북구") is None
    addr_hg1 = "서울특별시 동대문구 휘경동 57 휘경리오포레1단지"
    assert identity._identity_fail_reason("주공1단지", "휘경리오포레1단지", 1224, 1224, addr_hg1, frame_gu="동대문구") is None
    addr_hg2 = "서울특별시 동대문구 휘경동 57 휘경리오포레2단지"
    assert identity._identity_fail_reason("주공2단지", "휘경리오포레2단지", 800, 800, addr_hg2, frame_gu="동대문구") is None


def test_identity_fail_reason_rule2_still_fails_outside_household_tolerance():
    """같은 구·이름무관이라도 세대수가 tolerance(max(3,2%)) 밖이면 개명으로 구제되지 않는다."""
    addr_jn = "서울특별시 성북구 정릉동 1037 한화포레나정릉아파트"
    assert identity._identity_fail_reason("정릉꿈에그린", "한화포레나정릉아파트", 349, 330, addr_jn, frame_gu="성북구") == "name-mismatch"


def test_identity_fail_reason_rule2_gu_mismatch_takes_priority_over_equal_counts():
    """세대수가 완전히 같아도 구가 다르면 rule 2 이전에 ①gu 검사에서 이미 거부된다."""
    addr_jn = "서울특별시 성북구 정릉동 1037 한화포레나정릉아파트"
    assert identity._identity_fail_reason("정릉꿈에그린", "한화포레나정릉아파트", 349, 349, addr_jn, frame_gu="강남구") == "gu-mismatch"


def test_verify_kapt_basis_identity_applies_rule1_and_rule2_via_delegation():
    """verify_kapt_basis_identity 는 _identity_fail_reason 에 위임하므로 rule 1·rule 2 가 그대로 적용된다."""
    basis_sungin = {"kaptName": "숭인상가아파트", "kaptAddr": "서울특별시 종로구 숭인동 204-11 숭인상가아파트",
                    "kaptdaCnt": 109}
    assert identity.verify_kapt_basis_identity("숭인[주상복합]", "종로구", 109, basis_sungin) is None   # rule 1
    basis_jn = {"kaptName": "한화포레나정릉아파트", "kaptAddr": "서울특별시 성북구 정릉동 1037 한화포레나정릉아파트",
               "kaptdaCnt": 349}
    assert identity.verify_kapt_basis_identity("정릉꿈에그린", "성북구", 349, basis_jn) is None          # rule 2


# ── 2026-09-05 메인 검수 보강: 주상복합 hoCnt 폴백 · basis 세대수 미상 · 경계단지 '/' 결합 frame_gu ──

def test_verify_kapt_basis_identity_uses_hocnt_when_kaptdacnt_is_zero_for_mixed_use():
    """주상복합은 K-apt kaptdaCnt=0 이고 hoCnt 만 채워진다(동도센트리움 실측) — hoCnt 로 세대수 절을
    판정해야 포함관계 후보가 count-mismatch 로 오탈락하지 않는다."""
    basis = {"kaptName": "동도센트리움 아파트 오피스텔", "kaptAddr": "서울특별시 구로구 개봉동 70-12",
             "kaptdaCnt": 0, "hoCnt": 136}
    assert identity.verify_kapt_basis_identity("동도센트리움[주상복합]", "구로", 136, basis) is None
    assert identity.verify_kapt_basis_identity("동도센트리움[주상복합]", "구로", 300, basis) == "count-mismatch"


def test_verify_kapt_basis_identity_skips_count_clause_when_basis_households_unknown():
    """basis 세대수(kaptdaCnt·hoCnt 둘 다 0/없음)면 세대수 절은 판정 불가 → 건너뛴다(구·이름 검사는 유지).
    세대수·준공 교차검증은 _resolve_kapt_basis 가 이 게이트 앞에서 이미 수행한다."""
    basis = {"kaptName": "등촌태진아름", "kaptAddr": "서울특별시 강서구 등촌동 1-1"}
    assert identity.verify_kapt_basis_identity("태진아름", "강서", 500, basis) is None
    assert identity.verify_kapt_basis_identity("태진아름", "양천", 500, basis) == "gu-mismatch"
    assert identity.verify_kapt_basis_identity("무관한이름", "강서", 500, basis) == "name-mismatch"


def test_normalize_frame_gu_keeps_every_gu_of_a_slash_joined_border_district():
    """경계단지(둔촌하이츠: FRAME 첫 행 gu=송파, 실제=강동)는 revalidate/audit 가 '서울 송파구/서울 강동구'
    로 넘긴다 — 마지막 토큰만 취하면 송파구가 사라져 실제 소재구가 송파인 단지를 gu-mismatch 로 오탈락시킨다."""
    assert identity._normalize_frame_gu("서울 송파구/서울 강동구") == "송파구/강동구"
    assert identity._normalize_frame_gu("송파/강동") == "송파구/강동구"
    assert identity._normalize_frame_gu("") == ""
    basis = {"kaptName": "둔촌하이츠", "kaptAddr": "서울특별시 강동구 둔촌동 1-1", "kaptdaCnt": 500}
    assert identity.verify_kapt_basis_identity("둔촌하이츠", "서울 송파구/서울 강동구", 500, basis) is None
    assert identity.verify_kapt_basis_identity("둔촌하이츠", "서울 송파구", 500, basis) == "gu-mismatch"
