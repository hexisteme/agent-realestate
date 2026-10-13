"""시각 우선 일간 글(2026-10-10) — 관측요약 → 시장흐름대시보드 → 읽는법·값 표 → 지리타일맵 → 예산대."""
import re

import pytest

import blog.build_explorer as be
from blog import visual_first as vf
from blog.build_site import BASE_URL
from blog.daily_digest import _TISTORY_TEXT_BUDGET, build_daily_digest
from blog import market_charts as mch
from test_daily_digest import _ALLOWED_TAGS, _inventory, _row, _sample_ds
from test_market_charts import snapshot

# 구청 소재지 기준 대략 좌표(위도, 경도) — 타일 배치가 실제 지도의 남북·동서 순서를 지키는지만 본다.
_CENTROIDS = {
    "강남": (37.517, 127.047), "강동": (37.530, 127.124), "강북": (37.640, 127.011), "강서": (37.551, 126.850),
    "관악": (37.478, 126.951), "광진": (37.538, 127.082), "구로": (37.495, 126.887), "금천": (37.457, 126.895),
    "노원": (37.654, 127.056), "도봉": (37.669, 127.047), "동대문": (37.574, 127.040), "동작": (37.512, 126.940),
    "마포": (37.566, 126.901), "서대문": (37.579, 126.937), "서초": (37.484, 127.033), "성동": (37.563, 127.037),
    "성북": (37.589, 127.017), "송파": (37.515, 127.106), "양천": (37.517, 126.866), "영등포": (37.526, 126.896),
    "용산": (37.532, 126.990), "은평": (37.603, 126.929), "종로": (37.573, 126.979), "중구": (37.564, 126.998),
    "중랑": (37.606, 127.093),
}
TODAY, ASOF = "2026-09-05", "2026-09-04"


def _visual(tmp_path):
    pytest.importorskip("PIL")
    visual = vf.prepare_visual_first(snapshot("2026-09-05"), TODAY, tmp_path / "images")
    assert visual is not None
    return visual


def _digest(tmp_path):
    ds = _sample_ds()
    ds["listing_inventory"] = _inventory()
    return build_daily_digest(ds, TODAY, ASOF, visual_first=_visual(tmp_path))


def _contrast(fg, bg):
    def lum(color):
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_geo_tiles_cover_all_districts_in_map_order():
    tiles = vf.SEOUL_GEO_TILES
    assert set(tiles) == set(be.GU_LAWD) and len(set(tiles.values())) == 25
    for a, (ra, ca) in tiles.items():
        for b, (rb, cb) in tiles.items():
            if ca == cb and ra < rb:
                assert _CENTROIDS[a][0] > _CENTROIDS[b][0], (a, b)
            if ra == rb and ca < cb:
                assert _CENTROIDS[a][1] < _CENTROIDS[b][1], (a, b)


def test_geo_tile_cells_state_text_color_with_readable_contrast():
    html = vf.render_geo_tile_heatmap(_sample_ds(), ASOF, surface="tistory")
    styles = re.findall(r'<td data-district="[^"]+" style="([^"]+)"', html)
    assert len(styles) == 25
    for style in styles:
        bg = re.search(r"background-color:(#[0-9a-f]{6})", style).group(1)
        fg = re.search(r"(?<!-)color:(#[0-9a-f]{6})", style).group(1)
        assert _contrast(fg, bg) >= 4.5, style
    assert html.count("data-district=") == 25 and "강남" in html and "—" in html


def test_visual_first_tistory_reads_summary_dashboard_notes_table_map_then_budget(tmp_path):
    body = _digest(tmp_path)["tistory_html"]
    image = re.search(rf'<img src="{re.escape(BASE_URL)}/images/(dashboard-{TODAY}-[0-9a-f]{{16}}\.png)"', body)
    assert image
    order = [body.find(f"{ASOF} 관측"), image.start(), body.find("data-dashboard-table"), body.find("읽는 법"),
             body.find("▲ 높아진 단지 수"), body.find("data-district-list"), body.find("가격대별 요약 <span")]   # U2 — 티스토리는 구 목록 표
    assert -1 not in order and order == sorted(order), order
    for kept in (be.DISCLAIMER, "AI로 정리", "작성:", "방법론 전문"):
        assert kept in body


