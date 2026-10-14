"""메일 발송 Tool.

Generic Agent 가 Tool 로 호출해 SMTP 서버로 메일을 보낸다. 특정 메일 서비스에 묶이지 않는 범용 SMTP 클라이언트다.

진입점:
    send_mail: 메일 발송. 등록 impl 은 `tools.send_mail:send_mail`.
    check: 연결 테스트. 메일을 보내지 않고 접속·STARTTLS·로그인까지만 확인한다. 등록 위치는 `tools.send_mail:check`.

입력은 세 갈래를 합친 키워드 인자다.
    연결 항목: smtp_host, smtp_port, smtp_security, smtp_username, smtp_password (운영자가 연결로 등록)
    Agent 설정 항목: from_address, from_name (운영자가 Agent 에 Tool 을 붙일 때 입력)
    호출 항목: to, cc, bcc, subject, body, body_format (모델이 Tool 을 호출할 때 입력)

실패는 MailToolError 로 알리며, 메시지는 모델에 그대로 전달될 수 있다.
명세는 docs/interface.md, 설계 근거는 docs/design.md 를 따른다. 표준 라이브러리만 쓴다.
"""

import re
import smtplib
import ssl
from email import policy
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

TOOL_VERSION = "1.0.0"

# Tool 의 안전 한도. 발송에 필요한 정보가 아니므로 입력으로 받지 않는다.
_SOCKET_TIMEOUT = 10  # 초. 소켓 작업마다 적용되므로 전체 소요 시간은 이보다 길 수 있다
_MAX_RECIPIENTS = 20  # to, cc, bcc 를 합친 수신자 상한
# 오입력과 내부망 서비스 접속을 막기 위해 SMTP 포트만 허용한다.
_ALLOWED_PORTS = frozenset({25, 465, 587, 2525})
_SECURITY_MODES = ("starttls", "ssl", "none")
# 인증 방식 시도 순서. smtplib.login() 과 같다.
_AUTH_PREFERENCE = ("CRAM-MD5", "PLAIN", "LOGIN")
# RFC 3463 확장 상태 코드. 서버 응답에서 이 코드만 오류 메시지에 남긴다.
_ENHANCED_STATUS = re.compile(r"[245]\.\d{1,3}\.\d{1,3}")
# 8BITMIME 를 지원하지 않는 서버도 받도록 비ASCII 본문은 base64 나 quoted-printable 로 인코딩한다.
_MESSAGE_POLICY = policy.default.clone(cte_type="7bit")


class MailToolError(Exception):
    """메일 Tool 의 모든 실패를 알리는 예외.

    메시지는 `[코드] 설명` 형식이며 모델에 그대로 전달될 수 있다.
    그래서 비밀번호·계정·호스트·서버 응답 원문은 넣지 않고, 서버가 응답한 경우 SMTP 코드와 확장 상태 코드만 붙인다.
    예: `[AUTH_FAILED] 메일 서버 로그인에 실패했습니다 (SMTP 535 5.7.8)`

    Attributes:
        code: 오류 코드. CONFIG_ERROR, INVALID_INPUT, CONNECTION_FAILED, AUTH_FAILED,
            SEND_REJECTED, DELIVERY_UNKNOWN 중 하나. 코드별 의미와 재시도 여부는 docs/interface.md 5.1.
    """

    def __init__(self, code: str, message: str):
        """
        Args:
            code: 오류 코드.
            message: 한국어 설명. 민감정보를 넣지 않는다.
        """
        super().__init__(f"[{code}] {message}")
        self.code = code


