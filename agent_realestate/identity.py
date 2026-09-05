"""agent_realestate/identity.py — 단지 신원(identity) 검증 게이트(2026-09-05 collect_gongsi.py 에서 이관).

collect_gongsi.py 의 VWorld 공시가 신원게이트(verify_parcel_identity)와 collect_universe_enrich.py 의
K-apt 코드 배정 신원게이트(verify_kapt_basis_identity)가 이 모듈의 로직을 공유한다 — 둘 다 "이 레코드/코드가
정말 이 단지의 것인가"를 같은 규칙(구·이름·세대수)으로 판정해야 타 단지 오매칭(성동 '현대'→강남
청담2차현대 등)을 동일하게 막는다.

순환 임포트 결정: canonical_complex_name 은 blog.build_explorer 에 있고 그 모듈은 최상단에서
agent_realestate.collectors.naver_live 를 import 한다. agent_realestate/__init__.py 는 아무것도 import
하지 않는 빈 패키지 초기화(버전 문자열만)이고, naver_live 는 agent_realestate.domain 만 참조할 뿐
agent_realestate.identity 로 되돌아오지 않으므로 이 모듈이 blog.build_explorer 를 최상단에서 import 해도
순환이 생기지 않는다(collect_gongsi.py 가 이미 동일 패턴으로 이 import 를 쓰고 있었다 — 실측 확인).
따라서 지연 임포트 없이 canonical_complex_name 을 그대로 가져온다.
"""
from __future__ import annotations

import re

from blog.build_explorer import canonical_complex_name


def _identity_norm(nm: str) -> str:
    """단지 신원(identity) 검증 전용 느슨한 정규화 — build_explorer.canonical_complex_name(대괄호·비식별
    괄호·브랜드표기[IPARK/아이파크·e편한세상/이편한세상·SK뷰 계열·자이/XI] 을 이미 접는다) 위에 이 모듈
    고유 확장을 더한다: 'N차'→'N'(전위치, 하계1차청구↔하계1청구) · '주상' 삭제(삼창타워프라자↔
    삼창타워주상프라자) · 지번처럼 보이는 괄호(대시·3자리+ 숫자) 삭제 · 중간 위치 '아파트' 삭제(동도센트리움 아파트 오피스텔). match_molit_names(발행 경로)에는 쓰지 않는다 — 전위치 N차 collapse 는
    상계주공1~16단지 뭉침 재발 위험이라 발행 경로엔 부적합하고 이 모듈의 완화된 신원확인(§verify_
    parcel_identity)에서만 쓴다. 숫자 자체는 보존 — 단지 번호 차이는 여전히 불일치."""
    c = canonical_complex_name(nm) or ""
    c = re.sub(r"\(\s*(?:\d+-\d*|\d{3,})\s*\)", "", c)   # 지번처럼 보이는 괄호만 제거 — 대시 포함('70-12') 또는 3자리+ 순수숫자('276')만 지번으로 간주, 1~2자리 순수숫자('현대(1)'·'현대(12)')는 단지번호 표기일 수 있어 보존(2026-09-05 완화 — VWorld 실측 괄호내용 고층/저층/101동/70-12/해등마을/276/치현마을/래미안/'1,2차' 검토, '276'류만 지번이고 나머지는 비식별qualifier·법정동·단지고유명이었다)
    c = c.replace("아파트", "").replace("주상", "")   # 중간 위치 '아파트'(K-apt '동도센트리움 아파트 오피스텔') — canonical 은 말미 '아파트'만 제거
    c = re.sub(r"(\d)단지$", r"\1", c)   # 'N단지' 접미어 → 'N'(도봉파크빌3단지↔도봉파크빌3; 번호는 보존되어 1단지≠2단지)
    return re.sub(r"(\d)차", r"\1", c)


_DONG_TOKEN_RE = re.compile(r"^(?P<stem>[가-힣]+?)(?:동\d*가|동|가|읍|면|리)$")
_GU_TOKEN_RE = re.compile(r"^(?P<stem>[가-힣]{1,4})구$")


