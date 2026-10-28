"""티스토리 일일 자동 게시 — Playwright 재구축 (2026-06-28).

배경: Tistory newpost 가 Kakao SPA 에디터(#post-editor-app, 3모드 kakao/markdown/html)로
바뀌면서, 발행 직렬화가 **에디터 내부 모델**에서 이뤄진다. 기존 AppleScript+JS 주입
(tistory_publish.py)은 `iframe#editor-tistory_ifr` body.innerHTML 만 바꿔 **모델 미반영 →
제목·카테고리만 있고 본문이 빈 글**이 발행됐다 (#3~#7 raw HTML 로 확정). 모델에 도달하는 유일
경로는 HTML모드 `CodeMirror.setValue` 인데, 모드전환·발행 시 native confirm 다이얼로그가 떠
AppleScript 의 `execute javascript` 가 -1712 로 행 (window.confirm 오버라이드로도 차단됨).

Playwright 가 이를 푼다:
  - `page.on("dialog", d.accept())` 로 모드전환/발행 confirm 을 네이티브 자동수락 → 행 없음.
  - `page.evaluate` 로 HTML모드 CodeMirror 인스턴스에 `.setValue(body)` → 본문 모델 주입.
  - 전용 영속 프로필(채널 chrome)로 Tistory 로그인 1회 후 쿠키 재사용 → 사용자 메인 Chrome 과
    독립, cron 무인 동작.

검증된 셀렉터(2026-06-28 라이브):
  - 제목 `#post-title-inp` · 본문 모델 `#html-editor-container .CodeMirror`(.CodeMirror.setValue)
  - 태그 `#tagText` · 카테고리 `#category-btn` → 메뉴 `.mce-text` 텍스트 매칭
  - 모드 토글 `#editor-mode-layer-btn-open` → `#editor-mode-html-tistory`
  - 발행 `#publish-layer-btn`(완료) → `#open20`(공개) → `#publish-btn`(발행)

mode: inject(주입+카테고리, 발행 안함) | draft(임시저장) | publish(공개 발행).
첫 실행은 headful 로 뜨고 미로그인이면 로그인 대기(카카오 캡차는 사람 1회).
"""
from __future__ import annotations
import argparse
import datetime
import glob
import os
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

# 기존 파서 재사용 (헬퍼 HTML → title/tags/body)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from blog.tistory_publish import _parse_helper  # noqa: E402
from blog.build_explorer import TAKEDOWN_PLACEHOLDER  # noqa: E402
from blog.tistory_contract import DraftContractError  # noqa: E402
from blog.periodic_approval import needs_review  # noqa: E402
from blog.tistory_delivery import (PUBLICATION_KINDS, is_publication_kind, payload_digest,  # noqa: E402
                                   read_delivery, write_delivery)
from blog.tistory_keychain import read_kakao_credentials  # noqa: E402
from blog.tistory_session import load_session_state, save_session_state  # noqa: E402
from blog.tistory_sso import select_kakao_account, submit_kakao_credentials  # noqa: E402
from blog.tistory_media import MediaContractError, read_media_manifest  # noqa: E402
from blog.body_media import (BodyMediaError, read_body_media, read_editor_images,  # noqa: E402
                             verify_body_images, wait_for_source_images)
from blog.tistory_design_review import grade_design_review, resolve_design_review_path  # noqa: E402

NEWPOST_URL = os.environ.get("TISTORY_NEWPOST_URL", "https://floker.tistory.com/manage/newpost/")


def resolve_editor_url(post_id: str | None = None) -> str:
    """새 글이면 newpost, 글번호를 주면 그 글의 편집 URL(같은 에디터, 경로에 번호만 붙는다).

    발행 뒤 데이터 정정이 필요할 때 새 글을 또 올리면 URL·색인이 갈라진다 — 같은 글을 고쳐
    원래 URL 을 유지한다(2026-09-06 소재구 정정)."""
    if not post_id:
        return NEWPOST_URL
    return NEWPOST_URL.rstrip("/") + "/" + str(post_id).strip("/") + "/"
CATEGORY_MATCH = os.environ.get("TISTORY_CATEGORY", "오늘의 변화")
# 외부 리뷰 U13(2026-10-13) — 주간·월간 결산은 일간과 다른 카테고리. 과거 글은 옮기지 않는다(소급 금지).
PERIODIC_CATEGORY_MATCH = os.environ.get("TISTORY_CATEGORY_PERIODIC", "주간·월간 결산")
# 연재 원고(2026-10-14 승인) — 사람이 날짜를 지정해 1편씩 발행한다(cron·--pending 미연결). 카테고리는 기존 '부동산 > 서울(아파트)'.
SERIES_KINDS = ("upsize", "deposit", "watch")
SERIES_CATEGORY_MATCH = os.environ.get("TISTORY_CATEGORY_SERIES", "서울(아파트)")


# 카테고리 후보를 앞부터: ① 보이는 메뉴 항목(.mce-menu-item)의 정확한 이름 ② 없으면 종전 부분일치(짧은 요소 첫 항목).
# ①이 안내 문구·숨은 항목을 집지 않게 한다(R6 Codex U13-1). 고른 후보 이름(없으면 null)을 돌려준다.
CATEGORY_PICK_JS = """(names) => {
    const norm = x => (x.textContent || '').replace(/\\s+/g, ' ').trim();
    const shown = x => !!(x.offsetWidth || x.offsetHeight || x.getClientRects().length);
    for (const M of names) {
      const exact = [].slice.call(document.querySelectorAll('.mce-menu-item')).filter(x => shown(x) && norm(x) === M)[0];
      if (exact) { exact.click(); return M; }
      const it = [].slice.call(document.querySelectorAll('.mce-menu-item,.mce-text,li,a,span'))
        .filter(x => norm(x).indexOf(M) > -1 && (x.textContent || '').length < 40)[0];
      if (!it) continue;
      ((it.closest && it.closest('.mce-menu-item')) || it).click();
      return M;
    }
    return null;
}"""


def category_candidates(kind: str = "daily") -> tuple[str, ...]:
    """원고 종류별 카테고리 후보(앞부터 시도). periodic 은 전용 카테고리가 블로그에 없을 때 '분류 없음'으로 새지 않게
    일간 카테고리로 물러선다."""
    if kind == "periodic":
        return (PERIODIC_CATEGORY_MATCH, CATEGORY_MATCH)
    if kind in SERIES_KINDS:   # 연재가 일간 '오늘의 변화'로 새지 않게 상위 '부동산'으로만 물러선다
        return (SERIES_CATEGORY_MATCH, "부동산")
    return (CATEGORY_MATCH,)
# 영속 프로필: EXT_SSD (APFS 가드 — 내장 디스크 금지). Tistory 로그인 쿠키 보관.
PROFILE_DIR = os.environ.get(
    "TISTORY_PW_PROFILE", "/Volumes/EXT_SSD/bot/agent_realestate/.pw-profile")
