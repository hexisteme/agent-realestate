"""카카오/서울 수집기 파서 — 네트워크 없이."""
from agent_realestate.collectors.kakao import walk_min_from_distance
from agent_realestate.collectors.seoul_redev import map_stage, parse_seoul_json


def test_kakao_walk_min():
    assert walk_min_from_distance(670) == 10
    assert walk_min_from_distance(335) == 5
    assert walk_min_from_distance(10) == 1     # 최소 1분


def test_seoul_stage_map():
    assert map_stage("관리처분인가") == "MGMT_DISPOSAL"
    assert map_stage("조합설립인가") == "UNION_SETUP"
    assert map_stage("안전진단 통과") == "SAFETY_PASS"
    assert map_stage("정비구역 지정") == "PROMOTION"
    assert map_stage("이주/철거 단계") == "MOVE_OUT"
    assert map_stage("미정") == "NONE"


SAMPLE = ('{"tbgisJeongbiSaupInfo":{"list_total_count":2,'
          '"RESULT":{"CODE":"INFO-000","MESSAGE":"정상"},'
          '"row":[{"SAUP_NM":"상계주공5단지","STEP":"관리처분인가"},'
          '{"SAUP_NM":"상계주공3단지","STEP":"안전진단"}]}}')


def test_seoul_parse_json():
    rows = parse_seoul_json(SAMPLE)
    assert len(rows) == 2
    s5 = next(r for r in rows if "상계주공5" in r["name"])
    assert s5["stage"] == "MGMT_DISPOSAL"