def _addr_gu(kapt_addr: str) -> str:
    """K-apt 지번주소의 자치구 토큰('강남구'). 없으면 ''."""
    for tok in kapt_addr.split():
        if _GU_TOKEN_RE.match(tok):
            return tok
    return ""


def _dong_prefix_forms(kapt_addr: str) -> list[str]:
    """K-apt 지번주소에서 단지명 접두어 후보 — 자치구 어간('서대문구'→'서대문')과 법정동 어간('등촌동'→'등촌',
    '당산동5가'→'당산'). 어간이 1자('창동'→'창')면 오절단 위험이라 전체 토큰('창동')을 쓴다. 전체 동 토큰을
    우선하면 '답십리동'+'서울한양' 처럼 이름의 '동'을 잘라먹으므로(답십리+동서울한양) 어간이 우선이다."""
    forms: list[str] = []
    gu = _addr_gu(kapt_addr)
    if gu and len(gu) > 2:
        forms.append(gu[:-1])
    for tok in kapt_addr.split():
        m = _DONG_TOKEN_RE.match(tok)
        if m and len(tok) >= 2:
            stem = m.group("stem")
            forms.append(stem if len(stem) >= 2 else tok)
            break
    return forms


def _strip_dong_prefix(norm_name: str, forms: list[str]) -> str:
    """정규화 단지명에서 주소의 법정동 접두어를 1회 제거('등촌태진아름'→'태진아름'). 남는 게 없으면 원문."""
    for f in forms:
        if norm_name.startswith(f) and len(norm_name) > len(f):
            return norm_name[len(f):]
    return norm_name


def count_households(recs: list[dict]) -> int:
    """VWorld 공시가 레코드의 실제 호수 — (dongNm, hoNm) 기준 distinct. 같은 호가 2건씩 오는
    실측(2026-09-05, 강남자곡힐스테이트 2,678건/1,339호)이라 len(recs) 는 세대수의 2배가 되어
    count-mismatch 오판을 낳았다. dong/ho 가 전부 비면 len(recs) 로 폴백."""
    keys = {(str(r.get("dongNm") or ""), str(r.get("hoNm") or "")) for r in recs}
    keys.discard(("", ""))
    return len(keys) if keys else len(recs)


def _shorter_in_longer(x: str, y: str, min_len: int) -> bool:
    """정규화된 두 이름 중 짧은 쪽이 min_len 이상이면서 긴 쪽에 포함되는지 — 원문(raw)·동접두어제거
    (stripped) 두 페어 모두에 동일한 단방향 포함 규칙을 적용하기 위한 공용 판정(2026-09-05, rule 1)."""
    shorter, longer = (x, y) if len(x) <= len(y) else (y, x)
    return len(shorter) >= min_len and shorter in longer


