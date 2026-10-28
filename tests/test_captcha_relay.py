"""티스토리 캡차 텔레그램 중계(2026-10-28) — 사람이 보낸 답만 캡차 칸에 넣는다.

10-27·10-28 발행 클릭마다 DKAPTCHA 가 떠 사람 입력 없이는 발행이 멈췄다. 캡차 사진을 텔레그램으로 보내고,
그 사진에 단 답장을 그대로 입력·제출한다.
"""
import io
import json

import pytest

from blog import captcha_relay as relay

CHAT = "42"
CDP = "http://127.0.0.1:9334"


class _Captcha:
    """iframe 안쪽 — 문제 그림 주소(question)·입력·클릭 기록."""

    def __init__(self, widget, question="q1"):
        self.src = f"https://dkaptcha.kakao.com/dkaptcha/quiz?widget={widget}&platform=pc"
        self.question, self.filled, self.clicked = question, [], []


class _Locator:
    def __init__(self, page, selector, inside=False):
        self.page, self.selector, self.inside = page, selector, inside

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.page.captcha else 0

    def get_attribute(self, name):
        if self.inside:
            return self.page.captcha.question
        return self.page.captcha.src

    def fill(self, text):
        self.page.captcha.filled.append((self.selector, text))

    def press(self, key):
        self.page.captcha.clicked.append(f"key:{key}")
        if key == "End" and self.page.enables_on_key:
            self.page.enabled = True

    def is_enabled(self):
        return self.page.enabled

    def click(self):
        if self.selector == relay.SUBMIT_BUTTON and not self.page.enabled:
            raise TimeoutError("element is not enabled")
        self.page.captcha.clicked.append(self.selector)
        self.page.on_click(self.selector)

    def screenshot(self):
        return b"PNG"


class _FrameLocator:
    def __init__(self, page):
        self.page = page

    def locator(self, selector):
        return _Locator(self.page, selector, inside=True)


class _Page:
    def __init__(self, widget="w1", after_submit="publish"):
        self.url = "https://floker.tistory.com/manage/newpost/"
        self.captcha = _Captcha(widget)
        self.after_submit, self.closed = after_submit, False
        self.enabled, self.enables_on_key, self.listeners, self.dialog = False, True, [], None

    def on(self, event, handler):
        self.listeners.append(handler)

    def remove_listener(self, event, handler):
        self.listeners.remove(handler)

    def locator(self, selector):
        return _Locator(self, selector)

    def frame_locator(self, selector):
        return _FrameLocator(self)

    def on_click(self, selector):
        if self.dialog is not None:
            for handler in list(self.listeners):
                handler(self.dialog)
        if selector == relay.SUBMIT_BUTTON and self.after_submit == "publish":
            self.url, self.captcha = "https://floker.tistory.com/manage/posts/", None
        elif selector == relay.SUBMIT_BUTTON and self.after_submit == "pass":
            self.captcha = None
        elif self.after_submit == "wrong":
            self.captcha.question = "q2"

    def close(self):
        self.closed = True


def _tab(page, target="T1", widget="w1"):
    return {"cdp": CDP, "target": target, "widget": widget, "host": "floker.tistory.com",
            "title": "서울 아파트 실거래가", "page": page}


class _Telegram:
    """api.telegram.org 대역 — 보낸 요청을 기록하고 getUpdates 는 미리 넣은 메시지를 한 번 돌려준다."""

    def __init__(self, updates=()):
        self.calls, self.updates, self.next_id = [], list(updates), 100

    def __call__(self, request, timeout):
        method = request.full_url.rsplit("/", 1)[1]
        self.calls.append((method, request.data))
        if method == "sendPhoto":
            self.next_id += 1
            result = {"message_id": self.next_id}
        elif method == "getUpdates":
            result, self.updates = self.updates, []
        else:
            result = {}
        return io.BytesIO(json.dumps({"ok": True, "result": result}).encode())

    def methods(self):
        return [m for m, _ in self.calls]


def _message(update_id, text, reply_to=None, chat=CHAT, date=2_000_000_000):
    message = {"chat": {"id": int(chat)}, "text": text, "date": date}
    if reply_to:
        message["reply_to_message"] = {"message_id": int(reply_to)}
    return {"update_id": update_id, "message": message}


def _state(**sent):
    return {"offset": 0, "bot": relay.bot_key("tok"),
            "sent": {mid: dict(e, sent_epoch=e.get("sent_epoch", 0)) for mid, e in sent.items()}}