# 세션 쿠키 영속 파일: ctx.close() 로 증발하는 __T_/__T_SECURE 등 persist=0 쿠키를 보존.
STATE_FILE = os.path.join(PROFILE_DIR, "tistory_state.json")
# 떠 있는 정식 크롬(launchd 상주, 원격 디버깅)에 CDP 로 붙는다 — 비면 Playwright 가 크롬을 띄운다.
# 10-24·10-26 07시 무인 발행이 클릭 뒤 이동 없이 끝나 Playwright 가 띄운 창 대신 상주 창에서 전용 탭만 쓴다.
CDP_URL = os.environ.get("TISTORY_CDP_URL", "")
DIAG_DIR = "report/blog/tistory/diag"
# 상주 크롬 launchd 라벨. 붙기가 실패하면 한 번 재기동하고 다시 붙는다 — 10-28 07:05 응답 없는 newtab 탭 하나가
# connect_over_cdp 를 180초 묶어 슬롯 전체가 실패했다. 재기동은 사람 입력을 기다리던 캡차 탭도 닫는다.
RESIDENT_CHROME_LABEL = "com.hexisteme.re-blog.chrome"
CDP_CONNECT_TIMEOUT_MS = 45000
CDP_RESTART_WAIT_S = 30
_CAPTCHA_PROBE = (
    "() => { const q = ['captcha', 'kaptcha'].map(k => `[id*=${k} i],[class*=${k} i],iframe[src*=${k} i]`).join(',');"
    " const t = document.body ? document.body.innerText : '';"
    " return !!document.querySelector(q)"
    " || /자동\\s*(입력|등록)\\s*방지|보안\\s*문자|빈칸에 들어갈 글자|로봇이 아닙니다/.test(t); }")
# 발행 성공 마커(중복발행 방지)·시도 마커(at-most-once)·알림 nag-once 마커 — outroot 기준.
PUBLISH_MARKER = ".last-tistory-published"
ATTEMPT_MARKER = ".last-tistory-attempted"
ALERT_MARKER = ".last-tistory-alerted"
# 디자인 리뷰 게이트(2026-10-09): 보류 알림 nag-once 마커는 실패 알림 마커와 분리한다 —
# 같은 날 로그인 만료 알림이 디자인 보류 알림에 가려지지 않게. 종료코드는 cron_daily.sh 가 크래시로 보지 않는다.
DESIGN_ALERT_MARKER = ".last-tistory-design-alerted"
DESIGN_BLOCK_EXIT = 5  # open P1 defect(check=rule) — 발행 안 함
DESIGN_AWAIT_EXIT = 6  # 깨진·오래된 리뷰 또는 open P1 defect(check=advisory) — 사람 판단 전 발행 안 함
# 부동산 블로그 소유 카카오 계정(비밀 아님). 공개 저장소라 값은 .env/환경변수에만 둔다.
KAKAO_ACCOUNT_KEY = "TISTORY_KAKAO_ACCOUNT"


def restart_resident_chrome(cdp_url: str = CDP_URL, wait_s: int = CDP_RESTART_WAIT_S) -> bool:
    """상주 크롬을 launchd 로 재기동하고 원격 디버깅 포트가 다시 답할 때까지 기다린다."""
    try:
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{RESIDENT_CHROME_LABEL}"],
                       check=True, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=3):
                return True
        except OSError:
            time.sleep(1)
    return False


def connect_resident_chrome(chromium, log: list):
    """상주 크롬에 붙는다. 실패하면 크롬을 한 번 재기동해 다시 붙고, 그래도 안 되면 None(발행 시도 전이라 다음 슬롯이 다시 한다)."""
    for restarted in (False, True):
        try:
            return chromium.connect_over_cdp(CDP_URL, timeout=CDP_CONNECT_TIMEOUT_MS)
        except Exception as e:
            log.append(f"CDP_CONNECT_FAIL:{type(e).__name__}")
        if restarted or not restart_resident_chrome():
            return None
        log.append("CDP_CHROME_RESTARTED")
    return None


