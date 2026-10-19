"""격일 연재 「한 단지의 시간」 티스토리 무인 발행 — 2026-10-19 사용자 결정으로 회차별 사람 승인을 대신한다.

초안 세션(launchd `com.hexisteme.re-series.deep-dive`)은 `report/series-deep-dive/D/` 에 검토본과 판정 파일을
남기고 원장 회차 상태를 `DRAFT_READY` 로 적는다. 이 모듈은 그 산출물을 결정론으로 다시 읽어 게이트를 통과할
때만 편집기를 열고, 붙여 넣은 본문이 검토본과 같을 때만 공개 발행한다. 사람의 승인 기록은 더 쓰지 않는다.

게이트(초안 세션의 자기 판정이 아니라 파일 계약을 본다):
  원장       회차가 정확히 1개, 상태 DRAFT_READY(PUBLISHED·PUBLISH_ATTEMPTED 는 at-most-once 로 거부),
             `draft_dir` 가 D 폴더
  README.md  첫 줄이 정확히 DRAFT_READY
  render-check.json   render_pass·numbers_pass 참, n_number_checks ≥ 1, 금지 문구·미치환 자리표시자 없음,
                      desktop·mobile 두 화면 모두 가로 넘침·JS 오류 없고 그림이 모두 뜨고 대체 텍스트가 있음
  article-claims.json 비어 있지 않은 목록, 모든 항목 ok 참, 개수 = n_number_checks
  preview.html        h1 1개, 그림 1개 이상, 그림은 모두 같은 폴더의 png 이고 대체 텍스트가 있음

  --dry      편집기 입력·대조·화면 저장까지만(발행·임시저장 안 함)
  --publish  대조 통과 시 공개 발행(원장 at-most-once)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blog import tistory_publish_pw as pw  # noqa: E402

DRAFT_ROOT = "report/series-deep-dive"
LEDGER = "report/realestate-series-ledger.json"
PUBLICATION_MODE = "auto_after_draft_gate"
READY = "DRAFT_READY"
CHART_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\.png")
TAGS_TAIL = ("서울아파트", "실거래가", "부동산분석")
SERIES_TAG = "한단지의시간"
CATEGORY = (pw.SERIES_CATEGORY_MATCH, "부동산")
KST = ZoneInfo("Asia/Seoul")

# 티스토리는 <style> 을 받지 않는다 — 1·2편(report/series-deep-dive/*/publish) 에서 확인한 인라인 스타일.
STYLES = {"p": "margin:20px 0;", "h2": "font-size:24px;line-height:1.5;margin:42px 0 18px;color:#176556;",
          "table": "width:100%;border-collapse:collapse;font-size:14px;line-height:1.6;",
          "th": "padding:9px 6px;background:#edf1e7;", "td": "padding:9px 6px;", "a": "color:#176556;",
          "strong": "color:#125a48;", "ul": "padding-left:22px;", "li": "margin:10px 0;",
          "blockquote": "margin:26px 0;background:#edf1e7;padding:4px 20px;font-size:15px;border-left:3px solid #819b70;"}
WRAP = '<div style="font-size:16px;line-height:1.9;color:#1b3831;">{}</div>'

EDITOR_STATE_JS = """() => { const d = document.getElementById('editor-tistory_ifr').contentDocument;
  return {html: d.body.innerHTML, text: d.body.innerText,
          links: [...d.querySelectorAll('a')].map(a => a.getAttribute('href')),
          rows: [...d.querySelectorAll('table')].map(t => t.querySelectorAll('tr').length),
          h2: d.querySelectorAll('h2').length,
          images: [...d.querySelectorAll('img')].map(i => ({src: i.getAttribute('src'), file: i.getAttribute('data-filename'),
                   w: i.naturalWidth, h: i.naturalHeight, ok: i.complete && i.naturalWidth > 0,
                   fig: (i.closest('figure') || i).outerHTML}))}; }"""


# ── 경로·원장 ─────────────────────────────────────────────────────────────

def draft_dir(outroot: str, day: str) -> Path:
    return Path(outroot) / DRAFT_ROOT / day


def load_ledger(outroot: str) -> dict:
    return json.loads((Path(outroot) / LEDGER).read_text(encoding="utf-8"))


def episodes_for(ledger: dict, day: str) -> list[dict]:
    return [e for e in ledger.get("episodes", []) if isinstance(e, dict) and e.get("asof") == day]


def episode(ledger: dict, day: str) -> dict | None:
    found = episodes_for(ledger, day)
    return found[0] if len(found) == 1 else None


def write_ledger(outroot: str, day: str, **fields) -> dict:
    """회차 1개만 고쳐 쓴다 — 다른 회차 기록은 건드리지 않는다."""
    path = Path(outroot) / LEDGER
    led = json.loads(path.read_text(encoding="utf-8"))
    ep = episode(led, day)
    if ep is None:
        raise ValueError(f"원장에 {day} 회차가 1개가 아닙니다")
    ep.update(fields)
    ep["updated_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return ep


def tags(ep: dict) -> str:
    return ",".join((SERIES_TAG, "".join((ep.get("name") or "").split()), *TAGS_TAIL))


# ── 검토본 ────────────────────────────────────────────────────────────────

def _main_of(outroot: str, day: str):
    html = (draft_dir(outroot, day) / "preview.html").read_text(encoding="utf-8")
    return BeautifulSoup(html, "html.parser").main


def charts(outroot: str, day: str) -> list[str]:
    """검토본 그림 파일 이름 — 본문에 나오는 순서. 편집기 업로드·대조가 같은 순서를 쓴다."""
    return [img.get("src") or "" for img in _main_of(outroot, day).find_all("img")]


def _preview_reasons(outroot: str, day: str, base: Path) -> list[str]:
    try:
        main = _main_of(outroot, day)
    except (OSError, ValueError):
        return ["preview.html 을 읽을 수 없습니다."]
    if main is None:
        return ["preview.html 에 <main> 이 없습니다."]
    if len(main.find_all("h1")) != 1:
        return [f"preview.html 의 h1 이 {len(main.find_all('h1'))}개입니다(1개여야 합니다)."]
    imgs = main.find_all("img")
    if not imgs:
        return ["preview.html 에 그림이 없습니다."]
    out = []
    for img in imgs:
        src, alt = img.get("src") or "", (img.get("alt") or "").strip()
        if not CHART_NAME.fullmatch(src):
            out.append(f"그림 주소 {src!r} 가 같은 폴더의 png 이름이 아닙니다.")
        elif not (base / src).is_file():
            out.append(f"그림 파일 {src} 가 검토본 폴더에 없습니다.")
        if not alt:
            out.append(f"그림 {src} 에 대체 텍스트가 없습니다.")
    return out


# ── 게이트 ────────────────────────────────────────────────────────────────

def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _render_reasons(rc, n_charts: int) -> list[str]:
    if not isinstance(rc, dict):
        return ["render-check.json 을 읽을 수 없습니다."]
    out = []
    for key in ("render_pass", "numbers_pass"):
        if rc.get(key) is not True:
            out.append(f"render-check.json {key}={rc.get(key)!r} — 참이 아닙니다.")
    n = rc.get("n_number_checks")
    if not isinstance(n, int) or n < 1:
        out.append(f"render-check.json n_number_checks={n!r} — 수치 대조가 없습니다.")
    for key in ("banned_phrases", "unformatted_placeholders"):
        if rc.get(key) != []:
            out.append(f"render-check.json {key}={rc.get(key)!r} — 비어 있어야 합니다.")
    views = rc.get("render") if isinstance(rc.get("render"), list) else []
    seen = {v.get("viewport") for v in views if isinstance(v, dict)}
    if not {"desktop", "mobile"} <= seen:
        return out + [f"render-check.json 에 desktop·mobile 화면 결과가 모두 없습니다(있는 것: {sorted(seen)})."]
    for v in views:
        name = v.get("viewport")
        if v.get("overflow"):
            out.append(f"{name} 화면에 가로 넘침이 있습니다.")
        if v.get("page_errors"):
            out.append(f"{name} 화면에 JS 오류가 있습니다: {v['page_errors']}.")
        imgs = v.get("images") if isinstance(v.get("images"), list) else []
        if len(imgs) != n_charts:
            out.append(f"{name} 화면 그림이 {len(imgs)}개로 검토본 {n_charts}개와 다릅니다.")
        for im in imgs:
            if not (isinstance(im, dict) and im.get("loaded") is True and im.get("alt") is True):
                out.append(f"{name} 화면 그림 {im.get('src') if isinstance(im, dict) else im!r} 가 뜨지 않거나 대체 텍스트가 없습니다.")
    return out


def _claim_reasons(claims, n_expected) -> list[str]:
    if not isinstance(claims, list) or not claims:
        return ["article-claims.json 이 비어 있거나 목록이 아닙니다."]
    bad = [c for c in claims if not (isinstance(c, dict) and c.get("ok") is True)]
    out = []
    if bad:
        out.append(f"article-claims.json 수치 대조 {len(bad)}건이 ok 가 아닙니다(예: {bad[0]!r}).")
    if isinstance(n_expected, int) and len(claims) != n_expected:
        out.append(f"article-claims.json {len(claims)}건과 render-check n_number_checks {n_expected} 가 다릅니다.")
    return out


def gate(outroot: str, day: str) -> list[str]:
    """발행을 막는 사유 목록 — 빈 목록이면 발행 가능."""
    base = draft_dir(outroot, day)
    try:
        led = load_ledger(outroot)
    except (OSError, ValueError):
        return [f"원장 {LEDGER} 을 읽을 수 없습니다."]
    found = episodes_for(led, day)
    if len(found) != 1:
        return [f"원장에 {day} 회차 기록이 {len(found)}개입니다(1개여야 합니다)."]
    ep = found[0]
    reasons = []
    if ep.get("state") != READY:
        reasons.append(f"회차 상태가 {ep.get('state')!r} 입니다 — {READY} 가 아니면 발행하지 않습니다(at-most-once).")
    if ep.get("draft_dir") and Path(ep["draft_dir"]).resolve() != base.resolve():
        reasons.append(f"원장의 draft_dir {ep['draft_dir']} 가 {base} 와 다릅니다.")
    head = ""
    try:
        head = (base / "README.md").read_text(encoding="utf-8").split("\n", 1)[0].strip()
    except OSError:
        reasons.append("검토본 README.md 가 없습니다(초안이 끝나지 않았습니다).")
    else:
        if head != READY:
            reasons.append(f"README.md 첫 줄이 {head!r} 입니다 — {READY} 가 아닙니다.")
    pr = _preview_reasons(outroot, day, base)
    reasons += pr
    rc = _read_json(base / "render-check.json")
    reasons += _render_reasons(rc, 0 if pr else len(charts(outroot, day)))
    reasons += _claim_reasons(_read_json(base / "article-claims.json"),
                              rc.get("n_number_checks") if isinstance(rc, dict) else None)
    return reasons


# ── 본문 ──────────────────────────────────────────────────────────────────

def build_body(outroot: str, day: str):
    """검토본 main(제목 h1 제외)을 티스토리용 본문으로. 그림 자리는 업로드 뒤 채우는 자리표시자."""
    main = _main_of(outroot, day)
    title = main.h1.get_text(strip=True)
    main.h1.decompose()
    reference = main.__copy__()
    names, alts = charts(outroot, day), []
    for i, img in enumerate(main.find_all("img")):
        if (img.get("src") or "") != names[i]:
            raise ValueError(f"그림 순서가 어긋났습니다: {img.get('src')} != {names[i]}")
        alts.append(img.get("alt") or "")
        holder = img.parent if img.parent.name == "a" else img
        holder.replace_with(BeautifulSoup(f"<div>CHART_PLACEHOLDER_{i}</div>", "html.parser"))
    for el in main.find_all(list(STYLES)):
        el["style"] = STYLES[el.name]
    for el in main.select("div.tw"):
        del el["class"]
        el["style"] = "overflow-x:auto;"
    body = "".join(str(c) for c in main.contents).strip()
    # 1편에서 확인한 붙여넣기 호환 형식: th → td+strong, thead/tbody 제거
    body = re.sub(r"<th\b([^>]*)>", r"<td\1><strong>", body).replace("</th>", "</strong></td>")
    body = re.sub(r"</?t(head|body)>", "", body)
    if re.search(r"file:|data:image|<script|<style|\.png", body):
        raise ValueError("본문에 로컬 참조가 남았습니다")
    return title, WRAP.format(body), reference, alts


def _norm(s: str) -> str:
    return "".join(s.split())


def check_editor(st: dict, final_body: str, reference, base: Path, names: list[str]) -> tuple[list[str], int]:
    """붙여 넣은 편집기 내용이 검토본과 같은지 — 다르면 발행하지 않는다."""
    fb = BeautifulSoup(final_body, "html.parser")
    errs = []
    if _norm(st["text"]) != _norm(reference.get_text()):
        errs.append("text != preview")
    want_links = [a["href"] for a in reference.find_all("a") if a.get("href", "").startswith("http")]
    if [h for h in st["links"] if h and h.startswith("http")] != want_links:
        errs.append("links")
    if st["rows"] != [len(t.find_all("tr")) for t in reference.find_all("table")]:
        errs.append(f"table rows {st['rows']}")
    if st["h2"] != len(reference.find_all("h2")):
        errs.append(f"h2 {st['h2']}")
    imgs = st["images"]
    if [i["file"] for i in imgs] != names:
        errs.append(f"images {[i['file'] for i in imgs]}")
    else:
        for i, name in zip(imgs, names):
            w, h = Image.open(base / name).size
            if not (i["ok"] and (i["src"] or "").startswith("https://blog.kakaocdn.net/") and (i["w"], i["h"]) == (w, h)):
                errs.append(f"image {name} {(i['src'] or '')[:40]} {i['w']}x{i['h']} vs {w}x{h}")
    if len(fb.find_all("img")) != len(names):
        errs.append("final body images")
    return errs, len(want_links)


# ── 편집기 ────────────────────────────────────────────────────────────────

def _paste_html(page, html: str) -> None:
    try:
        saved = subprocess.run(["pbpaste"], capture_output=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        saved = None
    page.bring_to_front()
    page.evaluate("""async (html) => { await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([html], {type: 'text/html'}), 'text/plain': new Blob([html], {type: 'text/plain'})})]); }""", html)
    page.keyboard.press("Meta+V")
    page.wait_for_timeout(3000)
    if saved is not None:
        subprocess.run(["pbcopy"], input=saved, timeout=5)


def _upload_charts(page, base: Path, names: list[str], log: list[str]) -> dict:
    page.evaluate("document.getElementById('mceu_0').querySelector('button,div').click()")
    page.wait_for_timeout(800)
    item = page.locator(".mce-menu-item:visible", has_text="사진").first
    with page.expect_file_chooser(timeout=10000) as fc:
        item.click()
    fc.value.set_files([str(base / n) for n in names])
    ifr = page.frame_locator("#editor-tistory_ifr")
    imgs = []
    for _ in range(60):
        imgs = ifr.locator("img").evaluate_all(
            "xs => xs.map(x => ({src: x.getAttribute('src') || '', file: x.getAttribute('data-filename'),"
            " ok: x.complete && x.naturalWidth > 0, fig: (x.closest('figure') || x).outerHTML}))")
        if len(imgs) == len(names) and all(i["ok"] and i["src"].startswith("https://blog.kakaocdn.net/") for i in imgs):
            log.append("CHARTS_UPLOADED")
            return {i["file"]: i["fig"] for i in imgs}
        page.wait_for_timeout(1000)
    raise RuntimeError(f"chart upload not confirmed: {imgs}")


def _publish_locked(outroot: str, day: str, publish: bool) -> str:
    base = draft_dir(outroot, day)
    out = base / "publish"
    out.mkdir(exist_ok=True)
    ep = episode(load_ledger(outroot), day)
    names = charts(outroot, day)
    title, body, reference, alts = build_body(outroot, day)
    (out / "tistory-body.html").write_text(body, encoding="utf-8")
    log: list[str] = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(pw.PROFILE_DIR, channel="chrome", headless=False,
                                                   args=["--no-first-run", "--no-default-browser-check"])
        ctx.grant_permissions(["clipboard-read", "clipboard-write"],
                              origin=pw.NEWPOST_URL.split("/manage")[0])

        def on_dialog(dlg):
            try:
                if "이어서" in (dlg.message or "") or "저장된 글" in (dlg.message or ""):
                    dlg.dismiss()   # 이전 자동저장 본문 복원 거부
                else:
                    dlg.accept()
            except Exception:
                pass
        ctx.on("dialog", on_dialog)
        pw._load_state(ctx, log)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        authed = False
        try:
            page.goto(pw.NEWPOST_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_selector("#post-title-inp", timeout=30000)
            except Exception:
                if not pw._relogin_via_kakao_sso(page, log, pw.NEWPOST_URL):
                    return f"ERR:not_logged_in {log}"
            authed = True
            page.wait_for_timeout(2000)
            page.bring_to_front()
            figs = _upload_charts(page, base, names, log)
            final = body
            for i, name in enumerate(names):
                fig = re.sub(r' data-mce-selected="[^"]*"', "", figs[name])
                fig = re.sub(r"<img ", f'<img alt="{alts[i]}" ', re.sub(r' alt="[^"]*"', "", fig), count=1)
                final = final.replace(f"<div>CHART_PLACEHOLDER_{i}</div>", fig)
            if "CHART_PLACEHOLDER" in final:
                return f"ERR:chart_placeholder_left {log}"
            (out / "tistory-final-body.html").write_text(final, encoding="utf-8")
            ed = page.frame_locator("#editor-tistory_ifr").locator("body")
            ed.click()
            page.keyboard.press("Meta+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(500)
            if page.evaluate(EDITOR_STATE_JS)["images"]:
                return f"ERR:editor_not_empty_before_paste {log}"
            _paste_html(page, final)
            st = page.evaluate(EDITOR_STATE_JS)
            (out / "tistory-editor-body.html").write_text(st["html"], encoding="utf-8")
            errs, n_links = check_editor(st, final, reference, base, names)
            if errs:
                return f"ERR:editor_mismatch {errs} {log}"
            log.append(f"EDITOR_VERIFIED(text,links={n_links},tables={len(st['rows'])},h2={st['h2']},images={len(names)})")
            page.fill("#post-title-inp", title)
            page.evaluate("document.getElementById('category-btn').click()")
            page.wait_for_timeout(900)
            picked = page.evaluate(pw.CATEGORY_PICK_JS, list(CATEGORY))
            page.wait_for_timeout(500)
            shown = page.evaluate("(document.getElementById('category-btn') || {}).textContent || ''")
            if picked != CATEGORY[0] or CATEGORY[0] not in shown:
                return f"ERR:category picked={picked} shown={shown} {log}"
            page.fill("#tagText", tags(ep))
            page.press("#tagText", "Enter")
            page.wait_for_timeout(500)
            log.append(f"TITLE CATEGORY:{shown.strip()} TAGS")
            page.screenshot(path=str(out / "tistory-ready.png"))
            if not publish:
                return f"DRY_OK {log}"
            page.evaluate("document.getElementById('publish-layer-btn').click()")
            page.wait_for_timeout(1500)
            page.evaluate("var r=document.getElementById('open20'); if(r){r.click();r.checked=true;"
                          "r.dispatchEvent(new Event('change',{bubbles:true}));}")
            page.wait_for_timeout(600)
            if not page.evaluate("(document.getElementById('open20') || {}).checked === true"):
                return f"ERR:public_option_not_set {log}"
            page.screenshot(path=str(out / "tistory-publish-layer.png"))
            write_ledger(outroot, day, state="PUBLISH_ATTEMPTED",
                         publish_attempt_at=dt.datetime.now(KST).isoformat(timespec="seconds"),
                         publication_mode=PUBLICATION_MODE,
                         publication_note="게이트 통과 뒤 편집기 대조(본문·출처·표·그림)까지 확인하고 공개 발행 1회 클릭. "
                                          "결과 확인 전 재발행 금지.")
            page.evaluate("document.getElementById('publish-btn').click()")
            log.append("CLICK_PUBLISH")
            try:
                page.wait_for_url("**/manage/posts/**", timeout=30000)
                log.append("PUBLISH_REDIRECT")
            except Exception:
                log.append(f"NO_REDIRECT({page.url})")
            page.wait_for_timeout(3000)
            pw._verify_published_on_blog(title, log)
            url = pw._remote_post_url(log)
            if not url:
                return f"ERR:publication_unconfirmed (재클릭 금지, 관리 목록 수동 확인) {log}"
            page.goto(url, wait_until="networkidle", timeout=60000)
            art = page.locator("#article-view .contents_style")
            # 공개 페이지 그림은 지연 로딩이라 바로 재면 아직 안 뜬 것으로 잡힌다 — 실제로 뜰 때까지 기다린다.
            for _ in range(20):
                if art.evaluate("(el, n) => { const a = [...el.querySelectorAll('figure img')];"
                                " return a.length === n && a.every(i => i.complete && i.naturalWidth > 0); }", len(names)):
                    log.append("PUB_IMAGES_LOADED")
                    break
                page.wait_for_timeout(1000)
            pub = art.evaluate("el => ({html: el.innerHTML, text: el.innerText, links: [...el.querySelectorAll('a')]"
                               ".map(a => a.getAttribute('href')), tables: el.querySelectorAll('table').length,"
                               " images: [...el.querySelectorAll('figure img')].map(i => ({ok: i.complete && i.naturalWidth > 0,"
                               " file: i.getAttribute('data-filename')}))})")
            (out / "published-body.html").write_text(pub["html"], encoding="utf-8")
            page.screenshot(path=str(out / "published.png"))
            check = {"log": log, "url": url, "pub_text_match": _norm(pub["text"]) == _norm(reference.get_text()),
                     "pub_tables": pub["tables"], "pub_images": pub["images"],
                     "pub_links": [h for h in pub["links"] if h and h.startswith("http")]
                     == [a["href"] for a in reference.find_all("a") if a.get("href", "").startswith("http")]}
            (out / "published-check.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
            write_ledger(outroot, day, state="PUBLISHED", published_url=url,
                         published_at=dt.datetime.now(KST).isoformat(timespec="seconds"),
                         publication_note="무인 공개 발행 확인. 공개 본문 대조는 publish/published-check.json.")
            log.append(f"PUBLISHED {url}")
            return json.dumps(check, ensure_ascii=False)
        finally:
            try:
                if authed:
                    pw.save_session_state(ctx, pw.STATE_FILE, log)
            finally:
                ctx.close()


def run(outroot: str, day: str, publish: bool) -> str:
    reasons = gate(outroot, day)
    if reasons:
        return "REFUSE: " + " / ".join(reasons)
    import fcntl
    os.makedirs(pw.PROFILE_DIR, exist_ok=True)
    with open(os.path.join(pw.PROFILE_DIR, ".lock"), "w") as lock_f:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "SKIP:profile_locked — 같은 프로필의 다른 발행 프로세스 실행 중"
        return _publish_locked(outroot, day, publish)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="격일 연재 「한 단지의 시간」 무인 발행")
    ap.add_argument("--day", default=dt.datetime.now(KST).date().isoformat())
    ap.add_argument("--outroot", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true", help="편집기 입력·대조까지만")
    g.add_argument("--publish", action="store_true", help="대조 통과 시 공개 발행")
    g.add_argument("--gate", action="store_true", help="게이트만 판정(브라우저 열지 않음)")
    a = ap.parse_args(argv)
    if a.gate:
        reasons = gate(a.outroot, a.day)
        print("GATE_OK" if not reasons else "GATE_BLOCK: " + " / ".join(reasons))
        return 0 if not reasons else 3
    out = run(a.outroot, a.day, a.publish)
    print(out)
    return 0 if not out.startswith(("REFUSE:", "ERR:")) else (3 if out.startswith("REFUSE:") else 1)


if __name__ == "__main__":
    raise SystemExit(main())