def _entry(target="T1", widget="w1"):
    return {"cdp": CDP, "target": target, "widget": widget, "host": "floker.tistory.com", "title": "t"}


def test_reply_goes_to_the_photo_it_answers():
    state = _state(**{"7": _entry("T1"), "8": _entry("T2")})
    pairs, offset = relay.match_answers(state, [_message(5, " 중앙 ", reply_to="8")], CHAT)
    assert pairs == [("8", "중앙")] and offset == 6


def test_plain_text_goes_to_the_only_pending_captcha_but_not_when_two_wait():
    one = _state(**{"7": _entry()})
    assert relay.match_answers(one, [_message(1, "중앙")], CHAT)[0] == [("7", "중앙")]
    two = _state(**{"7": _entry("T1"), "8": _entry("T2")})
    assert relay.match_answers(two, [_message(1, "중앙")], CHAT)[0] == []


def test_other_chats_and_messages_older_than_the_photo_are_ignored():
    state = _state(**{"7": dict(_entry(), sent_epoch=1_000)})
    updates = [_message(1, "남의답", chat="99"), _message(2, "옛날말", date=999), _message(3, "중앙", date=1_001)]
    pairs, offset = relay.match_answers(state, updates, CHAT)
    assert pairs == [("7", "중앙")] and offset == 4


def test_announce_only_new_tab_question_pairs():
    state = _state(**{"7": _entry("T1", "w1")})
    tabs = [_tab(_Page(), "T1", "w1"), _tab(_Page(), "T1", "w2"), _tab(_Page(), "T2", "w1")]
    picked = relay.tabs_to_announce(state, tabs)
    assert [(t["target"], t["widget"]) for t in picked] == [("T1", "w2"), ("T2", "w1")]


def test_caption_names_the_post_and_carries_no_answer():
    caption = relay.compose_caption(_tab(_Page()))
    assert "floker.tistory.com" in caption and "서울 아파트 실거래가" in caption and "답장" in caption


def test_submit_types_the_human_answer_verbatim_and_sees_the_publish():
    page = _Page(after_submit="publish")
    captcha = page.captcha
    assert relay.submit_answer(page, "구립중앙동", sleep=lambda s: None) == "PUBLISHED"
    assert captcha.filled == [(relay.ANSWER_INPUT, "구립중앙동")]
    assert captcha.clicked == ["key:End", relay.SUBMIT_BUTTON]
    assert page.listeners == []


def test_wrong_answer_reports_new_question():
    page = _Page(after_submit="wrong")
    assert relay.submit_answer(page, "틀림", sleep=lambda s: None) == "NEW_QUESTION"


def test_new_question_word_presses_change_not_submit():
    page = _Page(after_submit="wrong")
    captcha = page.captcha
    assert relay.submit_answer(page, "새문제", sleep=lambda s: None) == "NEW_QUESTION"
    assert captcha.clicked == [relay.CHANGE_BUTTON] and captcha.filled == []


def test_relay_once_sends_photo_then_applies_reply_and_closes_published_tab(monkeypatch):
    monkeypatch.setattr(relay.time, "time", lambda: 1_000)
    monkeypatch.setattr(relay, "OUTCOME_WAIT_S", 0)
    page = _Page(after_submit="publish")
    telegram = _Telegram([_message(1, "중앙", reply_to="101", date=1_001)])
    state = relay.relay_once([CDP], ("tok", CHAT), _state(), connect=lambda cdp: [_tab(page)],
                             answer_wait_s=5, opener=telegram, log=lambda m: None)
    assert telegram.methods() == ["sendPhoto", "getUpdates", "sendMessage"]
    assert state["sent"] == {} and state["offset"] == 2
    assert page.closed


def test_relay_once_reannounces_after_wrong_answer(monkeypatch):
    monkeypatch.setattr(relay.time, "time", lambda: 1_000)
    page = _Page(after_submit="wrong")
    telegram = _Telegram([_message(1, "틀림", reply_to="101", date=1_001)])
    state = relay.relay_once([CDP], ("tok", CHAT), _state(), connect=lambda cdp: [_tab(page)],
                             answer_wait_s=1, opener=telegram, log=lambda m: None)
    assert telegram.methods()[:4] == ["sendPhoto", "getUpdates", "sendMessage", "sendPhoto"]
    assert [e["widget"] for e in state["sent"].values()] == [relay.question_of(page)]
    assert relay.question_of(page).startswith("w1:")
    assert not page.closed


