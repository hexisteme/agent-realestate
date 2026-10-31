"""주간 청년안심주택 모집공고 정리(blog.youth_rental_weekly) — 2026-10-29 공고 8건 실물 픽스처로 고정.

금액 정답은 2026-10-31 공고 PDF 표를 사람이 대조해 /180 에 공개한 값(report/2026-10-31-youth-rental-captures.md)."""
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

import pytest

from blog import youth_rental_weekly as yw
from blog.wording_guard import FORBIDDEN_WORDS

FX = Path(__file__).parent / "fixtures" / "youth_weekly"
ROWS = json.loads((FX / "soco-list-p1-20261031.json").read_text(encoding="utf-8"))["resultList"]
WON = 10_000

# boardId → (보증금 최소, 최대, 월 임대료 최소, 최대) 원 단위, 전용면적 표기
TRUTH = {
    6708: ((10_500, 16_600), (56.2, 98), ()),
    6709: ((7_300, 9_700), (56, 66), ("18.01",)),
    6710: ((14_400, 15_800), (51, 56), ("41.68", "47.02")),
    6711: ((6_260, 26_809), (14, 71), ("17", "26", "33", "36", "41")),
    6714: ((12_825, 14_760), (54.8, 62.7), ("31.93", "32.05")),
    6715: ((2_900, 8_200), (44, 94), None),
    6716: ((11_900, 41_200), (28, 117), ("19.86", "36.93", "59.88")),
    6717: ((4_800, 9_300), (33.76, 60.24), None),
}


def _notices():
    return [yw.parse_board_row(r) for r in ROWS]


def _week_notices():
    mon, sun = yw.target_week(dt.date(2026, 10, 31))
    picked = yw.select_for_week(_notices(), mon, sun)
    rents = {n.board_id: yw.parse_rent_table((FX / f"soco-{n.board_id}.txt").read_text(encoding="utf-8")) for n in picked}
    return mon, [n.with_rent(rents[n.board_id]) for n in picked]


def test_target_week_is_next_monday_to_sunday():
    assert yw.target_week(dt.date(2026, 10, 31)) == (dt.date(2026, 11, 2), dt.date(2026, 11, 8))
    assert yw.target_week(dt.date(2026, 11, 7)) == (dt.date(2026, 11, 9), dt.date(2026, 11, 15))


def test_parse_board_row_reads_board_summary_fields():
    n = {x.board_id: x for x in _notices()}[6717]
    assert (n.kind, n.is_additional, n.station) == ("민간임대", True, "연신내역")
    assert n.complex_name == "연신내역 루미노 816"
    assert (n.gu, n.dong) == ("은평구", "불광동")
    assert n.exit_note == "3,6호선 연신내역 3번 출구"
    assert (n.apply_start_at, n.apply_end_at) == ("2026-11-02T00:00:00+09:00", "2026-11-02T23:00:00+09:00")
    assert (n.special_units, n.general_units, n.reserve) == (3, 1, False)
    assert n.apply_url == "https://naver.me/xONnDETh"
    assert n.url.endswith("boardId=6717&menuNo=400008")


def test_select_for_week_keeps_only_windows_overlapping_next_week():
    mon, picked = _week_notices()
    assert sorted(n.board_id for n in picked) == [6708, 6709, 6710, 6711, 6714, 6715, 6716, 6717]


@pytest.mark.parametrize("board", sorted(TRUTH))
def test_parse_rent_table_matches_human_checked_ranges(board):
    (dmin, dmax), (rmin, rmax), areas = TRUTH[board]
    r = yw.parse_rent_table((FX / f"soco-{board}.txt").read_text(encoding="utf-8"))
    assert r is not None
    assert (r.deposit_min, r.deposit_max) == (dmin * WON, dmax * WON)
    assert (r.rent_min, r.rent_max) == (round(rmin * WON), round(rmax * WON))
    if areas is not None:
        assert r.areas == areas


