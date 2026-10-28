"""티스토리 캡차 텔레그램 중계(2026-10-28) — 사람이 텔레그램으로 보낸 답을 열린 캡차 칸에 넣는다.

발행 클릭 뒤 카카오 DKAPTCHA 가 뜬 탭(부동산 상주 크롬 9334·블로그 에이전트 9333)을 찾아 캡차 영역 사진을 보내고,
그 사진에 단 답장 글자를 그대로 입력·제출한다. 답은 사람이 정한다 — 이 모듈은 캡차 그림을 읽거나 답을 만들지 않는다.
launchd 가 1분마다 돌리는 유한 실행이고, 같은 봇의 텔레그램 수신(getUpdates)은 이 모듈만 쓴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from urllib.parse import urlsplit

ROOT = os.environ.get("AGENT_REALESTATE_ROOT", "/Volumes/EXT_SSD/bot/agent_realestate")
STATE_PATH = os.path.join(ROOT, "report/blog/tistory/captcha-relay/state.json")
ENV_PATH = os.path.join(ROOT, ".env")
DAILY_LOCK = os.path.join(ROOT, ".daily.lock")
DEFAULT_CDP_URLS = ("http://127.0.0.1:9334", "http://127.0.0.1:9333")
CAPTCHA_FRAME = 'iframe[src*="dkaptcha"]'
ANSWER_INPUT = "#inpDkaptcha"
SUBMIT_BUTTON = "#btn_dkaptcha_submit"
CHANGE_BUTTON = "#btn_dkaptcha_change"
QUESTION_IMAGE = "img.img_map"
NEW_QUESTION_WORDS = ("새문제", "새 문제", "/new")
ANSWER_WAIT_S = 45
OUTCOME_WAIT_S = 15
ENABLE_WAIT_S = 3
# 캡차 통과 뒤 티스토리가 저장을 거부할 때 뜨는 알림(10-28 10:41).
SAVE_REJECTED_TEXT = "게시글을 작성하는데 실패"


def read_credentials(env_path: str = ENV_PATH) -> tuple[str, str] | None:
    """텔레그램 봇 토큰·채팅 id — 부동산 cron 과 블로그 에이전트가 함께 읽는 .env 에서만 가져온다.

    캡차 전용 봇(TELEGRAM_CAPTCHA_BOT_TOKEN, 채팅은 TELEGRAM_CAPTCHA_CHAT_ID 없으면 기존 채팅)이 있으면 그것을 쓴다 —
    기존 봇 채팅은 잦은 코인 알림 때문에 무음이라, 캡차 알림만 따로 울리게 한다(10-28).
    """
    try:
        lines = open(env_path, encoding="utf-8").read().splitlines()
    except OSError:
        return None
    values = dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))

    def value(key):
        return values.get(key, "").strip().strip('"')
    token = value("TELEGRAM_CAPTCHA_BOT_TOKEN") or value("TELEGRAM_BOT_TOKEN")
    chat = value("TELEGRAM_CAPTCHA_CHAT_ID") or value("TELEGRAM_CHAT_ID")
    return (token, chat) if token and chat else None


def bot_key(token: str) -> str:
    """상태 파일에 남기는 봇 식별자 — 토큰 대신 해시 앞자리."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def load_state(path: str = STATE_PATH) -> dict:
    try:
        state = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("offset", 0)
    state.setdefault("sent", {})
    return state


