"""주간 청년안심주택 정리 원고 1편을 상주 크롬(CDP 9334)의 티스토리 편집기로 공개 발행한다.

/180(2026-10-31) 단발 발행 경로를 원고 묶음 폴더 단위로 옮긴 것. 같은 묶음은 한 번만 발행 버튼을 누른다.
- 발행 클릭 직전에 state=PUBLISH_ATTEMPTED 를 쓴다. 이후 실행은 다시 누르지 않고 공개 목록으로 확인만 한다.
- 캡차가 뜨면 탭을 남기고 바로 끝낸다(CAPTCHA_PENDING). 잠금을 풀어야 캡차 중계(captcha-relay)가 텔레그램 답을 넣는다.
  사람이 맥 앞에서 직접 풀 때만 captcha_wait 초 동안 기다린다.
- 본문 첫 그림이 대표이미지로 자동 지정되므로 지우고 대표 카드(cover.png)를 붙인다(10-31 MediaContractError).
"""
from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path

os.environ.setdefault("TISTORY_CDP_URL", "http://127.0.0.1:9334")
from bs4 import BeautifulSoup  # noqa: E402

from blog import deep_dive_publish as dd  # noqa: E402
from blog import tistory_publish_pw as pw  # noqa: E402
from blog.tistory_media import DraftMedia  # noqa: E402
from blog.youth_rental_weekly import KST, ROOT, read_state, write_state  # noqa: E402

NAMES = ["summary.png"]
CATEGORY = ("주거지원", "부동산")


def build_body(base: Path):
    """preview.html → (제목, 편집기 본문, 대조 기준, 그림 alt)."""
    soup = BeautifulSoup((base / "preview.html").read_text(encoding="utf-8"), "html.parser")
    main = soup.main
    title = main.h1.get_text(strip=True)
    main.h1.decompose()
    reference = copy.copy(main)
    alts = []
    for i, img in enumerate(main.find_all("img")):
        if img.get("src") != NAMES[i]:
            raise ValueError(f"예상 밖 그림 {img.get('src')}")
        alts.append(img.get("alt") or "")
        img.replace_with(BeautifulSoup(f"<div>CHART_PLACEHOLDER_{i}</div>", "html.parser"))
    for el in main.find_all(list(dd.STYLES)):
        el["style"] = dd.STYLES[el.name]
    for el in main.find_all("h3"):
        el["style"] = "font-size:19px;line-height:1.5;margin:30px 0 10px;color:#13332b;"
    for el in main.find_all("td"):
        el["style"] = dd.STYLES["td"] + "border:1px solid #c9d3c2;vertical-align:top;"
    body = "".join(str(c) for c in main.contents).strip()
    body = re.sub(r"<th\b([^>]*)>", r"<td\1><strong>", body).replace("</th>", "</strong></td>")
    body = re.sub(r"<p>\s*<div>(CHART_PLACEHOLDER_\d+)</div>\s*</p>", r"<div>\1</div>", body)
    if re.search(r"file:|data:image|<script|<style|\.png", body):
        raise ValueError("로컬 참조가 남음")
    return title, dd.WRAP.format(body), reference, alts


