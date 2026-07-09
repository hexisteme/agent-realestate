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
import os, sys, argparse

# 기존 파서 재사용 (헬퍼 HTML → title/tags/body)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tistory_publish import _parse_helper, _latest_draft  # noqa: E402

NEWPOST_URL = os.environ.get("TISTORY_NEWPOST_URL", "https://floker.tistory.com/manage/newpost/")
CATEGORY_MATCH = os.environ.get("TISTORY_CATEGORY", "서울(아파트)")
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


def _load_state(ctx, log: list[str]) -> None:
    """STATE_FILE 쿠키를 컨텍스트에 주입 — 세션 쿠키 복원.
    세션 쿠키(persist=0)만 주입한다: 영속 쿠키는 프로필 SQLite 가 단일 진실원 —
    사람이 프로필 Chrome 으로 직접 재로그인한 신선한 카카오 토큰을 며칠 전 스냅샷이
    되돌려 세션을 다시 죽이는 사고 방지 (2026-07-06 리뷰)."""
    if not os.path.isfile(STATE_FILE):
        log.append("STATE_EMPTY"); return
    try:
        import json as _j
        cookies = _j.loads(open(STATE_FILE, encoding="utf-8").read()).get("cookies", [])
        cookies = [c for c in cookies if c.get("expires", -1) <= 0]
        if cookies:
            ctx.add_cookies(cookies)
        log.append(f"STATE_LOADED({len(cookies)})")
    except Exception as e:
        log.append(f"STATE_FAIL:{e}")


def _save_state(ctx, log: list[str]) -> None:
    """현재 세션 쿠키(persist=0 포함)를 STATE_FILE 에 덤프."""
    try:
        import json as _j
        state = ctx.storage_state()
        open(STATE_FILE, "w", encoding="utf-8").write(_j.dumps(state))
        log.append("STATE_SAVED")
    except Exception as e:
        log.append(f"STATE_SAVE_FAIL:{e}")


