"""Shared acquisition ensures browser and bind failures count as setup errors."""
import pytest

from browser_setup import browser_resource, server_resource


@pytest.fixture(scope="module")
def required_browser():
    with browser_resource() as browser:
        yield browser


@pytest.fixture
def local_test_server(tmp_path):
    with server_resource(tmp_path) as server:
        yield server
