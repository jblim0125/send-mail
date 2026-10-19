"""사내 무인증 릴레이 통합 시험 (docs/follow-ups.md F-04).

사내 메일 릴레이로 메일 발송 Tool 을 로그인 없이(smtp_auth="none") 시험한다. 고른 항목에 따라 실제로 메일이 발송된다.

실행 (mail-tool 폴더에서):
    python scripts/relay_integration.py

입력:
    릴레이 호스트, 보내는 주소, 받는 사람 주소를 차례로 묻는다. 반복 실행할 때는 환경 변수로 미리 줄 수 있다.
        RELAY_TEST_HOST   릴레이 호스트. 사내 접속 정보이므로 코드·문서·커밋에 적지 않는다
        RELAY_TEST_FROM   보내는 주소(smtp_username). 릴레이가 허용하는 사내 도메인 주소
        RELAY_TEST_TO     받는 사람 주소. 쉼표로 여러 명
    비밀번호는 받지 않는다.

준비:
    실행하는 PC·서버의 송신 IP 가 릴레이에 허용돼 있어야 한다. 허용 기준은 메일 관리자에게 확인한다(design.md 8장).
    받는 사람은 본인이 확인할 수 있는 주소를 쓴다.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# mail-tool 폴더 밖에서 실행해도 tools 패키지를 찾게 한다
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.send_mail import MailToolError, check, send_mail  # noqa: E402

# 2026-10-19 실측: 사내 릴레이는 25번에서 STARTTLS 를 지원하고 AUTH 를 광고하지 않는다
SMTP_PORT = 25
SMTP_SECURITY = "starttls"
# 오설정 시험에 쓰는 값. 서버가 AUTH 를 광고하지 않으면 Tool 은 로그인을 시도하지 않으므로 전송되지 않는다
DUMMY_PASSWORD = "dummy-password-for-test"

SCENARIOS = {
    "1": "연결 테스트 check() (메일을 보내지 않음)",
    "2": "텍스트 메일 발송",
    "3": "HTML 메일 발송 (한글 제목, 표시 이름)",
    "4": "오설정: smtp_auth=login 으로 연결 테스트 (메일을 보내지 않음)",
}
SENDING = {"2", "3"}
DEFAULT_SCENARIOS = "1,2,4"

# 실행 뒤 사람이 직접 확인할 것 (F-04 검증 항목)
FOLLOW_UP = {
    "2": "받은편지함(없으면 스팸함)에 도착했는지, 제목과 본문 한글이 깨지지 않았는지."
    " 서버는 CHARSET=EUC-KR 을 광고하지만 Tool 은 UTF-8 로 보낸다",
    "3": "HTML 렌더링과 한글 표시 이름이 맞는지",
    "4": "오류가 AUTH_FAILED '지원하는 로그인 방식이 없습니다' 인지 (docs/interface.md 5.2)",
}


def ask(prompt: str, default: str = "") -> str:
    """한 줄 입력을 받는다. 비어 있으면 default 를 쓴다."""
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def split_addresses(value: str) -> list[str]:
    return [address.strip() for address in value.split(",") if address.strip()]


def run(number: str, expected: str, func, **kwargs) -> tuple[str, str]:
    """시험 항목 하나를 실행하고 (결과, 판정)을 돌려준다."""
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
    verdict = "기대와 같음" if outcome == expected else f"기대와 다름 (기대 {expected})"
    print(f"    판정: {verdict}")
    return outcome, verdict


def main() -> int:
    print("사내 무인증 릴레이 시험. 로그인 없이 실제 메일을 보낸다. Ctrl+C 로 언제든 중단할 수 있다.\n")

    host = ask("릴레이 호스트", os.environ.get("RELAY_TEST_HOST", ""))
    if not host:
        print("릴레이 호스트가 없어 중단했다.")
        return 1
    sender = ask("보내는 주소 (릴레이가 허용하는 사내 도메인)", os.environ.get("RELAY_TEST_FROM", ""))
    to = split_addresses(ask("받는 사람 (쉼표로 여러 명)", os.environ.get("RELAY_TEST_TO", sender)))
    from_name = ask("보내는 사람 표시 이름", "메일 Tool 시험")

    print("\n시험 항목:")
    for key, title in SCENARIOS.items():
        print(f"  {key}) {title}")
    chosen = [key for key in split_addresses(ask("실행할 번호 (쉼표로 여러 개)", DEFAULT_SCENARIOS)) if key in SCENARIOS]

    sends = sum(1 for key in chosen if key in SENDING)
    print(f"\n서버 {host}:{SMTP_PORT} ({SMTP_SECURITY}, 로그인 없음), 보내는 주소 {sender}, 받는 사람 {', '.join(to)}")
    if sends and ask(f"실제로 메일 {sends}통을 보낸다. 계속할까 (y/N)", "N").lower() != "y":
        print("중단했다. 메일을 보내지 않았다.")
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    connection = {
        "smtp_host": host,
        "smtp_port": SMTP_PORT,
        "smtp_security": SMTP_SECURITY,
        "smtp_auth": "none",
        "smtp_username": sender,
        "from_name": from_name,
    }
    mail = {**connection, "to": to}
    note = f"메일 발송 Tool 사내 릴레이 시험(F-04)으로 보낸 메일입니다.\n보낸 시각: {stamp}\n"

    results = {}
    for key in chosen:
        if key == "1":
            results[key] = run(key, "ok", check, **connection)
        elif key == "2":
            results[key] = run(key, "sent", send_mail, **mail, subject=f"[메일 Tool 시험] 사내 릴레이 {stamp}", body=note)
        elif key == "3":
            html = f"<h3>메일 Tool 시험</h3><p>{note.replace(chr(10), '<br>')}</p><ul><li>HTML 본문</li></ul>"
            results[key] = run(
                key, "sent", send_mail, **mail,
                subject=f"[메일 Tool 시험] 사내 릴레이 HTML 한글 제목 {stamp}", body=html, body_format="html",
            )
        elif key == "4":
            results[key] = run(
                key, "AUTH_FAILED", check, **{**connection, "smtp_auth": "login", "smtp_password": DUMMY_PASSWORD}
            )

    print("\n요약")
    for key, (outcome, verdict) in results.items():
        print(f"  [{key}] {outcome:<16} {verdict:<20} {SCENARIOS[key]}")
    follow_up = [f"  [{key}] {FOLLOW_UP[key]}" for key in results if key in FOLLOW_UP]
    if follow_up:
        print("\n직접 확인할 것 (결과는 docs/interface.md 5.2·7장과 F-04 에 반영)")
        print("\n".join(follow_up))
        if sends:
            print("  공통: 원본 보기에서 SPF·DKIM·DMARC 결과. k8s 송신 IP 허용 여부는 샌드박스에서 실행해야 확인된다")
    return 0 if all(verdict == "기대와 같음" for _, verdict in results.values()) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n중단했다.")
        sys.exit(130)
