"""scope_inputs.resolve_scope_inputs/current_scope 및 blog.run_daily.build_arg_parser 스코프 배선
단위 테스트(2026-09-05 run_daily 단독실행 스코프 사고 회귀 방지).

배경: cmd_daily(cli.py)는 RE_SCAN_SCOPE=25gu 일 때 --molit/--jeonse/--public-frame/--survivors 경로를
blog.run_daily 서브프로세스에 넘겼지만, run_daily.py 를 인자 없이 단독 실행하면 legacy 11gu 기본값으로
조립돼 11구·117단지 산출물이 만들어졌다(643단지 사이트 위에 얹혀 축소 발행될 뻔함). resolve_scope_inputs
하나로 cli.py 와 run_daily.py build_arg_parser 양쪽이 항상 같은 값을 보는지 여기서 고정한다."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from agent_realestate.scope_inputs import current_scope, resolve_scope_inputs

ROOT = Path("/fake/root")


# ── resolve_scope_inputs / current_scope (순수 함수) ────────────────────

def test_resolve_25gu_returns_exact_four_paths(monkeypatch):
    monkeypatch.delenv("RE_PUBLIC_GU_ALLOW", raising=False)
    inputs = resolve_scope_inputs("25gu", ROOT)
    assert inputs["molit"] == ROOT / "examples/molit_recent_25gu_20260710.json"
    assert inputs["jeonse"] == ROOT / "examples/molit_jeonse_recent_25gu_20260710.json"
    assert inputs["public_frame"] == "examples/frame_25gu_20260710.json"
    assert inputs["survivors"] == "examples/screen_25gu_survivors_20260710.json"
    assert inputs["public_gu_allow"] == ""


def test_resolve_25gu_public_gu_allow_reads_env(monkeypatch):
    monkeypatch.setenv("RE_PUBLIC_GU_ALLOW", "성동,강동")
    assert resolve_scope_inputs("25gu", ROOT)["public_gu_allow"] == "성동,강동"


def test_resolve_11gu_returns_legacy_molit_and_no_frame_survivors():
    inputs = resolve_scope_inputs("11gu", ROOT)
    assert inputs["molit"] == ROOT / "examples/molit_recent_11gu_20260606.json"
    assert inputs["jeonse"] is None
    assert inputs["public_frame"] is None
    assert inputs["survivors"] is None
    assert inputs["public_gu_allow"] == ""


def test_resolve_unknown_scope_falls_back_to_11gu_like_cli(monkeypatch):
    """cli.py 의 기존 `if scope == "25gu": ... else: ...` 분기와 동일 — 오타/미설정 스코프는
    크래시 없이 11gu 로 안전 폴백."""
    monkeypatch.delenv("RE_PUBLIC_GU_ALLOW", raising=False)
    assert resolve_scope_inputs("oops", ROOT) == resolve_scope_inputs("11gu", ROOT)


def test_current_scope_defaults_to_11gu(monkeypatch):
    monkeypatch.delenv("RE_SCAN_SCOPE", raising=False)
    assert current_scope() == "11gu"


def test_current_scope_reads_env(monkeypatch):
    monkeypatch.setenv("RE_SCAN_SCOPE", "25gu")
    assert current_scope() == "25gu"


# ── blog.run_daily.build_arg_parser — 파서 기본값이 scope_inputs 를 보는지 ──
# chdir(tmp_path) 로 examples/ 글롭 의존(_latest_or)을 CI 와 동일하게(빈 디렉토리) 만든다 —
# 이 저장소의 실제 examples/*.json 유무에 테스트 결과가 좌우되지 않게(스펙 명시 요구사항).

def _clear_run_daily_env(monkeypatch):
    for k in ("RE_MOLIT", "RE_JEONSE_RECENT", "RE_PUBLIC_GU_ALLOW", "RE_UNIVERSE", "RE_ENRICH_OVERLAY"):
        monkeypatch.delenv(k, raising=False)


def test_run_daily_defaults_resolve_25gu_scope(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _clear_run_daily_env(monkeypatch)
    monkeypatch.setenv("RE_SCAN_SCOPE", "25gu")
    run_daily = importlib.import_module("blog.run_daily")
    ap = run_daily.build_arg_parser()
    root = Path(run_daily.__file__).resolve().parents[1]
    expected = resolve_scope_inputs("25gu", root)

    a = ap.parse_args(["--asof", "2026-09-05"])
    assert a.public_frame == expected["public_frame"]
    assert a.survivors == expected["survivors"]
    assert a.jeonse == str(expected["jeonse"])
    assert a.molit == str(expected["molit"])
    assert a.public_gu_allow == ""


def test_run_daily_defaults_legacy_when_scope_unset(monkeypatch, tmp_path):
    """CI(examples/ 없음)와 동일 조건 — RE_SCAN_SCOPE 미설정이면 public-frame/survivors 는 여전히
    None(11gu 는 public 경로 자체가 없음), molit 은 scope_inputs 의 legacy 11gu 경로."""
    monkeypatch.chdir(tmp_path)
    _clear_run_daily_env(monkeypatch)
    monkeypatch.delenv("RE_SCAN_SCOPE", raising=False)
    run_daily = importlib.import_module("blog.run_daily")
    ap = run_daily.build_arg_parser()
    root = Path(run_daily.__file__).resolve().parents[1]
    expected = resolve_scope_inputs("11gu", root)

    a = ap.parse_args(["--asof", "2026-09-05"])
    assert a.public_frame is None
    assert a.survivors is None
    assert a.molit == str(expected["molit"])
    assert a.public_gu_allow == ""
    assert a.jeonse == ""   # _latest_or fallback — tmp_path 에 examples/ 없으므로 빈 값


def test_run_daily_molit_env_override_wins_over_scope(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _clear_run_daily_env(monkeypatch)
    monkeypatch.setenv("RE_SCAN_SCOPE", "25gu")
    monkeypatch.setenv("RE_MOLIT", "/x/y.json")
    run_daily = importlib.import_module("blog.run_daily")
    ap = run_daily.build_arg_parser()
    a = ap.parse_args(["--asof", "2026-09-05"])
    assert a.molit == "/x/y.json"


def test_run_daily_jeonse_env_override_wins_over_scope(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _clear_run_daily_env(monkeypatch)
    monkeypatch.setenv("RE_SCAN_SCOPE", "25gu")
    monkeypatch.setenv("RE_JEONSE_RECENT", "/x/jeonse.json")
    run_daily = importlib.import_module("blog.run_daily")
    ap = run_daily.build_arg_parser()
    a = ap.parse_args(["--asof", "2026-09-05"])
    assert a.jeonse == "/x/jeonse.json"


def test_run_daily_public_gu_allow_env_override_wins_over_scope(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _clear_run_daily_env(monkeypatch)
    monkeypatch.setenv("RE_SCAN_SCOPE", "11gu")
    monkeypatch.setenv("RE_PUBLIC_GU_ALLOW", "성동")
    run_daily = importlib.import_module("blog.run_daily")
    ap = run_daily.build_arg_parser()
    a = ap.parse_args(["--asof", "2026-09-05"])
    assert a.public_gu_allow == "성동"