def test_parse_rent_table_refuses_text_without_rent_table():
    assert yw.parse_rent_table("청년안심주택 안내문\n임대보증금 보증보험 가입여부\n자동차가액 4,542만원") is None


def test_format_won_reads_like_the_published_post():
    assert yw.format_won(128_250_000) == "1억2,825만"
    assert yw.format_won(73_000_000) == "7,300만"
    assert yw.format_won(337_600) == "33만7,600"     # Codex R1: 반올림하면 범위가 좁아진다 — 원 단위까지
    assert yw.format_won(562_000) == "56만2,000"
    assert yw.format_won(660_000) == "66만"


def test_render_post_title_lead_tags_follow_template():
    mon, picked = _week_notices()
    post = yw.render_post(mon, picked, checked_at=dt.datetime(2026, 10, 31, 10, 35, tzinfo=yw.KST))
    assert post["title"] == "2026년 11월 1주차 청년안심주택 추가모집 11/2~11/8 접수 8곳 정리 (노량진역·연신내역 등)"
    assert 40 <= len(post["title"]) <= 60
    tags = post["tags"]
    assert len(tags) <= 10 and len(set(tags)) == len(tags)
    assert tags[:5] == ["청년안심주택", "청년안심주택모집공고", "서울청년주택", "주택청약", "서울임대주택"]
    assert "청년안심주택추가모집" in tags
    lead = post["lead"]
    assert "2026년 11월 2일(월)" in lead and "2026년 11월 8일(일)" in lead and "8곳" in lead


def test_render_post_body_covers_every_notice_with_official_link_and_faq():
    mon, picked = _week_notices()
    post = yw.render_post(mon, picked, checked_at=dt.datetime(2026, 10, 31, 10, 35, tzinfo=yw.KST))
    body = post["body_html"]
    text = re.sub(r"<[^>]+>", " ", body)
    for n in picked:
        assert n.name in text
        assert n.url.replace("&", "&amp;") in body
    assert text.count("자주 묻는 질문") == 1 and len(re.findall(r"<h2>", body)) == 7
    assert "1억2,825만" in text and "54만8,000" in text      # 비바힐스강변 범위
    assert not any(w in text for w in FORBIDDEN_WORDS)
    assert "당첨 가능" not in text and "자격이 됩니다" not in text


def test_render_post_is_deterministic():
    mon, picked = _week_notices()
    at = dt.datetime(2026, 10, 31, 10, 35, tzinfo=yw.KST)
    assert yw.render_post(mon, picked, checked_at=at) == yw.render_post(mon, picked, checked_at=at)


def test_rent_fallback_points_to_notice_instead_of_guessing():
    mon, picked = _week_notices()
    no_rent = [n.with_rent(None) for n in picked]
    post = yw.render_post(mon, no_rent, checked_at=dt.datetime(2026, 10, 31, 10, 35, tzinfo=yw.KST))
    text = re.sub(r"<[^>]+>", " ", post["body_html"])
    assert text.count("금액은 공고문 공급현황 표에서 확인") == len(picked)
    assert "만 /" not in text


def test_empty_week_is_skipped_not_published(tmp_path):
    out = yw.build(dt.date(2026, 10, 31), tmp_path, rows=[], fetch_pdf_text=lambda n: None, render_cards=False)
    assert out["state"] == "SKIPPED_EMPTY"
    assert json.loads((tmp_path / "state.json").read_text())["state"] == "SKIPPED_EMPTY"


def test_build_writes_bundle_without_network(tmp_path):
    texts = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8") for p in FX.glob("soco-67*.txt")}
    out = yw.build(dt.date(2026, 10, 31), tmp_path, rows=ROWS, fetch_pdf_text=lambda n: texts.get(n.board_id),
                   render_cards=False)
    assert out["state"] == "BUILT" and out["n"] == 8
    meta = json.loads((tmp_path / "post.json").read_text(encoding="utf-8"))
    assert "접수 8곳 정리" in meta["title"] and (tmp_path / "preview.html").exists()


