"""메일 발송 Tool 테스트. 케이스는 docs/design.md 7장 테스트 계획을 따른다."""

import email
import json
import smtplib
from email import policy
from email.message import EmailMessage

import pytest
from aiosmtpd.smtp import AuthResult

from tests.support import PASSWORD, USERNAME, RecordingHandler, authenticate
from tools.send_mail import TOOL_VERSION, MailToolError, check, send_mail


def call(server, **overrides):
    """설정 항목과 호출 항목을 합쳐 send_mail 을 부른다. 기본은 STARTTLS 와 로그인."""
    args = {
        "smtp_host": server.host if server else "127.0.0.1",
        "smtp_port": server.port if server else 587,
        "smtp_security": "starttls",
        "smtp_username": USERNAME,
        "smtp_password": PASSWORD,
        "to": ["kim@example.com"],
        "subject": "주간 보고",
        "body": "본문",
    }
    args.update(overrides)
    return send_mail(**args)


def expect_error(code, func, *args, **kwargs) -> MailToolError:
    """오류 코드를 확인하고, 메시지에 비밀번호·계정·호스트가 없는지 함께 확인한다."""
    with pytest.raises(MailToolError) as info:
        func(*args, **kwargs)
    error = info.value
    message = str(error)
    assert error.code == code
    assert message.startswith(f"[{code}] ")
    for secret in (PASSWORD, USERNAME, "127.0.0.1", "localhost"):
        assert secret not in message
    # 원래 예외(서버 응답 원문을 담을 수 있다)가 함께 전달되지 않아야 한다
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    return error


def parse(received) -> EmailMessage:
    return email.message_from_bytes(received.content, policy=policy.default)


# 정상


