"""공개 목록 판독 합성 회귀. 실제 네트워크·브라우저 사용 없음."""
import datetime
import html

import pytest

from blog import tistory_remote as remote


BLOG = "https://example.tistory.com"
HEADING = '<h2 class="title-search article-title-thumbnail"><b class="archives">전체 글</b> <span>74</span></h2>'
TITLE = "서울 아파트 오늘의 변화 — 2026-09-09 · 합성 원고"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    class Today(datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 11)
    monkeypatch.setattr(remote.datetime, "date", Today)


def article(title=TITLE, href="/82", **attrs):
    extra = " ".join(f'{key}="{html.escape(value, quote=True)}"' for key, value in attrs.items())
    return (f'<article class="article-type-common article-type-thumbnail"><a href="{html.escape(href, quote=True)}" '
            f'class="link-article" data-tiara-name="{html.escape(title, quote=True)}" {extra}>'
            "본문의 다른 제목</a></article>")


def listing():
    return HEADING + article() + article(TITLE.replace("09-09", "09-08"), "/81")


def test_exact_hit_needs_only_one_closed_article_and_same_blog_link():
    assert remote.parse_publication_listing(article(), TITLE, BLOG) == (True, BLOG + "/82")
    raw = article(**{"data-tiara-plink": "/82"}).replace("—", "&mdash;").replace("·", "&middot;")
    assert remote.parse_publication_listing(raw, TITLE, BLOG) == (True, BLOG + "/82")
    spaced = TITLE.replace("오늘의 변화", "오늘의\n  변화")
    assert remote.parse_publication_listing(article(spaced), TITLE, BLOG) == (True, BLOG + "/82")


@pytest.mark.parametrize("href", ["/82", BLOG + "/82", "//example.tistory.com/82", "https://example.tistory.com:443/82"])
def test_relative_and_absolute_same_origin_post_links(href):
    assert remote.parse_publication_listing(article(href=href), TITLE, BLOG) == (True, BLOG + "/82")


@pytest.mark.parametrize("href", ["https://evil.invalid/82", "http://example.tistory.com/82",
                                 "//evil.invalid/82", "https://example.tistory.com.evil.invalid/82",
                                 "https://example.tistory.com@evil.invalid/82", "/manage/82", "/82?secret=value",
                                 "/82#other", "/entry/title", "/082", "/82/", "/0", "javascript:alert(1)",
                                 "/82\n", "/%38%32", "https://example.tistory.com:8443/82"])
def test_fake_or_non_public_links_never_count_as_publication(href):
    assert remote.parse_publication_listing(article(href=href), TITLE, BLOG) == (None, "")


def test_invalid_links_do_not_make_a_normal_miss_listing():
    raw = HEADING + article() + article(TITLE.replace("09-09", "09-08"), "https://evil.invalid/81")
    assert remote.parse_publication_listing(raw, TITLE.replace("09-09", "09-10"), BLOG) == (None, "")


@pytest.mark.parametrize("target", [TITLE.replace("09-09", "09-08"), TITLE.replace("09-09", "09-10"),
                                  TITLE.replace("09-09", "09-11")])
def test_normal_listing_miss_within_observed_date_range(target):
    target += " 다른 합성 제목"
    assert remote.parse_publication_listing(listing(), target, BLOG) == (False, "")


@pytest.mark.parametrize("target", [TITLE.replace("09-09", "09-07"), TITLE.replace("09-09", "09-12"),
                                  TITLE.replace("2026-09-09", "2026-02-30"), "날짜 없는 합성 제목"])
def test_old_future_invalid_or_undated_target_cannot_prove_absence(target):
    assert remote.parse_publication_listing(listing(), target, BLOG) == (None, "")


@pytest.mark.parametrize("raw", ["", "<html><body>점검 중</body></html>", HEADING,
                                 HEADING + article(), '<p>' + TITLE + '</p>',
                                 '<a href="/82" class="link-article" data-tiara-name="' + TITLE + '">글</a>',
                                 article().replace('class="link-article"', 'class="changed-schema"'),
                                 article().replace("</article>", ""), article().replace("</a>", ""),
                                 "<script>" + article() + "</script>", "<template>" + article() + "</template>"])
def test_blank_maintenance_drift_truncation_or_body_only_is_unknown(raw):
    target = TITLE if raw != HEADING + article() else TITLE + " 없음"
    assert remote.parse_publication_listing(raw, target, BLOG) == (None, "")


def test_partial_titles_never_hit_and_normal_miss_has_no_url():
    assert remote.parse_publication_listing(article(TITLE + " 추가"), TITLE, BLOG) == (None, "")
    assert remote.parse_publication_listing(listing(), TITLE + " 추가", BLOG) == (False, "")


def test_conflicting_anchor_identity_or_multiple_hits_is_unknown():
    raw = article(**{"data-tiara-plink": "/999"})
    assert remote.parse_publication_listing(raw, TITLE, BLOG) == (None, "")
    assert remote.parse_publication_listing(article() + article(href="/83"), TITLE, BLOG) == (None, "")


def test_miss_requires_archives_heading_and_two_distinct_dated_posts():
    target = TITLE.replace("09-09", "09-10")
    assert remote.parse_publication_listing(listing().replace(HEADING, ""), target, BLOG) == (None, "")
    raw = HEADING + article() + article()
    assert remote.parse_publication_listing(raw, target, BLOG) == (None, "")
    raw = HEADING + article() + article("날짜 없는 제목", "/81")
    assert remote.parse_publication_listing(raw, target, BLOG) == (None, "")


@pytest.mark.parametrize("tail", ["<article>",
                                  article(TITLE.replace("09-09", "09-10"), "/83").replace("</article>", ""),
                                  article(TITLE.replace("09-09", "09-10"), "/83").split("</a>")[0],
                                  "<h2>미완성 제목", "<script>", "<style>", "<template>", "<noscript>"])
def test_partial_tail_cannot_turn_front_items_into_proven_miss(tail):
    target = TITLE.replace("09-09", "09-10")
    assert remote.parse_publication_listing(listing() + tail, target, BLOG) == (None, "")
    # 이미 완성된 동일 제목·글 링크의 양성 증거는 후미 잘림으로 사라지지 않는다.
    assert remote.parse_publication_listing(listing() + tail, TITLE, BLOG) == (True, BLOG + "/82")
