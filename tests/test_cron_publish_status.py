"""Execute the production shell wrapper with every external command stubbed."""

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_cron(tmp_path, daily_rc=0, periodic_rc=0, *, periodic=True,
              build_rc=0, already_published=False, review_rc=0,
              series_due="", series_due_rc=0, series_rc=0, series_publish_rc=0,
              gu=(), gu_published=(), gu_publish_rc=0, gu_other_day=(), gu_attempted=(), hour="12"):
    root = tmp_path / "root"
    root.mkdir()
    bins = tmp_path / "bin"
    bins.mkdir()
    calls = tmp_path / "calls.txt"
    today = "2026-09-11"
    if build_rc == 0:
        (root / ".last-published").write_text(today + "\n")
    if already_published:
        (root / ".last-tistory-published").write_text(today + "\n")
    drafts = root / "report/blog/tistory"
    drafts.mkdir(parents=True)
    if periodic:
        (drafts / f"{today}-periodic-tistory-draft.html").write_text("fixture")
    for g in gu:
        (drafts / f"{today}-gu-{g}-tistory-draft.html").write_text("fixture")
    for g in gu_other_day:
        (drafts / f"2026-09-10-gu-{g}-tistory-draft.html").write_text("fixture")
    for g in gu_published:
        (root / f".last-tistory-published-gu-{g}").write_text(today + "\n")
    receipts = root / "report/blog/publications"
    receipts.mkdir(parents=True)
    for g in gu_attempted:
        (receipts / f"{today}-gu-{g}.json").write_text('{"state": "ATTEMPTED"}')
    stubs = {
        "date": '''case "${1:-}" in
  +%H) printf '%s\\n' "$STUB_HOUR" ;;
  *) printf '%s\\n' "2026-09-11" ;;
esac
''',
        "cat": '''if [ ! -f "$1" ]; then exit 1; fi
while IFS= read -r line || [ -n "$line" ]; do printf '%s\\n' "$line"; done < "$1"
''',
        "grep": '''case "$2" in
  '^TELEGRAM_BOT_TOKEN=') printf '%s\\n' 'TELEGRAM_BOT_TOKEN=fixture' ;;
  '^TELEGRAM_CHAT_ID=') printf '%s\\n' 'TELEGRAM_CHAT_ID=fixture' ;;
  *) exit 98 ;;
esac
''',
        "cut": '''while IFS= read -r line; do printf '%s\\n' "${line#*=}"; done
''',
        "curl": '''printf '%s\\n' 'last_resort_notification' >> "$STUB_CALLS"
''',
        "caffeinate": '''printf '%s\\n' "caffeinate $*" >> "$STUB_CALLS"
if [ "$1" != '-d' ] || [ "$2" != '-i' ]; then exit 98; fi
shift 2
"$@"
''',
        "python3": '''if [ "$1" = '-c' ]; then exit 0; fi
if [ "$1" = '-m' ] && [ "$2" = 'blog.series_edition' ]; then
  printf '%s\\n' "series $3 ${*:4}" >> "$STUB_CALLS"
  if [ "$3" = 'due' ]; then
    printf '%s\\n' "$STUB_SERIES_DUE"
    exit "$STUB_SERIES_DUE_RC"
  fi
  exit "$STUB_SERIES_RC"
fi
if [ "$1" = '-m' ]; then
  printf '%s\\n' 'build_daily' >> "$STUB_CALLS"
  exit "$STUB_BUILD_RC"
fi
if [ "$1" = 'blog/draft_design_review.py' ]; then
  printf '%s\\n' "review $*" >> "$STUB_CALLS"
  exit "$STUB_REVIEW_RC"
fi
if [ "$1" != 'blog/tistory_publish_pw.py' ]; then exit 98; fi
printf '%s\\n' "publish $*" >> "$STUB_CALLS"
case " $* " in
  *' --kind periodic '*) exit "$STUB_PERIODIC_RC" ;;
  *' --kind upsize '*|*' --kind deposit '*|*' --kind watch '*) exit "$STUB_SERIES_PUBLISH_RC" ;;
  *' --kind gu-'*) exit "$STUB_GU_PUBLISH_RC" ;;
  *) exit "$STUB_DAILY_RC" ;;
esac
''',
    }
    for name, body in stubs.items():
        command = bins / name
        command.write_text("#!/bin/bash\nset -eu\n" + body)
        command.chmod(0o700)
    source = (ROOT / "blog/cron_daily.sh").read_text()
    script = tmp_path / "cron_daily.sh"
    script.write_text(source)
    env = {
        "PATH": str(bins),
        "HOME": str(tmp_path),
        "STUB_CALLS": str(calls),
        "STUB_DAILY_RC": str(daily_rc),
        "STUB_PERIODIC_RC": str(periodic_rc),
        "STUB_BUILD_RC": str(build_rc),
        "STUB_REVIEW_RC": str(review_rc),
        "STUB_SERIES_DUE": series_due,
        "STUB_SERIES_DUE_RC": str(series_due_rc),
        "STUB_SERIES_RC": str(series_rc),
        "STUB_SERIES_PUBLISH_RC": str(series_publish_rc),
        "STUB_GU_PUBLISH_RC": str(gu_publish_rc),
        "STUB_HOUR": hour,
        "AGENT_REALESTATE_ROOT": str(root),
        "LC_ALL": "C",
    }
    # No inherited secrets or shell initialization; no actual child binaries.
    result = subprocess.run(["/bin/bash", str(script)], env=env, cwd=tmp_path,
                            text=True, capture_output=True, timeout=10)
    lines = calls.read_text().splitlines() if calls.exists() else []
    return result, [line for line in lines if not line.startswith("series due")]