def test_unreachable_browser_keeps_its_pending_entries_and_no_wait_when_idle():
    state = _state(**{"7": dict(_entry(), cdp="http://127.0.0.1:9333")})
    telegram = _Telegram()
    out = relay.relay_once([CDP, "http://127.0.0.1:9333"], ("tok", CHAT), state,
                           connect=lambda cdp: [] if cdp == CDP else None, answer_wait_s=1,
                           opener=telegram, log=lambda m: None)
    assert "7" in out["sent"]
    idle = relay.relay_once([CDP], ("tok", CHAT), _state(), connect=lambda cdp: [], answer_wait_s=5,
                            opener=telegram, log=lambda m: None)
    assert idle["sent"] == {} and telegram.methods() == []


def test_answer_for_a_vanished_captcha_is_reported_gone():
    page = _Page()
    page.captcha = None
    assert relay.submit_answer(page, "중앙", sleep=lambda s: None) == "GONE"


def test_unaccepted_answer_on_same_question_keeps_waiting(monkeypatch):
    monkeypatch.setattr(relay.time, "time", lambda: 1_000)
    monkeypatch.setattr(relay, "OUTCOME_WAIT_S", 0)
    page = _Page(after_submit="stuck")
    telegram = _Telegram([_message(1, "중앙", reply_to="101", date=1_001)])
    state = relay.relay_once([CDP], ("tok", CHAT), _state(), connect=lambda cdp: [_tab(page, widget=relay.question_of(page))],
                             answer_wait_s=1, opener=telegram, log=lambda m: None)
    assert list(state["sent"]) == ["101"] and not page.closed


def test_photo_upload_is_multipart_with_chat_and_png():
    telegram = _Telegram()
    assert relay.send_photo("tok", CHAT, b"\x89PNG", "cap", opener=telegram) == "101"
    body = telegram.calls[0][1]
    assert b'name="chat_id"' in body and b"\x89PNG" in body and b'filename="captcha.png"' in body


def test_credentials_come_from_env_file_only(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# c\nTELEGRAM_BOT_TOKEN=abc\nTELEGRAM_CHAT_ID=42\n")
    assert relay.read_credentials(str(env)) == ("abc", "42")
    assert relay.read_credentials(str(tmp_path / "none")) is None


@pytest.mark.parametrize("url,widget", [("https://dkaptcha.kakao.com/dkaptcha/quiz?widget=BE2z&platform=pc", "BE2z"),
                                        ("https://dkaptcha.kakao.com/dkaptcha/quiz", "")])
def test_widget_of(url, widget):
    assert relay.widget_of(url) == widget


class _Dialog:
    def __init__(self, message, kind="alert"):
        self.message, self.type, self.handled = message, kind, None

    def accept(self):
        self.handled = "accept"

    def dismiss(self):
        self.handled = "dismiss"


def test_r1_submit_button_unlocked_only_by_key_press_is_reported_not_crashed(monkeypatch):
    """R1(10-28 10:31): fill() 만으로는 제출 버튼이 disabled — click 30초 타임아웃·예외로 사람 답장이 사라졌다."""
    monkeypatch.setattr(relay, "ENABLE_WAIT_S", 0)
    monkeypatch.setattr(relay.time, "time", lambda: 1_000)
    page = _Page(after_submit="publish")
    page.enables_on_key = False
    telegram = _Telegram([_message(1, "중앙", reply_to="101", date=1_001)])
    state = relay.relay_once([CDP], ("tok", CHAT), _state(), connect=lambda cdp: [_tab(page, widget=relay.question_of(page))],
                             answer_wait_s=1, opener=telegram, log=lambda m: None)
    assert list(state["sent"]) == ["101"]
    assert relay.SUBMIT_BUTTON not in page.captcha.clicked


def test_r1_unexpected_error_keeps_the_captcha_waiting(monkeypatch):
    monkeypatch.setattr(relay.time, "time", lambda: 1_000)
    page = _Page()
    monkeypatch.setattr(relay, "submit_answer", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("driver")))
    telegram = _Telegram([_message(1, "중앙", reply_to="101", date=1_001)])
    state = relay.relay_once([CDP], ("tok", CHAT), _state(), connect=lambda cdp: [_tab(page, widget=relay.question_of(page))],
                             answer_wait_s=1, opener=telegram, log=lambda m: None)
    assert list(state["sent"]) == ["101"]