def test_visual_first_tistory_drops_duplicate_and_empty_blocks(tmp_path):
    body = _digest(tmp_path)["tistory_html"]
    for dropped in ("금리 → 가격 → 거래 → 지원", "함께 볼 지표 5가지", "최대 0개", "· 최대 3개)", "기준 충족 관측 없음",
                    "기준 충족 단지 없음", "가격대 분포 · 관측 단지 수", "서울 25개 구 요약", "▰"):
        assert dropped not in body, dropped
    ds = _sample_ds()
    ds["complexes"] = [r for r in ds["complexes"] if r["name"] != "서초하단단지"]
    sparse = build_daily_digest(ds, TODAY, ASOF, visual_first=_visual(tmp_path))["tistory_html"]
    assert "12개월 범위 하단 근접" not in sparse and "52주 범위 하단 근접 관측" not in sparse  # 빈 절은 제목째 생략
    assert "12개월 범위 하단 근접" in body


def test_visual_first_tistory_keeps_tag_whitelist_and_reserves_image_bytes(tmp_path):
    body = _digest(tmp_path)["tistory_html"]
    tags = {m.lower() for m in re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", body)}
    assert tags <= _ALLOWED_TAGS | {"img"}
    assert len(body.encode("utf-8")) <= _TISTORY_TEXT_BUDGET - vf.BODY_IMAGE_RESERVE


def test_visual_first_site_puts_visuals_before_details_with_accessible_tips(tmp_path):
    site = _digest(tmp_path)["site_html"]
    details = site.find('<details id="reader-details"')
    image = re.search(rf'<img src="\.\./images/dashboard-{TODAY}-[0-9a-f]{{16}}\.png" width="{mch.DASHBOARD_WIDTH}" '
                      rf'height="\d+" alt="[^"]{{20,}}"', site)
    assert image and image.start() < details
    assert -1 < site.find('data-geo-tiles="districts"') < details
    assert -1 < site.find("data-dashboard-table") < details
    tips = re.findall(r'role="tooltip" id="([a-z0-9-]+)"', site)
    assert len(tips) >= 3
    for tip in tips:
        assert re.search(rf'<button type="button" class="tip" aria-expanded="false" aria-describedby="{tip}"', site), tip
    assert "data-reader-overview" not in site[:details]


def test_dashboard_offers_full_size_view_on_both_surfaces(tmp_path):
    """U6 — 한 장 그래프를 휴대폰 폭으로 줄여 보이므로 원본 크기로 여는 길을 둔다(티스토리는 글 링크, 사이트는 그림 링크)."""
    digest = _digest(tmp_path)
    name = re.search(rf"dashboard-{TODAY}-[0-9a-f]{{16}}\.png", digest["site_html"]).group(0)
    assert re.search(rf'<p data-dashboard-zoom><a href="{re.escape(BASE_URL)}/images/{name}">[^<]*크게 보기</a></p>',
                     digest["tistory_html"])
    assert re.search(rf'<a href="\.\./images/{name}" data-dashboard-zoom[^>]*><img src="\.\./images/{name}"',
                     digest["site_html"])


def test_visual_first_digest_lists_body_media(tmp_path):
    visual = _visual(tmp_path)
    digest = build_daily_digest(_sample_ds(), TODAY, ASOF, visual_first=visual)
    assert digest["body_media"] == [{
        "url": f"{BASE_URL}/images/{visual.image.name}", "image": f"../images/{visual.image.name}",
        "sha256": visual.image.sha256, "width": visual.image.width, "height": visual.image.height}]
    assert "body_media" not in build_daily_digest(_sample_ds(), TODAY, ASOF)


def test_prepare_visual_first_is_none_without_usable_snapshot(tmp_path):
    assert vf.prepare_visual_first(None, TODAY, tmp_path) is None
    assert vf.prepare_visual_first(snapshot("2026-08-01"), TODAY, tmp_path) is None
    assert not list(tmp_path.iterdir())


def test_daily_outputs_bind_body_media_and_site_copies_verified_bytes(tmp_path, monkeypatch):
    import blog.build_site as bs
    from blog.body_media import read_body_media
    from blog.run_daily import write_digest_outputs
    from blog.tistory_contract import parse_helper
    visual = _visual(tmp_path)
    digest = build_daily_digest(_sample_ds(), TODAY, ASOF, visual_first=visual)
    outputs = write_digest_outputs(digest, TODAY, str(tmp_path))
    assert outputs["body_media"].endswith(f"{TODAY}-tistory-draft.body-media.json")
    images = read_body_media(outputs["draft"], parse_helper(outputs["draft"]))
    assert [i.name for i in images] == [visual.image.name]

    monkeypatch.setattr(bs, "SRC", str(tmp_path))
    monkeypatch.setattr(bs, "SITE", str(tmp_path / "site"))
    assert bs._daily_body_media(TODAY) == [f"images/{visual.image.name}"]
    assert (tmp_path / "site/images" / visual.image.name).read_bytes() == images[0].image_bytes

    plain = build_daily_digest(_sample_ds(), TODAY, ASOF)
    assert "body_media" not in write_digest_outputs(plain, TODAY, str(tmp_path))
    assert not (tmp_path / f"tistory/{TODAY}-tistory-draft.body-media.json").exists()


@pytest.mark.parametrize("width,touch", [(320, True), (390, True), (1440, False)])
def test_site_top_fits_phone_and_tips_open_inside_viewport_then_close(required_browser, tmp_path, width, touch):
    from blog.daily_digest import _SITE_CSS
    site = _digest(tmp_path)["site_html"]
    start = site.find("<section class=\"vf\" data-visual-first>")
    end = site.find("</script></section>", start)
    assert -1 < start < end
    top = site[start:end + len("</script></section>")]
    context = required_browser.new_context(viewport={"width": width, "height": 900}, has_touch=touch, is_mobile=touch)
    try:
        page = context.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content(f'<meta name="viewport" content="width=device-width"><style>{_SITE_CSS}</style>'
                         f'<body><div class="wrap"><h1>제목</h1>{top}</div></body>')
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        assert page.locator("table.vf-table").evaluate("t => t.parentElement.scrollWidth <= t.parentElement.clientWidth")
        tips = page.locator("button.tip")
        assert tips.count() >= 3
        for i in range(tips.count()):
            tip = tips.nth(i)
            (tip.tap if touch else tip.click)()
            assert tip.get_attribute("aria-expanded") == "true"
            body = page.locator("#" + tip.get_attribute("aria-describedby"))
            box = body.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (i, box)
            page.keyboard.press("Escape")   # WCAG 1.4.13 — 포커스·호버가 남아 있어도 Escape 로 닫힌다
            assert tip.get_attribute("aria-expanded") == "false" and not body.is_visible()
            page.mouse.move(1, 1)
    finally:
        context.close()


def test_site_note_text_keeps_aa_contrast_on_site_background():
    from blog.daily_digest import _SITE_CSS
    page_bg = re.search(r"body\{[^}]*background:(#[0-9a-f]{6})", _SITE_CSS).group(1)
    note = re.search(r"\.vf-note\{color:(#[0-9a-f]{6})", vf.SITE_CSS).group(1)
    assert _contrast(note, page_bg) >= 4.5, (note, page_bg)


def _gated_trends(rows):
    judged = [r for r in rows if be.passes_rank_gate(r) and r.get("molit_trend_pct") is not None]
    return len(judged), sum(r["molit_trend_dir"] == "▲" for r in judged), sum(r["molit_trend_dir"] == "▼" for r in judged)


def test_summary_and_tiles_count_trends_with_the_fact_lead_gate(tmp_path):
    ds = _sample_ds()
    ds["complexes"].append(_row("강남", "표본부족단지", molit_n=3, molit_trend_dir="▲", molit_trend_pct=9.0))
    ds["complexes"].append(_row("노원", "소형단지", area_m2=33.0, molit_trend_dir="▼", molit_trend_pct=-4.0))
    raw_up = sum(r.get("molit_trend_dir") == "▲" for r in ds["complexes"])
    n, up, down = _gated_trends(ds["complexes"])
    assert raw_up != up   # 표본 게이트를 거치지 않은 단지가 섞여 있어야 이 테스트가 의미가 있다
    digest = build_daily_digest(ds, TODAY, ASOF, visual_first=_visual(tmp_path))
    for surface in ("tistory_html", "site_html"):
        assert f"3·9개월 비교가 가능한 {n:,}단지" in digest[surface] and f"높은 곳 {up:,} · 낮은 곳 {down:,}" in digest[surface]
    tiles = vf.render_geo_tile_heatmap(ds, ASOF, surface="tistory")
    assert sum(map(int, re.findall(r"▲(\d+)", tiles))) == up and sum(map(int, re.findall(r"▼(\d+)", tiles))) == down
    lead = re.search(r"추세 판정 가능한 (\d+)단지 중 (\d+)곳", digest["tistory_html"])
    if lead:
        assert (int(lead.group(1)), int(lead.group(2))) == (n, up)


def test_site_body_media_ignores_drafts_without_sidecar_like_card_media(tmp_path, monkeypatch):
    import blog.build_site as bs
    monkeypatch.setattr(bs, "SRC", str(tmp_path))
    monkeypatch.setattr(bs, "SITE", str(tmp_path / "site"))
    (tmp_path / "tistory").mkdir()
    (tmp_path / "tistory" / f"{TODAY}-tistory-draft.html").write_text("<html><body>legacy helper incomplete</body></html>")
    assert bs._daily_body_media(TODAY) == [] and bs._daily_media_meta(TODAY) == ("", "")   # 꺼진 경로: 원고를 파싱하지 않는다


def test_site_copies_verified_dashboards_for_past_days_and_skips_broken_contracts(tmp_path, monkeypatch):
    import blog.build_site as bs
    from blog.run_daily import write_digest_outputs
    names = {}
    for day in ("2026-09-03", "2026-09-04"):
        visual = vf.prepare_visual_first(snapshot(day), day, tmp_path / "images")
        write_digest_outputs(build_daily_digest(_sample_ds(), day, ASOF, visual_first=visual), day, str(tmp_path))
        names[day] = visual.image.name
    (tmp_path / "tistory/2026-09-03-tistory-draft.body-media.json").write_text("{}")   # 지난날 계약 손상
    monkeypatch.setattr(bs, "SRC", str(tmp_path))
    monkeypatch.setattr(bs, "SITE", str(tmp_path / "site"))
    assert names["2026-09-03"] != names["2026-09-04"]
    assert bs._daily_body_media("2026-09-05") == [f"images/{names['2026-09-04']}"]
    assert not (tmp_path / "site/images" / names["2026-09-03"]).exists()
    with pytest.raises(Exception):
        bs._daily_body_media("2026-09-03")   # 오늘 계약이 깨졌으면 사이트 빌드를 멈춘다


def test_site_tip_closes_on_second_click_and_survives_pointer_travel(required_browser, tmp_path):
    from blog.daily_digest import _SITE_CSS
    site = _digest(tmp_path)["site_html"]
    start = site.find('<section class="vf" data-visual-first>')
    top = site[start:site.find("</script></section>", start) + len("</script></section>")]
    context = required_browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content(f'<style>{_SITE_CSS}</style><body><div class="wrap">{top}</div></body>')
        tip = page.locator("button.tip").first
        body = page.locator("#" + tip.get_attribute("aria-describedby"))
        tip.click()
        assert body.is_visible()
        tip.click()                         # 두 번째 클릭은 포인터가 버튼 위에 있어도 닫는다
        assert tip.get_attribute("aria-expanded") == "false" and not body.is_visible()
        page.mouse.move(1, 1)
        b = tip.bounding_box()
        page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        assert body.is_visible()
        t = body.bounding_box()
        page.mouse.move(b["x"] + b["width"] / 2, t["y"] + 10, steps=12)   # 버튼→툴팁 사이 틈을 실제로 지난다
        assert body.is_visible()
    finally:
        context.close()