@pytest.mark.parametrize("daily_rc,periodic_rc,expected", [
    (1, 0, 1), (0, 1, 1), (1, 3, 1), (3, 1, 1),
    (1, 4, 1), (4, 1, 1), (3, 0, 3), (0, 3, 3),
    (3, 4, 4), (4, 3, 4), (4, 0, 4), (0, 4, 4),
    (0, 0, 0), (2, 0, 1),
])
def test_publish_results_survive_both_kinds(tmp_path, daily_rc, periodic_rc, expected):
    result, calls = _run_cron(tmp_path, daily_rc, periodic_rc)
    assert result.returncode == expected, result.stderr
    published = [line for line in calls if line.startswith("publish ")]
    assert len(published) == 2
    assert " --pending" in published[0]
    assert " --kind periodic" in published[1]
    assert " --pending" not in published[1]
    assert "last_resort_notification" not in calls
    assert f"done (publish rc={expected})" in result.stdout
    for kind, rc in (("daily", daily_rc), ("periodic", periodic_rc)):
        label = {0: "완료 또는 기발행", 3: "사람 검토 대기", 4: "브라우저 프로필 사용 중"}.get(rc, "발행 실패")
        assert f"tistory {kind}: {label}" in result.stdout


@pytest.mark.parametrize("daily_rc,periodic_rc,expected", [
    (5, 0, 5), (0, 5, 5), (6, 0, 6), (0, 6, 6), (5, 6, 5), (6, 5, 6),
    (5, 1, 1), (1, 6, 1), (6, 4, 4), (4, 5, 4), (3, 5, 3), (5, 3, 5),
])
def test_design_review_hold_is_not_a_crash(tmp_path, daily_rc, periodic_rc, expected):
    result, calls = _run_cron(tmp_path, daily_rc, periodic_rc)
    assert result.returncode == expected, result.stderr
    assert "last_resort_notification" not in calls
    assert f"done (publish rc={expected})" in result.stdout
    for kind, rc in (("daily", daily_rc), ("periodic", periodic_rc)):
        if rc in (5, 6):
            assert f"tistory {kind}: 디자인 리뷰 보류 (rc={rc}" in result.stdout
            assert f"tistory {kind}: 발행 실패" not in result.stdout