def test_starttls_login_sends_text_mail_and_returns_message_id(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls")
    result = call(server)

    assert result["status"] == "sent"
    assert result["accepted_count"] == 1 and result["refused"] == []
    json.dumps(result)
    [received] = server.handler.received
    assert received.mail_from == USERNAME
    assert received.rcpt_tos == ["kim@example.com"]
    assert parse(received)["Message-ID"] == result["message_id"]


def test_ssl_login_sends_mail(smtp_server, trust_test_ca):
    server = smtp_server(tls="ssl")
    assert call(server, smtp_security="ssl")["status"] == "sent"
    assert len(server.handler.received) == 1


def test_html_mail_hides_bcc_and_encodes_korean_headers(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls")
    call(
        server,
        from_name="AI 에이전트",
        to=["kim@example.com"],
        cc=["team@example.com"],
        bcc=["archive@example.com"],
        subject="배포 완료 안내",
        body="<p>배포가 완료되었습니다.</p>",
        body_format="html",
    )

    [received] = server.handler.received
    raw_headers = received.content.split(b"\r\n\r\n")[0]
    msg = parse(received)
    assert received.rcpt_tos == ["kim@example.com", "team@example.com", "archive@example.com"]
    assert msg["Bcc"] is None and b"archive@example.com" not in raw_headers
    assert msg["Subject"] == "배포 완료 안내" and b"=?utf-8?" in raw_headers
    assert msg["From"].addresses[0].display_name == "AI 에이전트"
    assert msg.get_content_type() == "text/html"
    assert msg["Content-Transfer-Encoding"] == "base64"
    assert all(byte < 128 for byte in received.content)
    assert msg.get_content() == "<p>배포가 완료되었습니다.</p>\n"


def test_message_headers_use_sender_domain_instead_of_pod_name(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls")
    call(server)

    [received] = server.handler.received
    msg = parse(received)
    assert received.ehlo_name == "example.com"
    assert msg["Message-ID"].endswith("@example.com>")
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Date"]


def test_partially_refused_recipients_are_reported_as_sent(smtp_server, trust_test_ca):
    handler = RecordingHandler(refuse_rcpts={"old-user@example.com"})
    server = smtp_server(tls="starttls", handler=handler)
    result = call(server, to=["kim@example.com", "old-user@example.com"])

    assert result["status"] == "sent"
    assert result["accepted_count"] == 1
    assert result["refused"] == [{"address": "old-user@example.com", "smtp_code": 550}]


def test_succeeds_when_quit_fails_after_sending(smtp_server, trust_test_ca, monkeypatch):
    server = smtp_server(tls="starttls")

    def broken_quit(self):
        raise smtplib.SMTPServerDisconnected("closed")

    monkeypatch.setattr(smtplib.SMTP, "quit", broken_quit)
    assert call(server)["status"] == "sent"
    assert len(server.handler.received) == 1


# 설정 위반: 접속을 시도하지 않는다


@pytest.mark.parametrize(
    "overrides",
    [
        {"smtp_username": "not-an-address"},
        {"smtp_username": "에이전트@example.com"},
        {"smtp_password": ""},
        {"smtp_security": "none"},
        {"from_name": "AI\r\nBcc: x@example.com"},
        {"smtp_port": 6379},
        {"smtp_security": "tls"},
    ],
    ids=["bad-username", "non-ascii-username", "empty-password", "none-security",
         "from-name-newline", "port-not-allowed", "unknown-security"],
)
def test_config_violation_fails_before_connecting(overrides, no_connection):
    expect_error("CONFIG_ERROR", call, None, **overrides)


def test_check_rejects_config_violation_before_connecting(no_connection):
    expect_error("CONFIG_ERROR", check, "127.0.0.1", "not-an-address", PASSWORD)


# 호출 위반: 접속을 시도하지 않는다


@pytest.mark.parametrize(
    "address",
    ["kim", "kim@", "", "홍길동 <kim@example.com>", "김@example.com", "kim @example.com",
     "kim@example.com (x)", "a@b@example.com", "kim@example.com\r\nBcc: x@example.com"],
)
def test_invalid_recipient_address_is_rejected(address, no_connection):
    error = expect_error("INVALID_INPUT", call, None, to=["kim@example.com"], cc=[address])
    assert "cc[0]" in str(error)


def test_password_in_body_is_rejected_without_revealing_it(no_connection):
    expect_error("INVALID_INPUT", call, None, body=f"비밀번호는 {PASSWORD} 입니다")


@pytest.mark.parametrize(
    "overrides",
    [
        {"subject": "제목\r\nBcc: x@example.com"},
        {"subject": "   "},
        {"to": [f"user{i}@example.com" for i in range(21)]},
        {"to": [f"user{i}@example.com" for i in range(10)], "bcc": [f"b{i}@example.com" for i in range(11)]},
    ],
    ids=["subject-newline", "blank-subject", "21-to", "21-total"],
)
def test_call_violation_fails_before_connecting(overrides, no_connection):
    expect_error("INVALID_INPUT", call, None, **overrides)


def test_twenty_recipients_are_allowed(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls")
    result = call(server, to=[f"user{i}@example.com" for i in range(20)])
    assert result["accepted_count"] == 20


# 예외: 접속·TLS·로그인 단계


def test_unreachable_server_is_connection_failed(closed_port):
    expect_error("CONNECTION_FAILED", call, None, smtp_port=closed_port)


def test_starttls_unsupported_is_connection_failed_without_downgrade(smtp_server):
    server = smtp_server(tls=None, auth=False)
    error = expect_error("CONNECTION_FAILED", call, server)
    assert "STARTTLS" in str(error)
    assert server.handler.received == []


def test_untrusted_certificate_is_connection_failed(smtp_server):
    server = smtp_server(tls="starttls")
    expect_error("CONNECTION_FAILED", call, server)
    assert server.handler.received == []


def test_wrong_password_is_auth_failed_with_status_code(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls")
    error = expect_error("AUTH_FAILED", call, server, smtp_password="wrong-password")
    assert "(SMTP 535 5.7.8)" in str(error)


def test_wrong_password_is_auth_failed_when_server_closes_after_rejection(smtp_server, trust_test_ca):
    # Gmail 은 535 거부 직후 연결을 끊는다. smtplib.login() 은 다음 인증 방식을 시도하다 끊김을 만나 535 를 잃는다
    server = smtp_server(tls="starttls", close_after_auth_failure=True)
    error = expect_error("AUTH_FAILED", call, server, smtp_password="wrong-password")
    assert "(SMTP 535 5.7.8)" in str(error)


def test_login_falls_back_to_next_mechanism_when_server_keeps_connection(smtp_server, trust_test_ca):
    # smtplib.login() 처럼, 광고만 하고 실제로는 거부하는 방식(PLAIN)이 있으면 다음 방식(LOGIN)으로 넘어간다
    def login_only(server, session, envelope, mechanism, auth_data):
        if mechanism != "LOGIN":
            return AuthResult(success=False, handled=False)
        return authenticate(server, session, envelope, mechanism, auth_data)

    server = smtp_server(tls="starttls", authenticator=login_only)
    assert call(server)["status"] == "sent"


def test_server_without_auth_is_auth_failed(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls", auth=False)
    expect_error("AUTH_FAILED", call, server)


def test_timeout_during_login_is_retryable_connection_failed(smtp_server, trust_test_ca, monkeypatch):
    server = smtp_server(tls="starttls")

    def slow_auth(self, mechanism, authobject, *, initial_response_ok=True):
        raise TimeoutError("timed out")

    monkeypatch.setattr(smtplib.SMTP, "auth", slow_auth)
    expect_error("CONNECTION_FAILED", call, server)


# 예외: 전송 단계


def test_rejected_sender_is_send_rejected(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls", handler=RecordingHandler(refuse_sender=True))
    error = expect_error("SEND_REJECTED", call, server)
    assert "(SMTP 550 5.7.1)" in str(error)


def test_all_recipients_refused_is_send_rejected(smtp_server, trust_test_ca):
    handler = RecordingHandler(refuse_rcpts={"kim@example.com", "lee@example.com"})
    server = smtp_server(tls="starttls", handler=handler)
    error = expect_error("SEND_REJECTED", call, server, to=["kim@example.com", "lee@example.com"])
    assert "(SMTP 550 5.1.1)" in str(error)


def test_disconnect_during_data_is_delivery_unknown(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls", handler=RecordingHandler(drop_on_data=True))
    expect_error("DELIVERY_UNKNOWN", call, server)


# 연결 테스트


def test_check_logs_in_without_sending_mail(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls")
    result = check(server.host, USERNAME, PASSWORD, server.port, "starttls")
    assert result == {"status": "ok"}
    assert server.handler.received == []
    # send_mail 과 같이 Pod 이름 대신 발신 도메인을 쓴다. STARTTLS 전후로 두 번 보낸다
    assert server.handler.ehlo_names == ["example.com", "example.com"]


def test_check_reports_wrong_password(smtp_server, trust_test_ca):
    server = smtp_server(tls="starttls")
    expect_error("AUTH_FAILED", check, server.host, USERNAME, "wrong-password", server.port, "starttls")


def test_check_reports_unreachable_server(closed_port):
    expect_error("CONNECTION_FAILED", check, "127.0.0.1", USERNAME, PASSWORD, closed_port, "starttls")


# 등록 규약 (가이드 12장 점검표)


def test_tool_version_is_a_string():
    assert isinstance(TOOL_VERSION, str) and TOOL_VERSION
