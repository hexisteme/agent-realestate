"""The site build entry point must expose frozen-cohort controls before writing."""
import os
from pathlib import Path
import subprocess
import sys


def _invoke(tmp_path, *args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.run([sys.executable, "-m", "blog.build_site", *args],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)


def test_help_exposes_probe_and_cohort_without_building(tmp_path):
    result = _invoke(tmp_path, "--help")
    assert result.returncode == 0
    assert "--probe" in result.stdout and "--cohort" in result.stdout
    assert not list(tmp_path.iterdir())


def test_invalid_date_exits_before_any_site_is_written(tmp_path):
    result = _invoke(tmp_path, "--date", "2026-02-30")
    assert result.returncode == 2
    assert "YYYY-MM-DD" in result.stderr
    assert not list(tmp_path.iterdir())