def test_daily_failure_without_periodic_still_fails(tmp_path):
    result, calls = _run_cron(tmp_path, daily_rc=1, periodic=False)
    assert result.returncode == 1
    assert len([line for line in calls if line.startswith("publish ")]) == 1
    assert "last_resort_notification" not in calls


def test_upstream_build_failure_keeps_last_resort_notification(tmp_path):
    result, calls = _run_cron(tmp_path, build_rc=7)
    assert result.returncode == 7
    assert calls == ["build_daily", "last_resort_notification"]
    assert not (tmp_path / "root/.last-published").exists()


def test_existing_success_guard_skips_without_children(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True)
    assert result.returncode == 0
    assert calls == []
    assert "멱등 가드" in result.stdout


@pytest.mark.parametrize("review_rc", [0, 1])
def test_design_review_is_authored_before_each_publish(tmp_path, review_rc):
    result, calls = _run_cron(tmp_path, review_rc=review_rc)
    children = [line for line in calls if line.startswith(("review ", "publish "))]
    assert [line.split()[0] for line in children] == ["review", "publish", "review", "publish"]
    assert " --pending" in children[0] and " --pending" in children[1]
    assert " --kind periodic" in children[2] and " --kind periodic" in children[3]
    assert result.returncode == 0, result.stderr
    assert "last_resort_notification" not in calls
    assert ("디자인 리뷰 작성 실패 (rc=1)" in result.stdout) is (review_rc == 1)


# ── 연재(2026-10-14): due → build → 리뷰 → 발행 ─────────────────────────

def _series_calls(calls):
    return [line for line in calls if line.startswith(("series ", "review ", "publish ")) and "--pending" not in line
            and "--kind periodic" not in line]


def test_series_open_bypasses_success_guard_and_publishes_edition_day(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True, series_due="upsize 2026-09-08")
    assert result.returncode == 0, result.stderr
    assert "멱등 가드" not in result.stdout
    sc = _series_calls(calls)
    assert sc[0].startswith("series build") and "--day 2026-09-08" in sc[0] and "--today 2026-09-11" in sc[0]
    assert sc[1].startswith("review ") and sc[1].endswith("--draft " + str(tmp_path / "root/report/blog/tistory/2026-09-08-upsize-tistory-draft.html"))
    assert "--kind upsize" in sc[2] and "--date 2026-09-08" in sc[2]
    assert "last_resort_notification" not in calls


@pytest.mark.parametrize("series_rc,expected,label", [(3, 0, "발행하지 않음"), (5, 0, "발행하지 않음"),
                                                      (4, 0, "입력 대기"), (1, 1, "판 만들기 실패")])
def test_series_build_outcomes_do_not_publish(tmp_path, series_rc, expected, label):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True, series_due="deposit 2026-09-15",
                              series_rc=series_rc)
    assert result.returncode == expected, result.stderr
    assert [line.split()[0] for line in _series_calls(calls)] == ["series"]
    assert label in result.stdout and "last_resort_notification" not in calls


def test_series_publish_result_joins_exit_code(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True, series_due="watch 2026-09-22",
                              series_publish_rc=6)
    assert result.returncode == 6
    assert "tistory watch: 디자인 리뷰 보류 (rc=6" in result.stdout


def test_series_due_failure_alerts_and_keeps_daily_flow(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True, series_due_rc=2)
    assert result.returncode == 0
    assert "연재 일정 확인 실패" in result.stdout and "멱등 가드" in result.stdout
    assert calls == ["last_resort_notification"]


# ── 구 단위 분할(2026-10-20 사용자 결정 — 자동 공개): 오늘 원고 중 미발행 구만, 디자인 리뷰 없이 ─────

