"""텔레그램 주간 요약 단위테스트(2026-09-05 P1) — 멱등(marker)·요일게이트·무전송 회귀방지.
대상: blog.weekly_summary.build_weekly_summary / send_weekly_summary / main.
실제 텔레그램 전송은 절대 발생시키지 않는다 — agent_realestate.notify.telegram.send_message 를
항상 monkeypatch 로 가짜 함수로 치환한 뒤에만 send_weekly_summary 를 호출한다.
"""
from __future__ import annotations
import json
import sys

import pytest

import agent_realestate.notify.telegram as telegram
from blog.weekly_summary import build_weekly_summary, send_weekly_summary, main
from blog.wording_guard import FORBIDDEN_WORDS


def _row(gu: str, name: str, **kw) -> dict:
    base = dict(gu=gu, name=name, product_type="아파트", area_m2=59.0, molit_n=15,
                molit_recent_eok=10.0, molit_trend_dir=None, molit_pos_52w=None,
                jeonse_n=None, jeonse_ratio_complex_pct=None)
    base.update(kw)
    return base


def _sample_ds() -> dict:
    rows = [
        _row("강남", "강남상단단지", molit_recent_eok=20.0, molit_pos_52w=100, molit_trend_dir="▲"),
        _row("서초", "서초하단단지", molit_recent_eok=8.0, molit_pos_52w=3, molit_trend_dir="▼"),
        _row("강남", "강남전세단지", jeonse_n=8, jeonse_ratio_complex_pct=77.0),
    ]
    return {"complexes": rows, "count": len(rows), "data_asof": "2026-09-04"}


def _write_ds(tmp_path) -> str:
    p = tmp_path / "dataset.json"
    p.write_text(json.dumps(_sample_ds(), ensure_ascii=False), encoding="utf-8")
    return str(p)


class _FakeSend:
    """send_message 대역 — 실전송 없이 호출 인자만 기록. ok 로 성공/실패를 제어."""
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[dict] = []

    def __call__(self, text: str, parse_mode: str = "HTML", chat_id: str | None = None) -> bool:
        self.calls.append({"text": text, "parse_mode": parse_mode, "chat_id": chat_id})
        return self.ok


# ── build_weekly_summary ─────────────────────────────────────────────────

def test_build_weekly_summary_basic_shape_and_content():
    text = build_weekly_summary(_sample_ds(), "2026-09-07")
    assert isinstance(text, str) and len(text) <= 3500
    assert "서울 아파트 주간 요약" in text
    assert "강남상단단지" in text
    assert "서초하단단지" in text
    assert "강남전세단지" in text
    for w in FORBIDDEN_WORDS:
        assert w not in text


# ── send_weekly_summary: 요일 게이트 ─────────────────────────────────────

def test_wrong_weekday_never_sends(tmp_path, monkeypatch):
    fake = _FakeSend(ok=True)
    monkeypatch.setattr(telegram, "send_message", fake)
    ds_path = _write_ds(tmp_path)
    marker = tmp_path / "marker.txt"
    # 2026-09-05 은 토요일(weekday=5) — 기본 weekday=0(월) 과 불일치
    ok = send_weekly_summary(ds_path, "2026-09-05", str(marker), weekday=0)
    assert ok is False
    assert fake.calls == []
    assert not marker.exists()


# ── send_weekly_summary: 최초 발송 + marker 기록 ─────────────────────────

def test_first_send_on_matching_weekday_calls_telegram_and_writes_marker(tmp_path, monkeypatch):
    fake = _FakeSend(ok=True)
    monkeypatch.setattr(telegram, "send_message", fake)
    monkeypatch.setenv("TELEGRAM_WEEKLY_CHAT_ID", "test-chat-123")
    ds_path = _write_ds(tmp_path)
    marker = tmp_path / "marker.txt"
    # 2026-09-07 은 월요일(weekday=0), ISO 2026-W37
    ok = send_weekly_summary(ds_path, "2026-09-07", str(marker), weekday=0)
    assert ok is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["chat_id"] == "test-chat-123"
    assert marker.read_text(encoding="utf-8").strip() == "2026-W37"


# ── send_weekly_summary: 같은 주 재실행 = 멱등 무전송 ────────────────────

def test_same_week_second_call_is_idempotent_noop(tmp_path, monkeypatch):
    fake = _FakeSend(ok=True)
    monkeypatch.setattr(telegram, "send_message", fake)
    ds_path = _write_ds(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("2026-W37", encoding="utf-8")   # 이미 이번 주 전송 완료 상태로 시작
    ok = send_weekly_summary(ds_path, "2026-09-07", str(marker), weekday=0)
    assert ok is False
    assert fake.calls == []                            # 재전송 없음
    assert marker.read_text(encoding="utf-8").strip() == "2026-W37"   # marker 불변


def test_different_week_after_marker_sends_again(tmp_path, monkeypatch):
    fake = _FakeSend(ok=True)
    monkeypatch.setattr(telegram, "send_message", fake)
    ds_path = _write_ds(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("2026-W30", encoding="utf-8")     # 지난 주 marker
    ok = send_weekly_summary(ds_path, "2026-09-07", str(marker), weekday=0)
    assert ok is True
    assert len(fake.calls) == 1
    assert marker.read_text(encoding="utf-8").strip() == "2026-W37"


# ── send_weekly_summary: 전송 실패 시 marker 미기록(다음 슬롯 재시도 가능) ─

def test_marker_not_written_when_send_fails(tmp_path, monkeypatch):
    fake = _FakeSend(ok=False)
    monkeypatch.setattr(telegram, "send_message", fake)
    ds_path = _write_ds(tmp_path)
    marker = tmp_path / "marker.txt"
    ok = send_weekly_summary(ds_path, "2026-09-07", str(marker), weekday=0)
    assert ok is False
    assert len(fake.calls) == 1                # 시도는 했음
    assert not marker.exists()                  # 실패라 marker 안 남음


# ── CLI: --dry-run 여부와 무관하게 항상 무전송(출력만) ───────────────────

def test_cli_main_never_sends_regardless_of_dry_run(tmp_path, monkeypatch, capsys):
    fake = _FakeSend(ok=True)
    monkeypatch.setattr(telegram, "send_message", fake)
    ds_path = _write_ds(tmp_path)
    monkeypatch.setattr(sys, "argv", ["weekly_summary.py", "--dataset", ds_path, "--today", "2026-09-07"])
    main()
    assert fake.calls == []                     # CLI 는 send_weekly_summary 를 호출하지 않음
    out = capsys.readouterr().out
    assert "서울 아파트 주간 요약" in out


def test_cli_main_with_dry_run_flag_also_never_sends(tmp_path, monkeypatch, capsys):
    fake = _FakeSend(ok=True)
    monkeypatch.setattr(telegram, "send_message", fake)
    ds_path = _write_ds(tmp_path)
    monkeypatch.setattr(sys, "argv",
                         ["weekly_summary.py", "--dataset", ds_path, "--today", "2026-09-07", "--dry-run"])
    main()
    assert fake.calls == []
    out = capsys.readouterr().out
    assert "서울 아파트 주간 요약" in out
