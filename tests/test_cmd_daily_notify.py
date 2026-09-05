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


def test_site_build_step_sees_refreshed_molit_path_via_env():
    """P2 단지 페이지 월별 차트(2026-09-05): '사이트 조립' step 직전에 RE_MOLIT=molit_json 을 env 로 넘겨야
    build_site 가 방금 refresh 된 파일을 읽는다(미지정 시 25gu 예제 경로 폴백 → 11gu 스코프 불일치).
    _cmd_daily_inner 는 실파일 refresh(백업·unlink)를 수행해 실행형 테스트가 불가하므로 AST 로 배선 순서를 고정한다."""
    import ast, inspect
    tree = ast.parse(inspect.getsource(cli))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_cmd_daily_inner")
    assign_at = call_at = None
    for i, node in enumerate(ast.walk(fn)):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript):
            tgt = node.targets[0]
            if ast.unparse(tgt.value) == "os.environ" and ast.unparse(tgt.slice) == "'RE_MOLIT'":
                assign_at = i
                assert ast.unparse(node.value) == "str(molit_json)"
        if (isinstance(node, ast.Call) and ast.unparse(node.func) == "step"
                and node.args and ast.unparse(node.args[0]) == "'사이트 조립'"):
            call_at = i
    assert assign_at is not None, "os.environ['RE_MOLIT'] 배선이 사라짐"
    assert call_at is not None
    assert assign_at < call_at, "RE_MOLIT 은 '사이트 조립' step 보다 먼저 설정돼야 한다"
