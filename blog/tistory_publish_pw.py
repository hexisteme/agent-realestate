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
import datetime
import glob
import os, re, sys, argparse
from urllib.parse import urlsplit

# 기존 파서 재사용 (헬퍼 HTML → title/tags/body)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from blog.tistory_publish import _parse_helper  # noqa: E402
from blog.periodic_approval import needs_review  # noqa: E402
from blog.tistory_delivery import payload_digest, read_delivery, write_delivery  # noqa: E402
from blog.tistory_session import load_session_state, save_session_state  # noqa: E402
from blog.tistory_sso import select_kakao_account  # noqa: E402

NEWPOST_URL = os.environ.get("TISTORY_NEWPOST_URL", "https://floker.tistory.com/manage/newpost/")


def resolve_editor_url(post_id: str | None = None) -> str:
    """새 글이면 newpost, 글번호를 주면 그 글의 편집 URL(같은 에디터, 경로에 번호만 붙는다).

    발행 뒤 데이터 정정이 필요할 때 새 글을 또 올리면 URL·색인이 갈라진다 — 같은 글을 고쳐
    원래 URL 을 유지한다(2026-09-06 소재구 정정)."""
    if not post_id:
        return NEWPOST_URL
    return NEWPOST_URL.rstrip("/") + "/" + str(post_id).strip("/") + "/"
CATEGORY_MATCH = os.environ.get("TISTORY_CATEGORY", "오늘의 변화")
# 영속 프로필: EXT_SSD (APFS 가드 — 내장 디스크 금지). Tistory 로그인 쿠키 보관.
PROFILE_DIR = os.environ.get(
    "TISTORY_PW_PROFILE", "/Volumes/EXT_SSD/bot/agent_realestate/.pw-profile")
# 세션 쿠키 영속 파일: ctx.close() 로 증발하는 __T_/__T_SECURE 등 persist=0 쿠키를 보존.
STATE_FILE = os.path.join(PROFILE_DIR, "tistory_state.json")
# 발행 성공 마커(중복발행 방지)·시도 마커(at-most-once)·알림 nag-once 마커 — outroot 기준.
PUBLISH_MARKER = ".last-tistory-published"
ATTEMPT_MARKER = ".last-tistory-attempted"
ALERT_MARKER = ".last-tistory-alerted"


def _read_marker(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read().strip()
    except OSError:
        return ""


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


def _relogin_via_kakao_sso(page, log: list[str], target_url: str = NEWPOST_URL) -> bool:
    """티스토리 세션만료 시 무인 재로그인 시도 (2026-07-06, 이틀 연속 login_timeout 대응).

    티스토리는 세션(__T_) 이 죽으면 auth/login 으로 리다이렉트만 하고 카카오 SSO 를 자동
    개시하지 않는다 — '카카오계정으로 로그인' 클릭이 필요. 카카오 웹세션이 살아있으면
    (또는 간편로그인 저장계정이 있으면) 비밀번호 없이 SSO 왕복이 완주된다.
    비밀번호 폼이 뜨면(카카오도 만료) 사람 몫 — False 반환."""
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
                log.append("SSO_NO_BTN"); return False
        elif stage not in {"KAKAO_LOGIN", "KAKAO_ACCOUNT", "KAKAO_SSO"}:
            log.append("SSO_NO_LOGIN_ROUTE"); return False
        clicked_account = False
        last_state = "WAIT"
        for _ in range(20):
            page.wait_for_timeout(1000)
            url = page.url
            # /auth/kakao/redirect(코드 교환 중)도 /auth/ 라 제외 — 완주 후 URL 만 인정.
            if _auth_stage(url) == "TISTORY_PAGE":
                break  # SSO 왕복 완료
            if urlsplit(url).hostname == "accounts.kakao.com":
                state = select_kakao_account(page, already_clicked=clicked_account)
                last_state = state
                if state == "TILE_CLICKED":
                    clicked_account = True
                    log.append("SSO_ACCOUNT_CLICKED")
                if state == "PW_FORM":
                    log.append("SSO_PW_FORM"); return False
                if state == "ACCOUNT_SELECTION":
                    log.append("SSO_ACCOUNT_SELECTION_REQUIRED"); return False
        if last_state == "LAYOUT_UNRECOGNIZED":
            log.append("SSO_LAYOUT_UNRECOGNIZED")
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
            post_id: str | None = None, kind: str = "daily", probe_relogin: bool = False) -> str:
    today = datetime.date.today().isoformat()
    if kind not in {"daily", "periodic"} or mode not in {"auth", "inject", "draft", "publish"}:
        return "ERR:invalid publication mode/kind"
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
    data = _parse_helper(path) if path else {"title": "", "body": "", "tags": ""}
    if mode != "auth" and (not data["title"] or not data["body"]):
        return "ERR:empty title/body parsed"
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
                               explicit=bool(date), probe_relogin=probe_relogin)


