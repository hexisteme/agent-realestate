"""Search packaging keeps population, comparison windows and content coverage truthful."""
from blog.daily_digest import _build_digest_tags, _build_digest_title, build_daily_digest
from test_daily_digest import _inventory, _sample_ds


def test_title_preserves_comparison_population_and_windows_without_market_direction_claim():
    title = _build_digest_title(
        "2026-10-15", {"n_total": 3000, "n_judged": 550, "up": 100, "down": 449, "flat": 1},
        {"hi": [{"name": "두건뿐인단지", "gap_eok": 2}]},
    )
    assert "비교 550단지" in title
    assert "최근 3개월 vs 직전 9개월" in title
    assert "2026-10-15" in title
    assert all(text not in title for text in ("3,000", "상승", "하락", "보합", "두건뿐인단지", "갭"))


def test_no_comparison_does_not_call_mixed_observations_apartments():
    title = _build_digest_title("2026-10-15", {"n_total": 7, "n_judged": 0}, {})
    assert "관측 7단지" in title and "기간 비교 미산출" in title
    assert "아파트" not in title


def test_tags_do_not_promise_inventory_or_jeonse_without_content():
    tags = _build_digest_tags({}).split(",")
    assert "아파트매물" not in tags and "전세가율" not in tags
    assert "오늘의변화" not in tags and "아파트회전율" not in tags


def test_tags_cover_downside_rows_and_normalize_gu_suffix():
    tags = _build_digest_tags({"lo": [{"gu": "중구"}, {"gu": "강남"}, {"gu": "강남구"}]}).split(",")
    assert "중구아파트" in tags and "중구구아파트" not in tags
    assert tags.count("강남구아파트") == 1


def test_tags_only_include_current_complete_inventory_and_available_sections():
    selection = {"jr": [{"gu": "노원"}], "tv": [{"gu": "도봉"}]}
    inventory = {"fresh": True, "complete": True, "districts": {"노원": {}}}
    tags = _build_digest_tags(selection, inventory=inventory).split(",")
    assert {"아파트매물", "전세가율", "아파트회전율"} <= set(tags)
    for unavailable in (
        None, {}, {**inventory, "fresh": False}, {**inventory, "complete": False},
        {**inventory, "districts": {}},
    ):
        assert "아파트매물" not in _build_digest_tags(selection, inventory=unavailable).split(",")


def test_tags_are_unique_bounded_and_stable_at_full_coverage():
    selection = {
        "hi": [{"gu": "강남"}, {"gu": "강남구"}],
        "lo": [{"gu": "중구"}, {"gu": "서초"}],
        "jr": [{"gu": "노원"}], "tv": [{"gu": "도봉"}],
    }
    inventory = {"fresh": True, "complete": True, "districts": {"강남": {}}}
    value = _build_digest_tags(selection, inventory=inventory)
    tags = value.split(",")
    assert len(tags) <= 10 and len(tags) == len(set(tags))
    assert value == _build_digest_tags(selection, inventory=inventory)
    assert {"강남구아파트", "중구아파트", "서초구아파트", "노원구아파트"} <= set(tags)


def test_digest_tags_use_the_actual_inventory_view():
    ds = _sample_ds()
    without = build_daily_digest(ds, "2026-10-15", "2026-10-14")
    assert "아파트매물" not in without["tags"].split(",")
    ds["listing_inventory"] = _inventory()
    with_inventory = build_daily_digest(ds, "2026-10-15", "2026-10-14")
    assert "아파트매물" in with_inventory["tags"].split(",")
    assert "서울 25개 구 네이버 표시 매물" in with_inventory["tistory_html"]
