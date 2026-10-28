"""떠 있는 정식 크롬 직접 조작(2026-10-26) — Playwright 가 띄운 크롬 대신 CDP 로 붙고, 발행 미확인 시 원인을 남긴다.

10-24·10-26 07시 무인 발행이 클릭 뒤 이동 없이(NO_REDIRECT) 끝났고, 대화상자는 조용히 수락돼 원인 기록이 없었다.
"""
import sys
from unittest.mock import MagicMock, Mock

import pytest

from blog import tistory_publish_pw as pub
from test_visual_first_publish import _fake_browser, _image_draft, isolated  # noqa: F401


def _browser(monkeypatch, cdp_url="http://127.0.0.1:9334"):
    calls = _fake_browser(monkeypatch, lambda n: [])
    p = sys.modules["playwright.sync_api"].sync_playwright.return_value.__enter__.return_value
    ctx = p.chromium.launch_persistent_context.return_value
    page = ctx.pages[0]
    ctx.new_page.return_value = page
    p.chromium.connect_over_cdp.return_value.contexts = [ctx]
    monkeypatch.setattr(pub, "CDP_URL", cdp_url)
    monkeypatch.setattr(pub, "verify_body_images", Mock(return_value="ok"))
    return calls, p, ctx, page


def _no_redirect(page, monkeypatch, captcha):
    page.wait_for_url.side_effect = TimeoutError("synthetic")
    original = page.evaluate.side_effect

    def evaluate(script, *args):
        if "captcha" in script:
            return captcha
        return original(script, *args)
    page.evaluate.side_effect = evaluate


def test_cdp_url_attaches_to_running_chrome_and_closes_only_its_tab(isolated, monkeypatch):
    _image_draft(isolated)
    _, p, ctx, page = _browser(monkeypatch)
    result = pub.publish(str(isolated), "inject", "2026-10-10")
    assert "INJECT_OK" in result
    p.chromium.connect_over_cdp.assert_called_once_with("http://127.0.0.1:9334", timeout=pub.CDP_CONNECT_TIMEOUT_MS)
    p.chromium.launch_persistent_context.assert_not_called()
    ctx.new_page.assert_called_once()
    page.close.assert_called_once()
    ctx.close.assert_not_called()


def test_without_cdp_url_keeps_persistent_profile(isolated, monkeypatch):
    _image_draft(isolated)
    _, p, ctx, _ = _browser(monkeypatch, cdp_url="")
    pub.publish(str(isolated), "inject", "2026-10-10")
    p.chromium.launch_persistent_context.assert_called_once()
    p.chromium.connect_over_cdp.assert_not_called()
    ctx.close.assert_called_once()


def test_dialog_message_is_logged_not_silently_accepted(isolated, monkeypatch):
    _image_draft(isolated)
    calls, _, ctx, page = _browser(monkeypatch)
    original = page.evaluate.side_effect

    def evaluate(script, *args):
        value = original(script, *args)
        if "publish-btn" in script:
            handler = next(c.args[1] for c in ctx.on.call_args_list if c.args[0] == "dialog")
            handler(Mock(message="하루 발행\n한도를 넘었습니다", type="alert"))
        return value
    page.evaluate.side_effect = evaluate
    result = pub.publish(str(isolated), "publish", "2026-10-10")
    assert "DIALOG:alert:하루 발행 한도를 넘었습니다" in result


def test_no_redirect_captcha_is_named_shot_and_tab_left_for_human(isolated, monkeypatch):
    _image_draft(isolated)
    _, _, ctx, page = _browser(monkeypatch)
    _no_redirect(page, monkeypatch, captcha=True)
    result = pub.publish(str(isolated), "publish", "2026-10-10")
    assert "NO_REDIRECT" in result and "CAPTCHA_SHOWN" in result
    assert "ERR:publication_unconfirmed" in result
    shot = page.screenshot.call_args.kwargs["path"]
    assert shot == str(isolated / "report/blog/tistory/diag/2026-10-10-daily-no-redirect.png")
    page.close.assert_not_called()
    ctx.close.assert_not_called()


