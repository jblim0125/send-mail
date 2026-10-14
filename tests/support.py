"""테스트 공용 값과 로컬 SMTP 서버 핸들러."""

import socket
from dataclasses import dataclass, field

from aiosmtpd.smtp import AuthResult, LoginPassword

# 실제 형식을 흉내 내지 않은, 알아볼 수 있는 시험용 값이다
USERNAME = "test-account@example.com"
PASSWORD = "not-a-real-password-for-test"


@dataclass
class Received:
    mail_from: str
    rcpt_tos: list[str]
    content: bytes
    ehlo_name: str


@dataclass
class RecordingHandler:
    """받은 메일을 기록한다. 거부할 발신자·수신자와 DATA 중 연결 끊기를 지정할 수 있다."""

    refuse_sender: bool = False
    refuse_rcpts: set[str] = field(default_factory=set)
    drop_on_data: bool = False
    received: list[Received] = field(default_factory=list)

    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        if self.refuse_sender:
            return "550 5.7.1 Sender rejected"
        envelope.mail_from = address
        return "250 OK"

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if address in self.refuse_rcpts:
            return "550 5.1.1 Recipient rejected"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        if self.drop_on_data:
            server.transport.close()
            return "250 OK"
        self.received.append(Received(envelope.mail_from, list(envelope.rcpt_tos), envelope.content, session.host_name))
        return "250 Message accepted"


def authenticate(server, session, envelope, mechanism, auth_data):
    ok = (
        isinstance(auth_data, LoginPassword)
        and auth_data.login == USERNAME.encode()
        and auth_data.password == PASSWORD.encode()
    )
    return AuthResult(success=ok, handled=False)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
