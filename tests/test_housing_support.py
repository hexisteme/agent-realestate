"""Official notice dates/status differ from category guidance and announcement state."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "housing_support"
LH_DETAIL = "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancInfo.do?panId=2015122300020843&ccrCnntSysDsCd=03&uppAisTpCd=13&aisTpCd=26&mi=1026"


def collector():
    return importlib.import_module("agent_realestate.collectors.housing_support")


def renderer():
    return importlib.import_module("blog.housing_support")


def notice(**changes):
    fields = dict(issuer="lh", title="서울 청년 공공임대 모집공고", url=LH_DETAIL,
                  published_on="2026-10-01", apply_start="2026-10-06", apply_end="2026-10-16",
                  scope="서울", announcement_end="2026-10-16", declared_state="공고중")
    fields.update(changes)
    return collector().HousingNotice(**fields)


def snapshot(**changes):
    result = collector().empty_housing_support("2026-10-03")
    result.update(changes)
    return result


def test_lh_actual_list_keeps_announcement_state_separate_from_application_dates():
    notices = collector().parse_lh_notice_list((FIXTURES / "lh_notice_list.html").read_text())
    assert len(notices) == 1
    item = notices[0]
    assert item.published_on == "2026-10-01" and item.announcement_end == "2026-10-16"
    assert item.scope == "서울" and item.url == LH_DETAIL
    assert item.declared_state == "공고중"
    assert item.apply_start is None and item.apply_end is None
    assert collector().notice_status(item, "2026-10-03") == "미확인"
    assert "1일전" not in item.title


def test_sh_actual_list_and_explicit_application_period():
    c = collector()
    items = c.parse_sh_notice_list((FIXTURES / "sh_notice_list.html").read_text())
    assert len(items) == 2 and all(item.scope == "서울" for item in items)
    spouses = next(item for item in items if "310650" in item.url)
    assert spouses.published_on == "2026-09-30" and spouses.apply_start is None
    assert c.parse_sh_application_period((FIXTURES / "sh_notice_period.html").read_text()) == ("2026-10-13", "2026-10-15")
    assert c.parse_sh_application_period("<p>공고일 2026.09.30; 마감일 2026.10.15</p>") == (None, None)


def test_sh_application_period_rejects_invalid_calendar_and_ambiguous_ranges():
    text = (FIXTURES / "sh_notice_period.html").read_text()
    for broken in (text.replace("2026. 10. 13.", "2026. 13. 13."), text + text):
        with pytest.raises(ValueError, match="^주거지원 공식 공고 표를 해석할 수 없음$"):
            collector().parse_sh_application_period(broken)


@pytest.mark.parametrize(("today", "expected"), [
    ("2026-10-03", "예정"), ("2026-10-06", "미확인"),
    ("2026-10-07", "접수중"), ("2026-10-16", "미확인"), ("2026-10-17", "마감"),
])
def test_application_status_is_date_based_with_inclusive_deadline(today, expected):
    assert collector().notice_status(notice(), today) == expected


def test_announcement_state_or_partial_dates_do_not_confirm_application_status():
    assert collector().notice_status(notice(apply_start=None, apply_end=None, declared_state="접수중"), "2026-10-03") == "미확인"
    assert collector().notice_status(notice(apply_start=None), "2026-10-03") == "미확인"


@pytest.mark.parametrize(("asof", "expected"), [
    ("2026-10-06T09:59:00+09:00", "예정"), ("2026-10-06T10:00:00+09:00", "접수중"),
    ("2026-10-16T17:00:00+09:00", "마감"), ("2026-10-16T17:01:00+09:00", "마감"),
])
def test_verified_exact_times_control_boundary_day_status(asof, expected):
    item = notice(apply_start_at="2026-10-06T10:00:00+09:00", apply_end_at="2026-10-16T17:00:00+09:00")
    assert collector().notice_status(item, asof[:10], asof_datetime=asof) == expected


def test_sh_schedule_preserves_exact_public_times():
    result = collector().parse_sh_application_schedule((FIXTURES / "sh_notice_period.html").read_text())
    assert result == ("2026-10-13T10:00:00+09:00", "2026-10-15T17:00:00+09:00")


@pytest.mark.parametrize("changes", [
    {"url": "https://apply.lh.or.kr.evil.example/notice"},
    {"url": "https://evil.example/notice"},
    {"url": "http://apply.lh.or.kr/notice"},
    {"url": "https://user:pass@apply.lh.or.kr/notice"},
    {"url": "https://apply.lh.or.kr/notice?token=PRIVATE"},
    {"published_on": "2026-13-01"},
    {"published_on": "2026-02-30"},
    {"published_on": "2026-10"},
    {"apply_end": "2026-10-05"},
    {"scope": "미상"},
    {"issuer": "unregistered"},
])
def test_invalid_date_scope_or_nonofficial_origin_raises_fixed_error(changes):
    with pytest.raises(ValueError, match="^주거지원 공고 입력을 확인할 수 없음$"):
        notice(**changes)


def test_lh_region_column_filters_non_seoul_notices():
    text = (FIXTURES / "lh_notice_list.html").read_text().replace("서울특별시", "대구광역시")
    assert collector().parse_lh_notice_list(text) == []


def test_lh_bad_calendar_or_header_change_fails_closed():
    text = (FIXTURES / "lh_notice_list.html").read_text()
    for broken in (text.replace("2026.10.01", "2026.02.30"), text.replace("게시일", "unknown")):
        with pytest.raises(ValueError, match="^주거지원 공식 공고 표를 해석할 수 없음$"):
            collector().parse_lh_notice_list(broken)


def test_latest_collection_rejects_future_notices_and_does_not_reuse_failed_previous():
    calls = []
    def fetch(url, encoding="utf-8"):
        calls.append(url)
        if "selectWrtancList" in url:
            return (FIXTURES / "lh_notice_list.html").read_text().replace("2026.10.01", "2026.10.09")
        raise RuntimeError("PRIVATE-UPSTREAM-CONTENT")
    prior = snapshot(notices=[notice().as_dict("2026-10-03")])
    result = collector().collect_housing_support("2026-10-03", fetch=fetch, previous=prior)
    assert result["target_date"] == "2026-10-03" and result["notices"] == []
    assert "PRIVATE" not in str(result)
    assert len(calls) <= collector().MAX_REQUESTS


def test_transport_failure_is_visible_without_upstream_error_or_carried_active_notices():
    def fetch(url, encoding="utf-8"):
        raise RuntimeError("PRIVATE-UPSTREAM-CONTENT")
    result = collector().collect_housing_support("2026-10-03", fetch=fetch,
                                                  previous=snapshot(notices=[notice().as_dict("2026-10-03")]))
    assert result["notices"] == []
    assert result["errors"] and "PRIVATE" not in str(result)
    assert set(result["sources"]) == {"sh", "lh", "applyhome"}


def test_missing_brief_has_five_guidance_categories_official_links_and_unconfirmed_state():
    output = renderer().render_housing_support_brief(snapshot())
    for label in ("청년", "신혼", "생애최초", "무순위", "공공임대", "미확인", "공고별"):
        assert label in output
    for source in collector().OFFICIAL_SOURCES.values():
        assert source["list_url"].replace("&", "&amp;") in output
    assert "housing-support.html" in output
    assert len(output.encode("utf-8")) <= 2200


def test_minimal_snapshot_still_has_visible_fallback():
    result = renderer().render_housing_support_brief({"target_date": "2026-10-03", "sources": {}, "notices": [], "errors": {}})
    assert "미확인" in result and "LH" in result and "SH" in result and "청약홈" in result


def test_stale_snapshot_never_shows_active_application_as_current():
    old = snapshot(notices=[notice(apply_start="2026-10-01").as_dict("2026-10-03")])
    output = renderer().render_housing_support_brief(old, today="2026-10-10")
    assert "오래된" in output and "접수중" not in output
    assert "서울 청년 공공임대 모집공고" not in output


def test_future_snapshot_is_not_treated_as_current():
    output = renderer().render_housing_support_brief(snapshot(target_date="2026-10-10"), today="2026-10-03")
    assert "미확인" in output


def test_render_revalidates_notice_origin_and_escapes_public_titles():
    real = notice(title="서울 <script>alert(1)</script> 청년 공고").as_dict("2026-10-03")
    output = renderer().render_housing_support_page(snapshot(notices=[real]))
    assert "<script>" not in output and "&lt;script&gt;" in output
    real["url"] = "https://evil.example/notice"
    output = renderer().render_housing_support_page(snapshot(notices=[real]))
    assert "evil.example" not in output


def test_detail_page_separates_guidance_from_official_notice_and_dates():
    output = renderer().render_housing_support_page(snapshot(notices=[notice().as_dict("2026-10-03")]))
    for phrase in ("확인 항목", "실제 모집공고", "공고별", "2026-10-01", "10/06", "2026-10-16", "예정", "무주택", "소득", "자산"):
        assert phrase in output
    assert "무순위는 모두 무주택" not in output


def test_save_load_picks_latest_valid_snapshot_without_future_or_month_file(tmp_path):
    import json
    c = collector()
    c.save_housing_support(snapshot(), tmp_path)
    c.save_housing_support(snapshot(target_date="2026-10-09"), tmp_path)
    (tmp_path / "support-2026-10.json").write_text("{}")
    (tmp_path / "support-2026-10-02.json").write_text(json.dumps({"schema_version": 1, "target_date": "2026-10-01"}))
    assert c.load_housing_support_latest(tmp_path, "2026-10-04")["target_date"] == "2026-10-03"
    assert c.load_housing_support_latest(tmp_path, "2026-09-30") is None


def test_current_lh_list_preserves_verified_schedule_only_for_same_current_notice():
    c = collector()
    text = (FIXTURES / "lh_notice_list.html").read_text()
    original = c.parse_lh_notice_list(text)[0]
    from dataclasses import replace
    verified = replace(original, apply_start="2026-10-14", apply_end="2026-10-16",
                       apply_start_at="2026-10-14T10:00:00+09:00", apply_end_at="2026-10-16T16:00:00+09:00")
    previous = snapshot(notices=[verified.as_dict("2026-10-03")])
    def fetch(url, encoding="utf-8"):
        if "selectWrtancList" in url: return text
        raise RuntimeError("unavailable")
    result = c.collect_housing_support("2026-10-03", fetch=fetch, previous=previous)
    assert result["notices"][0]["apply_start_at"] == "2026-10-14T10:00:00+09:00"
    previous["notices"][0]["title"] += " 정정"
    result = c.collect_housing_support("2026-10-03", fetch=fetch, previous=previous)
    assert result["notices"][0]["apply_start_at"] is None


def test_fresh_sh_schedule_keeps_same_notice_verified_eligibility_and_duplicate_warning():
    c = collector()
    from dataclasses import replace
    original = c.parse_sh_notice_list((FIXTURES / "sh_notice_list.html").read_text())[-1]
    original = replace(original, eligibility_brief="무주택세대구성원. Ⅰ·Ⅱ 중복 신청 유의.",
                       apply_start="2026-10-12", apply_end="2026-10-15",
                       apply_start_at="2026-10-12T10:00:00+09:00", apply_end_at="2026-10-15T17:00:00+09:00")
    previous = snapshot(notices=[original.as_dict("2026-10-03")])
    def fetch(url, encoding="utf-8"):
        if "list.do?" in url: return (FIXTURES / "sh_notice_list.html").read_text()
        if "seq=" in url: return (FIXTURES / "sh_notice_period.html").read_text()
        raise RuntimeError("unavailable")
    result = c.collect_housing_support("2026-10-03", fetch=fetch, previous=previous)
    matched = next(item for item in result["notices"] if "seq=310650" in item["url"])
    assert matched["apply_start_at"] == "2026-10-13T10:00:00+09:00"
    assert matched["eligibility_brief"] == original.eligibility_brief
    previous["notices"][0]["title"] += " 정정"
    result = c.collect_housing_support("2026-10-03", fetch=fetch, previous=previous)
    matched = next(item for item in result["notices"] if "seq=310650" in item["url"])
    assert matched["apply_start_at"] == "2026-10-13T10:00:00+09:00" and matched["eligibility_brief"] == ""


def test_matching_prior_does_not_replace_current_eligibility_or_current_schedule():
    c = collector()
    from dataclasses import replace
    current = notice(eligibility_brief="현재 확인한 자격 안내")
    prior = replace(current, eligibility_brief="이전 자격 안내", apply_start="2026-10-05")
    result = c._previous_schedule(current, snapshot(notices=[prior.as_dict("2026-10-03")]), "2026-10-03")
    assert result == current


@pytest.mark.parametrize("changes", [{"sources": None}, {"notices": None}, {"target_date": "2026-W40-6"}])
def test_corrupt_snapshot_still_renders_official_fallback(changes):
    output = renderer().render_housing_support_brief(snapshot(**changes), today="2026-10-03")
    assert "공고별" in output and "청약홈" in output


def test_brief_keeps_lh_and_sh_schedules_within_budget_before_duplicate_sh():
    c = collector()
    sh = c.parse_sh_notice_list((FIXTURES / "sh_notice_list.html").read_text())[-1]
    from dataclasses import replace
    sh = replace(sh, apply_start="2026-10-13", apply_end="2026-10-15",
                 apply_start_at="2026-10-13T10:00:00+09:00", apply_end_at="2026-10-15T17:00:00+09:00")
    sh2 = replace(sh, title=sh.title.replace("Ⅰ", "Ⅱ"), url=sh.url.replace("310650", "310653"))
    lh = notice(apply_start="2026-10-14", apply_end="2026-10-16",
                apply_start_at="2026-10-14T10:00:00+09:00", apply_end_at="2026-10-16T16:00:00+09:00")
    output = renderer().render_housing_support_brief(snapshot(notices=[sh.as_dict("2026-10-03"), sh2.as_dict("2026-10-03"), lh.as_dict("2026-10-03")]))
    assert "seq=310650" in output and "panId=2015122300020843" in output
    assert "10/13 10:00 ~ 10/15 17:00 (한국시간)" in output and "10/14 10:00 ~ 10/16 16:00 (한국시간)" in output
    assert len(output.encode()) <= 2200


@pytest.mark.parametrize("asof", ["2026-10-03T00:00:00+00:00", "2026-10-03T12:99:00+09:00"])
def test_invalid_or_unsupported_asof_falls_back_without_discarding_valid_notice(asof):
    item = notice(apply_start_at="2026-10-06T10:00:00+09:00", apply_end_at="2026-10-16T17:00:00+09:00")
    output = renderer().render_housing_support_page(snapshot(collected_at=asof, notices=[item.as_dict("2026-10-03")]))
    assert "서울 청년 공공임대 모집공고" in output and "예정" in output


@pytest.mark.parametrize("function_name", ["render_housing_support_brief", "render_housing_support_page"])
@pytest.mark.parametrize(("start", "end", "expected"), [
    ("2026-10-14T10:00:00+09:00", "2026-10-16T16:00:00+09:00", "10/14 10:00 ~ 10/16 16:00 (한국시간)"),
    ("2027-01-05T10:00:00+09:00", "2027-01-07T16:00:00+09:00", "2027/01/05 10:00 ~ 2027/01/07 16:00 (한국시간)"),
    ("2026-12-31T10:00:00+09:00", "2027-01-02T16:00:00+09:00", "2026/12/31 10:00 ~ 2027/01/02 16:00 (한국시간)"),
])
def test_application_period_is_readable_and_preserves_raw_iso(function_name, start, end, expected):
    item = notice(apply_start=start[:10], apply_end=end[:10], apply_start_at=start, apply_end_at=end)
    payload = snapshot(notices=[item.as_dict("2026-10-03")])
    from copy import deepcopy
    original = deepcopy(payload)
    output = getattr(renderer(), function_name)(payload, today="2026-10-03")
    assert expected in output and start not in output and end not in output
    assert payload == original
    if function_name == "render_housing_support_brief": assert len(output.encode()) <= 2200


def test_date_only_boundary_remains_unconfirmed_with_readable_date_and_time_notice():
    payload = snapshot(target_date="2026-10-14", notices=[notice(apply_start="2026-10-14").as_dict("2026-10-14")])
    output = renderer().render_housing_support_page(payload, today="2026-10-14")
    assert "10/14 ~ 10/16 (접수시각 미확인)" in output and "접수중" not in output