def _public_check(page, url: str, reference, base: Path) -> dict:
    page.goto(url, wait_until="networkidle", timeout=60000)
    art = page.locator("#article-view .contents_style")
    for _ in range(20):
        if art.evaluate("el => { const a=[...el.querySelectorAll('figure img')];"
                        " return a.length===1 && a.every(i=>i.complete&&i.naturalWidth>0); }"):
            break
        page.wait_for_timeout(1000)
    pub = art.evaluate("el => ({html: el.innerHTML, text: el.innerText, tables: el.querySelectorAll('table').length,"
                       " images: el.querySelectorAll('figure img').length,"
                       " links: [...el.querySelectorAll('a')].map(a => a.getAttribute('href'))})")
    (base / "published-body.html").write_text(pub["html"], encoding="utf-8")
    want = [a["href"] for a in reference.find_all("a") if a.get("href", "").startswith("http")]
    check = {"url": url, "text_match": dd._norm(pub["text"]) == dd._norm(reference.get_text()),
             "tables": pub["tables"], "tables_expected": len(reference.find_all("table")), "images": pub["images"],
             "links_match": [h for h in pub["links"] if h and h.startswith("http")] == want,
             "manage_newpost_anchor": pub["html"].count("manage/newpost/#")}
    (base / "published-check.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
    return check


def _attach(log: list[str]):
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = pw.connect_resident_chrome(p.chromium, log)
    if browser is None:
        p.stop()
        return None, None, None
    return p, browser, browser.contexts[0]


def confirm(base: Path, page=None) -> str:
    """발행 시도 뒤 결과 확인만 한다(재클릭 금지). 공개 목록에 같은 제목이 있으면 PUBLISHED.

    page 를 주면 그 탭으로 공개 본문을 대조하고, 없으면 상주 크롬에 새로 붙는다(Playwright 는 한 스레드에 하나)."""
    st = read_state(base)
    title, log = st.get("title", ""), []
    if pw._verify_published_on_blog(title, log) is not True:
        return f"PENDING:not_on_blog_yet {log[-2:]}"
    url = pw._remote_post_url(log)
    _, _, reference, _ = build_body(base)
    check = {}
    if page is not None:
        check = _public_check(page, url, reference, base)
    else:
        p, browser, ctx = _attach(log)
        if ctx is not None:
            tab = ctx.new_page()
            try:
                check = _public_check(tab, url, reference, base)
            finally:
                tab.close()
                p.stop()
    ok = bool(check) and check["text_match"] and check["links_match"] and check["images"] == len(NAMES) \
        and check["tables"] == check["tables_expected"] and check["manage_newpost_anchor"] == 0
    # 공개는 이미 됐으니 상태는 PUBLISHED, 대조 결과는 따로 남겨 알림이 구분한다(Codex R1 #4).
    write_state(base, state="PUBLISHED", published_url=url, public_check_ok=ok,
                published_at=dt.datetime.now(KST).isoformat(timespec="seconds"), public_check=check)
    return "PUBLISHED " + json.dumps({"url": url, **check}, ensure_ascii=False)


def publish_bundle(base: Path, do_publish: bool, captcha_wait: int = 0) -> str:
    st = read_state(base)
    if st.get("state") == "PUBLISHED":
        return f"SKIP:already_published {st.get('published_url')}"
    if st.get("state") == "PUBLISH_ATTEMPTED":
        return confirm(base)
    if st.get("state") != "BUILT":
        return f"REFUSE: 원고 상태 {st.get('state')}"
    meta = json.loads((base / "post.json").read_text(encoding="utf-8"))
    title, body, reference, alts = build_body(base)
    if title != meta["title"]:
        return "ERR:title_mismatch"
    (base / "tistory-body.html").write_text(body, encoding="utf-8")
    cover = (base / "cover.png").read_bytes()
    media = DraftMedia(image_path=str(base / "cover.png"), sha256=hashlib.sha256(cover).hexdigest(),
                       alt=meta["cover_alt"], image_bytes=cover)
    log: list[str] = []
    keep_tab = False
    p, browser, ctx = _attach(log)
    if ctx is None:
        return f"ERR:cdp_connect_failed {log}"
    try:
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://floker.tistory.com")
    except Exception:
        pass

    def on_dialog(d):
        try:
            msg = d.message or ""
            log.append(f"DIALOG:{' '.join(msg.split())[:60]}")
            d.dismiss() if ("이어서" in msg or "저장된 글" in msg) else d.accept()
        except Exception:
            pass
    ctx.on("dialog", on_dialog)
    pw._load_state(ctx, log)
    page = ctx.new_page()
    authed = False
    try:
        page.bring_to_front()
        page.goto(pw.NEWPOST_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("#post-title-inp", timeout=30000)
        except Exception:
            if not pw._relogin_via_kakao_sso(page, log, pw.NEWPOST_URL):
                return f"ERR:not_logged_in {log}"
        authed = True
        page.wait_for_timeout(2000)
        page.bring_to_front()
        figs = dd._upload_charts(page, base, NAMES, log)
        final = body
        for i, name in enumerate(NAMES):
            fig = re.sub(r' data-mce-selected="[^"]*"', "", figs[name])
            fig = re.sub(r"<img ", f'<img alt="{alts[i]}" ', re.sub(r' alt="[^"]*"', "", fig), count=1)
            final = final.replace(f"<div>CHART_PLACEHOLDER_{i}</div>", fig)
        if "CHART_PLACEHOLDER" in final:
            return f"ERR:placeholder_left {log}"
        (base / "tistory-final-body.html").write_text(final, encoding="utf-8")
        page.frame_locator("#editor-tistory_ifr").locator("body").click()
        page.keyboard.press("Meta+A")
        page.keyboard.press("Delete")
        page.wait_for_timeout(500)
        if page.evaluate(dd.EDITOR_STATE_JS)["images"]:
            return f"ERR:editor_not_empty {log}"
        dd._paste_html(page, final)
        est = page.evaluate(dd.EDITOR_STATE_JS)
        (base / "tistory-editor-body.html").write_text(est["html"], encoding="utf-8")
        errs, n_links = dd.check_editor(est, final, reference, base, NAMES)
        if errs:
            return f"ERR:editor_mismatch {errs} {log}"
        log.append(f"EDITOR_VERIFIED(links={n_links},tables={len(est['rows'])},h2={est['h2']})")
        page.fill("#post-title-inp", title)
        page.evaluate("document.getElementById('category-btn').click()")
        page.wait_for_timeout(900)
        picked = page.evaluate(pw.CATEGORY_PICK_JS, list(CATEGORY))
        page.wait_for_timeout(500)
        shown = page.evaluate("(document.getElementById('category-btn') || {}).textContent || ''")
        if not picked or picked not in shown:
            return f"ERR:category picked={picked} shown={shown} {log}"
        page.fill("#tagText", ",".join(meta["tags"]))
        page.press("#tagText", "Enter")
        page.wait_for_timeout(500)
        log.append(f"CATEGORY:{shown.strip()} TAGS:{len(meta['tags'])}")
        page.screenshot(path=str(base / "tistory-ready.png"), full_page=False)
        if not do_publish:
            return f"DRY_OK {log}"
        page.evaluate("document.getElementById('publish-layer-btn').click()")
        page.wait_for_timeout(1500)
        page.evaluate("var r=document.getElementById('open20'); if(r){r.click();r.checked=true;"
                      "r.dispatchEvent(new Event('change',{bubbles:true}));}")
        page.wait_for_timeout(600)
        if not page.evaluate("(document.getElementById('open20') || {}).checked === true"):
            return f"ERR:public_option_not_set {log}"
        if page.locator(".box_thumb .ico_delete").count():
            page.evaluate("document.querySelector('.box_thumb .ico_delete').click()")
            page.wait_for_timeout(1000)
            log.append("AUTO_THUMB_REMOVED")
        pw.attach_representative_card(page, media)
        log.append("REPRESENTATIVE_ATTACHED")
        page.screenshot(path=str(base / "tistory-publish-layer.png"))
        failed = _click_publish(page, base, title, log)
        if failed:
            keep_tab = True
            return f"{failed} {log}"
        try:
            page.wait_for_url("**/manage/posts/**", timeout=20000)
            log.append("PUBLISH_REDIRECT")
        except Exception:
            # 이동이 없으면 캡차 탐지 여부와 상관없이 탭을 남긴다 — 늦게 그려진 캡차도 중계·사람이 풀 수 있게(Codex R1 #4).
            keep_tab = True
            for _ in range(5):
                if page.evaluate(pw._CAPTCHA_PROBE):
                    log.append("CAPTCHA_SHOWN")
                    break
                page.wait_for_timeout(2000)
            try:
                page.screenshot(path=str(base / "no-redirect.png"))
            except Exception:
                pass
            write_state(base, no_redirect_at=dt.datetime.now(KST).isoformat(timespec="seconds"),
                        captcha_seen="CAPTCHA_SHOWN" in log)
            if captcha_wait <= 0:
                return f"CAPTCHA_PENDING 탭을 남김 — 다음 실행이 재클릭 없이 공개 확인 {log}"
            try:
                page.wait_for_url("**/manage/posts/**", timeout=captcha_wait * 1000)
                keep_tab = False
            except Exception:
                return f"CAPTCHA_PENDING wait_timeout {log}"
        page.wait_for_timeout(3000)
        return confirm(base, page=page)
    finally:
        try:
            if authed:
                pw.save_session_state(ctx, pw.STATE_FILE, log)
        finally:
            if not keep_tab:
                try:
                    page.close()
                except Exception:
                    pass
            p.stop()


def _click_publish(page, base: Path, title: str, log: list[str]) -> str | None:
    """시도 기록 → 발행 클릭 1회. 클릭에서 예외가 나면 눌렸는지 알 수 없으니 재클릭하지 않고 사람에게 넘긴다(Codex R2)."""
    write_state(base, state="PUBLISH_ATTEMPTED", title=title,
                publish_attempt_at=dt.datetime.now(KST).isoformat(timespec="seconds"))
    try:
        page.evaluate("document.getElementById('publish-btn').click()")
    except Exception as e:
        write_state(base, click_error=f"{type(e).__name__}: {str(e)[:120]}")
        return "ERR:click_exception 재클릭 안 함 — 관리 화면에서 글이 없으면 release 뒤 다시"
    log.append("CLICK_PUBLISH")
    return None


def release(base: Path) -> str:
    """사람 전용: 발행 시도 기록을 풀어 다시 발행할 수 있게 한다. 이미 공개된 묶음은 거부."""
    st = read_state(base)
    if st.get("state") != "PUBLISH_ATTEMPTED":
        return f"REFUSE: 상태 {st.get('state')} — 발행 시도 기록일 때만 해제"
    write_state(base, state="BUILT", released_at=dt.datetime.now(KST).isoformat(timespec="seconds"),
                released_from=st.get("publish_attempt_at"))
    return "RELEASED 관리 화면에 글이 없음을 사람이 확인한 뒤에만 쓴다"


def run_locked(base: Path, publish: bool = True, captcha_wait: int = 0) -> str:
    """상주 크롬 프로필 잠금 + 부동산 발행 전역 잠금을 잡은 채 한 번 실행. 잠겨 있으면 다음 슬롯에 맡긴다."""
    os.makedirs(pw.PROFILE_DIR, exist_ok=True)
    with open(os.path.join(pw.PROFILE_DIR, ".lock"), "w") as lf, open(os.path.join(ROOT, ".daily.lock"), "a") as dl:
        for f in (lf, dl):
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "SKIP:locked 다른 발행 작업 중 — 다음 슬롯"
        return publish_bundle(base, publish, captcha_wait)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="주간 청년안심주택 원고 묶음 발행")
    ap.add_argument("bundle")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true", help="편집기에 넣고 대조·화면 저장만(발행 안 함)")
    g.add_argument("--publish", action="store_true")
    g.add_argument("--confirm", action="store_true", help="발행 시도 뒤 공개 확인만")
    g.add_argument("--release", action="store_true", help="사람 전용: 관리 화면에 글이 없음을 확인한 뒤 시도 기록 해제")
    ap.add_argument("--captcha-wait", type=int, default=0, help="사람이 맥 앞에서 캡차를 풀 때 기다릴 초")
    a = ap.parse_args(argv)
    base = Path(a.bundle)
    if a.release:
        out = release(base)
    else:
        out = confirm(base) if a.confirm else run_locked(base, publish=a.publish, captcha_wait=a.captcha_wait)
    print(out)
    return 0 if out.startswith(("DRY_OK", "PUBLISHED", "SKIP", "CAPTCHA_PENDING", "PENDING", "RELEASED")) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