def _identity_fail_reason(aphus_nm: str, kapt_name: str, record_count: int, units: int,
                          kapt_addr: str = "", frame_gu: str = "") -> str | None:
    """verify_parcel_identity 판정의 실패 사유(로그 구분용) — 통과면 None, 아니면
    'gu-mismatch'|'name-mismatch'|'count-mismatch'. 로직은 verify_parcel_identity 와 단일 소스.
    ① frame_gu(발행 프레임의 자치구)와 kapt_addr 의 자치구가 다르면 즉시 거부 — 구식 substring 해소가
       성동 '현대'→강남 청담2차현대, 은평 코오롱하늘채→마포 연남동 처럼 타 구 kapt_code 를 붙인 5건(2026-09-05 전량 재검증).
    ② kapt_addr 가 있으면 양쪽 이름에서 그 주소의 자치구·법정동 접두어를 걷어낸 뒤 비교('등촌태진아름'↔'태진아름').
       원문(raw)·접두어제거(stripped) 두 페어를 각각 비교한다(2026-09-05 rule 1) — 접두어 제거가 한쪽
       에만 걸리는 비대칭(같은 길이면 제거를 보류하는 가드 때문에, 예: '숭인'↔'숭인상가'에서 '숭인'은
       제거 후 빈 문자열이 되어 원문 유지되지만 '숭인상가'는 '상가'로 잘려 서로 무관해짐)이 원문에서는
       이미 포함관계였던 케이스를 망가뜨리는 오탈락을 방지한다(숭인[주상복합]↔숭인상가아파트 감사).
    ③ 완전일치(원문 또는 접두어제거 어느 한쪽)면 즉시 통과 — 카운트 검사 이전에 단락.
       완전일치가 아니면 단방향 포함(원문 또는 접두어제거 어느 한쪽)을 딱 하나의 조건에서만 허용 —
       포함되는(짧은) 쪽 정규화 이름이 5자 이상(주소가 있으면 2자) AND units>0 AND VWorld 레코드 수
       (호수, 전 페이지 합)가 K-apt 세대수(units)와 25% 이내로 일치할 때만(세대수가 다른 단지끼리는
       이 경로로도 통과 불가).
    ④ 위 어느 것도 아니면(이름이 서로 무관) — gu 가 확인된 상태(kapt_addr·frame_gu 둘 다 있고 ①을
       통과)에서 세대수가 ±max(3, 2%) 이내로 정확히 일치하면 개명(rebrand)으로 간주해 통과시킨다
       (2026-09-05 감사 근거: 정릉꿈에그린→한화포레나정릉아파트, 상계/휘경 주공1·2단지→휘경리오포레1·
       2단지 — 시공사·조합이 재건축/리모델링 후 단지명을 바꾼 사례들이 세대수는 그대로 유지). 근거는
       'gu 일치 + 세대수 정확 일치' 뿐이라, 실제로는 무관한 두 단지가 세대수만 우연히 같은 경우를
       개명으로 오통과시킬 잔여위험이 있다 — 수용된 리스크로 문서화한다(주소·구 자체가 다르면 ①에서
       이미 걸러진다는 전제 위에서만 감수 가능한 리스크).
    실패 사유(name-mismatch/count-mismatch)는 이 함수로 조회해 로그에 남긴다."""
    addr_gu = _addr_gu(kapt_addr)
    if frame_gu and addr_gu and addr_gu not in frame_gu.replace(" ", ""):
        return "gu-mismatch"
    a_raw = _identity_norm(aphus_nm)
    k_raw = _identity_norm(kapt_name)
    if not a_raw or not k_raw:
        return "name-mismatch"
    forms = _dong_prefix_forms(kapt_addr)
    a_strip = _strip_dong_prefix(a_raw, forms)
    k_strip = _strip_dong_prefix(k_raw, forms)
    if a_raw == k_raw or a_strip == k_strip:
        return None
    min_len = 2 if kapt_addr else 5
    if _shorter_in_longer(a_raw, k_raw, min_len) or _shorter_in_longer(a_strip, k_strip, min_len):
        if units <= 0 or abs(record_count - units) > units * 0.25:
            return "count-mismatch"
        return None
    if kapt_addr and frame_gu and units > 0 and abs(record_count - units) <= max(3, units * 0.02):
        return None   # rule 2: gu 일치 + 세대수 정확 일치 = 개명 증거(잔여위험은 위 ④ 참조)
    return "name-mismatch"


