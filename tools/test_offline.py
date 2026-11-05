"""Run the complete pytest suite with external socket connections forbidden."""
from contextlib import contextmanager
import ipaddress
from pathlib import Path
import os
import socket
import sys
import tempfile
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_address(sock, address):
    if sock.family == socket.AF_UNIX:
        return
    if (sock.family in (socket.AF_INET, socket.AF_INET6)
            and isinstance(address, tuple) and address and _loopback(address[0])):
        return
    raise AssertionError("External network forbidden in offline tests; mock the provider/transport")


@contextmanager
def offline_network():
    """Allow in-process HTTP test servers, while refusing remote connects and DNS."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def local_connect(sock, address):
        _check_address(sock, address)
        return connect(sock, address)

    def local_connect_ex(sock, address):
        _check_address(sock, address)
        return connect_ex(sock, address)

    def local_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host is not None and not _loopback(host):
            raise AssertionError("External DNS forbidden in offline tests; mock the resolver")
        return getaddrinfo(host, port, family, type, proto, flags)

    with patch.object(socket.socket, "connect", local_connect), \
            patch.object(socket.socket, "connect_ex", local_connect_ex), \
            patch.object(socket, "getaddrinfo", local_getaddrinfo):
        yield


def main(args=None):
    with tempfile.TemporaryDirectory(prefix="regular-tests-") as directory:
        env = {"REGULAR_HOME": directory, "DATA_DIR": directory,
               "AI_PROVIDER": "gemini", "FALLBACK_PROVIDER": "zhipu", "SSL_VERIFY": "true",
               "QQ_APP_ID": "", "QQ_APP_SECRET": "", "BANTER_BURST_SECONDS": "0"}
        # Never inherit a real provider key. Dummy keys retain the two-provider structure
        # exercised by offline routing tests; keys_are_real() recognizes their prefix.
        env.update({name: "" for name in os.environ if name.endswith("_API_KEY")})
        env.update(OPENAI_API_KEY="", IMAGE_GENERATION_API_KEY="",
                   GEMINI_API_KEY="sk-hermetic-dummy-gemini",
                   ZHIPU_API_KEY="sk-hermetic-dummy-zhipu")
        with patch.dict(os.environ, env), offline_network():
            return pytest.main(list(sys.argv[1:] if args is None else args) or [str(ROOT / "tests"), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
