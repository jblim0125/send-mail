"""Gmail 실서비스 통합 시험 (docs/follow-ups.md F-04).

실제 Gmail SMTP 서버(smtp.gmail.com)로 메일 발송 Tool 을 시험한다. 고른 항목에 따라 실제로 메일이 발송된다.

실행 (mail-tool 폴더에서):
    python scripts/gmail_integration.py

입력:
    보내는 Gmail 주소, 앱 비밀번호, 받는 사람 주소를 차례로 묻는다. 반복 실행할 때는 환경 변수로 미리 줄 수 있다.
        GMAIL_TEST_ADDRESS   보내는 Gmail 주소. 로그인 계정이자 발신 주소다
        GMAIL_TEST_TO        받는 사람 주소. 쉼표로 여러 명
        GMAIL_APP_PASSWORD   앱 비밀번호. 없으면 화면에 보이지 않는 입력으로 묻는다
    비밀번호는 명령행 인자로 받지 않는다(ps 와 셸 기록에 남는다). 화면과 파일에 출력하지 않는다.

준비:
    Google 계정에서 2단계 인증을 켜고 앱 비밀번호(16자)를 만든다. 계정 비밀번호로는 로그인할 수 없다.
    받는 사람은 본인이 확인할 수 있는 주소를 쓴다.
"""

import getpass
import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

# mail-tool 폴더 밖에서 실행해도 tools 패키지를 찾게 한다
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.send_mail import MailToolError, check, send_mail  # noqa: E402

SMTP_HOST = "smtp.gmail.com"
# 선택 번호 -> (포트, 보안 방식, 설명)
SECURITY_OPTIONS = {
    "1": (587, "starttls", "587 STARTTLS"),
    "2": (465, "ssl", "465 SSL"),
}
# 로그인 실패 시험에 쓰는 값. 실제 비밀번호와 섞이지 않게 고정한다
WRONG_PASSWORD = "wrong-password-for-test"

SCENARIOS = {
    "1": "연결 테스트 check() (메일을 보내지 않음)",
    "2": "텍스트 메일 발송",
    "3": "HTML 메일 발송 (한글 제목, 표시 이름, 숨은 참조)",
    "4": "로그인 실패: 일부러 틀린 비밀번호 (메일을 보내지 않음)",
    "5": "없는 Gmail 주소로 발송 (수신자 거부·반송 확인)",
}
SENDING = {"2", "3", "5"}
DEFAULT_SCENARIOS = "1,2,4"

# 실행 뒤 사람이 직접 확인할 것 (F-04 검증 항목)
FOLLOW_UP = {
    "2": "받은편지함에 도착했는지, 제목과 본문 한글이 깨지지 않았는지",
    "3": "HTML·표시 이름이 맞는지. 받는 사람 메일함의 사본에 숨은 참조가 보이지 않는지(원본 보기에 Bcc 줄 없음),"
    " 숨은 참조 주소에도 도착했는지. 보낸 계정의 보낸편지함에 숨은 참조가 보이는 것은 정상",
    "4": "오류의 SMTP 코드를 docs/interface.md 5.2 표(Gmail 535 5.7.8)와 비교",
    "5": "결과가 sent 면 발신 계정으로 반송(Mail Delivery Subsystem) 메일이 오는지",
}


def ask(prompt: str, default: str = "") -> str:
    """한 줄 입력을 받는다. 비어 있으면 default 를 쓴다."""
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def ask_password() -> str:
    """앱 비밀번호를 환경 변수나 보이지 않는 입력으로 받는다. 앱 비밀번호는 4자씩 띄어 표시되므로 공백을 뺀다."""
    value = os.environ.get("GMAIL_APP_PASSWORD") or getpass.getpass("앱 비밀번호 (입력이 화면에 보이지 않습니다): ")
    return "".join(value.split())


def split_addresses(value: str) -> list[str]:
    return [address.strip() for address in value.split(",") if address.strip()]


def run(number: str, expected: str | None, func, **kwargs) -> tuple[str, str]:
    """시험 항목 하나를 실행하고 (결과, 판정)을 돌려준다. expected 가 None 이면 관찰만 한다."""
    print(f"\n[{number}] {SCENARIOS[number]}")
    try:
        result = func(**kwargs)
    except MailToolError as error:
        outcome = error.code
        print(f"    오류: {error}")
        # Tool 은 원래 예외를 모델에 넘기지 않지만(from None) __context__ 에는 남는다. 운영자 진단용으로만 보여 준다
        if error.__context__ is not None:
            print(f"    원인 (로컬 진단용): {type(error.__context__).__name__}: {error.__context__}")
    else:
        outcome = result["status"]
        print(f"    결과: {json.dumps(result, ensure_ascii=False)}")
    if expected is None:
        verdict = "관찰"
    else:
        verdict = "기대와 같음" if outcome == expected else f"기대와 다름 (기대 {expected})"
    print(f"    판정: {verdict}")
    return outcome, verdict