def test_probe_names_kakao_dkaptcha_widget():
    """R1(10-26): 발행 클릭 뒤 뜬 캡차는 카카오 DKAPTCHA(<div id="dkaptcha">, 지도 빈칸 글자) — 'captcha' 만 찾으면 놓친다."""
    assert "kaptcha" in pub._CAPTCHA_PROBE and "빈칸에 들어갈 글자" in pub._CAPTCHA_PROBE


def test_no_redirect_without_captcha_still_keeps_tab_in_cdp_mode(isolated, monkeypatch):
    """R2(10-26 09:06 강북): '저장중'에서 멈춘 순간엔 캡차가 아직 없어 탭을 닫았다 — 미확인이면 사람 확인용으로 남긴다."""
    _image_draft(isolated)
    _, _, ctx, page = _browser(monkeypatch)
    _no_redirect(page, monkeypatch, captcha=False)
    result = pub.publish(str(isolated), "publish", "2026-10-10")
    assert "NO_REDIRECT" in result and "CAPTCHA_SHOWN" not in result
    page.screenshot.assert_called_once()
    page.close.assert_not_called()
    ctx.close.assert_not_called()


def test_late_captcha_is_still_named(isolated, monkeypatch):
    _image_draft(isolated)
    _, _, _, page = _browser(monkeypatch)
    _no_redirect(page, monkeypatch, captcha=False)
    seen = iter([False, False, True])
    inner = page.evaluate.side_effect
    page.evaluate.side_effect = lambda script, *a: next(seen) if "captcha" in script else inner(script, *a)
    assert "CAPTCHA_SHOWN" in pub.publish(str(isolated), "publish", "2026-10-10")


def test_published_tab_is_closed_in_cdp_mode(isolated, monkeypatch):
    _image_draft(isolated)
    _, _, _, page = _browser(monkeypatch)
    pub.publish(str(isolated), "publish", "2026-10-10")
    page.close.assert_called_once()


def test_hung_resident_chrome_is_restarted_once_and_reattached(isolated, monkeypatch):
    """R3(10-28 07:05): 응답 없는 newtab 탭 하나가 connect_over_cdp 를 180초 묶어 슬롯 전체가 실패했다."""
    _image_draft(isolated)
    _, p, ctx, _ = _browser(monkeypatch)
    p.chromium.connect_over_cdp.side_effect = [TimeoutError("synthetic"), Mock(contexts=[ctx])]
    restart = Mock(return_value=True)
    monkeypatch.setattr(pub, "restart_resident_chrome", restart)
    result = pub.publish(str(isolated), "inject", "2026-10-10")
    assert "INJECT_OK" in result
    assert "CDP_CONNECT_FAIL:TimeoutError" in result and "CDP_CHROME_RESTARTED" in result
    restart.assert_called_once()
    assert p.chromium.connect_over_cdp.call_count == 2


def test_unrecoverable_cdp_is_run_wide_failure_before_any_attempt(isolated, monkeypatch):
    _image_draft(isolated)
    _, p, _, _ = _browser(monkeypatch)
    p.chromium.connect_over_cdp.side_effect = TimeoutError("synthetic")
    monkeypatch.setattr(pub, "restart_resident_chrome", Mock(return_value=False))
    result = pub.publish(str(isolated), "publish", "2026-10-10")
    assert "ERR:cdp_connect_failed" in result
    assert pub.result_exit_code(result, "publish") == 1
    assert any(m in result for m in pub.RUN_WIDE_FAILURES)
    _, attempted, _ = pub.marker_paths(str(isolated))
    assert not (isolated / attempted).exists()
    assert p.chromium.connect_over_cdp.call_count == 1


def test_restart_kicks_launchd_job_and_waits_for_debug_port(monkeypatch):
    import subprocess
    import urllib.request
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    answers = iter([OSError("refused"), MagicMock()])

    def urlopen(url, timeout):
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(pub.time, "sleep", Mock())
    assert pub.restart_resident_chrome("http://127.0.0.1:9334", wait_s=5) is True
    argv = run.call_args.args[0]
    assert argv[:3] == ["launchctl", "kickstart", "-k"] and argv[3].endswith("/" + pub.RESIDENT_CHROME_LABEL)


def test_restart_reports_failure_when_launchctl_fails(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, "launchctl")))
    assert pub.restart_resident_chrome("http://127.0.0.1:9334", wait_s=1) is False