def _read_marker(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read().strip()
    except OSError:
        return ""


def category_outcome(kind: str, picked: str | None, shown: str | None = None) -> tuple[str, bool]:
    """카테고리 선택 결과 → (로그 토큰, 발행 중단 여부). 연재는 후보를 못 고르거나(G6 Codex) 에디터 카테고리 버튼에
    보이는 값이 고른 후보가 아니면(숨은 항목 클릭 — R3 Codex) '분류 없음'으로 공개되지 않게 멈춘다. 일간·결산은 종전대로 기록만."""
    if not picked:
        return "CAT_NOTFOUND", kind in SERIES_KINDS
    if kind in SERIES_KINDS and picked not in " ".join((shown or "").split()):
        return f"CAT_MISMATCH:{' '.join((shown or '').split())}", True
    return ("CAT" if picked == category_candidates(kind)[0] else f"CAT_FALLBACK:{picked}"), False


def marker_paths(outroot: str, kind: str = "daily") -> tuple[str, str, str]:
    """(발행, 시도, 알림) 마커 경로 — daily 는 기존 이름 그대로(cron_daily.sh 멱등 가드가 읽는다), 그 외 kind 는
    접미사(-{kind}) — 하루 2편째(periodic=주간결산/월간결산, 2026-09-07)가 daily 의 "오늘 이미 발행" 게이트에 막히지 않게."""
    suf = "" if kind == "daily" else f"-{kind}"
    return (os.path.join(outroot, PUBLISH_MARKER + suf), os.path.join(outroot, ATTEMPT_MARKER + suf),
            os.path.join(outroot, ALERT_MARKER + suf))


_DAILY_DRAFT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-tistory-draft\.html$")


def _latest_draft_for_kind(outroot: str, kind: str = "daily") -> str | None:
    """kind 별 최신 원고 — daily 는 {date}-tistory-draft.html 만(periodic 원고를 daily 로 잘못 집어 이중 발행하는 경로 차단)."""
    hits = glob.glob(os.path.join(outroot, "report/blog/tistory/*-tistory-draft.html"))
    if kind == "daily":
        hits = [h for h in hits if _DAILY_DRAFT_RE.match(os.path.basename(h))]
    else:
        hits = [h for h in hits if os.path.basename(h).endswith(f"-{kind}-tistory-draft.html")]
    hits.sort(reverse=True)
    return hits[0] if hits else None


def _load_state(ctx, log: list[str]) -> None:
    """현재 프로필 쿠키를 우선하고 없는 세션 쿠키만 복원한다."""
    load_session_state(ctx, STATE_FILE, log)


def _save_state(ctx, log: list[str]) -> None:
    """인증 성공 뒤의 최신 세션을 권한600으로 원자적으로 보존한다."""
    save_session_state(ctx, STATE_FILE, log)


def _auth_stage(url: str) -> str:
    """OAuth 쿼리·토큰을 로그에 남기지 않고 실패 단계만 반환한다."""
    parsed = urlsplit(url)
    if parsed.hostname == "accounts.kakao.com":
        return "KAKAO_LOGIN" if "/login" in parsed.path else "KAKAO_ACCOUNT"
    if parsed.hostname == "kauth.kakao.com":
        return "KAKAO_SSO"
    if parsed.hostname in {"www.tistory.com", "floker.tistory.com"}:
        return "TISTORY_LOGIN" if "/auth/" in parsed.path else "TISTORY_PAGE"
    return "OTHER_PAGE"


def _expected_kakao_account() -> str | None:
    """간편로그인에서 고를 블로그 소유 계정. 환경변수 우선, 없으면 .env 의 같은 키.

    한 프로필에 다른 블로그 계정이 함께 저장돼도(2026-10-06~09 장애) 이 계정만 고른다.
    .env 의 다른 시크릿은 os.environ 에 주입하지 않는다."""
    value = os.environ.get(KAKAO_ACCOUNT_KEY)
    if value is None:
        try:
            from agent_realestate.config import read_env_value
            value = read_env_value(KAKAO_ACCOUNT_KEY)
        except Exception:
            value = None
    return (value or "").strip() or None


def _relogin_via_kakao_sso(page, log: list[str], target_url: str = NEWPOST_URL) -> bool:
    """티스토리 세션만료 시 무인 재로그인 시도 (2026-07-06, 이틀 연속 login_timeout 대응).

    티스토리는 세션(__T_) 이 죽으면 auth/login 으로 리다이렉트만 하고 카카오 SSO 를 자동
    개시하지 않는다 — '카카오계정으로 로그인' 클릭이 필요. 카카오 웹세션이 살아있으면
    (또는 간편로그인 저장계정이 있으면) 비밀번호 없이 SSO 왕복이 완주된다.
    비밀번호 폼이 뜨면 macOS Keychain 자격증명을 한 번만 제출한다. Keychain 부재·거부·폼
    변경은 False 로 닫혀 기존 사람 로그인 대기로 넘어간다."""
    try:
        stage = _auth_stage(page.url)
        log.append(f"AUTH_STAGE:{stage}")
        if stage == "TISTORY_LOGIN":
            clicked = page.evaluate(
            """() => {
                const a = [].slice.call(document.querySelectorAll('a,button'))
                  .filter(x => /카카오계정으로 로그인/.test((x.textContent||'')))[0];
                if (a) { a.click(); return true; }
                return false;
            }""")
            if not clicked:
                log.append("SSO_NO_BTN")
                return False
        elif stage not in {"KAKAO_LOGIN", "KAKAO_ACCOUNT", "KAKAO_SSO"}:
            log.append("SSO_NO_LOGIN_ROUTE")
            return False
        expected_account = _expected_kakao_account()
        if expected_account:
            log.append("SSO_ACCOUNT_PINNED")
        clicked_account = False
        keychain_submitted = False
        last_state = "WAIT"
        for _ in range(20):
            page.wait_for_timeout(1000)
            url = page.url
            # /auth/kakao/redirect(코드 교환 중)도 /auth/ 라 제외 — 완주 후 URL 만 인정.
            if _auth_stage(url) == "TISTORY_PAGE":
                break  # SSO 왕복 완료
            if urlsplit(url).hostname == "accounts.kakao.com":
                state = select_kakao_account(page, already_clicked=clicked_account,
                                             expected_account=expected_account)
                last_state = state
                if state == "TILE_CLICKED":
                    clicked_account = True
                    log.append("SSO_ACCOUNT_CLICKED")
                if state == "PW_FORM":
                    if keychain_submitted:
                        continue
                    log.append("SSO_PW_FORM")
                    credentials = read_kakao_credentials(log)
                    if credentials is None:
                        return False
                    submitted = submit_kakao_credentials(
                        page, credentials.login_id, credentials.password,
                        allow_password_only=clicked_account)
                    del credentials
                    if submitted != "SUBMITTED":
                        log.append(f"KEYCHAIN_{submitted}")
                        return False
                    keychain_submitted = True
                    log.append("KEYCHAIN_LOGIN_SUBMITTED")
                    continue
                if state == "ACCOUNT_SELECTION":
                    log.append("SSO_ACCOUNT_SELECTION_REQUIRED")
                    return False
                if state == "EXPECTED_ACCOUNT_MISSING":
                    log.append("SSO_EXPECTED_ACCOUNT_MISSING")
                    return False
        if last_state == "LAYOUT_UNRECOGNIZED":
            log.append("SSO_LAYOUT_UNRECOGNIZED")
        if _auth_stage(page.url) != "TISTORY_PAGE":
            if keychain_submitted:
                log.append("KEYCHAIN_LOGIN_NOT_CONFIRMED")
            return False
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#post-title-inp", timeout=15000)
        log.append("AUTO_RELOGIN_OK")
        return True
    except Exception as e:
        log.append(f"SSO_FAIL:{type(e).__name__}")
        return False


def _wait_for_login_return(page, target_url: str, wait_s: int) -> None:
    """사람 로그인이 관리 홈으로 끝나도 요청했던 에디터로 돌아간다."""
    page.wait_for_function(
        """host => location.hostname === host &&
        (!!document.querySelector('#post-title-inp') ||
         (location.pathname.startsWith('/manage/') &&
          !!document.querySelector('a[href$="/manage/posts"]')))""",
        arg=urlsplit(target_url).hostname, timeout=wait_s * 1000)
    page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("#post-title-inp", timeout=30000)


def _probe_kakao_relogin(ctx, target_url: str, log: list[str]) -> bool:
    """운영 세션을 지우지 않고 별도 메모리 컨텍스트에서 Tistory 만료를 재현한다."""
    isolated = ctx.browser.new_context()
    try:
        # 이미 허용된 카카오 세션만 같은 인증 서비스에 재사용한다. 디스크 저장 없음.
        kakao = [cookie for cookie in ctx.cookies()
                 if cookie["domain"].lstrip(".").lower() == "kakao.com"
                 or cookie["domain"].lstrip(".").lower().endswith(".kakao.com")]
        isolated.add_cookies(kakao)
        log.append("RELOGIN_PROBE_NO_TISTORY_SESSION")
        page = isolated.new_page()
        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        if not _relogin_via_kakao_sso(page, log, target_url):
            return False
        log.append("RELOGIN_PROBE_OK")
        return True
    finally:
        isolated.close()


def _refresh_kakao_session(ctx, log: list[str]) -> None:
    """카카오 웹세션 keepalive (2026-07-06). 발행이 성공하는 날엔 카카오를 안 건드려
    세션이 조용히 썩고, 티스토리 세션이 죽는 날엔 카카오도 이미 죽어 사람이 필요했다.
    매 실행 카카오 인증 페이지를 1회 방문해 세션을 신선하게 유지한다. 실패해도 비치명."""
    kp = None
    try:
        kp = ctx.new_page()
        kp.goto("https://accounts.kakao.com/weblogin/account/info",
                wait_until="domcontentloaded", timeout=20000)
        kp.wait_for_timeout(2000)
        # 임의 오류 페이지/다른 리다이렉트를 정상 인증으로 취급하지 않는다.
        current = urlsplit(kp.url)
        alive = current.hostname == "accounts.kakao.com" and current.path.rstrip("/") == "/weblogin/account/info"
        log.append("KAKAO_ALIVE" if alive else f"KAKAO_NOT_CONFIRMED:{_auth_stage(kp.url)}")
    except Exception as e:
        log.append(f"KAKAO_KEEPALIVE_FAIL:{type(e).__name__}")
    finally:
        if kp is not None:
            try:
                kp.close()
            except Exception:
                pass


def _verify_published_on_blog(title: str, log: list[str]) -> bool | None:
    """공개 목록의 정확한 글 제목·링크를 확인한다. 불완전한 응답은 None."""
    try:
        import urllib.request
        from blog.tistory_remote import parse_publication_listing
        blog_home = NEWPOST_URL.split("/manage")[0] + "/"
        with urllib.request.urlopen(blog_home, timeout=15) as response:
            raw = response.read().decode("utf-8", "replace")
        hit, url = parse_publication_listing(raw, title, blog_home)
        log.append("REMOTE_VERIFY_HIT" if hit else "REMOTE_VERIFY_MISS" if hit is False else "REMOTE_VERIFY_UNKNOWN")
        if url:
            log.append(f"PUBLIC_URL:{url}")
        return hit
    except Exception as e:
        log.append(f"REMOTE_VERIFY_FAIL:{type(e).__name__}")
        return None  # 확인 장애는 미발행이 아니다. 재클릭 금지.


def _remote_post_url(log: list[str]) -> str:
    return next((line.removeprefix("PUBLIC_URL:") for line in reversed(log)
                 if line.startswith("PUBLIC_URL:")), "")


def _resolve_draft(outroot: str, date: str | None, kind: str = "daily") -> str | None:
    if date:
        nm = f"{date}-tistory-draft.html" if kind == "daily" else f"{date}-{kind}-tistory-draft.html"
        p = os.path.join(outroot, "report/blog/tistory", nm)
        return p if os.path.isfile(p) else None
    return _latest_draft_for_kind(outroot, kind)


def _write_marker(path: str, day: str) -> None:
    from blog.periodic_approval import write_review
    from pathlib import Path
    write_review(Path(path), day)


def _record_published(outroot: str, day: str, kind: str, data: dict, url: str = "") -> None:
    # 날짜별 기록을 먼저 확정한다. 마커 실패 뒤 재시도도 이 기록으로 중복을 막는다.
    write_delivery(outroot, day, kind, state="PUBLISHED", data=data, url=url)
    marker = marker_paths(outroot, kind)[0]
    if day >= _read_marker(marker):
        _write_marker(marker, day)


def _check_delivery(outroot: str, day: str, kind: str, data: dict, *, explicit: bool,
                    log: list[str]) -> str | None:
    """프로필 잠금 안에서만 호출. 날짜 지정도 동일한 중복 방지를 거친다."""
    receipt = read_delivery(outroot, day, kind)
    marker, attempted, _ = marker_paths(outroot, kind)
    if receipt and receipt["state"] == "PUBLISHED":
        if day >= _read_marker(marker):
            _write_marker(marker, day)
        return f"SKIP:already_published({day},{kind})"
    if _read_marker(marker) == day:
        return f"SKIP:already_published({day},{kind})"
    if receipt and receipt["digest"] != payload_digest(data):
        return "ERR:draft_changed_after_attempt — 이전 발행 시도 확인 필요"
    attempted_before = bool(receipt) or _read_marker(attempted) == day
    if explicit or attempted_before:
        found = _verify_published_on_blog(data["title"], log)
        if found is None:
            return f"ERR:publication_unknown | {' '.join(log)}"
        if found:
            _record_published(outroot, day, kind, data, url=_remote_post_url(log))
            return f"SKIP:verified_published_remote | {' '.join(log)}"
        if attempted_before:
            # 공개 목록의 반영 지연/페이지 이동은 이전 클릭 실패의 증거가 아니다.
            return f"ERR:publication_unknown_after_attempt | {' '.join(log)}"
        log.append("REMOTE_LISTING_NO_MATCH")
    return None


def publish(outroot: str = ".", mode: str = "inject", date: str | None = None,
            headless: bool = False, login_wait_s: int = 300,
            post_id: str | None = None, kind: str = "daily", probe_relogin: bool = False,
            design_review: str | None = None) -> str:
    today = datetime.date.today().isoformat()
    if not is_publication_kind(kind) or mode not in {"auth", "inject", "draft", "publish"}:
        return "ERR:invalid publication mode/kind"
    if kind in SERIES_KINDS and mode != "auth" and not date:
        return "ERR:series_requires_date"   # 연재는 '최신 원고' 자동 선택 없이 날짜로 1편만
    if kind in SERIES_KINDS and mode == "publish":
        from blog.series_edition import publishable
        why = publishable(outroot, kind, date)   # held·unjudged·draft_mismatch — 손으로 돌려도 판정과 맞는 원고만 연다
        if why:
            return f"ERR:series_{why}"
    if probe_relogin and mode != "auth":
        return "ERR:relogin_probe_requires_auth_mode"
    if date and (not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date)
                 or datetime.date.fromisoformat(date).isoformat() != date):
        return "ERR:invalid publication date"
    if post_id and not re.fullmatch(r"[0-9]+", post_id):
        return "ERR:invalid post id"
    path = _resolve_draft(outroot, date, kind) if mode != "auth" else None
    if mode != "auth" and not path:
        return "ERR:no draft helper found"
    name = os.path.basename(path) if path else "auth"
    day = name[:10] if path else today
    if mode == "publish" and not date and day != today:
        return f"ERR:draft_stale({name}) — 오늘자 draft 없음"
    try:
        data = _parse_helper(path) if path else {"title": "", "body": "", "tags": ""}
    except DraftContractError:
        return "ERR:draft contract invalid"
    if TAKEDOWN_PLACEHOLDER in data["body"]:
        return "ERR:takedown_contact_placeholder"   # .env 없이 만든 원고(2026-10-20 /135~/139 공개 사고)
    try:
        media = read_media_manifest(path, data) if path else None
    except MediaContractError:
        return "ERR:representative image contract invalid"
    try:
        body_images = read_body_media(path, data) if path else None
    except BodyMediaError:
        return "ERR:body image contract invalid"
    if mode == "publish" and needs_review(outroot, path, data, kind):
        return f"AWAIT_REVIEW:{name} — 첫 결산 원고의 사람 승인 필요"
    import fcntl
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with open(os.path.join(PROFILE_DIR, ".lock"), "w") as lock_f:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "SKIP:profile_locked — 같은 프로필의 다른 발행 프로세스 실행 중"
        # 마커·원격 대조·클릭을 하나의 잠금으로 묶는다.
        return _publish_locked(outroot, mode, day, name, data, headless, login_wait_s, post_id, kind,
                               explicit=bool(date), probe_relogin=probe_relogin, media=media,
                               draft_path=path, design_review=design_review, body_images=body_images)


