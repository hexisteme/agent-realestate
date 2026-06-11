"""naver_nav.parse_nav — 단지 검색→이동 결과 파싱 (수집 전 탭 위치 자동화)."""

from agent_realestate.collectors.naver_nav import navigate_to_complex, parse_nav


def test_parse_nav_found():
    r = parse_nav('{"query":"등촌주공5단지","found":true,"complexNo":13,'
                  '"complexName":"등촌주공5단지","navigatedTo":"/complexes/13"}')
    assert r.found is True
    assert r.complex_no == "13"            # int → str 정규화
    assert r.name == "등촌주공5단지"
    assert r.navigated_to == "/complexes/13"


def test_parse_nav_not_found():
    r = parse_nav('{"query":"없는단지","found":false}')
    assert r.found is False
    assert r.complex_no is None


def test_navigate_blank_graceful():
    """빈 이름 → found=False (추정·예외 없이 graceful, G1)."""
    r = navigate_to_complex("   ")
    assert r.found is False


def test_navigate_missing_script_graceful():
    """스크립트 부재 → found=False (Chrome/스크립트 없을 때 안전)."""
    r = navigate_to_complex("등촌주공5단지", script="/nonexistent/naver-nav.sh")
    assert r.found is False