def _publish_locked(outroot, mode, day, name, data, headless, login_wait_s, post_id, kind, *, explicit,
                    probe_relogin=False):
    from playwright.sync_api import sync_playwright
    title, body, tags = data["title"], data["body"], data["tags"]
    target_url = resolve_editor_url(post_id)
    tracked = mode == "publish" and not post_id
    marker, attempted, _ = marker_paths(outroot, kind)
    log = []
    if tracked:
        stopped = _check_delivery(outroot, day, kind, data, explicit=explicit, log=log)
        if stopped:
            return stopped
    with sync_playwright() as p:
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
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        authenticated = False
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
                ok = page.evaluate(
                    """(M) => {
                        const it = [].slice.call(document.querySelectorAll('.mce-menu-item,.mce-text,li,a,span'))
                          .filter(x => ((x.textContent||'').replace(/\\s+/g,' ').trim()).indexOf(M) > -1
                                       && (x.textContent||'').length < 40)[0];
                        if (!it) return false;
                        ((it.closest && it.closest('.mce-menu-item')) || it).click();
                        return true;
                    }""", CATEGORY_MATCH)
                log.append("CAT" if ok else "CAT_NOTFOUND")
            except Exception as e:
                log.append(f"CAT_FAIL:{type(e).__name__}")

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
            # 클립보드 주입은 paste 직전 — 사용자 복사와의 레이스 창 최소화 (2026-07-06 리뷰).
            page.evaluate(
                """async (html) => {
                    await navigator.clipboard.write([new ClipboardItem({
                        'text/html': new Blob([html], {type: 'text/html'}),
                        'text/plain': new Blob([html], {type: 'text/plain'})
                    })]);
                }""", body)
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
            import html as _h, re as _r
            # 앵커는 태그 사이 '단일 텍스트런' 내부에서만 추출 — 태그→공백 평탄화로 뽑으면
            # innerText 의 인라인 무공백 결합('…</b>(' 등)과 어긋나 앵커가 구조적으로 죽는다
            # (2026-07-07 리뷰 실측: 구 방식 앵커1 영구 실패).
            runs = [" ".join(_h.unescape(s).split()) for s in _r.split(r"<[^>]+>", body)]
            runs = [s for s in runs if len(s) >= 20]
            if runs:
                anchors = [runs[0][:40], runs[len(runs) // 2][:40], runs[-1][-40:]]
            else:
                anchors = [" ".join(_h.unescape(_r.sub(r"<[^>]+>", " ", body)).split())[:40]]
            pasted_ok = page.evaluate(
                """(anchors) => { const ifr = document.getElementById('editor-tistory_ifr');
                    const d = ifr && (ifr.contentDocument || ifr.contentWindow.document);
                    if (!d) return false;
                    const t = (d.body.innerText || '').replace(/\\s+/g, ' ');
                    return anchors.some(a => t.indexOf(a) > -1); }""", anchors)
            if not pasted_ok:
                return f"[{name}] ERR:paste_content_mismatch | {' '.join(log)}"
            log.append("PASTE_VERIFIED")

            # 5) 태그
            if tags:
                try:
                    page.fill("#tagText", tags)
                    log.append("TAGS")
                except Exception:
                    log.append("TAGS_FAIL")

            if mode == "inject":
                return f"[{name}] INJECT_OK | {' '.join(log)}"

            # 6) 발행/저장
            if mode == "draft":
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
                ctx.close()


def _notify_failure(detail: str, outroot: str = ".", kind: str = "daily") -> None:
    """발행 실패(주로 세션 만료) 시 텔레그램 알림 + 재로그인 명령.
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
            f"재로그인: <code>cd /Volumes/EXT_SSD/bot/agent_realestate &amp;&amp; "
            f"python3 blog/tistory_publish_pw.py --mode publish --login-wait 600</code>")
        if sent:
            open(alert_marker, "w", encoding="utf-8").write(today)
    except Exception as e:
        print(f"[notify] 알림 전송 실패(비치명): {type(e).__name__}")


def result_exit_code(result: str, mode: str) -> int:
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


def pending_dates(outroot: str, today: str) -> list[str]:
    """마지막 성공 다음부터 준비된 원고를 최대 두 건씩 복구한다."""
    marker = _read_marker(marker_paths(outroot)[0])
    try:
        lower = datetime.date.fromisoformat(marker).isoformat()
    except ValueError:
        lower = today  # 기존 기록이 없으면 과거 전체를 일괄 발행하지 않는다.
    drafts = glob.glob(os.path.join(outroot, "report/blog/tistory/*-tistory-draft.html"))
    days = sorted({os.path.basename(p)[:10] for p in drafts
                   if _DAILY_DRAFT_RE.fullmatch(os.path.basename(p))
                   and lower <= os.path.basename(p)[:10] <= today})
    return [d for d in days if d > lower or d == today][:2]


def main():
    ap = argparse.ArgumentParser(description="티스토리 Playwright 퍼블리셔 (본문 모델 주입)")
    ap.add_argument("--mode", choices=["auth", "inject", "draft", "publish"], default="inject")
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 최신)")
    ap.add_argument("--outroot", default=".")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--login-wait", type=int, default=300, help="미로그인 시 로그인 대기 초")
    ap.add_argument("--post-id", help="기존 글 번호(예: 79) — 새 글 대신 그 글을 고친다(URL 유지)")
    ap.add_argument("--kind", default="daily", help="원고 종류: daily(기본) | periodic(주간결산/월간결산, 2026-09-07) — kind 별 원고·마커")
    ap.add_argument("--pending", action="store_true", help="일간 누락 원고를 오래된 순서로 최대 2건 복구")
    ap.add_argument("--no-notify", action="store_true", help="수동 복구/검증 시 실패 알림 전송 생략")
    ap.add_argument("--probe-relogin", action="store_true", help="auth 전용: 별도 메모리 컨텍스트에서 티스토리 세션 만료 후 SSO 검증")
    a = ap.parse_args()
    if a.kind not in {"daily", "periodic"}:
        ap.error("--kind 는 daily 또는 periodic")
    if a.pending and (a.mode != "publish" or a.kind != "daily" or a.date or a.post_id):
        ap.error("--pending 은 날짜/글번호 없는 daily publish 전용")
    if a.probe_relogin and a.mode != "auth":
        ap.error("--probe-relogin 은 auth 전용")
    dates = pending_dates(a.outroot, datetime.date.today().isoformat()) if a.pending else [a.date]
    if not dates:
        dates = [None]  # 원고 부재/stale를 성공으로 숨기지 않는다.
    for stamp in dates:
        try:
            result = publish(a.outroot, a.mode, stamp, headless=a.headless, login_wait_s=a.login_wait,
                             post_id=a.post_id, kind=a.kind, probe_relogin=a.probe_relogin)
        except Exception as e:
            # Playwright 예외/URL에는 인증 토큰이 있을 수 있어 종류만 출력한다.
            result = f"ERR:exception:{type(e).__name__}"
        print(result, flush=True)
        rc = result_exit_code(result, a.mode)
        if rc == 1 and a.mode == "publish" and not a.no_notify:
            _notify_failure(result, a.outroot, kind=a.kind)
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