def pasted_anchors_present(page, anchors) -> bool:
    """Require every expected text anchor in the actual editor iframe."""
    return page.evaluate("""(anchors) => {
        const frame = document.getElementById('editor-tistory_ifr');
        const doc = frame && frame.contentDocument;
        if (!doc || !anchors.length) return false;
        const text = (doc.body.innerText || '').replace(/\\s+/g, ' ');
        return anchors.every(anchor => anchor.length > 0 && text.includes(anchor));
    }""", anchors)


def attach_representative_card(page, media) -> None:
    """Select verified bytes in the observed publish-dialog representative input.

    A local digest binds the upload bytes; it does not authenticate remote reencoding.
    Selection alone is insufficient: a loaded preview must appear in this container.
    """
    try:
        container = page.locator(".box_thumb").filter(has=page.locator(".txt_thumb", has_text="대표이미지 추가"))
        upload = container.locator('input.inp_g[type="file"][accept="image/*"]')
        if container.count() != 1 or upload.count() != 1:
            raise MediaContractError()
        selected_container = container.element_handle()
        previous_sources = container.evaluate("""box => {
            const sources = [...box.querySelectorAll('img')].map(i => i.currentSrc || i.src);
            for (const node of [box, ...box.querySelectorAll('*')]) {
                const match = /^url\\(["']?(.*?)["']?\\)$/.exec(getComputedStyle(node).backgroundImage);
                if (match) sources.push(match[1]);
            }
            return sources;
        }""")
        upload.set_input_files({"name": os.path.basename(media.image_path),
                                "mimeType": "image/png", "buffer": media.image_bytes})
        page.evaluate("""async ({previousSources, container}) => {
            const previous = new Set(previousSources);
            const currentBox = () => {
                // A successful native upload removes the file input and label.
                if (!container || !container.isConnected) return null;
                const boxes = container.querySelectorAll('.inner_box');
                return boxes.length === 1 ? boxes[0] : null;
            };
            const loaded = async () => {
                // Reacquire on every poll: React may replace the selected input's container.
                const box = currentBox();
                if (!box) return false;
                if ([...box.querySelectorAll('img')].some(i =>
                    i.complete && i.naturalWidth > 0 && !previous.has(i.currentSrc || i.src))) return true;
                // The delete button's CSS sprite is not the uploaded thumbnail.
                for (const node of [box, ...box.querySelectorAll('.thumb_g')]) {
                    const background = getComputedStyle(node).backgroundImage;
                    const match = /^url\\(["']?(.*?)["']?\\)$/.exec(background);
                    if (!match || previous.has(match[1])) continue;
                    const image = new Image(); image.src = match[1];
                    const decoded = image.complete && image.naturalWidth > 0 || await new Promise(resolve => {
                        image.onload = () => resolve(image.naturalWidth > 0);
                        image.onerror = () => resolve(false);
                        setTimeout(() => resolve(false), 250);
                    });
                    if (decoded && box === currentBox() && box.contains(node)
                        && getComputedStyle(node).backgroundImage === background) return true;
                }
                return false;
            };
            const deadline = Date.now() + 5000;
            do { if (await loaded()) return; await new Promise(r => setTimeout(r, 100)); }
            while (Date.now() < deadline);
            throw new Error('representative preview unavailable');
        }""", {"previousSources": previous_sources, "container": selected_container})
    except Exception:
        raise MediaContractError() from None


