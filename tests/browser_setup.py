"""Required offline browser resources; environmental failures are setup errors."""
from contextlib import ExitStack, contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class BrowserSetupError(RuntimeError):
    """A required test resource could not be acquired."""


@contextmanager
def browser_resource(playwright_factory=None):
    if playwright_factory is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserSetupError("browser setup: Playwright package missing") from exc
        playwright_factory = sync_playwright
    with ExitStack() as resources:
        try:
            driver = resources.enter_context(playwright_factory())
            browser = driver.chromium.launch()
        except ModuleNotFoundError as exc:
            raise BrowserSetupError("browser setup: Playwright package missing") from exc
        except Exception as exc:
            reason = "browser installation missing" if "Executable doesn't exist" in str(exc) else "launch denied or failed"
            raise BrowserSetupError(f"browser setup: {reason}: {exc}") from exc
        resources.callback(browser.close)
        yield browser


@contextmanager
def server_resource(directory, server_factory=ThreadingHTTPServer, thread_factory=Thread):
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    try:
        server = server_factory(("127.0.0.1", 0), handler)
    except OSError as exc:
        raise BrowserSetupError(f"browser setup: local server bind denied or failed: {exc}") from exc
    with ExitStack() as resources:
        resources.callback(server.server_close)
        try:
            thread = thread_factory(target=server.serve_forever, daemon=True)
            thread.start()
        except Exception as exc:
            raise BrowserSetupError(f"browser setup: local server thread failed: {exc}") from exc
        resources.callback(thread.join, 5)
        resources.callback(server.shutdown)
        yield server
