"""Execute the production shell wrapper with every external command stubbed."""

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_cron(tmp_path, daily_rc=0, periodic_rc=0, *, periodic=True,
              build_rc=0, already_published=False):
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
    if periodic:
        drafts = root / "report/blog/tistory"
        drafts.mkdir(parents=True)
        (drafts / f"{today}-periodic-tistory-draft.html").write_text("fixture")
    stubs = {
        "date": 'printf "%s\\n" "2026-09-11"\n',
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
if [ "$1" = '-m' ]; then
  printf '%s\\n' 'build_daily' >> "$STUB_CALLS"
  exit "$STUB_BUILD_RC"
fi
if [ "$1" != 'blog/tistory_publish_pw.py' ]; then exit 98; fi
printf '%s\\n' "publish $*" >> "$STUB_CALLS"
case " $* " in
  *' --kind periodic '*) exit "$STUB_PERIODIC_RC" ;;
  *) exit "$STUB_DAILY_RC" ;;
esac
''',
    }
    for name, body in stubs.items():
        command = bins / name
        command.write_text("#!/bin/bash\nset -eu\n" + body)
        command.chmod(0o700)
    # Replace only the workspace location: all scheduling, guards and traps run.
    source = (ROOT / "blog/cron_daily.sh").read_text()
    script = tmp_path / "cron_daily.sh"
    script.write_text(source.replace(str(ROOT), str(root)))
    env = {
        "PATH": str(bins),
        "HOME": str(tmp_path),
        "STUB_CALLS": str(calls),
        "STUB_DAILY_RC": str(daily_rc),
        "STUB_PERIODIC_RC": str(periodic_rc),
        "STUB_BUILD_RC": str(build_rc),
        "LC_ALL": "C",
    }
    # No inherited secrets or shell initialization; no actual child binaries.
    result = subprocess.run(["/bin/bash", str(script)], env=env, cwd=tmp_path,
                            text=True, capture_output=True, timeout=10)
    lines = calls.read_text().splitlines() if calls.exists() else []
    return result, lines


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
