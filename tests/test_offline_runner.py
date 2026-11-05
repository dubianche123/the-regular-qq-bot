"""The CI runner must exercise local HTTP without permitting external requests."""
import importlib.util
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "regular_offline_runner", Path(__file__).resolve().parents[1] / "tools" / "test_offline.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
@pytest.mark.parametrize("family,address", [
    (socket.AF_INET, ("127.0.0.1", 8000)),
    (socket.AF_INET, ("127.1.2.3", 8000)),
    (socket.AF_INET6, ("::1", 8000, 0, 0)),
    (socket.AF_UNIX, "/tmp/regular-test.sock"),
])
def test_local_connections_delegate_to_the_socket(method, family, address):
    original = Mock(return_value=0)
    sock = SimpleNamespace(family=family)
    with patch.object(socket.socket, method, original), runner.offline_network():
        assert getattr(socket.socket, method)(sock, address) == 0
    original.assert_called_once_with(sock, address)


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
@pytest.mark.parametrize("family,address", [
    (socket.AF_INET, ("203.0.113.1", 443)),
    (socket.AF_INET, ("10.0.0.1", 443)),
    (socket.AF_INET6, ("2001:db8::1", 443, 0, 0)),
    (socket.AF_INET, ("api.openai.com", 443)),
    (socket.AF_INET, ("localhost.example.com", 443)),
])
def test_remote_connections_are_rejected_before_the_socket(method, family, address):
    original = Mock()
    with patch.object(socket.socket, method, original), runner.offline_network():
        with pytest.raises(AssertionError, match="External network forbidden"):
            getattr(socket.socket, method)(SimpleNamespace(family=family), address)
    original.assert_not_called()


@pytest.mark.parametrize("host", ["api.openai.com", "203.0.113.1", "2001:db8::1"])
def test_remote_resolution_is_rejected_before_dns(host):
    original = Mock()
    with patch.object(socket, "getaddrinfo", original), runner.offline_network():
        with pytest.raises(AssertionError, match="External DNS forbidden"):
            socket.getaddrinfo(host, 443)
    original.assert_not_called()


def test_loopback_resolution_and_guard_restoration():
    original = Mock(return_value=["local"])
    with patch.object(socket, "getaddrinfo", original):
        with runner.offline_network():
            assert socket.getaddrinfo("::1", 8000) == ["local"]
        assert socket.getaddrinfo is original
    original.assert_called_once_with("::1", 8000, 0, 0, 0, 0)


def test_main_runs_pytest_with_isolated_data_and_dummy_keys():
    seen = {}

    def run(args):
        seen.update(args=args, directory=os.environ["REGULAR_HOME"])
        assert Path(seen["directory"]).is_dir()
        assert os.environ["DATA_DIR"] == seen["directory"]
        assert os.environ["GEMINI_API_KEY"].startswith("sk-hermetic-dummy")
        assert os.environ["ZHIPU_API_KEY"].startswith("sk-hermetic-dummy")
        assert os.environ["OPENAI_API_KEY"] == ""
        assert os.environ["IMAGE_GENERATION_API_KEY"] == ""
        assert os.environ["AI_PROVIDER"] == "gemini"
        assert os.environ["FALLBACK_PROVIDER"] == "zhipu"
        return 3

    with patch.dict(os.environ, {"OPENAI_API_KEY": "real-key", "REGULAR_HOME": "original"}), \
            patch.object(runner.pytest, "main", side_effect=run):
        assert runner.main(["tests/test_panel.py", "-q"]) == 3
        assert os.environ["REGULAR_HOME"] == "original"
        assert os.environ["OPENAI_API_KEY"] == "real-key"
    assert seen["args"] == ["tests/test_panel.py", "-q"]
    assert not Path(seen["directory"]).exists()