def verify_parcel_identity(aphus_nm: str, kapt_name: str, record_count: int, units: int,
                           kapt_addr: str = "", frame_gu: str = "") -> bool:
    """VWorld 공시가 레코드(aphus_nm)가 실제로 이 K-apt 단지(kapt_name)의 것인지 검증 — 타 단지
    pnu 오조립 방어(2026-09-05 수정, 구 _name_gate 대체). ① _identity_norm 정규화 후 완전일치면 통과.
    ② 완전일치가 아니면 단방향 포함(containment)을 딱 하나의 조건에서만 허용 — 포함되는(짧은) 쪽
    정규화 이름이 5자 이상 AND units>0 AND VWorld 레코드 수(호수, 전 페이지 합)가 K-apt 세대수(units)
    와 25% 이내로 일치할 때만(세대수가 다른 단지끼리는 이 경로로도 통과 불가). 그 외 전부 실패.
    실패 사유(name-mismatch/count-mismatch)는 _identity_fail_reason 으로 별도 조회해 로그에 남긴다."""
    return _identity_fail_reason(aphus_nm, kapt_name, record_count, units, kapt_addr, frame_gu) is None


def _normalize_frame_gu(frame_gu: str) -> str:
    """frame_gu 를 '구'로 끝나는 한 토큰으로 정규화 — '노원'→'노원구', '노원구'→'노원구',
    '서울 노원구'→'노원구'(마지막 토큰만 사용). _identity_fail_reason 의 gu 포함검사(addr_gu in
    frame_gu)가 세 표기 형태 전부에서 성립하게 한다 — 어간만 오면 '노원구' ⊄ '노원' 로 오탈락하던
    결함 수정(2026-09-05)."""
    parts = []
    for part in (frame_gu or "").split("/"):   # 경계단지는 '서울 송파구/서울 강동구' 처럼 '/'로 이어져 온다(revalidate·audit)
        toks = part.split()
        if not toks:
            continue
        last = toks[-1]
        parts.append(last if last.endswith("구") else f"{last}구")
    return "/".join(parts)


def verify_kapt_basis_identity(frame_name: str, frame_gu: str, frame_households: int,
                               basis: dict) -> str | None:
    """K-apt 코드 배정(basis)이 실제로 이 발행 프레임 단지(frame_name)인지 검증 —
    collect_universe_enrich._resolve_kapt_basis 의 기존 교차검증(세대수·준공연도만)이 놓친 타 구
    오매칭(성동 '현대'→강남 청담2차현대 등 14건, 2026-09-05 감사) 방어. 로직은 verify_parcel_identity/
    _identity_fail_reason 과 단일 소스(rule 1 이름-페어 이중검사·rule 2 개명허용 포함) — basis 의
    kaptName·kaptAddr·kaptdaCnt(또는 hoCnt)를 그 함수의 kapt_name·kapt_addr·record_count 자리에
    그대로 넣는다.

    frame_gu 는 어간('노원')·완전형('노원구')·시도 접두('서울 노원구') 세 형태 전부 허용하고, 경계단지의
    '/' 결합('서울 송파구/서울 강동구')도 부분별로 정규화해 어느 한 구가 맞으면 통과한다 —
    _normalize_frame_gu 로 '구'로 끝나는 형태로 맞춘 뒤 넘긴다.

    frame_households 가 0/미상이거나(신규 universe 행 등) basis 세대수가 0/미상이면(주상복합은
    kaptdaCnt=0 이고 hoCnt 만 채워지며, 둘 다 비면 미상) 세대수 절을 건너뛴다 — count-mismatch 는
    한쪽을 모르는 상태에서 판정할 수 없고, 여기서 오탈락시키면 구·이름이 맞는 후보까지 통째로
    버리게 된다(세대수·준공 교차검증은 _resolve_kapt_basis 가 이 게이트 앞에서 이미 했다).
    구·이름 검사(rule 2 개명허용 포함)는 그대로 적용된다."""
    record_count = int(basis.get("kaptdaCnt") or basis.get("hoCnt") or 0)
    reason = _identity_fail_reason(
        aphus_nm=frame_name,
        kapt_name=basis.get("kaptName", ""),
        record_count=record_count,
        units=frame_households,
        kapt_addr=basis.get("kaptAddr", ""),
        frame_gu=_normalize_frame_gu(frame_gu),
    )
    if reason == "count-mismatch" and (frame_households <= 0 or record_count <= 0):
        return None
    return reason