def main() -> int:
    print(f"Gmail 실서비스 시험. {SMTP_HOST} 로 실제 메일을 보낸다. Ctrl+C 로 언제든 중단할 수 있다.\n")

    address = ask("보내는 Gmail 주소 (로그인 계정)", os.environ.get("GMAIL_TEST_ADDRESS", ""))
    password = ask_password()
    to = split_addresses(ask("받는 사람 (쉼표로 여러 명)", os.environ.get("GMAIL_TEST_TO", address)))
    from_name = ask("보내는 사람 표시 이름", "메일 Tool 시험")
    for key, (_, _, label) in SECURITY_OPTIONS.items():
        print(f"  {key}) {label}")
    port, security, label = SECURITY_OPTIONS.get(ask("연결 방식", "1"), SECURITY_OPTIONS["1"])

    print("\n시험 항목:")
    for key, title in SCENARIOS.items():
        print(f"  {key}) {title}")
    chosen = [key for key in split_addresses(ask("실행할 번호 (쉼표로 여러 개)", DEFAULT_SCENARIOS)) if key in SCENARIOS]
    # 숨은 참조를 보낸 계정 자신으로 보내면, 보낸 계정 쪽 사본에는 숨은 참조가 보이는 것이 정상이라 누출 여부를 가릴 수 없다
    bcc = split_addresses(ask("\n[3] 에 쓸 숨은 참조 주소 (받는 사람·보낸 계정과 다른, 확인 가능한 주소)")) if "3" in chosen else []

    sends = sum(1 for key in chosen if key in SENDING)
    print(f"\n서버 {SMTP_HOST}:{port} ({label}), 계정 {address}, 받는 사람 {', '.join(to)}")
    if sends and ask(f"실제로 메일 {sends}통을 보낸다. 계속할까 (y/N)", "N").lower() != "y":
        print("중단했다. 메일을 보내지 않았다.")
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    connection = {
        "smtp_host": SMTP_HOST,
        "smtp_port": port,
        "smtp_security": security,
        "smtp_username": address,
        "smtp_password": password,
        "from_name": from_name,
    }
    mail = {**connection, "to": to}
    note = f"메일 발송 Tool 실서비스 시험(F-04)으로 보낸 메일입니다.\n보낸 시각: {stamp}\n"

    results = {}
    for key in chosen:
        if key == "1":
            results[key] = run(key, "ok", check, **connection)
        elif key == "2":
            results[key] = run(key, "sent", send_mail, **mail, subject=f"[메일 Tool 시험] 텍스트 {stamp}", body=note)
        elif key == "3":
            html = f"<h3>메일 Tool 시험</h3><p>{note.replace(chr(10), '<br>')}</p><ul><li>HTML 본문</li></ul>"
            results[key] = run(
                key, "sent", send_mail, **mail, bcc=bcc,
                subject=f"[메일 Tool 시험] HTML 한글 제목 {stamp}", body=html, body_format="html",
            )
        elif key == "4":
            results[key] = run(key, "AUTH_FAILED", check, **{**connection, "smtp_password": WRONG_PASSWORD})
        elif key == "5":
            nobody = f"no-such-user-{secrets.token_hex(8)}@gmail.com"
            results[key] = run(
                key, None, send_mail, **{**mail, "to": [nobody]},
                subject=f"[메일 Tool 시험] 없는 주소 {stamp}", body=note,
            )

    print("\n요약")
    for key, (outcome, verdict) in results.items():
        print(f"  [{key}] {outcome:<16} {verdict:<20} {SCENARIOS[key]}")
    follow_up = [f"  [{key}] {FOLLOW_UP[key]}" for key in results if key in FOLLOW_UP]
    if follow_up:
        print("\n직접 확인할 것 (결과는 docs/interface.md 5.2·7장과 F-04 에 반영)")
        print("\n".join(follow_up))
        print("  공통: Google 에서 보안 알림 메일이 왔는지. 데이터센터 IP 차단 여부는 k8s 샌드박스에서 실행해야 확인된다")
    return 0 if all(verdict in ("기대와 같음", "관찰") for _, verdict in results.values()) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n중단했다.")
        sys.exit(130)
