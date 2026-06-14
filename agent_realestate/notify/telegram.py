"""텔레그램 알림 — cron 실패/성공 알림 (Task I, 2026-06-14).

설정: 환경변수 TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID.
미설정이면 무음(silent skip) — 알림 없어도 일일 파이프라인은 정상 동작.

사용:
    from agent_realestate.notify.telegram import notify_daily_result
    notify_daily_result(ok=True, today="2026-06-14", detail="발행 9구")
    notify_daily_result(ok=False, today="2026-06-14", detail="MOLIT 재수집 실패(rc=1)")
"""
from __future__ import annotations

import os
import urllib.request
import urllib.parse
import json


_BOT_TOKEN = None  # lazy load from env


def _token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def _chat_id() -> str | None:
    return os.environ.get("TELEGRAM_CHAT_ID")


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """텔레그램 메시지 전송. 전송 성공이면 True, 미설정/실패면 False(비치명)."""
    token = _token()
    chat_id = _chat_id()
    if not token or not chat_id:
        return False  # 미설정: silent skip
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": parse_mode}).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[telegram] 알림 전송 실패(비치명): {e}")
        return False


def notify_daily_result(ok: bool, today: str, detail: str = "") -> None:
    """일일 파이프라인 완료/실패 알림.
    ok=True → 성공 알림 (상세 포함).
    ok=False → 실패 알림 (어떤 단계에서 실패했는지 detail 전달)."""
    icon = "✅" if ok else "❌"
    status = "완료" if ok else "실패"
    text = (f"{icon} <b>부동산 일일 파이프라인 {status}</b>\n"
            f"<code>날짜: {today}</code>\n"
            + (f"<code>{detail}</code>" if detail else ""))
    send_message(text)


def notify_step_failure(step_label: str, returncode: int, today: str) -> None:
    """개별 단계 실패 알림 — cmd_daily step() 훅에서 호출."""
    text = (f"❌ <b>daily 단계 실패</b> — {today}\n"
            f"단계: <code>{step_label}</code>\n"
            f"rc={returncode}")
    send_message(text)