def _gu_calls(calls):
    return [line for line in calls if line.startswith("publish ") and "--kind gu-" in line]


def _gu_kinds(calls):
    return [line.split("--kind ")[1].split(" ")[0] for line in _gu_calls(calls)]


def test_today_gu_drafts_publish_after_daily_without_design_review(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, gu=("금천", "노원"), gu_other_day=("도봉",))
    assert result.returncode == 0, result.stderr
    assert _gu_calls(calls) == ["publish blog/tistory_publish_pw.py --mode publish --login-wait 120 "
                                "--kind gu-금천 --date 2026-09-11"]
    published = [line for line in calls if line.startswith("publish ")]
    assert " --pending" in published[0]
    assert not [line for line in calls if line.startswith("review ") and "gu-" in line]
    assert "tistory gu-금천: 완료 또는 기발행" in result.stdout


# 2026-10-27 사용자 결정(카카오 DKAPTCHA): 구 원고는 이름순 k번째가 GU_SLOT_HOURS[k] 시(12·18) 슬롯부터, 슬롯당 새 클릭 1편.
@pytest.mark.parametrize("hour", ["07", "09", "11"])
def test_gu_waits_for_noon_slot(tmp_path, hour):
    result, calls = _run_cron(tmp_path, periodic=False, gu=("금천", "노원"), hour=hour)
    assert result.returncode == 0, result.stderr
    assert _gu_kinds(calls) == []


def test_second_gu_waits_for_evening_slot(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True, gu=("금천", "노원"),
                              gu_published=("금천",), hour="15")
    assert result.returncode == 0, result.stderr
    assert _gu_kinds(calls) == [] and "멱등 가드" in result.stdout


def test_evening_slot_publishes_second_gu(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True, gu=("금천", "노원"),
                              gu_published=("금천",), hour="18")
    assert result.returncode == 0, result.stderr
    assert _gu_kinds(calls) == ["gu-노원"]


def test_one_new_gu_click_per_slot(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, gu=("금천", "노원"), hour="18")
    assert _gu_kinds(calls) == ["gu-금천"]


def test_attempted_gu_is_rechecked_in_any_slot_without_using_the_click(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True, gu=("금천", "노원"),
                              gu_attempted=("금천", "노원"), hour="09")
    assert "멱등 가드" not in result.stdout
    assert _gu_kinds(calls) == ["gu-금천", "gu-노원"]
    (tmp_path / "evening").mkdir()
    result, calls = _run_cron(tmp_path / "evening", periodic=False, gu=("금천", "노원"),
                              gu_attempted=("금천",), hour="18")
    assert _gu_kinds(calls) == ["gu-금천", "gu-노원"]


def test_drafts_beyond_slots_are_not_published(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True,
                              gu=("광진", "구로", "금천", "노원", "도봉"), gu_published=("광진", "구로"), hour="21")
    assert result.returncode == 0, result.stderr
    assert _gu_kinds(calls) == [] and "멱등 가드" in result.stdout


def test_pending_gu_bypasses_success_guard_and_published_gu_is_skipped(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True,
                              gu=("금천", "노원"), gu_published=("금천",), hour="18")
    assert result.returncode == 0, result.stderr
    assert "멱등 가드" not in result.stdout
    assert [line.split("--kind ")[1] for line in _gu_calls(calls)] == ["gu-노원 --date 2026-09-11"]


def test_all_gu_published_keeps_success_guard(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, already_published=True,
                              gu=("금천", "노원"), gu_published=("금천", "노원"))
    assert result.returncode == 0
    assert calls == [] and "멱등 가드" in result.stdout


def test_gu_publish_failure_joins_exit_code(tmp_path):
    result, calls = _run_cron(tmp_path, periodic=False, gu=("금천",), gu_publish_rc=1)
    assert result.returncode == 1
    assert "tistory gu-금천: 발행 실패" in result.stdout
    assert "last_resort_notification" not in calls