def _gate_design_review(draft_path: str, explicit: str | None, name: str, required: bool = False) -> str | None:
    """발행 직전·브라우저 실행 전 디자인 리뷰 게이트(blog/tistory_design_review.py).

    보류(BLOCK/AWAIT_HUMAN)면 결과 문자열을, 진행이면 None 을 돌려준다. 진행 시 로그는 한 줄만
    출력하고 결과 문자열(log)에는 아무것도 더하지 않는다 — 리뷰 부재는 기존 발행과 같은 결과(사용자 결정)."""
    grade = grade_design_review(resolve_design_review_path(draft_path, explicit), draft_path=draft_path)
    if grade.result == "BLOCK":
        return f"DESIGN_BLOCK:{name} — {grade.detail}"
    if grade.result == "AWAIT_HUMAN":
        return f"DESIGN_AWAIT_HUMAN:{name} — {grade.detail}"
    if required and grade.result == "NOT_RUN":   # 연재 무인 발행(2026-10-14): 리뷰가 없으면 사람 판단 전 발행 안 함
        return f"DESIGN_AWAIT_HUMAN:{name} — 연재는 디자인 리뷰가 있어야 발행합니다 — {grade.detail}"
    print(f"[design-review] {grade.detail}", flush=True)
    return None


def _publish_locked(outroot, mode, day, name, data, headless, login_wait_s, post_id, kind, *, explicit,
                    probe_relogin=False, media=None, draft_path=None, design_review=None, body_images=None):
    title, body, tags = data["title"], data["body"], data["tags"]
    target_url = resolve_editor_url(post_id)
    tracked = mode == "publish" and not post_id
    marker, attempted, _ = marker_paths(outroot, kind)
    log = []
    if tracked:
        stopped = _check_delivery(outroot, day, kind, data, explicit=explicit, log=log)
        if stopped:
            return stopped
    # 이미 발행·시도된 날은 위에서 끝난다. 여기부터가 실제로 원고를 공개하려는 유일한 지점이다.
    if mode == "publish" and draft_path:
        held = _gate_design_review(draft_path, design_review, name, required=kind in SERIES_KINDS)
        if held:
            return held
    # 본문 이미지: 편집기가 원고 URL 을 받아 카카오 CDN 에 올린다 — 사이트가 같은 그림을 서빙할 때만 브라우저를 연다.
    if body_images:
        source = wait_for_source_images(body_images)
        if source != "ok":
            return f"[{name}] ERR:body_image_source_unready({source}) | {' '.join(log)}"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if CDP_URL:
            browser = connect_resident_chrome(p.chromium, log)
            if browser is None:
                return f"[{name}] ERR:cdp_connect_failed | {' '.join(log)}"
            ctx = browser.contexts[0]
        else:
            ctx = p.chromium.launch_persistent_context(
                PROFILE_DIR, channel="chrome", headless=headless,
                args=["--no-first-run", "--no-default-browser-check"])
        try:
            ctx.grant_permissions(["clipboard-read", "clipboard-write"],
                                  origin="https://floker.tistory.com")
        except Exception:
            pass
        # 모든 native 다이얼로그(모드전환·발행 confirm, beforeunload)를 자동 수락 — 핵심.
        # context 레벨 1곳만 등록(page 에도 걸면 이중 accept → "already handled" 에러).
        def _accept(d):
            try:
                # '저장된 글 이어서 작성' 복원 confirm 만 거부 — 수락하면 이전 슬롯의
                # 자동저장 본문이 복원돼 이중 본문 발행 위험 (2026-07-06 리뷰).
                msg = d.message or ""
                log.append(f"DIALOG:{d.type}:{' '.join(msg.split())[:80]}")
                if "이어서" in msg or "저장된 글" in msg:
                    d.dismiss()
                else:
                    d.accept()
            except Exception:
                pass
        ctx.on("dialog", _accept)
        # 세션 쿠키 복원: 직전 실행에서 저장한 __T_/__T_SECURE 등을 컨텍스트에 주입.
        # (ctx.close() 는 persist=0 쿠키를 SQLite 에서 제거 → goto 전에 재주입 필요.)
        _load_state(ctx, log)
        if CDP_URL:
            page = ctx.new_page()
        else:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        authenticated = False
        keep_tab = False
        try:
            page.bring_to_front()
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            page.bring_to_front()

            # 로그인 확인: #post-title-inp 가 뜰 때까지. 없으면 로그인 대기(사람 1회).
            try:
                page.wait_for_selector("#post-title-inp", timeout=30000)
            except Exception:
                # 1차: 카카오 SSO 무인 재로그인 (카카오 세션 생존 시 사람 불필요, 2026-07-06)
                if not _relogin_via_kakao_sso(page, log, target_url):
                    if headless:
                        return f"ERR:not_logged_in (headless) | {' '.join(log)} AUTH_STAGE:{_auth_stage(page.url)}"
                    log.append("LOGIN_WAIT")
                    print(f"[로그인 필요] 뜬 창에서 Tistory(카카오) 로그인하세요. "
                          f"최대 {login_wait_s}s 대기…", flush=True)
                    try:
                        _wait_for_login_return(page, target_url, login_wait_s)
                    except Exception:
                        return f"ERR:login_timeout — 티스토리 재로그인 필요 | {' '.join(log)} AUTH_STAGE:{_auth_stage(page.url)}"

            # 로그인 확인 후 → 카카오 keepalive → 세션 쿠키를 STATE_FILE 에 즉시 덤프.
            authenticated = True
            _refresh_kakao_session(ctx, log)
            _save_state(ctx, log)
            if mode == "auth":
                if probe_relogin and not _probe_kakao_relogin(ctx, target_url, log):
                    return f"ERR:relogin_probe_failed | {' '.join(log)}"
                return f"AUTH_OK | {' '.join(log)}"
            # keepalive 탭이 포커스를 가져가면 clipboard.write 가 NotAllowedError —
            # 본문 주입 전에 메인 탭 포커스 복원.
            page.bring_to_front()

            # 액션은 probe(2026-06-28)에서 검증된 DOM 클릭 JS 를 page.evaluate 로 실행한다.
            # (mce 메뉴/모드 항목은 Playwright 가 'not visible'로 판정 + 같은 id 2개 → 표준 click 실패.
            #  native confirm 은 ctx.on('dialog', accept) 가 처리하므로 evaluate 가 행 없이 진행됨.)

            # 1) 제목
            page.fill("#post-title-inp", title)
            log.append("TITLE")

            # 2) 카테고리 (기본모드 툴바)
            try:
                page.evaluate("document.getElementById('category-btn').click()")
                page.wait_for_timeout(900)
                picked = page.evaluate(CATEGORY_PICK_JS, list(category_candidates(kind)))
                shown = None
                if kind in SERIES_KINDS:   # 연재만 실제 표시값 대조 — 일간·결산 흐름은 그대로
                    page.wait_for_timeout(400)
                    shown = page.evaluate("(document.getElementById('category-btn') || {}).textContent || ''")
            except Exception as e:
                picked, shown = None, None
                log.append(f"CAT_FAIL:{type(e).__name__}")
            token, stop = category_outcome(kind, picked, shown)
            log.append(token)
            if stop:
                return f"[{name}] ERR:category_not_selected | {' '.join(log)}"

            # 3) 본문 주입 — 기본(kakao) 에디터에 실제 클립보드 붙여넣기(Meta+V).
            #    발행은 기본 kakao 모델에서 직렬화한다. HTML모드 setValue/스위치백은 전환 시 내용이
            #    폐기돼(BASIC:30 확인) 실패 → 인간 워크플로우대로 paste 파이프라인으로 모델을 채운다.
            # 시스템 클립보드 보존(텍스트만, 낮 재시도 슬롯이 사용자 클립보드를 파괴하지 않게).
            import subprocess
            try:
                saved_clip = subprocess.run(["pbpaste"], capture_output=True, timeout=5).stdout
            except Exception:
                saved_clip = None
            ifr = page.frame_locator("#editor-tistory_ifr")
            ed_body = ifr.locator("body")
            ed_body.click()
            page.wait_for_timeout(300)
            # 클린 슬레이트: 자동저장 복원 등 잔여 본문 위에 덧붙지 않게 전체선택 후 삭제.
            page.keyboard.press("Meta+A")
            page.keyboard.press("Delete")
            # 본문 이미지 결박: 비운 편집기에 이미지가 남아 있으면 이후 대조가 예전 이미지를 통과시킬 수 있다.
            if body_images and read_editor_images(page):
                return f"[{name}] ERR:editor_not_empty_before_paste | {' '.join(log)}"
            # 클립보드 주입은 paste 직전 — 사용자 복사와의 레이스 창 최소화 (2026-07-06 리뷰).
            page.evaluate(
                """async (html) => {
                    await navigator.clipboard.write([new ClipboardItem({
                        'text/html': new Blob([html], {type: 'text/html'}),
                        'text/plain': new Blob([html], {type: 'text/plain'})
                    })]);
                }""", body)
            # 본문 이미지는 원고 URL 그대로 붙인다 — data URI(약 390KB)는 attach.json 413 으로 붙여넣기 전체가
            # 버려졌다(10-11~10-13 PASTE_BASIC:30, 2026-10-13 실측). 사이트 서빙은 브라우저를 열기 전에 확인했다.
            page.keyboard.press("Meta+V")
            page.wait_for_timeout(2500)
            if saved_clip is not None:
                try:
                    subprocess.run(["pbcopy"], input=saved_clip, timeout=5)
                except Exception:
                    pass
            basic_len = page.evaluate(
                """() => { const ifr = document.getElementById('editor-tistory_ifr');
                    const d = ifr && (ifr.contentDocument || ifr.contentWindow.document);
                    return d ? d.body.innerHTML.length : -1; }""")
            log.append(f"PASTE_BASIC:{basic_len}")
            if basic_len is not None and basic_len < 1000:
                return f"[{name}] ERR:paste_basic_empty({basic_len}) | {' '.join(log)}"
            # 본문 무결성 대조: 붙은 내용이 우리가 주입한 body 인지 앵커 3점으로 확인 —
            # 클립보드 레이스로 임의 내용이 실명 블로그에 공개발행되는 사고 차단 (2026-07-06 리뷰).
            import html as _h
            import re as _r
            # 앵커는 태그 사이 '단일 텍스트런' 내부에서만 추출 — 태그→공백 평탄화로 뽑으면
            # innerText 의 인라인 무공백 결합('…</b>(' 등)과 어긋나 앵커가 구조적으로 죽는다
            # (2026-07-07 리뷰 실측: 구 방식 앵커1 영구 실패).
            runs = [" ".join(_h.unescape(s).split()) for s in _r.split(r"<[^>]+>", body)]
            runs = [s for s in runs if len(s) >= 20]
            if runs:
                anchors = [runs[0][:40], runs[len(runs) // 2][:40], runs[-1][-40:]]
            else:
                anchors = [" ".join(_h.unescape(_r.sub(r"<[^>]+>", " ", body)).split())[:40]]
            pasted_ok = pasted_anchors_present(page, anchors)
            if not pasted_ok:
                return f"[{name}] ERR:paste_content_mismatch | {' '.join(log)}"
            log.append("PASTE_VERIFIED")
            # 본문 이미지: 재호스팅은 비동기이고 실패 이미지는 조용히 사라진다 — 클릭·ATTEMPTED 마커 전에
            # 수·순서·카카오 CDN·원본 크기와 내려받은 그림 내용까지 대조하고, 어긋나면 다음 슬롯이 같은 본문으로
            # 재시도하게 멈춘다.
            if body_images:
                state = verify_body_images(page, body_images)
                if state != "ok":
                    return f"[{name}] ERR:body_image_unverified({state}) | {' '.join(log)}"
                log.append(f"BODY_IMAGES_VERIFIED:{len(body_images)}")

            # 5) 태그
            if tags:
                try:
                    page.fill("#tagText", tags)
                    log.append("TAGS")
                except Exception:
                    log.append("TAGS_FAIL")

            if mode == "inject":
                if media is not None:
                    log.append("REPRESENTATIVE_PENDING_PUBLISH_DIALOG")
                return f"[{name}] INJECT_OK | {' '.join(log)}"

            # 6) 발행/저장
            if mode == "draft":
                if media is not None:
                    log.append("REPRESENTATIVE_PENDING_PUBLISH_DIALOG")
                page.evaluate(
                    "(function(){var s=[].slice.call(document.querySelectorAll('button,a'))"
                    ".filter(function(x){return (x.textContent||'').trim()==='임시저장';})[0];"
                    "if(s)s.click();})()")
                page.wait_for_timeout(2000)
                log.append("DRAFT_SAVED")
                return f"[{name}] {' '.join(log)}"

            # publish: 완료 → 공개 → 발행 (confirm 들은 on(dialog) 자동수락)
            page.evaluate("document.getElementById('publish-layer-btn').click()")
            page.wait_for_timeout(1200)
            if media is not None:
                try:
                    attach_representative_card(page, media)
                except Exception:
                    return f"[{name}] ERR:representative_image_upload | {' '.join(log)}"
                log.append(f"REPRESENTATIVE_PREVIEW_VERIFIED:LOCAL_SHA256:{media.sha256[:16]}")
            page.evaluate(
                "var r=document.getElementById('open20'); if(r){r.click();r.checked=true;"
                "r.dispatchEvent(new Event('change',{bubbles:true}));}")
            log.append("PUBLIC")
            page.wait_for_timeout(600)
            # at-most-once: 발행 클릭 '직전' 시도 마커 — 클릭 후 확인 실패(NO_REDIRECT)여도
            # 다음 슬롯이 원격 대조 없이는 재클릭하지 않게 한다 (2026-07-06 리뷰 critical).
            if tracked:
                write_delivery(outroot, day, kind, state="ATTEMPTED", data=data)
                _write_marker(attempted, day)
            # 발행 클릭 + manage/posts 리다이렉트 대기 = 게시 성공 1차 신호
            clicked = page.evaluate(
                """() => { const b = document.getElementById('publish-btn');
                    if (b) { b.click(); return 'id'; }
                    const t = [].slice.call(document.querySelectorAll('button,a'))
                      .filter(x => ['발행', '수정', '공개 발행'].indexOf((x.textContent || '').trim()) > -1)[0];
                    if (t) { t.click(); return 'text'; }
                    return ''; }""")
            log.append(f"CLICK_PUBLISH:{clicked or 'NONE'}")
            if not clicked:
                return f"[{name}] ERR:publish_button_not_found | {' '.join(log)}"
            published = False
            try:
                page.wait_for_url("**/manage/posts/**", timeout=20000)
                log.append("PUBLISH_REDIRECT")
            except Exception:
                # 리다이렉트 20s 단일 신호로 실패 단정 금지 — 공개 블로그 원격 대조 2차 확인.
                page.wait_for_timeout(5000)
                log.append(f"NO_REDIRECT(stage={_auth_stage(page.url)})")
                # 캡차는 사람이 푼다. 상주 크롬이면 미확인 탭을 남겨 두고, 다음 슬롯은 원격 대조로만 마무리한다.
                # 캡차는 '저장중' 뒤 늦게 그려지기도 해(10-26 강북) 몇 초 더 본다.
                keep_tab = bool(CDP_URL)
                try:
                    for _ in range(5):
                        if page.evaluate(_CAPTCHA_PROBE):
                            log.append("CAPTCHA_SHOWN")
                            break
                        page.wait_for_timeout(2000)
                    shot = os.path.join(outroot, DIAG_DIR, f"{day}-{kind}-no-redirect.png")
                    os.makedirs(os.path.dirname(shot), exist_ok=True)
                    page.screenshot(path=shot)
                except Exception as e:
                    log.append(f"DIAG_FAIL:{type(e).__name__}")
            # 이동만으로 성공 마커를 쓰지 않는다. 공개 글 링크가 확인돼야 완료다.
            published = _verify_published_on_blog(title, log) is True
            if published:
                log.append("PUBLISHED(remote-verified)")
            else:
                log.append("ERR:publication_unconfirmed")
            if published and tracked:
                _record_published(outroot, day, kind, data, url=_remote_post_url(log))
            return f"[{name}] {' '.join(log)} | FINAL_STAGE={_auth_stage(page.url)}"
        finally:
            try:
                if authenticated and not save_session_state(ctx, STATE_FILE, log):
                    raise RuntimeError("session persistence failed")
            finally:
                if not CDP_URL:
                    ctx.close()
                elif not keep_tab:
                    page.close()


def _notify_failure(detail: str, outroot: str = ".", kind: str = "daily") -> None:
    """발행 실패(주로 세션 만료) 시 텔레그램 알림 + Keychain 복구 명령.
    cron 로그인셸(-lc)엔 토큰이 없어 config.load_env_file 로 .env 를 직접 주입 후 전송.
    미설정/전송실패는 비치명(무음). 재시도 스케줄 도입으로 nag-once/day (2026-07-06)."""
    try:
        from agent_realestate.config import load_env_file
        from agent_realestate.notify.telegram import send_message
        load_env_file()
        today = datetime.date.today().isoformat()
        alert_marker = marker_paths(outroot, kind)[2]
        if _read_marker(alert_marker) == today:
            print("[notify] 오늘 이미 알림 발송 — skip (nag-once)")
            return
        sent = send_message(
            f"❌ <b>티스토리 자동발행 실패</b> — {today}\n"
            f"<code>{detail[:300]}</code>\n"
            f"Keychain 재설정: <code>cd /Volumes/EXT_SSD/bot/agent_realestate &amp;&amp; "
            f"python3 -m blog.tistory_keychain setup</code>\n"
            f"인증검증: <code>python3 blog/tistory_publish_pw.py --mode auth "
            f"--probe-relogin --login-wait 600</code>")
        if sent:
            open(alert_marker, "w", encoding="utf-8").write(today)
    except Exception as e:
        print(f"[notify] 알림 전송 실패(비치명): {type(e).__name__}")


def _notify_design_review(detail: str, outroot: str = ".", kind: str = "daily") -> None:
    """디자인 리뷰 보류(차단·사람 판단)를 실패 알림과 같은 경로(.env → telegram.send_message)로 알린다.
    nag-once 는 (날짜, 사유) 단위 — 재시도 슬롯마다 같은 사유를 반복하지 않되 사유가 바뀌면 다시 알린다.
    미설정/전송실패는 비치명(무음). 사유 문자열에는 경로·finding id·개수만 있다(시크릿 없음)."""
    try:
        import hashlib
        import html
        from agent_realestate.config import load_env_file
        from agent_realestate.notify.telegram import send_message
        load_env_file()
        today = datetime.date.today().isoformat()
        suffix = "" if kind == "daily" else f"-{kind}"
        alert_marker = os.path.join(outroot, DESIGN_ALERT_MARKER + suffix)
        stamp = f"{today} {hashlib.sha256(detail.encode('utf-8')).hexdigest()[:16]}"
        if _read_marker(alert_marker) == stamp:
            print("[notify] 같은 디자인 리뷰 보류 알림을 오늘 이미 발송 — skip (nag-once)")
            return
        held = "차단" if detail.startswith("DESIGN_BLOCK:") else "사람 판단 대기"
        sent = send_message(
            f"<b>티스토리 발행 보류 — 디자인 리뷰 {held}</b> — {today} ({kind})\n"
            f"<code>{html.escape(detail[:600])}</code>\n"
            f"성공 마커를 쓰지 않았다. 리뷰를 고친 뒤(수정 후 재검증 fixed, 또는 사람이 waived+waiver_reason) "
            f"다음 재시도 슬롯이 다시 판정한다.")
        if sent:
            with open(alert_marker, "w", encoding="utf-8") as out:
                out.write(stamp)
    except Exception as e:
        print(f"[notify] 알림 전송 실패(비치명): {type(e).__name__}")


def result_exit_code(result: str, mode: str) -> int:
    # 디자인 리뷰 보류는 접두로 먼저 가른다 — 사유에 리뷰 파일 경로·finding id 가 실려 "ERR:" 문자열과 섞일 수 있다.
    if result.startswith("DESIGN_BLOCK:"):
        return DESIGN_BLOCK_EXIT
    if result.startswith("DESIGN_AWAIT_HUMAN:"):
        return DESIGN_AWAIT_EXIT
    if "ERR:" in result or "MARKER_FAIL" in result or "STATE_SAVE_FAIL" in result:
        return 1
    if result.startswith("AWAIT_REVIEW:"):
        return 3
    if result.startswith("SKIP:profile_locked"):
        return 4
    if result.startswith("SKIP:"):
        return 0
    success = {"auth": "AUTH_OK", "inject": "INJECT_OK", "draft": "DRAFT_SAVED", "publish": "PUBLISHED"}[mode]
    return 0 if re.search(r"(?:^|\s|\|)" + success + r"(?:\(|\s|$)", result) else 1


# --pending 한 번에 새로 발행하는 상한. 실패한 날은 세지 않는다.
PENDING_PUBLISH_LIMIT = 2
# 원고와 무관한 실패 — 다음 날짜도 같은 이유로 실패하거나 마커가 어긋나므로 건너뛰지 않고 멈춘다.
RUN_WIDE_FAILURES = ("ERR:not_logged_in", "ERR:login_timeout", "ERR:exception", "ERR:cdp_connect_failed",
                     "MARKER_FAIL", "STATE_SAVE_FAIL")


def pending_dates(outroot: str, today: str) -> list[str]:
    """마지막 성공 다음부터 오늘까지 준비된 원고 날짜를 오래된 순서로 돌려준다(상한은 main 이 성공 건수로 센다)."""
    marker = _read_marker(marker_paths(outroot)[0])
    try:
        lower = datetime.date.fromisoformat(marker).isoformat()
    except ValueError:
        lower = today  # 기존 기록이 없으면 과거 전체를 일괄 발행하지 않는다.
    drafts = glob.glob(os.path.join(outroot, "report/blog/tistory/*-tistory-draft.html"))
    days = sorted({os.path.basename(p)[:10] for p in drafts
                   if _DAILY_DRAFT_RE.fullmatch(os.path.basename(p))
                   and lower <= os.path.basename(p)[:10] <= today})
    return [d for d in days if d > lower or d == today]


def main():
    ap = argparse.ArgumentParser(description="티스토리 Playwright 퍼블리셔 (본문 모델 주입)")
    ap.add_argument("--mode", choices=["auth", "inject", "draft", "publish"], default="inject")
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 최신)")
    ap.add_argument("--outroot", default=".")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--login-wait", type=int, default=300, help="미로그인 시 로그인 대기 초")
    ap.add_argument("--post-id", help="기존 글 번호(예: 79) — 새 글 대신 그 글을 고친다(URL 유지)")
    ap.add_argument("--kind", default="daily", help="원고 종류: daily(기본) | periodic(주간결산/월간결산, 2026-09-07) | "
                    "upsize·deposit·watch(연재, --date 필수, 2026-10-14) | gu-<구명>(구 단위 분할, "
                    "2026-10-20) — kind 별 원고·마커")
    ap.add_argument("--pending", action="store_true",
                    help="일간 누락 원고를 오래된 순서로 최대 2건 발행 — 원고 탓 실패한 날은 알리고 건너뜀")
    ap.add_argument("--no-notify", action="store_true", help="수동 복구/검증 시 실패 알림 전송 생략")
    ap.add_argument("--probe-relogin", action="store_true", help="auth 전용: 별도 메모리 컨텍스트에서 티스토리 세션 만료 후 SSO 검증")
    ap.add_argument("--design-review", help="publish 전용: design_review.json 경로 "
                    "(기본: 원고 옆 <원고>.design_review.json, 없으면 게이트 미실행·비차단)")
    a = ap.parse_args()
    if not is_publication_kind(a.kind):
        ap.error("--kind 는 " + ", ".join(PUBLICATION_KINDS) + " 또는 gu-<구명> 중 하나")
    if a.pending and (a.mode != "publish" or a.kind != "daily" or a.date or a.post_id):
        ap.error("--pending 은 날짜/글번호 없는 daily publish 전용")
    if a.probe_relogin and a.mode != "auth":
        ap.error("--probe-relogin 은 auth 전용")
    if a.design_review and a.mode != "publish":
        ap.error("--design-review 는 publish 전용")
    dates = pending_dates(a.outroot, datetime.date.today().isoformat()) if a.pending else [a.date]
    if not dates:
        dates = [None]  # 원고 부재/stale를 성공으로 숨기지 않는다.
    published, rc = 0, 0
    for stamp in dates:
        try:
            result = publish(a.outroot, a.mode, stamp, headless=a.headless, login_wait_s=a.login_wait,
                             post_id=a.post_id, kind=a.kind, probe_relogin=a.probe_relogin,
                             design_review=a.design_review)
        except Exception as e:
            # Playwright 예외/URL에는 인증 토큰이 있을 수 있어 종류만 출력한다.
            result = f"ERR:exception:{type(e).__name__}"
        print(result, flush=True)
        rc = result_exit_code(result, a.mode)
        if rc == 1 and a.mode == "publish" and not a.no_notify:
            _notify_failure(result, a.outroot, kind=a.kind)
        if rc in (DESIGN_BLOCK_EXIT, DESIGN_AWAIT_EXIT) and not a.no_notify:
            _notify_design_review(result, a.outroot, kind=a.kind)
        if rc == 0:
            published += 1
            if a.pending and published >= PENDING_PUBLISH_LIMIT:
                break
            continue
        # 10-11~10-13: 가장 오래된 원고 1편의 실패가 뒤 날짜를 사흘 막았다 — 원고 탓 실패만 건너뛴다.
        # 뒤 날짜가 발행되면 성공 마커가 넘어가 건너뛴 날은 다시 시도하지 않는다(사용자 결정 2026-10-13).
        if not (a.pending and rc == 1 and not any(m in result for m in RUN_WIDE_FAILURES)):
            return rc
        print(f"[pending] {stamp} 건너뜀 — 다음 원고로", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
