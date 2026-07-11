"""cmd_daily 무알림 크래시 봉합(2026-07-11 사고) 회귀 테스트.

사고: cli.py `import os` 누락 NameError 가 step() 알림망 밖에서 전파 →
cron set -e 로 티스토리 단계(자체 알림 보유) 미도달 → 완전 침묵.
래퍼가 미처리 예외를 텔레그램으로 표면화 + re-raise 하는지 고정한다
(grok-4.5 적대검증: 래퍼 무테스트 시 데드코드화 위험 지적 반영).
"""
from __future__ import annotations

import pytest

from agent_realestate import cli


def _raiser(exc):
    def _inner(args):
        raise exc
    return _inner


def test_unhandled_exception_notifies_and_reraises(monkeypatch):
    """본문 미처리 예외(사고 재현: NameError) → 알림 1회 + 원본 예외 전파."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "notify_step_failure", lambda label, rc, today: calls.append(label))
    monkeypatch.setattr(cli, "_cmd_daily_inner", _raiser(NameError("name 'os' is not defined")))
    with pytest.raises(NameError):
        cli.cmd_daily(object())
    assert len(calls) == 1 and "NameError" in calls[0]


def test_step_int_systemexit_passthrough_without_duplicate_notify(monkeypatch):
    """step() 경로(int returncode exit)는 이미 알림 완료 — 래퍼가 중복알림하지 않는다."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "notify_step_failure", lambda label, rc, today: calls.append(label))
    monkeypatch.setattr(cli, "_cmd_daily_inner", _raiser(SystemExit(3)))
    with pytest.raises(SystemExit):
        cli.cmd_daily(object())
    assert calls == []


def test_string_systemexit_notifies(monkeypatch):
    """문자열 sys.exit(예: repo 루트탐지 실패)는 미알림 경로였음 — 래퍼가 표면화한다."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "notify_step_failure", lambda label, rc, today: calls.append(label))
    monkeypatch.setattr(cli, "_cmd_daily_inner", _raiser(SystemExit("[daily] repo 루트 탐지 실패")))
    with pytest.raises(SystemExit):
        cli.cmd_daily(object())
    assert len(calls) == 1 and "루트 탐지 실패" in calls[0]


def test_notify_failure_does_not_mask_original_exception(monkeypatch):
    """알림 자체가 죽어도 원본 예외가 전파된다 (원본 유실 방지, grok-4.5 지적 반영)."""
    def _broken_notify(label, rc, today):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(cli, "notify_step_failure", _broken_notify)
    monkeypatch.setattr(cli, "_cmd_daily_inner", _raiser(NameError("boom")))
    with pytest.raises(NameError):
        cli.cmd_daily(object())