def send_mail(
    smtp_host: str,
    from_address: str,
    to: list[str],
    subject: str,
    body: str,
    smtp_port: int = 587,
    smtp_security: str = "starttls",
    smtp_username: str | None = None,
    smtp_password: str | None = None,
    from_name: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    body_format: str = "text",
) -> dict:
    """메일을 SMTP 서버로 발송한다.

    매개변수 이름은 등록 스키마(연결·Agent 설정·호출)의 속성 이름과 같다. 플랫폼이 세 갈래를 합쳐 넘긴다.

    동작 순서:
        1. 검증: 설정 항목은 CONFIG_ERROR, 호출 항목은 INVALID_INPUT 으로 거부한다. 위반하면 접속하지 않는다.
        2. 메시지 구성: From, To, Cc, Subject, Date, Message-ID, Auto-Submitted 헤더와 본문을 만든다.
           Bcc 는 헤더에 넣지 않고 envelope 수신자에만 넣는다.
        3. 접속: 서버에 접속해 EHLO 를 보내고, smtp_security 가 "starttls" 면 TLS 로 전환한다.
        4. 로그인: smtp_username 이 있으면 로그인한다.
        5. 전송: 모든 수신자에게 한 번에 보낸다. 일부 수신자가 거부돼도 나머지가 접수되면 성공이다.
        6. 종료: QUIT 를 보낸다. 메일은 이미 접수됐으므로 이 단계의 오류는 무시한다.

    Args:
        smtp_host: [연결] SMTP 서버 호스트명 또는 IPv4 주소. 예: "smtp.gmail.com"
        from_address: [Agent 설정] 보내는 사람 주소. `local@domain` 형식의 ASCII 주소만 받는다.
            보통 smtp_username 과 같은 주소를 넣는다. 도메인은 EHLO 이름과 Message-ID 에도 쓴다.
        to: [호출] 받는 사람 주소 목록. 1명 이상(스키마에서 검증).
        subject: [호출] 제목. 앞뒤 공백을 뺀 뒤 비어 있으면 안 되고, 줄바꿈 문자를 쓸 수 없다.
        body: [호출] 본문. body_format 에 따라 텍스트 또는 HTML 로 보낸다.
        smtp_port: [연결] SMTP 포트. 25, 465, 587, 2525 중 하나. 기본 587.
        smtp_security: [연결] 연결 보안 방식. 기본 "starttls".
            "starttls": 평문으로 접속한 뒤 TLS 로 전환한다(보통 587). 서버가 지원하지 않으면 평문으로 강등하지 않고 실패한다.
            "ssl": 접속 즉시 TLS 를 쓴다(보통 465).
            "none": 평문. 비밀번호가 평문으로 전송되므로 로그인 정보와 함께 쓸 수 없다.
            TLS 인증서는 시스템 CA 로 검증한다.
        smtp_username: [연결] 로그인 계정. smtp_password 와 함께 넣거나 함께 생략한다. 생략하면 로그인하지 않는다.
        smtp_password: [연결] 비밀번호 또는 앱 비밀번호. 결과와 오류 메시지에 넣지 않는다.
            제목이나 본문에 이 값이 그대로 들어 있으면 INVALID_INPUT 으로 거부한다.
        from_name: [Agent 설정] 보내는 사람 표시 이름. 줄바꿈 문자를 쓸 수 없다. 비ASCII 는 RFC 2047 로 인코딩한다.
        cc: [호출] 참조 주소 목록. 기본 없음.
        bcc: [호출] 숨은 참조 주소 목록. 메일 헤더에 나타나지 않는다. 기본 없음.
        body_format: [호출] "text" 또는 "html". 기본 "text".

        주소는 모두 `local@domain` 형식의 ASCII 만 받는다. 표시 이름(`홍길동 <a@b.com>`)과 비ASCII 주소는 거부한다.
        to, cc, bcc 를 합친 수신자는 20명 이하여야 한다.

    Returns:
        JSON 으로 바꿀 수 있는 dict.
            status: 항상 "sent". 실패는 예외로 알린다.
            message_id: 발송한 메일의 Message-ID. 메일 서버 기록과 대조하는 데 쓴다.
            accepted_count: 서버가 접수한 수신자 수.
            refused: 서버가 거부한 수신자 목록. 항목은 {"address": str, "smtp_code": int}.
        접수는 최종 배달을 뜻하지 않는다. 반송은 나중에 발신 주소로 따로 도착한다.

    Raises:
        MailToolError: code 가 다음 중 하나다.
            CONFIG_ERROR: 설정 항목 규칙 위반. 모델이 고칠 수 없으므로 재시도하지 않는다.
            INVALID_INPUT: 호출 항목 규칙 위반. 입력을 고쳐 다시 호출할 수 있다.
            CONNECTION_FAILED: 접속·TLS 실패, 인증서 검증 실패, 타임아웃, 로그인 중 연결 끊김. 아직 보낸 것이 없다.
            AUTH_FAILED: 서버가 로그인을 거부했거나 지원하는 로그인 방식이 없다.
            SEND_REJECTED: 서버가 발신자·수신자·메시지를 거부했다.
            DELIVERY_UNKNOWN: 전송 도중 연결이 끊기거나 타임아웃. 이미 발송됐을 수 있으므로 재시도하면 안 된다.
    """
    cc = cc or []
    bcc = bcc or []
    _validate_connection(smtp_port, smtp_security, smtp_username, smtp_password)
    sender = _validate_sender(from_address, from_name)
    recipients = _validate_call(to, cc, bcc, subject, body, smtp_password)

    msg = _build_message(sender, from_name, to, cc, subject, body, body_format)
    smtp = _connect(smtp_host, smtp_port, smtp_security, smtp_username, smtp_password, sender.domain)
    refused = _send(smtp, msg, sender.addr_spec, recipients)
    return {
        "status": "sent",
        "message_id": str(msg["Message-ID"]),
        "accepted_count": len(recipients) - len(refused),
        "refused": [{"address": address, "smtp_code": code} for address, (code, _) in refused.items()],
    }