def test_watchlist_flags_yongsan_targets_once():
    row = dict(ROWS[0], boardId="9999", nttSj="[민간임대] 효창공원앞역 해링턴플레이스 용산 리버뷰 모집공고")
    hits = yw.watch_hits([yw.parse_board_row(row)] + _notices(), seen=set())
    assert [h.board_id for h in hits] == [9999]
    assert yw.watch_hits([yw.parse_board_row(row)], seen={9999}) == []


def test_display_name_drops_station_prefix_and_series_suffix():
    by = {x.board_id: x for x in _notices()}
    assert (by[6717].name, by[6709].name) == ("루미노 816", "한강더퍼스트타워")
    assert by[6711].name == "한화포레나당산"
    mon, picked = _week_notices()
    body = yw.render_post(mon, picked, checked_at=dt.datetime(2026, 10, 31, 10, 35, tzinfo=yw.KST))["body_html"]
    assert "연신내역 루미노 816" not in body and "<h3>은평구 불광동 · 연신내역 · 루미노 816 · " in body


def test_series_does_not_run_before_first_saturday(capsys, monkeypatch):
    monkeypatch.setattr(yw, "fetch_rows", lambda since: [])
    monkeypatch.setattr(yw, "build", lambda *a, **k: pytest.fail("11/2 주는 /180 이 다뤘다"))
    assert yw.main(["run", "--day", "2026-10-31", "--publish"]) == 0
    assert "SKIP" in capsys.readouterr().out


def test_bare_origin_apply_url_gets_trailing_slash_like_the_editor():
    by = {x.board_id: x for x in _notices()}
    assert by[6715].apply_url == "https://egaebong.com/"
    assert by[6717].apply_url == "https://naver.me/xONnDETh"


def test_watchlist_ignores_same_brand_elsewhere_r1031():
    """10-31 실측 오탐: 노원·송파 해링턴은 /180 용산 대상이 아니다."""
    rows = [dict(ROWS[0], boardId="6694", nttSj="[민간임대] 노원역 해링턴플레이스 노원 센트럴 추가모집공고", content=""),
            dict(ROWS[0], boardId="6679", nttSj="[민간임대] 장지역 송파해링턴타워 최초모집공고", content=""),
            dict(ROWS[0], boardId="9998", nttSj="[민간임대] 남영역 롯데캐슬 헤리티지 잔여세대 모집공고", content="")]
    assert [h.board_id for h in yw.watch_hits([yw.parse_board_row(r) for r in rows], seen=set())] == [9998]


@pytest.mark.parametrize("text", [
    "(단위 : 원)\n임대보증금 및 임대료는 소득에 따라 달라집니다\n월평균소득 공제액\n8,168,429 579,278",
    "(단위 : 원)\n보증금             임대료                                           임대료 안내\n"
    "100,000,000       500,000         8,168,429 579,278",
])
def test_parse_rent_table_rejects_income_figures_r1(text):
    """Codex R1(2026-10-31) 반례: 문장 머리줄·오른쪽 단 '임대료 안내'로 소득 기준 숫자가 금액으로 읽히던 경로."""
    r = yw.parse_rent_table(text)
    assert r is None or (r.deposit_min, r.deposit_max, r.rent_min, r.rent_max) == (100_000_000, 100_000_000, 500_000, 500_000)


# ── Codex R1(2026-10-31) 반례 고정 ─────────────────────────────────────────
CTX = "(단위 : 만원)\n  공급     주거 전용    금회 공급\n  유형     (타입)       호수\n"


def test_r1_operator_line_not_published():
    """게시판 '에이치엔비개발' vs PDF '에이치앤비개발' — 출처가 엇갈리는 사업주체는 본문에 쓰지 않는다."""
    mon, picked = _week_notices()
    body = yw.render_post(mon, picked, checked_at=dt.datetime(2026, 10, 31, 10, 35, tzinfo=yw.KST))["body_html"]
    assert "사업주체" not in body


