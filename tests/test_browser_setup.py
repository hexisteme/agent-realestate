"""Setup failures must be errors and partially acquired resources must close."""
import subprocess
import sys
from pathlib import Path

import pytest



def test_missing_browser_package_is_explicit_setup_error():
    from browser_setup import BrowserSetupError, browser_resource
    def missing():
        raise ModuleNotFoundError("No module named 'playwright'")
    with pytest.raises(BrowserSetupError, match="package missing"):
        with browser_resource(missing):
            pytest.fail("missing browser yielded a resource")


def test_launch_denial_closes_playwright_driver():
    from browser_setup import BrowserSetupError, browser_resource
    events = []
    class Driver:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            events.append("driver closed")
        @property
        def chromium(self):
            return self
        def launch(self):
            raise PermissionError("macOS launch denied")
    with pytest.raises(BrowserSetupError, match="launch denied"):
        with browser_resource(Driver):
            pytest.fail("denied launch yielded a resource")
    assert events == ["driver closed"]


def test_browser_closes_when_test_body_fails():
    from browser_setup import browser_resource
    events = []
    class Driver:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            events.append("driver closed")
        @property
        def chromium(self):
            return self
        def launch(self):
            return self
        def close(self):
            events.append("browser closed")
    with pytest.raises(AssertionError):
        with browser_resource(Driver):
            raise AssertionError("test body failed")
    assert events == ["browser closed", "driver closed"]


def test_bind_denial_explains_local_server_setup_failure(tmp_path):
    from browser_setup import BrowserSetupError, server_resource
    def denied(*args):
        raise PermissionError("local bind denied")
    with pytest.raises(BrowserSetupError, match="local server bind denied"):
        with server_resource(tmp_path, server_factory=denied):
            pytest.fail("denied bind yielded a server")


def test_thread_start_failure_closes_bound_socket(tmp_path):
    from browser_setup import BrowserSetupError, server_resource
    events = []
    class Server:
        def __init__(self, *args):
            pass
        def serve_forever(self):
            pass
        def server_close(self):
            events.append("socket closed")
    class Thread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError("thread unavailable")
    with pytest.raises(BrowserSetupError, match="server thread"):
        with server_resource(tmp_path, server_factory=Server, thread_factory=Thread):
            pytest.fail("failed thread yielded a server")
    assert events == ["socket closed"]


def test_server_closes_and_joins_when_body_fails(tmp_path):
    from browser_setup import server_resource
    events = []
    class Server:
        def __init__(self, *args):
            pass
        def serve_forever(self):
            pass
        def shutdown(self):
            events.append("shutdown")
        def server_close(self):
            events.append("socket closed")
    class Thread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            events.append("started")
        def join(self, timeout):
            events.append("joined")
    with pytest.raises(AssertionError):
        with server_resource(tmp_path, server_factory=Server, thread_factory=Thread):
            raise AssertionError("test body failed")
    assert events == ["started", "shutdown", "joined", "socket closed"]


def test_pytest_collects_denied_launch_as_setup_error(tmp_path):
    helper = Path(__file__).parent
    (tmp_path / "conftest.py").write_text(
        "import sys\nsys.path.insert(0, " + repr(str(helper)) + ")\n"
        "import pytest\nfrom browser_setup import browser_resource\n"
        "@pytest.fixture\ndef required_browser():\n"
        "    def denied():\n        raise PermissionError('macOS denied')\n"
        "    with browser_resource(denied) as browser:\n        yield browser\n"
    )
    (tmp_path / "test_required.py").write_text(
        "def test_required(required_browser):\n    assert required_browser\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "1 error" in result.stdout
    assert "1 skipped" not in result.stdout
    assert "launch denied" in result.stdout
