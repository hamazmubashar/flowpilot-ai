import socket

import pytest


def test_external_connection_is_blocked() -> None:
    # 203.0.113.0/24 is TEST-NET-3 (RFC 5737); the guard raises before any packet is sent.
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(RuntimeError, match="external network access blocked"),
    ):
        sock.connect(("203.0.113.1", 80))