@pytest.mark.parametrize("text", [
    "(단위 : 원)\n  임대 보증금 및 임대료 산정 기준\n자산상한 월소득추가금\n251,000,000 579,278",
    "(단위 : 원)\n  임대 보증금 임대료 산정 기준\n자산상한 월소득추가금\n251,000,000 579,278",
])
def test_r1_prose_or_non_supply_header_is_not_a_table(text):
    assert yw.parse_rent_table(text) is None


def test_r1_right_column_heading_does_not_widen_table():
    text = ("(단위 : 원)\n  공급 유형  주거 전용(타입)  호수\n"
            "  보증금        임대료                                  보증금 및 임대료 안내\n"
            "  100,000,000   500,000                                 251,000,000 579,278\n  합 계\n")
    r = yw.parse_rent_table(text)
    assert r is not None and (r.deposit_min, r.deposit_max, r.rent_min, r.rent_max) == (100_000_000,) * 2 + (500_000,) * 2


def test_r1_unreadable_row_voids_the_range_instead_of_narrowing_it():
    assert yw.parse_rent_table(CTX + "  보증금           임대료\n  10,000           50\n  9000             45\n  합 계\n") is None


def test_r1_rows_after_midtable_note_void_the_range():
    text = CTX + "  보증금           임대료\n  10,000           50\n※ 일부 호실 상이\n  12,000           60\n  합 계\n"
    assert yw.parse_rent_table(text) is None


def _row(content, optn4="2026-12-31", board="9001"):
    return dict(ROWS[0], boardId=board, nttSj="[민간임대] 시험역 시험단지 모집공고", content=content, optn4=optn4)


def test_r1_date_only_period_keeps_end_date_across_year():
    n = yw.parse_board_row(_row("■청약신청 : 2026.12.31.(목) ~ 2027.01.05.(화) ■서류제출 : 2027.01.08."))
    assert (n.start_date, n.end_date) == (dt.date(2026, 12, 31), dt.date(2027, 1, 5))
    assert [x.board_id for x in yw.select_for_week([n], dt.date(2027, 1, 4), dt.date(2027, 1, 10))] == [9001]
    assert "시각은 공고문" in yw._period(n)


def test_r1_two_application_periods_span_both():
    n = yw.parse_board_row(_row("■청약신청 : ‘26. 11. 02. (월) 10:00 ~ 18:00 (특별공급) "
                                "■청약신청 : ‘26. 11. 03. (화) 10:00 ~ 24:00 (일반공급) ■발표 : ‘26. 11. 06.", optn4="2026-11-02"))
    assert (n.start_date, n.end_date) == (dt.date(2026, 11, 2), dt.date(2026, 11, 3))


def test_r1_build_never_overwrites_a_publish_attempt(tmp_path):
    texts = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8") for p in FX.glob("soco-67*.txt")}

    def racing_fetch(n):
        yw.write_state(tmp_path, state="PUBLISH_ATTEMPTED", title="다른 실행")
        return texts.get(n.board_id)
    out = yw.build(dt.date(2026, 10, 31), tmp_path, rows=ROWS, fetch_pdf_text=racing_fetch, render_cards=False)
    assert out["state"] == "PUBLISH_ATTEMPTED"
    assert json.loads((tmp_path / "state.json").read_text())["state"] == "PUBLISH_ATTEMPTED"
    assert not (tmp_path / "preview.html").exists()


def test_r1_confirm_records_failed_public_check(tmp_path, monkeypatch):
    from blog import youth_rental_publish as yp
    out = yw.build(dt.date(2026, 10, 31), tmp_path, rows=ROWS,
                   fetch_pdf_text=lambda n: None, render_cards=False)
    yw.write_state(tmp_path, state="PUBLISH_ATTEMPTED", title=out["title"])

    def fake_verify(title, log):
        log.append("PUBLIC_URL:https://floker.tistory.com/999")
        return True
    monkeypatch.setattr(yp.pw, "_verify_published_on_blog", fake_verify)
    monkeypatch.setattr(yp, "_attach", lambda log: (None, None, None))
    yp.confirm(tmp_path)
    st = yw.read_state(tmp_path)
    assert st["state"] == "PUBLISHED" and st["public_check_ok"] is False