def test_r2_dialog_after_submit_is_accepted_on_that_tab_and_reported(monkeypatch):
    """R2(10-28 10:33): 캡차 통과 직후 대화상자를 리스너 없는 Playwright 가 거절했고 이어진 발행이 '저장중'에서 멈췄다."""
    monkeypatch.setattr(relay.time, "time", lambda: 1_000)
    monkeypatch.setattr(relay, "OUTCOME_WAIT_S", 0)
    page = _Page(after_submit="publish")
    page.dialog = _Dialog("보안 인증이 완료되었습니다")
    telegram = _Telegram([_message(1, "중앙", reply_to="101", date=1_001)])
    relay.relay_once([CDP], ("tok", CHAT), _state(), connect=lambda cdp: [_tab(page, widget=relay.question_of(page))],
                     answer_wait_s=1, opener=telegram, log=lambda m: None)
    assert page.dialog.handled == "accept"
    assert "%EB%B3%B4%EC%95%88" in telegram.calls[-1][1].decode()


def test_r2_restore_draft_dialog_is_refused():
    notes = []
    dialog = _Dialog("저장된 글이 있습니다. 이어서 작성하시겠습니까?", "confirm")
    relay._dialog_policy(notes)(dialog)
    assert dialog.handled == "dismiss" and notes == ["confirm:저장된 글이 있습니다. 이어서 작성하시겠습니까?"]


def test_r2_no_answers_taken_while_publisher_runs(monkeypatch):
    monkeypatch.setattr(relay.time, "time", lambda: 1_000)
    telegram = _Telegram([_message(1, "중앙", reply_to="101", date=1_001)])
    state = relay.relay_once([CDP], ("tok", CHAT), _state(), connect=lambda cdp: [_tab(_Page())],
                             answer_wait_s=5, opener=telegram, log=lambda m: None, busy=lambda: True)
    assert telegram.methods() == ["sendPhoto"] and list(state["sent"]) == ["101"]


def test_publisher_busy_reads_the_cron_lock(tmp_path):
    import fcntl
    lock = tmp_path / ".daily.lock"
    assert relay.publisher_busy(str(lock)) is False
    with open(lock, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert relay.publisher_busy(str(lock)) is True


def test_dedicated_captcha_bot_wins_so_its_chat_can_stay_unmuted(tmp_path):
    """10-28: 5분마다 오는 코인 알림 때문에 기존 봇 채팅을 무음으로 둔다 — 캡차만 울리게 전용 봇 토큰을 먼저 쓴다."""
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=main\nTELEGRAM_CHAT_ID=42\nTELEGRAM_CAPTCHA_BOT_TOKEN=captcha\n")
    assert relay.read_credentials(str(env)) == ("captcha", "42")
    env.write_text("TELEGRAM_BOT_TOKEN=main\nTELEGRAM_CHAT_ID=42\nTELEGRAM_CAPTCHA_BOT_TOKEN=captcha\nTELEGRAM_CAPTCHA_CHAT_ID=7\n")
    assert relay.read_credentials(str(env)) == ("captcha", "7")


def test_switching_bots_resets_offset_and_pending_photos(monkeypatch):
    """봇마다 update_id 가 따로라 이전 봇의 offset 을 들고 가면 새 봇의 답장을 영영 못 읽는다."""
    state = dict(_state(**{"7": _entry()}), offset=999_999, bot=relay.bot_key("main"))
    telegram = _Telegram()
    out = relay.relay_once([CDP], ("captcha", CHAT), state, connect=lambda cdp: [], answer_wait_s=0,
                           opener=telegram, log=lambda m: None)
    assert out["offset"] == 0 and out["sent"] == {} and out["bot"] == relay.bot_key("captcha")
    assert "captcha" not in json.dumps(out)


def test_r3_save_rejected_after_captcha_is_its_own_outcome(monkeypatch):
    """R3(10-28 10:41): 캡차 통과 직후 '게시글을 작성하는데 실패했습니다' 알림 — '발행 미확인'과 구분해 알린다."""
    monkeypatch.setattr(relay, "OUTCOME_WAIT_S", 0)
    page = _Page(after_submit="pass")
    page.dialog = _Dialog("게시글을 작성하는데 실패했습니다. 잠시 후 다시 시도해주세요.")
    assert relay.submit_answer(page, "중앙", sleep=lambda s: None) == "SAVE_REJECTED"
    assert "SAVE_REJECTED" in relay.OUTCOME_TEXT
