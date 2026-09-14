"""운영 프로필·네트워크 없이 실제 Chromium DOM으로 간편로그인 계약을 검증한다."""

from unittest.mock import Mock

import pytest

from blog.tistory_sso import select_kakao_account


LOGIN_URL = "https://accounts.kakao.com/login/simple/"
SAVED = """<li><a class="wrap_profile" role="button" id="saved-{id}" {attrs}>
<div class="cont_profile"><span class="tit_profile">합성 계정</span>
<span class="info_profile">synthetic@example.invalid</span></div></a></li>"""
OTHER = """<li><a class="wrap_profile" role="button" id="new-account">
<div class="cont_profile"><span class="tit_profile">새로운 계정으로 로그인</span></div></a></li>"""
DELETE = '<button class="btn_delete" id="delete-account">간편로그인 계정 삭제</button>'


@pytest.fixture(scope="module")
def browser():
    api = pytest.importorskip("playwright.sync_api")
    with api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except api.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("설치된 Chromium이 없어 DOM 회귀 실행 불가")
            raise
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(service_workers="block")
    context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=""))
    page = context.new_page()
    page.goto(LOGIN_URL)
    yield page
    context.close()


def saved(number=1, attrs=""):
    return SAVED.format(id=number, attrs=attrs)


def show(page, tiles=None, extra=""):
    page.set_content(
        '<style>a {display:block; width:260px; min-height:24px}</style>'
        '<ul class="list_easy">' + (saved() if tiles is None else tiles) + OTHER + '</ul>'
        + DELETE + extra
        + """<script>window.clicked = []; document.addEventListener('click', event => {
            const target = event.target.closest('a,button');
            if (target) {event.preventDefault(); window.clicked.push(target.id);}
        });</script>"""
    )


def clicks(page):
    return page.evaluate("window.clicked")


def test_one_saved_account_excludes_new_and_delete_controls(page):
    show(page)
    assert select_kakao_account(page) == "TILE_CLICKED"
    assert clicks(page) == ["saved-1"]
    assert select_kakao_account(page, already_clicked=True) == "WAIT"
    assert clicks(page) == ["saved-1"]


def test_two_saved_accounts_require_choice_without_click(page):
    show(page, saved(1) + saved(2))
    assert select_kakao_account(page) == "ACCOUNT_SELECTION"
    assert clicks(page) == []


@pytest.mark.parametrize("attrs", [
    'hidden', 'style="display:none"', 'style="visibility:hidden"',
    'style="visibility:collapse"', 'disabled', 'aria-disabled="true"', 'inert',
])
def test_unavailable_saved_account_is_never_clicked(page, attrs):
    show(page, saved(attrs=attrs))
    assert select_kakao_account(page) == "WAIT"
    assert clicks(page) == []


def test_hidden_duplicate_does_not_require_false_account_choice(page):
    show(page, saved(1) + saved(2, 'hidden'))
    assert select_kakao_account(page) == "TILE_CLICKED"
    assert clicks(page) == ["saved-1"]


def test_disabled_second_account_does_not_choose_a_different_identity(page):
    show(page, saved(1) + saved(2, 'aria-disabled="true"'))
    assert select_kakao_account(page) == "ACCOUNT_SELECTION"
    assert clicks(page) == []


@pytest.mark.parametrize("already_clicked", [False, True])
def test_visible_password_always_requires_input(page, already_clicked):
    show(page, extra='<input type="password">')
    assert select_kakao_account(page, already_clicked=already_clicked) == "PW_FORM"
    assert clicks(page) == []


def test_password_route_requires_input(page):
    page.goto("https://accounts.kakao.com/login/")
    show(page, extra='<input type="password">')
    assert select_kakao_account(page) == "PW_FORM"
    assert clicks(page) == []


@pytest.mark.parametrize("attrs", ['hidden', 'style="display:none"', 'style="visibility:hidden"'])
def test_hidden_password_does_not_prevent_saved_login(page, attrs):
    show(page, extra=f'<input type="password" {attrs}>')
    assert select_kakao_account(page) == "TILE_CLICKED"
    assert clicks(page) == ["saved-1"]