# ── Codex R2(2026-10-31) 반례 고정 ─────────────────────────────────────────
def test_r2_short_commaless_row_voids_the_range():
    text = "(단위 : 만원)\n공급 전용 호수\n보증금           임대료\n1,000            10\n900              5\n합 계"
    assert yw.parse_rent_table(text) is None


def test_r2_time_is_not_read_as_a_day():
    n = yw.parse_board_row(_row("청약신청 : 2026.12.31. ~ 01.05. 24:00"))
    assert (n.start_date, n.end_date) == (dt.date(2026, 12, 31), dt.date(2027, 1, 5))
    assert [x.board_id for x in yw.select_for_week([n], dt.date(2027, 1, 4), dt.date(2027, 1, 10))] == [9001]


def test_r2_state_written_during_render_is_serialized_not_lost(tmp_path, monkeypatch):
    import threading
    texts = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8") for p in FX.glob("soco-67*.txt")}
    real, threads = yw.render_post, []

    def racing_render(*a, **k):
        th = threading.Thread(target=yw.write_state, args=(tmp_path,), kwargs={"state": "PUBLISHED"})
        th.start()
        threads.append(th)
        return real(*a, **k)
    monkeypatch.setattr(yw, "render_post", racing_render)
    yw.build(dt.date(2026, 10, 31), tmp_path, rows=ROWS, fetch_pdf_text=lambda n: texts.get(n.board_id), render_cards=False)
    threads[0].join(5)
    assert yw.read_state(tmp_path)["state"] == "PUBLISHED"


def test_r2_click_exception_is_reported_not_silently_pending(tmp_path):
    from blog import youth_rental_publish as yp
    yw.write_state(tmp_path, state="BUILT", title="t")

    class Page:
        def evaluate(self, js):
            raise RuntimeError("click failed before dispatch")
    out = yp._click_publish(Page(), tmp_path, "t", [])
    st = yw.read_state(tmp_path)
    assert out.startswith("ERR:click_exception") and st["state"] == "PUBLISH_ATTEMPTED" and st["click_error"]


def test_r2_release_is_the_only_way_back_and_never_after_publish(tmp_path):
    from blog import youth_rental_publish as yp
    yw.write_state(tmp_path, state="PUBLISH_ATTEMPTED", title="t")
    assert yp.release(tmp_path).startswith("RELEASED") and yw.read_state(tmp_path)["state"] == "BUILT"
    yw.write_state(tmp_path, state="PUBLISHED")
    assert yp.release(tmp_path).startswith("REFUSE") and yw.read_state(tmp_path)["state"] == "PUBLISHED"


# ── Codex R3(2026-10-31) 반례 고정 ─────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "(단위 : 만원)\n공급 전용 호수\n보증금           임대료\n1,000            10\n900\n                 5\n합 계",
    "(단위 : 만원)\n공급 전용 호수\n보증금           임대료\n1,000            10\n※ 주석\n900\n                 5\n합 계",
])
def test_r3_row_split_across_lines_voids_the_range(text):
    assert yw.parse_rent_table(text) is None


def test_r3_second_application_segment_extends_the_period():
    n = yw.parse_board_row(_row("청약신청 : 2026.12.31. 10:00 ~ 18:00 ■청약신청 : 2027.01.05."))
    assert (n.start_date, n.end_date) == (dt.date(2026, 12, 31), dt.date(2027, 1, 5))
    assert [x.board_id for x in yw.select_for_week([n], dt.date(2027, 1, 4), dt.date(2027, 1, 10))] == [9001]