def check(
    smtp_host: str,
    smtp_port: int = 587,
    smtp_security: str = "starttls",
    smtp_username: str | None = None,
    smtp_password: str | None = None,
) -> dict:
    """연결 테스트. 메일을 보내지 않고 접속·STARTTLS·로그인까지만 확인한다.

    플랫폼이 연결을 저장할 때와 호스트·비밀번호를 바꿀 때 호출하는 용도다. 모델에 노출하는 Tool 이 아니다.
    send_mail 과 같은 방식으로 접속·로그인하고, QUIT 로 끝낸다. 발신 주소가 없으므로 EHLO 이름은 "localhost" 를 쓴다.

    Args:
        smtp_host: SMTP 서버 호스트명 또는 IPv4 주소.
        smtp_port: SMTP 포트. 25, 465, 587, 2525 중 하나. 기본 587.
        smtp_security: "starttls", "ssl", "none" 중 하나. 기본 "starttls". 의미는 send_mail 과 같다.
        smtp_username: 로그인 계정. smtp_password 와 함께 넣거나 함께 생략한다.
        smtp_password: 비밀번호 또는 앱 비밀번호.

    Returns:
        {"status": "ok"}

    Raises:
        MailToolError: code 가 CONFIG_ERROR, CONNECTION_FAILED, AUTH_FAILED 중 하나다. 의미는 send_mail 과 같다.
    """
    _validate_connection(smtp_port, smtp_security, smtp_username, smtp_password)
    # 기본값 socket.getfqdn() 은 Pod 이름을 보낸다
    smtp = _connect(smtp_host, smtp_port, smtp_security, smtp_username, smtp_password, "localhost")
    _quit(smtp)
    return {"status": "ok"}


def _validate_connection(port: int, security: str, username: str | None, password: str | None) -> None:
    """연결 항목 규칙을 검사한다. 위반하면 CONFIG_ERROR.

    포트·보안 방식은 스키마에도 있지만, 현재 플랫폼이 설정 스키마를 따로 검증하지 않으므로 여기서도 본다.

    Args:
        port: smtp_port.
        security: smtp_security.
        username: smtp_username. 없거나 빈 값이면 로그인하지 않는 것으로 본다.
        password: smtp_password. username 과 함께 있거나 함께 없어야 한다.
    """
    if port not in _ALLOWED_PORTS:
        raise MailToolError("CONFIG_ERROR", "smtp_port 는 25, 465, 587, 2525 중 하나여야 합니다")
    if security not in _SECURITY_MODES:
        raise MailToolError("CONFIG_ERROR", "smtp_security 는 starttls, ssl, none 중 하나여야 합니다")
    if bool(username) != bool(password):
        raise MailToolError("CONFIG_ERROR", "smtp_username 과 smtp_password 는 함께 설정해야 합니다")
    if security == "none" and username:
        # 비밀번호가 평문으로 전송되는 것을 막는다
        raise MailToolError("CONFIG_ERROR", "smtp_security 가 none 이면 로그인 정보를 설정할 수 없습니다")