def _relogin_via_kakao_sso(page, log: list[str]) -> bool:
    """티스토리 세션만료 시 무인 재로그인 시도 (2026-07-06, 이틀 연속 login_timeout 대응).

    티스토리는 세션(__T_) 이 죽으면 auth/login 으로 리다이렉트만 하고 카카오 SSO 를 자동
    개시하지 않는다 — '카카오계정으로 로그인' 클릭이 필요. 카카오 웹세션이 살아있으면
    (또는 간편로그인 저장계정이 있으면) 비밀번호 없이 SSO 왕복이 완주된다.
    비밀번호 폼이 뜨면(카카오도 만료) 사람 몫 — False 반환."""
    try:
        if "auth/login" not in page.url:
            return False
        clicked = page.evaluate(
            """() => {
                const a = [].slice.call(document.querySelectorAll('a,button'))
                  .filter(x => /카카오계정으로 로그인/.test((x.textContent||'')))[0];
                if (a) { a.click(); return true; }
                return false;
            }""")
        if not clicked:
            log.append("SSO_NO_BTN"); return False
        for _ in range(20):
            page.wait_for_timeout(1000)
            url = page.url
            # /auth/kakao/redirect(코드 교환 중)도 /auth/ 라 제외 — 완주 후 URL 만 인정.
            if "tistory.com" in url and "/auth/" not in url and "kakao.com" not in url:
                break  # SSO 왕복 완료
            if "accounts.kakao.com" in url:
                # 비밀번호 폼이 보이면 사람 필요. 저장계정(간편로그인) 타일만 무인 클릭.
                state = page.evaluate(
                    """() => {
                        const pw = [].slice.call(document.querySelectorAll('input[type=password]'))
                          .filter(x => x.offsetParent !== null).length > 0;
                        if (pw) return 'PW_FORM';
                        const tile = [].slice.call(document.querySelectorAll('button,a'))
                          .filter(x => x.offsetParent !== null)
                          .filter(x => /계속하기|간편로그인/.test((x.textContent||''))
                                       || /account|profile/i.test(x.className||''))[0];
                        if (tile) { tile.click(); return 'TILE_CLICKED'; }
                        return 'WAIT';
                    }""")
                if state == "PW_FORM":
                    log.append("SSO_PW_FORM"); return False
        page.goto(NEWPOST_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#post-title-inp", timeout=15000)
        log.append("AUTO_RELOGIN_OK")
        return True
    except Exception as e:
        log.append(f"SSO_FAIL:{type(e).__name__}")
        return False


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
        log.append("KAKAO_DEAD" if "/login" in kp.url else "KAKAO_ALIVE")
    except Exception as e:
        log.append(f"KAKAO_KEEPALIVE_FAIL:{type(e).__name__}")
    finally:
        if kp is not None:
            try:
                kp.close()
            except Exception:
                pass


def _verify_published_on_blog(title: str, log: list[str]) -> bool:
    """공개 블로그 첫 페이지에 제목(날짜 포함, 일 단위 유일)이 있는지 원격 사후검증.
    wait_for_url 20s 리다이렉트는 false negative 가능 — 이 2차 신호가 없으면 재시도
    슬롯이 같은 글을 중복 공개발행한다 (2026-07-06 리뷰 critical)."""
    try:
        import html as _h, re as _r, urllib.request
        blog_home = NEWPOST_URL.split("/manage")[0] + "/"
        raw = urllib.request.urlopen(blog_home, timeout=15).read().decode("utf-8", "replace")
        page_norm = " ".join(_h.unescape(_r.sub(r"<[^>]+>", " ", raw)).split())
        hit = " ".join(title.split()) in page_norm
        log.append("REMOTE_VERIFY_HIT" if hit else "REMOTE_VERIFY_MISS")
        return hit
    except Exception as e:
        log.append(f"REMOTE_VERIFY_FAIL:{type(e).__name__}")
        return False


def _resolve_draft(outroot: str, date: str | None) -> str | None:
    if date:
        p = os.path.join(outroot, f"report/blog/tistory/{date}-tistory-draft.html")
        return p if os.path.isfile(p) else None
    return _latest_draft(outroot)


def publish(outroot: str = ".", mode: str = "inject", date: str | None = None,
            headless: bool = False, login_wait_s: int = 300) -> str:
    from playwright.sync_api import sync_playwright

    today = datetime.date.today().isoformat()
    marker = os.path.join(outroot, PUBLISH_MARKER)
    attempted = os.path.join(outroot, ATTEMPT_MARKER)
    # 마커 게이트는 무인 모드(--date 없는 스케줄 실행)에서만 — --date 백필이 오늘 마커를
    # 오염시켜 당일 발행을 무음 소실시키는 결함 방지 (2026-07-06 리뷰). 백필은 마커 불관여.
    unattended = (mode == "publish" and not date)
    if unattended and _read_marker(marker) == today:
        return f"SKIP:already_published_today({today})"

    path = _resolve_draft(outroot, date)
    if not path:
        return f"ERR:no draft helper found (date={date})"
    name = os.path.basename(path)
    # stale draft 가드: --date 명시 없이 최신 draft 가 오늘자가 아니면(파이프라인 상류 실패)
    # 어제 글 재게시 대신 중단 (2026-07-06).
    if mode == "publish" and not date and not name.startswith(today):
        return f"ERR:draft_stale({name}) — 오늘자 draft 없음"
    data = _parse_helper(path)
    if not data["body"]:
        return f"ERR:empty body parsed from {path}"
    title, body, tags = data["title"], data["body"], data["tags"]

    log: list[str] = []
    # at-most-once: 직전 슬롯이 발행 클릭 후 확인 실패(NO_REDIRECT)로 죽었을 수 있다 —
    # 재발행 전에 공개 블로그를 원격 대조, 이미 올라갔으면 발행 없이 마커만 복구.
    if unattended and _read_marker(attempted) == today:
        if _verify_published_on_blog(title, log):
            try:
                open(marker, "w", encoding="utf-8").write(today)
            except OSError:
                log.append("MARKER_FAIL")
            return f"SKIP:verified_published_remote | {' '.join(log)}"
        log.append("ATTEMPT_RETRY")

    os.makedirs(PROFILE_DIR, exist_ok=True)
    # 프로필 싱글턴 락: launchd 재시도 슬롯과 수동 재로그인 명령이 같은 .pw-profile 을
    # 두고 Chrome SingletonLock 충돌하지 않게 스크립트 레벨에서 직렬화 (2026-07-06 리뷰).
    import fcntl
    lock_f = open(os.path.join(PROFILE_DIR, ".lock"), "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return "SKIP:profile_locked — 같은 프로필의 다른 발행 프로세스 실행 중"

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
        try:
            page.bring_to_front()
            page.goto(NEWPOST_URL, wait_until="domcontentloaded", timeout=60000)
            page.bring_to_front()

            # 로그인 확인: #post-title-inp 가 뜰 때까지. 없으면 로그인 대기(사람 1회).
            try:
                page.wait_for_selector("#post-title-inp", timeout=30000)
            except Exception:
                # 1차: 카카오 SSO 무인 재로그인 (카카오 세션 생존 시 사람 불필요, 2026-07-06)
                if not _relogin_via_kakao_sso(page, log):
                    if headless:
                        return "ERR:not_logged_in (headless) — 1회 headful 로그인 필요"
                    log.append("LOGIN_WAIT")
                    print(f"[로그인 필요] 뜬 창에서 Tistory(카카오) 로그인하세요. "
                          f"최대 {login_wait_s}s 대기…", flush=True)
                    try:
                        page.wait_for_selector("#post-title-inp", timeout=login_wait_s * 1000)
                    except Exception:
                        return "ERR:login_timeout — 티스토리(카카오) 세션 만료, 재로그인 필요"
                    # 로그인 후 newpost 로 다시
                    page.goto(NEWPOST_URL, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("#post-title-inp", timeout=30000)

            # 로그인 확인 후 → 카카오 keepalive → 세션 쿠키를 STATE_FILE 에 즉시 덤프.
            _refresh_kakao_session(ctx, log)
            _save_state(ctx, log)
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
            if unattended:
                open(attempted, "w", encoding="utf-8").write(today)
            # 발행 클릭 + manage/posts 리다이렉트 대기 = 게시 성공 1차 신호
            page.evaluate("document.getElementById('publish-btn').click()")
            published = False
            try:
                page.wait_for_url("**/manage/posts/**", timeout=20000)
                published = True
                log.append("PUBLISHED")
            except Exception:
                # 리다이렉트 20s 단일 신호로 실패 단정 금지 — 공개 블로그 원격 대조 2차 확인.
                page.wait_for_timeout(5000)
                if _verify_published_on_blog(title, log):
                    published = True
                    log.append("PUBLISHED(remote-verified)")
                else:
                    log.append(f"NO_REDIRECT(url={page.url})")
            if published and unattended:
                # 성공 마커 — 같은 날 재시도 슬롯의 중복발행 차단. 기록 실패는 무음 금지.
                try:
                    open(marker, "w", encoding="utf-8").write(today)
                except OSError:
                    log.append("MARKER_FAIL")
            return f"[{name}] {' '.join(log)} | FINAL={page.url}"
        finally:
            ctx.close()


def _notify_failure(detail: str, outroot: str = ".") -> None:
    """발행 실패(주로 세션 만료) 시 텔레그램 알림 + 재로그인 명령.
    cron 로그인셸(-lc)엔 토큰이 없어 config.load_env_file 로 .env 를 직접 주입 후 전송.
    미설정/전송실패는 비치명(무음). 재시도 스케줄 도입으로 nag-once/day (2026-07-06)."""
    try:
        from agent_realestate.config import load_env_file
        from agent_realestate.notify.telegram import send_message
        load_env_file()
        today = datetime.date.today().isoformat()
        alert_marker = os.path.join(outroot, ALERT_MARKER)
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
        print(f"[notify] 알림 전송 실패(비치명): {e}")


def main():
    ap = argparse.ArgumentParser(description="티스토리 Playwright 퍼블리셔 (본문 모델 주입)")
    ap.add_argument("--mode", choices=["inject", "draft", "publish"], default="inject")
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 최신)")
    ap.add_argument("--outroot", default=".")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--login-wait", type=int, default=300, help="미로그인 시 로그인 대기 초")
    a = ap.parse_args()
    try:
        result = publish(a.outroot, a.mode, a.date, headless=a.headless, login_wait_s=a.login_wait)
    except Exception as e:
        # 예외도 알림 경로로 접어 넣는다 — goto 타임아웃/프로필 크래시류가 무음 실패로
        # 며칠 발행이 끊기던 사고 클래스 차단 (2026-07-06 리뷰).
        import traceback
        traceback.print_exc()
        result = f"ERR:exception:{type(e).__name__}:{e}"
    print(result)
    # publish 모드에서 발행 성공 신호(PUBLISHED)가 없으면 = 실패(세션만료/빈본문/무리다이렉트) → 알림.
    # SKIP(오늘 이미 발행/프로필 락)은 정상 경로 — 알림 제외. 마커 기록 실패는 성공이어도 알림.
    if a.mode == "publish" and not result.startswith("SKIP") \
            and ("PUBLISHED" not in result or "MARKER_FAIL" in result):
        _notify_failure(result, a.outroot)


if __name__ == "__main__":
    main()