def test_r3_empty_fetch_never_overwrites_a_publish(tmp_path, monkeypatch):
    yw.write_state(tmp_path, state="BUILT", title="t")

    def publish_lands_during_fetch(since):
        yw.write_state(tmp_path, state="PUBLISHED", published_url="https://example.invalid/1")
        return []
    monkeypatch.setattr(yw, "fetch_rows", publish_lands_during_fetch)
    first = yw.build(dt.date(2026, 10, 31), tmp_path, render_cards=False)
    second = yw.build(dt.date(2026, 10, 31), tmp_path, rows=ROWS[:1], fetch_pdf_text=lambda n: None, render_cards=False)
    assert first["state"] == second["state"] == yw.read_state(tmp_path)["state"] == "PUBLISHED"


# ── Codex R4(2026-10-31) 반례 고정 — 같은 결함 2회 재발이라 일반화 수정 ──────────
@pytest.mark.parametrize("row", ["900*\n                 5*", "900*            5*", "(900)            5", "1,000            10\n900원            5"])
def test_r4_any_unpaired_figure_in_money_column_voids_the_range(row):
    text = f"(단위 : 만원)\n공급 전용 호수\n보증금           임대료\n1,000            10\n{row}\n합 계"
    assert yw.parse_rent_table(text) is None


@pytest.mark.parametrize("content", [
    "청약신청 : 2026.12.31. 10:00 ~ 18:00 (특별공급), 2027.01.05. (일반공급)",
    "청약신청 : 2026.12.31. 10:00 ~ 18:00 ■청약신청 : 2027.01.05.",
])
def test_r4_any_later_date_in_application_segments_extends_the_period(content):
    n = yw.parse_board_row(_row(content))
    assert (n.start_date, n.end_date) == (dt.date(2026, 12, 31), dt.date(2027, 1, 5))
    assert [x.board_id for x in yw.select_for_week([n], dt.date(2027, 1, 4), dt.date(2027, 1, 10))] == [9001]


def test_r4_single_timed_period_keeps_its_times():
    n = yw.parse_board_row(_row("청약신청 : '26. 11. 02. (월) 09:00 ~ 11. 04. (수) 23:00"))
    assert n.apply_start_at.startswith("2026-11-02T09:00") and n.apply_end_at.startswith("2026-11-04T23:00")


# ── Codex R5(2026-10-31) 반례 고정 ─────────────────────────────────────────
def test_r5_row_after_note_with_a_text_word_voids_the_range():
    text = "(단위 : 만원)\n공급 전용 호수\n보증금           임대료\n1,000            10\n※ 주석\n900*             5* 일반\n합 계"
    assert yw.parse_rent_table(text) is None


def test_r5_labelled_application_segment_extends_the_period():
    n = yw.parse_board_row(_row("청약신청 : 2026.12.31. 10:00 ~ 18:00 ■청약신청(일반공급) : 2027.01.05."))
    assert (n.start_date, n.end_date) == (dt.date(2026, 12, 31), dt.date(2027, 1, 5))


def test_r5_apply_page_url_is_not_an_application_segment():
    n = yw.parse_board_row(_row("청약신청 : '26. 11. 02. (월) 09:00 ~ 11. 04. (수) 23:00 ■청약신청 페이지 : https://x.example/1.2.3"))
    assert (n.start_date, n.end_date) == (dt.date(2026, 11, 2), dt.date(2026, 11, 4)) and n.apply_start_at


def test_r5_known_limit_prose_like_line_after_note_is_not_read_as_a_row():
    """판독 한계(고치지 않음): 주석 뒤 줄의 낱말 절반 이상이 글자면 문장으로 본다 — 실 공고 6709·6715 의 주석 문장
    ('제 19조', '20%, 25%')이 금액 열에 걸치기 때문. 동작이 바뀌면 이 테스트가 알린다."""
    text = "(단위 : 만원)\n공급 전용 호수\n보증금           임대료\n1,000            10\n※ 주석\n900* 5* 일반 공급 잔여\n합 계"
    assert yw.parse_rent_table(text) == yw.RentRange(10_000_000, 10_000_000, 100_000, 100_000, 1, ())


