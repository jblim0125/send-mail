"""로컬 SMTP 서버(aiosmtpd)와 테스트 CA(trustme) fixture."""

import ssl
from dataclasses import dataclass

import pytest
import trustme
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP

from tests.support import RecordingHandler, authenticate, free_port
from tools import send_mail as tool


class _CloseAfterAuthFailure(SMTP):
    """Gmail 처럼 로그인 거부(535) 응답 직후 연결을 끊는 서버 (2026-10-14 Gmail 실측)."""

    async def smtp_AUTH(self, arg):
        await super().smtp_AUTH(arg)
        if not self.session.authenticated:
            self.transport.close()


class _GmailLikeController(Controller):
    def factory(self):
        return _CloseAfterAuthFailure(self.handler, **self.SMTP_kwargs)


@dataclass
class Server:
    host: str
    port: int
    handler: RecordingHandler


@pytest.fixture(scope="session")
def ca() -> trustme.CA:
    return trustme.CA()


@pytest.fixture
def trust_test_ca(ca, tmp_path, monkeypatch):
    """운영 코드의 ssl.create_default_context() 가 테스트 CA 를 신뢰하게 한다."""
    path = tmp_path / "test-ca.pem"
    ca.cert_pem.write_to_path(str(path))
    monkeypatch.setenv("SSL_CERT_FILE", str(path))


@pytest.fixture
def allow_port(monkeypatch):
    """Tool 은 포트를 4종으로 제한하므로, 테스트 포트를 허용 목록에 더한다."""

    def allow(port: int) -> int:
        monkeypatch.setattr(tool, "_ALLOWED_PORTS", tool._ALLOWED_PORTS | {port})
        return port

    return allow


@pytest.fixture
def closed_port(allow_port) -> int:
    """아무것도 듣지 않는 허용 포트."""
    return allow_port(free_port())


@pytest.fixture
def smtp_server(ca, allow_port):
    """`smtp_server(tls=..., auth=..., handler=..., close_after_auth_failure=...)` 로 로컬 서버를 띄운다.

    tls: "starttls" | "ssl" | None, auth: AUTH 지원 여부,
    close_after_auth_failure: 로그인 거부 응답 직후 연결을 끊는다(Gmail 동작).
    """
    controllers = []

    def start(tls=None, auth=True, handler=None, close_after_auth_failure=False, authenticator=authenticate):
        handler = handler or RecordingHandler()
        server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ca.issue_cert("127.0.0.1", "localhost").configure_cert(server_ctx)
        options = {}
        if tls == "starttls":
            options["tls_context"] = server_ctx
        elif tls == "ssl":
            options["ssl_context"] = server_ctx
        if auth:
            options["authenticator"] = authenticator
            # 접속 즉시 TLS(ssl_context)는 aiosmtpd 가 TLS 세션으로 인식하지 못해,
            # True 로 두면 AUTH 를 광고하지 않는다. STARTTLS 일 때만 TLS 후 AUTH 를 강제한다
            options["auth_require_tls"] = tls == "starttls"
        port = allow_port(free_port())
        controller_class = _GmailLikeController if close_after_auth_failure else Controller
        controller = controller_class(handler, hostname="127.0.0.1", port=port, **options)
        controller.start()
        controllers.append(controller)
        return Server("127.0.0.1", port, handler)

    yield start
    for controller in controllers:
        controller.stop()


@pytest.fixture
def no_connection(monkeypatch):
    """검증 단계에서 끝나야 하는 테스트용. 접속을 시도하면 실패시킨다."""

    def refuse(*args, **kwargs):
        raise AssertionError("검증 단계에서 끝나야 하는데 SMTP 접속을 시도했다")

    monkeypatch.setattr(tool.smtplib, "SMTP", refuse)
    monkeypatch.setattr(tool.smtplib, "SMTP_SSL", refuse)
