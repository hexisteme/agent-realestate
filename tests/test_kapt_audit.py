"""audit_kapt_identity.py — K-apt 코드 배정 신원 감사(2026-09-05) 순수 로직."""
import importlib


def _m():
    return importlib.import_module("audit_kapt_identity")


def test_normalize_frame_gu_accepts_stem_full_and_prefixed_forms():
    m = _m()
    assert m.normalize_frame_gu("노원") == "서울 노원구"
    assert m.normalize_frame_gu("노원구") == "서울 노원구"
    assert m.normalize_frame_gu("서울 노원구") == "서울 노원구"
    assert m.normalize_frame_gu("서울특별시 중구") == "서울 중구"
    assert m.normalize_frame_gu("중구") == "서울 중구"


def test_basis_household_count_falls_back_to_hocnt():
    m = _m()
    assert m.basis_household_count({"kaptdaCnt": 712.0, "hoCnt": 700}) == 712
    assert m.basis_household_count({"kaptdaCnt": 0.0, "hoCnt": 136}) == 136
    assert m.basis_household_count({"kaptdaCnt": "x"}) == 0


def test_audit_kapt_basis_reasons():
    m = _m()
    ok = {"kaptName": "등촌부영", "kaptAddr": "서울특별시 강서구 등촌동 691-3 등촌부영", "kaptdaCnt": 712.0}
    assert m.audit_kapt_basis("부영", ["강서"], 712, ok) == "pass"
    wrong_gu = {"kaptName": "청담2차현대", "kaptAddr": "서울특별시 강남구 청담동 1-1", "kaptdaCnt": 420.0}
    assert m.audit_kapt_basis("현대", ["성동"], 430, wrong_gu) == "gu-mismatch"
    assert m.audit_kapt_basis("현대", ["성동", "강남"], 430, {**wrong_gu, "kaptName": "현대"}) == "pass"   # 인접 스캔 구 중복 행
    other = {"kaptName": "한신", "kaptAddr": "서울특별시 성동구 행당동 1-1", "kaptdaCnt": 430.0}
    # rule 2(2026-09-05 개명 허용): 구 일치 + 세대수 정확 일치(±max(3,2%))면 이름이 무관해도 개명으로 간주해 통과
    # (정릉꿈에그린→한화포레나정릉, 주공1·2단지→휘경리오포레). 세대수만 우연히 같은 타 단지를 오통과시키는
    # 잔여위험은 문서화된 수용 리스크(identity._identity_fail_reason ④) — 세대수가 벌어지면 다시 name-mismatch.
    assert m.audit_kapt_basis("현대", ["성동"], 430, other) == "pass"
    assert m.audit_kapt_basis("현대", ["성동"], 430, {**other, "kaptdaCnt": 460.0}) == "name-mismatch"
    far = {"kaptName": "현대홈타운", "kaptAddr": "서울특별시 성동구 행당동 1-1", "kaptdaCnt": 1200.0}
    assert m.audit_kapt_basis("현대", ["성동"], 430, far) == "count-mismatch"
    assert m.audit_kapt_basis("현대", ["성동"], 430, None) == "no-basis"
    assert m.audit_kapt_basis("", ["성동"], 430, ok) == "no-frame"


def test_null_kapt_fields_keeps_non_kapt_fields():
    m = _m()
    e = {"kapt_code": "A1", "kapt_verified": True, "heating": "개별난방", "corridor_type": "계단식",
         "parking_per_unit": 1.1, "builder": "x", "maint_fee_won": 1000, "gongsi_man": 500,
         "academy_exam": 3, "nearest_elem_school": "s", "gu_ipsi_academy": 400}
    changed = m.null_kapt_fields(e)
    assert set(changed) == set(m.KAPT_FIELDS) | {"kapt_verified"}
    assert e["kapt_verified"] is False and e["kapt_code"] is None and e["maint_fee_won"] is None
    assert e["academy_exam"] == 3 and e["nearest_elem_school"] == "s" and e["gu_ipsi_academy"] == 400
    assert m.null_kapt_fields({"kapt_verified": False}) == []


def test_candidate_derivation_uses_frame_and_universe_fields():
    m = _m()
    ov = {"10": {"kapt_code": "A1"}, "11": {"kapt_code": None}, "12": {"kapt_code": "A2"}}
    frame = m.index_frame([{"complexNo": 10, "name": "부영", "gu": "양천", "households": 712},
                           {"complexNo": 10, "name": "부영", "gu": "강서", "households": 712}])
    assert frame["10"]["gus"] == ["양천", "강서"]
    c = m.derive_overlay_candidates(ov, frame)
    assert [x["key"] for x in c] == ["10", "12"] and c[0]["households"] == 712 and c[0]["gus"] == ["양천", "강서"] and c[1]["name"] == ""
    c2 = m.derive_overlay_candidates(ov, frame, {"10": "강서"})
    assert c2[0]["gus"] == ["강서"]   # 데이터셋 구가 있으면 단독 정본
    assert m.join_frame_gus(["송파", "강동구", "송파"]) == "서울 송파구/서울 강동구"
    rows = [{"complex_no": 7, "complex_name": "부영", "district": "서울 강서구", "units": 712, "kapt_code": "A1"},
            {"complex_no": 8, "complex_name": "x", "district": "서울 강서구", "units": 1, "kapt_code": None}]
    u = m.derive_universe_kapt_candidates(rows)
    assert u == [{"source": "universe", "key": "7", "name": "부영", "gus": ["서울 강서구"], "households": 712, "kapt_code": "A1"}]


def test_resolve_gu_by_cno_layers_district_map_under_published_dataset(tmp_path):
    """구 판정 정본 = 발행 데이터셋(실거래 LAWD 확정) > 좌표 소재구 맵. 서울 밖은 아예 넣지 않는다.

    FRAME 스캔 구를 후보로 쓰면 소재구가 후보에 없는 단지의 타 구 K-apt 코드가 통과한다
    (2026-09-06 감사에서 마포 성원·동대문 동아·광진 삼성 3건 실적발)."""
    import json
    m = _m()
    dmap = tmp_path / "frame_district_20260906.json"
    dmap.write_text(json.dumps({
        "842": {"sido": "서울특별시", "gu": "마포", "dong": "신수동"},
        "111": {"sido": "서울특별시", "gu": "성동", "dong": "성수동1가"},
        "999": {"sido": "경기도", "gu": "하남", "dong": "덕풍동"},
        "888": {"sido": "서울특별시", "gu": ""},
    }, ensure_ascii=False), encoding="utf-8")
    ds = tmp_path / "dataset.json"
    ds.write_text(json.dumps({"complexes": [{"complex_no": "111", "gu": "광진"}]},
                             ensure_ascii=False), encoding="utf-8")

    out = m.resolve_gu_by_cno(str(ds), str(dmap))
    assert out["842"] == "마포"        # 맵 단독
    assert out["111"] == "광진"        # 발행 데이터셋이 맵을 덮는다
    assert "999" not in out           # 서울 밖 제외
    assert "888" not in out           # 구 미상 제외


def test_resolve_gu_by_cno_tolerates_missing_files(tmp_path):
    m = _m()
    assert m.resolve_gu_by_cno("", "") == {}
    assert m.resolve_gu_by_cno(str(tmp_path / "none.json"), str(tmp_path / "none2.json")) == {}