# ── 마케팅 원장 영수증(MP-flokerblog-01) — 사용자 지시 2026-10-31 "글주소 넣어요" ──────
def _published(tmp_path):
    yw.write_state(tmp_path, state="PUBLISHED", title="2026년 11월 1주차 청년안심주택 정리", notified=True,
                   published_url="https://floker.tistory.com/190", published_at="2026-11-07T10:41:00+09:00")


def _fake_cli(calls, rc=0, out="반영 {'execution': 1, 'outcome': 0}"):
    def run(cmd, **kw):
        calls.append({"cmd": cmd, "cwd": kw.get("cwd"), "event": json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))})
        return subprocess.CompletedProcess(cmd, rc, out, "")
    return run


def test_marketing_receipt_is_off_unless_the_launchd_wrapper_enables_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.delenv("RE_YOUTH_MARKETING_RECEIPT", raising=False)
    monkeypatch.setattr(yw.subprocess, "run", _fake_cli(calls))
    _published(tmp_path)
    yw._report_outcome(tmp_path, "")
    assert calls == [] and not yw.read_state(tmp_path).get("marketing_recorded")


def test_published_post_is_recorded_once_in_the_marketing_ledger(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("RE_YOUTH_MARKETING_RECEIPT", "1")
    monkeypatch.setattr(yw.subprocess, "run", _fake_cli(calls))
    _published(tmp_path)
    yw._report_outcome(tmp_path, "")
    yw._report_outcome(tmp_path, "")
    assert len(calls) == 1 and yw.read_state(tmp_path)["marketing_recorded"] is True
    c = calls[0]
    assert c["cmd"][:2] == [str(yw.MARKETING_ROOT / ".venv/bin/agent-marketing"), "ingest-events"]
    assert c["cwd"] == yw.MARKETING_ROOT
    ev = c["event"]
    assert (ev["event_type"], ev["probe_id"], ev["count"]) == ("execution", "MP-flokerblog-01", 1)
    assert ev["receipt"] == "https://floker.tistory.com/190" and ev["occurred_at"] == "2026-11-07T10:41:00+09:00"
    assert ev["note"].startswith("floker-post-190 ")


def test_duplicate_receipt_counts_as_recorded(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("RE_YOUTH_MARKETING_RECEIPT", "1")
    monkeypatch.setattr(yw.subprocess, "run", _fake_cli(calls, out="중복 영수증 — skip\n반영 {'execution': 0}"))
    _published(tmp_path)
    yw._report_outcome(tmp_path, "")
    assert yw.read_state(tmp_path)["marketing_recorded"] is True


@pytest.mark.parametrize("rc,out", [(1, "[차단] action 미등록"), (0, "반영 {'execution': 0, 'outcome': 0}")])
def test_failed_receipt_notifies_once_and_retries_next_run(tmp_path, monkeypatch, rc, out):
    calls, sent = [], []
    monkeypatch.setenv("RE_YOUTH_MARKETING_RECEIPT", "1")
    monkeypatch.setattr(yw, "notify", lambda text: sent.append(text) or True)
    monkeypatch.setattr(yw.subprocess, "run", _fake_cli(calls, rc=rc, out=out))
    _published(tmp_path)
    yw._report_outcome(tmp_path, "")
    yw._report_outcome(tmp_path, "")
    assert len(calls) == 2 and len(sent) == 1 and "마케팅 원장" in sent[0]
    assert not yw.read_state(tmp_path).get("marketing_recorded")
    monkeypatch.setattr(yw.subprocess, "run", _fake_cli(calls))
    yw._report_outcome(tmp_path, "")
    assert yw.read_state(tmp_path)["marketing_recorded"] is True