def test_unknown_layout_does_not_use_text_or_loose_class_matches(page):
    show(page, tiles="")
    page.locator("ul.list_easy").evaluate("element => element.remove()")
    page.locator("body").evaluate("element => element.insertAdjacentHTML('beforeend', "
                                   "'<a class=account-profile id=loose>간편로그인 계속하기</a>')")
    assert select_kakao_account(page) == "LAYOUT_UNRECOGNIZED"
    assert clicks(page) == []


def test_new_account_only_is_not_a_saved_login(page):
    show(page, tiles="")
    assert select_kakao_account(page) == "WAIT"
    assert clicks(page) == []


@pytest.mark.parametrize("tiles", [
    '<li><a class="wrap_profile" role="button"><span class="info_profile">x</span></a></li>',
    '<li><button class="wrap_profile"><div class="cont_profile">'
    '<span class="tit_profile">x</span><span class="info_profile">y</span></div></button></li>',
])
def test_incomplete_or_wrong_element_contract_is_never_clicked(page, tiles):
    show(page, tiles=tiles)
    assert select_kakao_account(page) == "WAIT"
    assert clicks(page) == []


def test_already_clicked_waits_through_transient_missing_layout(page):
    show(page)
    assert select_kakao_account(page) == "TILE_CLICKED"
    page.locator("ul.list_easy").evaluate("element => element.remove()")
    assert select_kakao_account(page, already_clicked=True) == "WAIT"
    assert clicks(page) == ["saved-1"]


@pytest.mark.parametrize("url", [
    "https://accounts.kakao.com.evil.invalid/login/simple/",
    "https://example.invalid/login/simple/", "http://accounts.kakao.com/login/simple/",
    "https://accounts.kakao.com:8443/login/simple/", "https://accounts.kakao.com/account",
    "https://accounts.kakao.com/login/simple/unrecognized", "https://x@accounts.kakao.com/login/simple/",
    "https://accounts.kakao.com:bad/login/simple/",
])
def test_unknown_host_or_path_is_rejected_before_dom_access(url):
    page = Mock(url=url)
    assert select_kakao_account(page) == "LAYOUT_UNRECOGNIZED"
    page.evaluate.assert_not_called()


def test_changed_origin_is_rechecked_in_browser_before_click(page):
    show(page)
    page.goto("https://example.invalid/login/simple/")
    show(page)
    stale_page = Mock(url=LOGIN_URL)
    stale_page.evaluate = page.evaluate
    assert select_kakao_account(stale_page) == "LAYOUT_UNRECOGNIZED"
    assert clicks(page) == []


def observe_editor_route(page):
    target = "https://floker.tistory.com/manage/newpost/86/"
    requests = []

    def editor_response(route):
        requests.append(route.request.url)
        route.fulfill(status=200, content_type="text/html",
                      body='<input id="post-title-inp" value="합성 원고">')

    page.route(target, editor_response)
    return target, requests


def test_login_return_from_management_home_resumes_original_editor(page):
    from blog import tistory_publish_pw as pub

    target, requests = observe_editor_route(page)
    page.goto("https://floker.tistory.com/manage/")
    page.set_content('<a href="/manage/posts">글 관리</a>')

    pub._wait_for_login_return(page, target, wait_s=1)

    assert page.url == target
    assert requests == [target]
    assert page.locator("#post-title-inp").is_visible()


def test_login_return_still_on_kakao_does_not_navigate_to_editor(page):
    from playwright.sync_api import TimeoutError as BrowserTimeout
    from blog import tistory_publish_pw as pub

    target, requests = observe_editor_route(page)
    show(page, extra='<input type="password">')

    with pytest.raises(BrowserTimeout):
        pub._wait_for_login_return(page, target, wait_s=0.1)

    assert page.url == LOGIN_URL
    assert requests == []


@pytest.mark.parametrize("markup", [
    '<a href="/manage/posts">글 관리</a>',
    '<input id="post-title-inp">',
])
def test_login_return_rejects_same_markup_on_another_host(page, markup):
    from playwright.sync_api import TimeoutError as BrowserTimeout
    from blog import tistory_publish_pw as pub

    target, requests = observe_editor_route(page)
    foreign = "https://example.invalid/manage/"
    page.goto(foreign)
    page.set_content(markup)

    with pytest.raises(BrowserTimeout):
        pub._wait_for_login_return(page, target, wait_s=0.1)

    assert page.url == foreign
    assert requests == []