def _validate_sender(from_address: str, from_name: str | None) -> Address:
    """발신자 설정 항목을 검사한다. 위반하면 CONFIG_ERROR.

    Args:
        from_address: 보내는 사람 주소.
        from_name: 보내는 사람 표시 이름. 줄바꿈이 있으면 헤더 인젝션이 되므로 거부한다.

    Returns:
        파싱한 발신 주소. 도메인(`domain`)은 EHLO 이름과 Message-ID 에 쓴다.
    """
    sender = _parse_address(from_address)
    if sender is None:
        raise MailToolError("CONFIG_ERROR", "from_address 주소 형식이 올바르지 않습니다")
    if from_name and _has_newline(from_name):
        raise MailToolError("CONFIG_ERROR", "from_name 에 줄바꿈 문자를 쓸 수 없습니다")
    return sender


def _validate_call(
    to: list[str], cc: list[str], bcc: list[str], subject: str, body: str, password: str | None
) -> list[str]:
    """호출 항목 규칙을 검사한다. 위반하면 INVALID_INPUT. 모델이 입력을 고쳐 다시 부를 수 있는 오류다.

    검사 순서: 총 수신자 수, 주소 형식(위반 위치를 `cc[0]` 처럼 알려 준다), 빈 제목, 제목의 줄바꿈, 비밀번호 포함.

    Args:
        to: 받는 사람 주소 목록.
        cc: 참조 주소 목록.
        bcc: 숨은 참조 주소 목록.
        subject: 제목.
        body: 본문.
        password: smtp_password. 제목·본문에 그대로 들어 있는지 확인하는 데만 쓴다.

    Returns:
        envelope 수신자 목록. to, cc, bcc 순서로 합친 것이다.
    """
    recipients = [*to, *cc, *bcc]
    if len(recipients) > _MAX_RECIPIENTS:
        raise MailToolError("INVALID_INPUT", f"수신자는 to, cc, bcc 를 합쳐 {_MAX_RECIPIENTS}명 이하여야 합니다")
    for field, addresses in (("to", to), ("cc", cc), ("bcc", bcc)):
        for index, address in enumerate(addresses):
            if _parse_address(address) is None:
                raise MailToolError("INVALID_INPUT", f"{field}[{index}] 주소 형식이 올바르지 않습니다")
    if not subject.strip():
        raise MailToolError("INVALID_INPUT", "제목이 비어 있습니다")
    if _has_newline(subject):
        raise MailToolError("INVALID_INPUT", "제목에 줄바꿈 문자를 쓸 수 없습니다")
    # 보조 수단이다. 단순 포함 검사라 인코딩·분할로 우회할 수 있다(design.md 4.4).
    if password and (password in subject or password in body):
        raise MailToolError("INVALID_INPUT", "제목이나 본문에 smtp_password 값이 들어 있습니다")
    return recipients


def _parse_address(value: str) -> Address | None:
    """`local@domain` 형식의 ASCII 주소만 받는다.

    거부하는 예: 비ASCII 주소, 표시 이름(`홍길동 <a@b.com>`), 공백·주석이 섞인 주소,
    도메인이 빈 주소(`kim@`), `@` 가 둘 이상인 주소, 줄바꿈이 들어간 주소.

    Args:
        value: 검사할 주소 문자열.

    Returns:
        파싱한 주소. 형식이 맞지 않으면 None.
    """
    if not value.isascii():
        return None
    try:
        address = Address(addr_spec=value)
    except (ValueError, HeaderParseError, IndexError):
        # IndexError: "kim@" 처럼 도메인이 비면 파서가 던진다(Python 3.11.17 실측)
        return None
    # 공백과 주석은 파싱하며 빠지므로, 원문과 같을 때만 받는다
    return address if address.addr_spec == value else None


