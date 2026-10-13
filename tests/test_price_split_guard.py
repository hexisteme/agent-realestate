"""동명 단지 가격 군집 가드의 사이트 확대(2026-10-14 승인) — 정확 면적 군집 중위비 ≥1.4 면 발행 보류."""
import json

import blog.build_explorer as be
from blog import insight_series as ins


def _trades(name, groups, ym="202608"):
    """groups = [(정확면적, 가격억, 건수)] → MOLIT 행."""
    return [{"apt": name, "area": area, "price": int(eok * 1e8), "ym": ym}
            for area, eok, n in groups for _ in range(n)]


SPLIT = [(84.92, 17.0, 4), (84.89, 9.0, 4)]          # 동작 대림 실측 형태: 같은 이름 아래 두 가격 무리
CLEAN = [(84.92, 16.0, 4), (84.89, 16.5, 4)]


def _frame_row(cno, name, gu="동작"):
    return {"complexNo": cno, "name": name, "gu": gu, "households": 500, "builtYm": "199501",
            "far": 200, "type": "아파트"}


def _uni_row(name, district, cno, area=84.9):
    return {"complex_name": name, "dong_ho": "101동", "area_exclusive_m2": area, "floor": "3/15층",
            "facing": "남향", "price_krw": int(8.0e8), "agent_name": "테스트공인", "confirmed_date": "2026-06-03",
            "units": 500, "built_year": 1995, "far_pct": 200.0, "land_share_pyeong": 12.0,
            "land_share_is_estimate": False, "jeonse_krw": None, "district": district,
            "saenghwalgwon": "테스트생활권", "cbd_km": 5.0, "cbd_name": "여의도", "slope_pct": 3.0,
            "complex_no": cno, "broker_count": 1, "regulated": False, "redev_stage": "NONE"}


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_series_and_site_share_one_rule():
    assert ins.detect_area_price_split is be.detect_area_price_split
    assert (be.SPLIT_RATIO, be.SPLIT_MIN_GROUP) == (1.4, 3)


def test_public_path_holds_split_complex_and_keeps_clean_one(tmp_path):
    frame = [_frame_row(7, "대림"), _frame_row(8, "벽산")]
    molit = {"11590": _trades("대림", SPLIT) + _trades("벽산", CLEAN)}
    ds = be.build_dataset_public(_write(tmp_path, "f.json", frame), _write(tmp_path, "m.json", molit),
                                 "2026-10-13", "2026-10-14")
    assert [r["name"] for r in ds["complexes"]] == ["벽산"]
    assert ds["excluded"]["price_split"] == 1
    assert ds["held_price_split"] == [{"gu": "동작", "name": "대림", "complex_no": "7"}]


def test_legacy_path_holds_split_complex(tmp_path):
    uni = [_uni_row("대림", "서울 동작구", "7"), _uni_row("벽산", "서울 동작구", "8")]
    molit = {"11590": _trades("대림", SPLIT) + _trades("벽산", CLEAN)}
    ds = be.build_dataset(_write(tmp_path, "u.json", uni), _write(tmp_path, "m.json", molit), "2026-10-13", "2026-10-14")
    assert [r["name"] for r in ds["complexes"]] == ["벽산"]
    assert ds["excluded"]["price_split"] == 1
    assert ds["held_price_split"] == [{"gu": "동작", "name": "대림", "complex_no": "7"}]


def test_complex_held_on_legacy_path_is_not_regenerated_from_frame(tmp_path):
    """A 경로에서 보류한 단지명이 B 억제 목록에서 빠지면 프레임이 같은 단지를 다른 번호로 다시 만든다."""
    uni = [_uni_row("대림", "서울 동작구", "A7")]
    frame = [_frame_row(7, "대림")]
    molit = {"11590": _trades("대림", SPLIT)}
    ds = be.build_dataset_public(_write(tmp_path, "f.json", frame), _write(tmp_path, "m.json", molit),
                                 "2026-10-13", "2026-10-14", anchor_universe=_write(tmp_path, "u.json", uni))
    assert ds["complexes"] == []
    assert ds["excluded"]["price_split"] == 1
    assert [h["name"] for h in ds["held_price_split"]] == ["대림"]


def test_unsplit_small_groups_publish(tmp_path):
    """3건 미만 군집은 판정에서 빠진다 — 거래가 적은 단지를 보류하지 않는다."""
    frame = [_frame_row(9, "현대")]
    molit = {"11590": _trades("현대", [(84.92, 17.0, 6), (84.89, 9.0, 2)])}
    ds = be.build_dataset_public(_write(tmp_path, "f.json", frame), _write(tmp_path, "m.json", molit),
                                 "2026-10-13", "2026-10-14")
    assert [r["name"] for r in ds["complexes"]] == ["현대"] and ds["excluded"]["price_split"] == 0


def test_legacy_hold_blocks_frame_regeneration_at_another_anchor_area(tmp_path):
    """G6 Codex 반례: A 의 84㎡ 표본이 보류되면 B 가 같은 이름의 59㎡(최다 거래 평형)로 다시 발행하던 경로."""
    uni = [_uni_row("대림", "서울 동작구", "A7")]
    frame = [_frame_row(7, "대림")]
    molit = {"11590": _trades("대림", [(84.89, 9.0, 3), (84.92, 17.0, 3), (59.9, 6.0, 10)])}
    ds = be.build_dataset_public(_write(tmp_path, "f.json", frame), _write(tmp_path, "m.json", molit),
                                 "2026-10-13", "2026-10-14", anchor_universe=_write(tmp_path, "u.json", uni))
    assert ds["complexes"] == [] and ds["excluded"]["price_split"] == 1