def save_state(state: dict, path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as out:
        json.dump(state, out, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def widget_of(frame_url: str) -> str:
    """캡차 위젯 식별자 — 프레임 주소의 widget 값."""
    return urllib.parse.parse_qs(urlsplit(frame_url).query).get("widget", [""])[0]


def has_captcha(page) -> bool:
    return page.locator(CAPTCHA_FRAME).count() > 0


def question_of(page) -> str:
    """지금 떠 있는 문제의 식별자 — widget 값 + 문제 그림 주소의 해시. 그림은 열지 않는다(오답·새 문제 감지용)."""
    src = page.locator(CAPTCHA_FRAME).first.get_attribute("src") or ""
    image = page.frame_locator(CAPTCHA_FRAME).locator(QUESTION_IMAGE).first.get_attribute("src") or ""
    return f"{widget_of(src)}:{hashlib.sha256(image.encode()).hexdigest()[:10]}"


def tabs_to_announce(state: dict, tabs: list[dict]) -> list[dict]:
    """아직 사진을 보내지 않은 캡차(탭·문제 쌍)만 고른다."""
    announced = {(e["cdp"], e["target"], e["widget"]) for e in state["sent"].values()}
    return [t for t in tabs if (t["cdp"], t["target"], t["widget"]) not in announced]


def compose_caption(tab: dict) -> str:
    return (f"[티스토리 캡차] {tab['host']}\n{tab['title'][:120]}\n"
            f"이 사진에 답장으로 빈칸 글자를 보내 주세요. 다른 문제로 바꾸려면 '새문제'.")


def match_answers(state: dict, updates: list[dict], chat_id: str) -> tuple[list[tuple[str, str]], int]:
    """텔레그램 메시지를 (보낸 사진 message_id, 답) 쌍으로 묶는다.

    사진에 단 답장은 그 사진의 캡차로 간다. 답장이 아니면 대기 중인 캡차가 하나일 때만 그 캡차로 보낸다.
    지정한 채팅 밖의 메시지, 사진을 보내기 전에 쓴 메시지는 버린다.
    """
    offset = state["offset"]
    pairs = []
    for update in updates:
        offset = max(offset, update["update_id"] + 1)
        message = update.get("message") or {}
        if str((message.get("chat") or {}).get("id")) != str(chat_id):
            continue
        text = (message.get("text") or "").strip()
        if not text:
            continue
        replied = str((message.get("reply_to_message") or {}).get("message_id", ""))
        if replied in state["sent"]:
            mid = replied
        elif not replied and len(state["sent"]) == 1:
            mid = next(iter(state["sent"]))
        else:
            continue
        if message.get("date", 0) >= state["sent"][mid].get("sent_epoch", 0):
            pairs.append((mid, text))
    return pairs, offset


def _multipart(fields: dict, file_field: str, filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: image/png\r\n\r\n".encode() + payload + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def telegram_call(token: str, method: str, *, data: bytes | None = None, content_type: str | None = None,
                  params: dict | None = None, timeout: float = 15, opener=urllib.request.urlopen) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params is not None:
        data = urllib.parse.urlencode(params).encode()
        content_type = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, method="POST")
    if content_type:
        request.add_header("Content-Type", content_type)
    with opener(request, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8"))


def send_photo(token: str, chat_id: str, png: bytes, caption: str, opener=urllib.request.urlopen) -> str:
    body, content_type = _multipart({"chat_id": chat_id, "caption": caption}, "photo", "captcha.png", png)
    reply = telegram_call(token, "sendPhoto", data=body, content_type=content_type, opener=opener)
    return str(reply["result"]["message_id"])


def send_text(token: str, chat_id: str, text: str, reply_to: str | None = None,
              opener=urllib.request.urlopen) -> None:
    params = {"chat_id": chat_id, "text": text}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    telegram_call(token, "sendMessage", params=params, opener=opener)


def fetch_updates(token: str, offset: int, wait_s: int, opener=urllib.request.urlopen) -> list[dict]:
    reply = telegram_call(token, "getUpdates", params={"offset": offset, "timeout": wait_s,
                                                       "allowed_updates": '["message"]'},
                          timeout=wait_s + 10, opener=opener)
    return reply.get("result", [])


def _target_id(ctx, page) -> str:
    session = ctx.new_cdp_session(page)
    try:
        return session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        session.detach()


def find_captcha_tabs(cdp: str, ctx) -> list[dict]:
    """캡차가 떠 있는 탭 — iframe 주소로만 찾고 캡차 내용은 읽지 않는다."""
    tabs = []
    for page in ctx.pages:
        if not has_captcha(page):
            continue
        title = page.evaluate("() => (document.querySelector('#post-title-inp') || {}).value || document.title")
        tabs.append({"cdp": cdp, "target": _target_id(ctx, page), "widget": question_of(page),
                     "host": urlsplit(page.url).hostname or "", "title": title or "", "page": page})
    return tabs


def publisher_busy(lock_path: str = DAILY_LOCK) -> bool:
    """부동산 발행 작업(cron_daily.sh 전역 잠금)이 도는 중인가 — 그동안은 답을 넣지 않는다(대화상자 이중 처리 방지)."""
    import fcntl
    try:
        with open(lock_path, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
    except BlockingIOError:
        return True
    except OSError:
        return False
    return False


def _dialog_policy(notes: list):
    """제출한 탭의 대화상자만 발행 스크립트와 같은 규칙으로 받는다 — '저장된 글 이어서' 복원만 거부."""
    def handle(dialog):
        message = " ".join((dialog.message or "").split())[:120]
        notes.append(f"{dialog.type}:{message}")
        try:
            if "이어서" in message or "저장된 글" in message:
                dialog.dismiss()
            else:
                dialog.accept()
        except Exception:
            pass
    return handle


def submit_answer(page, answer: str, wait_s: float | None = None, sleep=time.sleep, notes: list | None = None) -> str:
    """사람이 보낸 답을 그대로 넣고 제출한다.

    결과: PUBLISHED / NEW_QUESTION / SAME_QUESTION / SUBMIT_DISABLED / SAVE_REJECTED / PASSED_UNCONFIRMED / GONE.
    제출 버튼은 키 입력 뒤에만 풀린다(10-28 실사용) — 값을 넣고 End 키를 한 번 누른다.
    """
    if not has_captcha(page):
        return "GONE"
    notes = [] if notes is None else notes
    handler = _dialog_policy(notes)
    page.on("dialog", handler)
    try:
        before = question_of(page)
        frame = page.frame_locator(CAPTCHA_FRAME)
        if answer in NEW_QUESTION_WORDS:
            frame.locator(CHANGE_BUTTON).click()
        else:
            field, button = frame.locator(ANSWER_INPUT), frame.locator(SUBMIT_BUTTON)
            field.fill(answer)
            field.press("End")
            enable_by = time.monotonic() + ENABLE_WAIT_S
            while not button.is_enabled():
                if time.monotonic() >= enable_by:
                    return "SUBMIT_DISABLED"
                sleep(0.2)
            button.click()
        deadline = time.monotonic() + (OUTCOME_WAIT_S if wait_s is None else wait_s)
        while True:
            if "/manage/posts" in page.url:
                return "PUBLISHED"
            shown = has_captcha(page)
            if shown and question_of(page) != before:
                return "NEW_QUESTION"
            if not shown and any(SAVE_REJECTED_TEXT in n for n in notes):
                return "SAVE_REJECTED"
            if time.monotonic() >= deadline:
                return "SAME_QUESTION" if shown else "PASSED_UNCONFIRMED"
            sleep(1)
    finally:
        page.remove_listener("dialog", handler)


OUTCOME_TEXT = {
    "PUBLISHED": "발행됐습니다. 다음 발행 슬롯이 원격 대조로 기록합니다.",
    "NEW_QUESTION": "새 문제가 떴습니다. 새 사진에 다시 답장해 주세요.",
    "SAME_QUESTION": "답이 받아들여지지 않았습니다. 같은 사진에 다시 답장해 주세요.",
    "SUBMIT_DISABLED": "답을 넣었지만 제출 버튼이 풀리지 않았습니다. 같은 사진에 다시 답장해 주세요.",
    "ERROR": "입력 중 오류가 났습니다. 같은 사진에 다시 답장해 주세요.",
    "SAVE_REJECTED": "캡차는 통과했지만 티스토리가 글 저장을 거부했습니다. 이 글은 발행되지 않았습니다.",
    "PASSED_UNCONFIRMED": "캡차는 사라졌지만 발행 이동은 확인하지 못했습니다. 다음 슬롯이 원격 대조합니다.",
    "GONE": "이 캡차 탭은 이미 닫혔거나 사라졌습니다.",
}


def _announce(state: dict, tab: dict, token: str, chat: str, opener, log) -> None:
    png = tab["page"].locator(CAPTCHA_FRAME).screenshot()
    mid = send_photo(token, chat, png, compose_caption(tab), opener=opener)
    entry = {k: tab[k] for k in ("cdp", "target", "widget", "host", "title")}
    entry["sent_epoch"] = int(time.time())
    state["sent"][mid] = entry
    log(f"CAPTCHA_ANNOUNCED {tab['host']} {tab['target'][:8]} msg={mid}")


def relay_once(cdp_urls, credentials, state: dict, *, connect, answer_wait_s: int = ANSWER_WAIT_S,
               opener=urllib.request.urlopen, log=print, busy=lambda: False) -> dict:
    """탭을 훑어 새 캡차 사진을 보내고, 대기 중인 캡차가 있으면 답을 기다려 넣는다.

    connect(cdp) 는 캡차 탭 목록을, 붙지 못하면 None 을 돌려준다 — 붙지 못한 브라우저의 대기 기록은 지우지 않는다.
    busy() 가 참이면(발행 작업 중) 답을 받지 않는다 — 텔레그램 메시지는 다음 실행이 읽는다.
    """
    token, chat = credentials
    if state.get("bot") != bot_key(token):
        state.update(bot=bot_key(token), offset=0, sent={})
    tabs, reached = [], set()
    for cdp in cdp_urls:
        found = connect(cdp)
        if found is not None:
            reached.add(cdp)
            tabs.extend(found)
    by_key = {(t["cdp"], t["target"], t["widget"]): t for t in tabs}
    state["sent"] = {mid: e for mid, e in state["sent"].items()
                     if e["cdp"] not in reached or (e["cdp"], e["target"], e["widget"]) in by_key}
    for tab in tabs_to_announce(state, tabs):
        _announce(state, tab, token, chat, opener, log)
    if busy():
        log("PUBLISHER_BUSY — 답 받기 건너뜀")
        return state
    deadline = time.monotonic() + answer_wait_s
    while any(e["cdp"] in reached for e in state["sent"].values()) and time.monotonic() < deadline:
        wait = max(1, min(20, int(deadline - time.monotonic())))
        pairs, state["offset"] = match_answers(state, fetch_updates(token, state["offset"], wait, opener=opener), chat)
        for mid, answer in pairs:
            entry = state["sent"].get(mid)
            if entry is None:
                continue
            if entry["cdp"] not in reached:
                send_text(token, chat, "지금 그 브라우저에 붙지 못했습니다. 잠시 뒤 같은 사진에 다시 답장해 주세요.",
                          reply_to=mid, opener=opener)
                continue
            del state["sent"][mid]
            tab = by_key.get((entry["cdp"], entry["target"], entry["widget"]))
            notes = []
            try:
                outcome = "GONE" if tab is None else submit_answer(tab["page"], answer, notes=notes)
            except Exception as e:
                outcome = "ERROR"
                notes.append(type(e).__name__)
            log(f"CAPTCHA_ANSWERED {entry['host']} {entry['target'][:8]} -> {outcome} {' | '.join(notes)}")
            dialogs = "".join(f"\n대화상자: {n}" for n in notes)
            send_text(token, chat, OUTCOME_TEXT[outcome] + dialogs, reply_to=mid, opener=opener)
            if outcome == "PUBLISHED":
                tab["page"].close()
            elif outcome in ("SAME_QUESTION", "SUBMIT_DISABLED", "ERROR"):
                state["sent"][mid] = entry
            elif outcome == "NEW_QUESTION" and has_captcha(tab["page"]):
                tab = dict(tab, widget=question_of(tab["page"]))
                by_key[(tab["cdp"], tab["target"], tab["widget"])] = tab
                _announce(state, tab, token, chat, opener, log)
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cdp", action="append", help="원격 디버깅 주소(여러 번). 기본: 9334·9333")
    ap.add_argument("--answer-wait", type=int, default=ANSWER_WAIT_S)
    a = ap.parse_args(argv)
    credentials = read_credentials()
    if credentials is None:
        print("SKIP:no_telegram_credentials")
        return 0
    from playwright.sync_api import sync_playwright
    state = load_state()
    with sync_playwright() as p:
        def connect(cdp):
            try:
                with urllib.request.urlopen(f"{cdp}/json/version", timeout=3):
                    pass
                browser = p.chromium.connect_over_cdp(cdp, timeout=20000)
            except Exception as e:
                print(f"CDP_SKIP {cdp} {type(e).__name__}")
                return None
            ctx = browser.contexts[0]
            # 리스너가 없으면 Playwright 가 모든 대화상자를 거절한다 — 발행 스크립트의 확인창을 건드리지 않게 빈 리스너를 단다.
            ctx.on("dialog", lambda dialog: None)
            return find_captcha_tabs(cdp, ctx)
        try:
            state = relay_once(a.cdp or DEFAULT_CDP_URLS, credentials, state, connect=connect,
                               answer_wait_s=a.answer_wait, busy=publisher_busy)
        finally:
            save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