def _has_newline(value: str) -> bool:
    """CR 이나 LF 가 있는지 확인한다. 헤더 값에 들어가면 헤더 인젝션이 된다.

    Args:
        value: 헤더에 들어갈 값(제목, 표시 이름).

    Returns:
        CR 이나 LF 가 하나라도 있으면 True.
    """
    return "\r" in value or "\n" in value


def _build_message(
    sender: Address,
    from_name: str | None,
    to: list[str],
    cc: list[str],
    subject: str,
    body: str,
    body_format: str,
) -> EmailMessage:
    """발송할 메시지를 만든다.

    헤더 규칙:
        From: `from_name <from_address>`, from_name 이 없으면 주소만.
        To, Cc: 입력값. Bcc 는 넣지 않는다.
        Date: 현재 시각(로컬 시간대).
        Message-ID: 발신 도메인으로 만든다.
        Auto-Submitted: "auto-generated".
    비ASCII 헤더는 RFC 2047 로, 비ASCII 본문은 UTF-8 base64 또는 quoted-printable 로 인코딩한다.

    Args:
        sender: 검증한 발신 주소.
        from_name: 보내는 사람 표시 이름.
        to: 받는 사람 주소 목록.
        cc: 참조 주소 목록.
        subject: 제목.
        body: 본문.
        body_format: "html" 이면 text/html, 그 밖에는 text/plain 으로 넣는다.

    Returns:
        발송할 EmailMessage.
    """
    msg = EmailMessage(policy=_MESSAGE_POLICY)
    msg["From"] = Address(display_name=from_name or "", addr_spec=sender.addr_spec)
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    # Bcc 는 헤더에 넣지 않고 envelope 수신자에만 넣는다
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    # 기본값 socket.getfqdn() 은 Pod 이름을 노출하므로 발신 도메인을 쓴다
    msg["Message-ID"] = make_msgid(domain=sender.domain)
    # RFC 3834. 자동 응답 루프를 막는다
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(body, subtype="html" if body_format == "html" else "plain")
    return msg


def _connect(
    host: str, port: int, security: str, username: str | None, password: str | None, ehlo_name: str
) -> smtplib.SMTP:
    """1~3단계: 서버에 접속하고, 필요하면 TLS 로 전환한 뒤 로그인한다.

    단계별 오류 분류:
        접속·EHLO·STARTTLS 실패: CONNECTION_FAILED. STARTTLS 미지원은 전용 메시지로 알린다.
        로그인 거부·로그인 방식 없음: AUTH_FAILED. 거부 직후 서버가 연결을 끊어도 거부로 본다(_login).
        거부 응답 없는 로그인 중 연결 끊김·타임아웃: CONNECTION_FAILED. 아직 보낸 것이 없어 재시도해도 안전하다.
    실패하면 연결을 닫고 원래 예외 없이(`from None`) MailToolError 만 던진다.

    Args:
        host: 서버 호스트.
        port: 서버 포트.
        security: "starttls", "ssl", "none" 중 하나.
        username: 로그인 계정. 없으면 로그인하지 않는다.
        password: 비밀번호.
        ehlo_name: EHLO 로 보낼 이름. 기본값 socket.getfqdn() 은 Pod 이름을 노출하므로 호출자가 정한다.

    Returns:
        접속(과 로그인)을 마친 SMTP 연결. 호출자가 닫아야 한다.
    """
    smtp = None
    try:
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(
                host, port, local_hostname=ehlo_name, timeout=_SOCKET_TIMEOUT, context=ssl.create_default_context()
            )
        else:
            smtp = smtplib.SMTP(host, port, local_hostname=ehlo_name, timeout=_SOCKET_TIMEOUT)
        # login() 안에서 EHLO 가 실패하면 로그인 오류로 잘못 분류되므로 여기서 먼저 보낸다
        smtp.ehlo_or_helo_if_needed()
        if security == "starttls":
            # 서버가 STARTTLS 를 지원하지 않으면 평문으로 강등하지 않고 오류로 끝낸다
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
    except OSError as exc:
        if smtp is not None:
            smtp.close()
        if isinstance(exc, smtplib.SMTPNotSupportedError):
            raise MailToolError("CONNECTION_FAILED", "메일 서버가 STARTTLS 를 지원하지 않습니다") from None
        raise _connection_failed() from None

    if username and password:
        try:
            _login(smtp, username, password)
        except OSError as exc:
            smtp.close()
            raise _login_error(exc) from None
    return smtp


