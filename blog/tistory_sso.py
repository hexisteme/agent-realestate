"""카카오 간편로그인에서 저장된 계정 타일만 한 번 선택한다."""

from urllib.parse import urlsplit


_LOGIN_PATHS = {"/login", "/login/", "/login/simple", "/login/simple/"}


def select_kakao_account(page, *, already_clicked: bool = False) -> str:
    """비밀번호 입력이나 계정 선택이 필요하면 동작 없이 고정 상태만 반환한다.

    계정의 표시명·주소·인증 URL은 읽거나 반환하지 않는다. 호출자는
    TILE_CLICKED 뒤 already_clicked를 유지해 지연된 화면에서 재클릭을 막는다.
    """
    try:
        current = urlsplit(page.url)
        valid_route = (
            current.scheme == "https"
            and current.hostname == "accounts.kakao.com"
            and current.port in {None, 443}
            and current.username is None
            and current.password is None
            and current.path in _LOGIN_PATHS
        )
    except (TypeError, ValueError):
        valid_route = False
    if not valid_route:
        return "LAYOUT_UNRECOGNIZED"

    return page.evaluate(
        """alreadyClicked => {
            const paths = ['/login', '/login/', '/login/simple', '/login/simple/'];
            if (location.origin !== 'https://accounts.kakao.com'
                || !paths.includes(location.pathname)) return 'LAYOUT_UNRECOGNIZED';

            const visible = element => {
                if (element.closest('[hidden]')) return false;
                const style = getComputedStyle(element);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.visibility !== 'collapse'
                    && element.getClientRects().length > 0;
            };
            const enabled = element => !element.closest(
                '[disabled], [aria-disabled="true"], [inert]'
            );

            if ([...document.querySelectorAll('input[type="password"]')].some(visible)) {
                return 'PW_FORM';
            }
            if (alreadyClicked) return 'WAIT';
            if (!['/login/simple', '/login/simple/'].includes(location.pathname)) {
                return 'LAYOUT_UNRECOGNIZED';
            }

            const accountLists = [...document.querySelectorAll('ul.list_easy')].filter(visible);
            if (!accountLists.length) return 'LAYOUT_UNRECOGNIZED';
            const saved = accountLists.flatMap(list => [...list.querySelectorAll(
                ':scope > li > a.wrap_profile[role="button"]'
            )]).filter(tile => tile.querySelector(':scope > div.cont_profile > span.tit_profile')
                && tile.querySelector(':scope > div.cont_profile > span.info_profile'))
                .filter(visible);

            // 저장된 계정이 여럿이면 신원을 임의로 선택하지 않는다.
            if (saved.length > 1) return 'ACCOUNT_SELECTION';
            if (!saved.length || !enabled(saved[0])) return 'WAIT';
            saved[0].click();
            return 'TILE_CLICKED';
        }""",
        already_clicked,
    )
