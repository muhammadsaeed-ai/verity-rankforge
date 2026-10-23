"""Security regressions for the user-supplied URL fetch boundary."""

import socket
import ssl

import pytest

from rankforge import core


def _dns_record(address: str, port: int = 443):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (address, port, 0, 0) if family == socket.AF_INET6 else (address, port))


def test_url_validation_rejects_any_private_dns_answer(monkeypatch):
    monkeypatch.setattr(
        core.socket,
        "getaddrinfo",
        lambda host, port, type: [
            _dns_record("93.184.216.34", port),
            _dns_record("10.12.0.4", port),
        ],
    )
    with pytest.raises(ValueError, match="non-public"):
        core.validate_public_url("https://public.example/")


def test_url_validation_rejects_nonstandard_ports(monkeypatch):
    monkeypatch.setattr(
        core.socket,
        "getaddrinfo",
        lambda host, port, type: [_dns_record("93.184.216.34", port)],
    )
    with pytest.raises(ValueError, match="standard HTTP"):
        core.validate_public_url("https://public.example:8443/")
    with pytest.raises(ValueError, match="standard HTTP"):
        core.validate_public_url("https://public.example:0/")


def test_safe_open_passes_the_validated_ip_to_both_transports(monkeypatch):
    resolved = "93.184.216.34"
    monkeypatch.setattr(
        core.socket,
        "getaddrinfo",
        lambda host, port, type: [_dns_record(resolved, port)],
    )
    observed = {}

    class FakeResponse:
        def getcode(self):
            return 200

        def close(self):
            pass

    class FakeOpener:
        def open(self, request, timeout):
            observed["url"] = request.full_url
            observed["timeout"] = timeout
            return FakeResponse()

    def fake_build_opener(*handlers):
        observed["pinned_ips"] = [
            handler._pinned_ip for handler in handlers if hasattr(handler, "_pinned_ip")
        ]
        return FakeOpener()

    monkeypatch.setattr(core.urllib.request, "build_opener", fake_build_opener)
    normalized, response = core.safe_open_url("https://public.example/path", timeout=3)

    assert normalized == "https://public.example/path"
    assert observed["url"] == normalized
    assert observed["timeout"] == 3
    assert observed["pinned_ips"] == [resolved, resolved]
    response.close()


def test_pinned_connections_dial_ip_but_keep_original_tls_name(monkeypatch):
    resolved = "93.184.216.34"
    observed = {}
    fake_socket = object()

    def fake_create_connection(address, timeout, source_address):
        observed["destination"] = address
        observed["timeout"] = timeout
        return fake_socket

    class FakeSSLContext:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True
        post_handshake_auth = False

        def set_alpn_protocols(self, protocols):
            self.alpn_protocols = protocols

        def wrap_socket(self, sock, server_hostname):
            observed["tls_hostname"] = server_hostname
            assert sock is fake_socket
            return object()

    monkeypatch.setattr(core.socket, "create_connection", fake_create_connection)
    http_connection = core._PinnedHTTPConnection("public.example", resolved, port=80, timeout=2)
    http_connection.connect()
    assert observed["destination"] == (resolved, 80)
    assert http_connection.host == "public.example"

    https_connection = core._PinnedHTTPSConnection(
        "public.example", resolved, port=443, timeout=2,
        context=FakeSSLContext(), check_hostname=False,
    )
    https_connection.connect()
    assert observed["destination"] == (resolved, 443)
    assert https_connection.host == "public.example"
    assert observed["tls_hostname"] == "public.example"