def _login(smtp: smtplib.SMTP, username: str, password: str) -> None:
    """smtplib.login() 과 같은 순서로 인증 방식을 시도하되, 서버의 거부 응답을 잃지 않는다.

    smtplib.login() 은 한 방식이 거부되면 다음 방식을 시도한다. 광고만 하고 실제로는 지원하지 않는 방식이 있는
    서버가 있기 때문이다. 그런데 Gmail 은 535 거부 직후 연결을 끊으므로(2026-10-14 실측) 다음 시도에서
    SMTPServerDisconnected 가 나고 535 응답이 버려진다. 그러면 틀린 비밀번호가 재시도 가능한 연결 오류로 보고된다.
    그래서 거부 응답이 나온 뒤의 연결 끊김은 그 거부 응답으로 보고한다.

    Args:
        smtp: EHLO 를 마친 연결.
        username: 로그인 계정.
        password: 비밀번호.

    Raises:
        smtplib.SMTPNotSupportedError: 서버가 AUTH 를 광고하지 않는다.
        smtplib.SMTPAuthenticationError: 서버가 로그인을 거부했다. 거부 뒤 연결이 끊긴 경우도 포함한다.
        smtplib.SMTPException: 서버가 광고한 방식 가운데 지원하는 것이 없다.
        OSError: 거부 응답 없이 연결이 끊기거나 타임아웃이 났다.
    """
    if not smtp.has_extn("auth"):
        raise smtplib.SMTPNotSupportedError("SMTP AUTH extension not supported by server.")
    advertised = smtp.esmtp_features["auth"].upper().split()
    # 내장 인증 함수(auth_plain 등)는 이 두 속성을 읽는다
    smtp.user, smtp.password = username, password
    rejection = None
    for mechanism in (m for m in _AUTH_PREFERENCE if m in advertised):
        try:
            smtp.auth(mechanism, getattr(smtp, "auth_" + mechanism.lower().replace("-", "_")))
            return
        except smtplib.SMTPAuthenticationError as exc:
            rejection = exc
        except smtplib.SMTPServerDisconnected:
            if rejection is None:
                raise
            # 앞 방식이 거부된 뒤 서버가 연결을 끊었다. 원인은 끊김이 아니라 거부다
            raise rejection from None
    raise rejection or smtplib.SMTPException("No suitable authentication method found.")


def _send(smtp: smtplib.SMTP, msg: EmailMessage, sender: str, recipients: list[str]) -> dict:
    """4~6단계: 메시지를 보내고 연결을 끝낸다.

    send_message() 가 MAIL FROM, RCPT TO, DATA 를 한 번에 수행한다.
    일부 수신자만 거부되면 예외 없이 거부 목록을 돌려준다. 오류 분류는 _send_error 를 따른다.

    Args:
        smtp: _connect 가 연 연결. 이 함수가 닫는다.
        msg: 보낼 메시지.
        sender: envelope 발신 주소(MAIL FROM).
        recipients: envelope 수신자 목록(RCPT TO). Bcc 포함.

    Returns:
        거부된 수신자. {주소: (SMTP 응답 코드, 응답 원문)} 형식이며, 응답 원문은 결과에 싣지 않는다.
    """
    try:
        refused = smtp.send_message(msg, from_addr=sender, to_addrs=recipients)
    except OSError as exc:
        smtp.close()
        raise _send_error(exc) from None
    # 이미 접수된 메일을 오류로 보고하면 Agent 가 재시도해 중복 발송한다
    _quit(smtp)
    return refused


def _quit(smtp: smtplib.SMTP) -> None:
    """QUIT 를 보내고 연결을 닫는다. QUIT 가 실패해도 예외를 내지 않고 연결만 닫는다.

    Args:
        smtp: 할 일을 마친 연결. 발송 또는 연결 테스트가 이미 끝난 뒤에만 부른다.
    """
    try:
        smtp.quit()
    except OSError:
        # 할 일은 이미 끝났다. 종료 단계 오류로 결과를 바꾸지 않는다
        smtp.close()


def _connection_failed() -> MailToolError:
    """접속 실패 오류. 원인(호스트, 인증서 내용 등)은 메시지에 넣지 않는다."""
    return MailToolError("CONNECTION_FAILED", "메일 서버에 연결하지 못했습니다")


def _login_error(exc: OSError) -> MailToolError:
    """로그인 단계 예외를 오류 코드로 바꾼다.

    `SMTPServerDisconnected` 는 `SMTPException` 의 하위 클래스이므로, 응답 오류 → 기타 SMTP 오류 → 연결 오류 순으로 가른다.

    Args:
        exc: smtp.login() 이 던진 예외.

    Returns:
        SMTP 응답 오류면 AUTH_FAILED(응답 코드 포함), AUTH 미지원·방식 없음이면 AUTH_FAILED,
        연결 끊김·타임아웃이면 CONNECTION_FAILED.
    """
    if isinstance(exc, smtplib.SMTPResponseException):
        return MailToolError("AUTH_FAILED", "메일 서버 로그인에 실패했습니다" + _status(exc.smtp_code, exc.smtp_error))
    if isinstance(exc, smtplib.SMTPException) and not isinstance(exc, smtplib.SMTPServerDisconnected):
        # 서버가 AUTH 를 지원하지 않거나(SMTPNotSupportedError) 맞는 인증 방식이 없다
        return MailToolError("AUTH_FAILED", "메일 서버가 지원하는 로그인 방식이 없습니다")
    # 끊김·타임아웃. 아직 보낸 것이 없으므로 재시도할 수 있는 연결 오류로 본다
    return _connection_failed()


def _send_error(exc: OSError) -> MailToolError:
    """전송 단계 예외를 오류 코드로 바꾼다.

    Args:
        exc: smtp.send_message() 가 던진 예외.

    Returns:
        전체 수신자 거부·발신자 거부·DATA 거부처럼 서버가 응답으로 거부했으면 SEND_REJECTED(응답 코드 포함).
        그 밖의 연결 끊김·타임아웃은 DELIVERY_UNKNOWN.
    """
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        code, response = next(iter(exc.recipients.values()))
        return MailToolError("SEND_REJECTED", "메일 서버가 발송을 거부했습니다" + _status(code, response))
    if isinstance(exc, smtplib.SMTPResponseException):
        return MailToolError("SEND_REJECTED", "메일 서버가 발송을 거부했습니다" + _status(exc.smtp_code, exc.smtp_error))
    # send_message() 는 MAIL FROM·RCPT TO·DATA 를 한 번에 수행해 DATA 전후를 구분할 수 없다
    return MailToolError("DELIVERY_UNKNOWN", "발송 결과를 확인하지 못했습니다. 이미 발송됐을 수 있습니다")


def _status(code: int, response: bytes | str) -> str:
    """오류 메시지 끝에 붙일 SMTP 코드 표기를 만든다.

    응답 원문은 계정·호스트를 담을 수 있으므로 버리고, SMTP 응답 코드와 확장 상태 코드만 남긴다.

    Args:
        code: SMTP 응답 코드. 예: 535
        response: 서버 응답 원문. 예: b"5.7.8 Username and Password not accepted"

    Returns:
        예: " (SMTP 535 5.7.8)". 확장 상태 코드가 없으면 " (SMTP 535)".
    """
    text = response.decode("ascii", "replace") if isinstance(response, bytes) else response
    match = _ENHANCED_STATUS.match(text)
    return f" (SMTP {code} {match.group()})" if match else f" (SMTP {code})"
