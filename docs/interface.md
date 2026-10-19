# 메일 발송 Tool 인터페이스 명세

| 항목           | 값                                                                |
| -------------- | ----------------------------------------------------------------- |
| 문서 상태      | 1차 구현 완료 (`tools/send_mail.py`)                              |
| Tool 함수      | `send_mail`                                                       |
| `TOOL_VERSION` | `1.0.0` (표기 확인 필요, 2장)                                     |
| 관련 문서      | `docs/design.md`, `docs/follow-ups.md`, `docs/Tool-Dev-Guides.md` |

## 1. 개요

SMTP 서버로 메일을 발송하는 Generic Agent용 Tool이다. 입력은 두 종류로 나뉜다.

- 설정 항목: 메일 서버, 로그인 방식, 계정, 보내는 사람 표시 이름. 운영자가 한 번 등록하고 여러 Agent가 재사용한다.
  저장할 때 연결 테스트를 실행한다.
- 호출 항목: 수신자, 제목, 본문. 모델이 Tool을 호출할 때 입력한다.

함수는 두 항목을 합친 값을 키워드 인자로 받는다. 발신 주소는 `smtp_username`으로 고정되며, 다른 주소로는 보낼 수 없다.
사내 무인증 릴레이는 `smtp_auth`를 `none`으로 두어 비밀번호 없이 보낸다.
특정 메일 서비스에 묶이지 않으며, 서비스별 설정값은 7장에 정리했다. 첨부 파일은 지원하지 않는다.

> 설정 항목을 모델에 보여주지 않는 처리와 기록 마스킹은 후속 과제다(`follow-ups.md` F-01~F-03). 그 전까지는 발송 전용 계정과 앱 비밀번호를 쓴다(9.1).
> 설정 등록과 선택, 연결 테스트는 플랫폼에 아직 없는 기능이며 이 명세는 제안이다. 현재 플랫폼은 입력 규격을 `input_schema` 하나만 받는다(3.5).

| 독자                  | 볼 곳                                                                                    |
| --------------------- | ---------------------------------------------------------------------------------------- |
| Tool 등록 담당자      | 2장 등록 정보, 3장 입력                                                                  |
| 설정 등록 담당자      | 2.2 연결 테스트, 3.1 설정 항목, 6.1 설정 등록 예, 7장 서비스별 설정값, 9.1 비밀번호 취급 |
| Agent·프롬프트 작성자 | 3.2 호출 항목, 4장 출력, 5장 오류, 6장 사용 예                                           |
| Tool 개발자           | 8장 개발 환경, 9장 제약과 주의사항                                                       |

## 2. 등록 정보

| 항목              | 값                               | 비고                                                                                     |
| ----------------- | -------------------------------- | ---------------------------------------------------------------------------------------- |
| `name`            | `메일 발송`                      | 사람이 읽는 표시 이름 (가안). 모델에 노출되지 않음                                       |
| `remote_tool`     | `send_mail`                      | 모델에 노출되는 Tool 이름. `builtin` 등록표 조회 키 (API 문서 §8)                        |
| `kind`            | `builtin`                        | 가이드 3장                                                                               |
| `impl`            | `tools.send_mail:send_mail`      | 공통 Python Tool 은 `generic-agent` `default_tools` 에 둔다(API 문서 §8). 경로 확인 필요 |
| 연결 테스트       | `tools.send_mail:check`          | `tool_def`에 등록할 열이 없음. 등록 방식 확인 필요 (2.2)                                 |
| 등록 버전         | `1.0.0`                          | `TOOL_VERSION`과 같아야 함. Registry 는 정수 문자열이라 표기 확인 필요                   |
| 입력 규격         | 3.3 설정 스키마, 3.4 호출 스키마 | 현재 플랫폼은 `input_schema` 하나만 받음 (3.5)                                           |
| `timeout_seconds` | `30`                             | 등록 필수 열. 소켓 타임아웃(10초)의 3배                                                  |
| 네트워크 목적지   | 사용할 SMTP 서버의 host:port     | 예: `smtp.gmail.com:587`, 사내 릴레이는 `<사내 릴레이 호스트>:25`                        |
| 외부 패키지       | 없음                             | 표준 라이브러리만 사용                                                                   |
| 자격 사용         | 가이드 10장 예외                 | `smtp_auth=login`일 때만. `none`이면 자격 없음 (2026-10-14, 10-19 결정)                  |

### 2.1. 모델에 보여줄 Tool 설명

등록 시 `description`에 아래 문안을 넣는다. 이 값은 그대로 모델에 Tool 설명으로 전달된다(API 문서 §8.1).
재시도 금지 규칙은 중복 발송을 막기 위해 넣었다. Tool은 이전 호출을 기억하지 못한다.

```text
메일을 발송합니다. 받는 사람(to, cc, bcc), 제목, 본문을 받습니다.
- 메일 서버와 보내는 사람은 설정에 고정되어 있어 지정할 수 없습니다.
- 주소는 user@example.com 형식만 씁니다. 첨부 파일은 보낼 수 없습니다.
- 결과의 refused 에 있는 주소는 메일을 받지 못했습니다.
- CONFIG_ERROR, AUTH_FAILED 는 설정 문제입니다. 다시 호출하지 말고 사용자에게 알리세요.
- DELIVERY_UNKNOWN 이거나 시간 초과로 끝나면 이미 발송됐을 수 있습니다.
  다시 호출하지 말고 사용자에게 알리세요.
```

### 2.2. 연결 테스트 함수

플랫폼이 설정을 저장할 때와 호스트·비밀번호를 바꿀 때 호출하는 것을 제안한다. 메일을 보내지 않고 접속·STARTTLS·로그인까지만 확인한다.
모델에 노출하는 Tool이 아니다. 현재 `tool_def`에는 연결 테스트 진입점을 담을 열이 없고, 설정 저장 시점에 Sandbox에서 실행할 경로도 없다(확인 필요).

```python
def check(
    smtp_host: str,
    smtp_username: str,
    smtp_port: int = 587,
    smtp_security: str = "starttls",
    smtp_auth: str = "login",
    smtp_password: str | None = None,
    from_name: str | None = None,
) -> dict: ...
```

설정 항목 전체를 받아 `send_mail()`과 같은 규칙(3.6)으로 검증한다. `from_name`은 검증에만 쓴다.
`smtp_auth`가 `none`이면 접속과 STARTTLS까지만 확인한다. 릴레이가 송신 IP를 허용하는지는 메일을 보내야 알 수 있으므로,
설정을 등록한 뒤 시험 발송으로 확인한다(`design.md` 4.8).
성공하면 `{"status": "ok"}`를 반환한다. 실패하면 `send_mail()`과 같은 형식의 예외를 던지며,
코드는 `CONFIG_ERROR`, `CONNECTION_FAILED`, `AUTH_FAILED` 중 하나다(5장).

## 3. 입력

### 3.1. 설정 항목

운영자가 입력하며 모델에는 보이지 않는다. 메일 서버, 로그인 방식, 계정, 보내는 사람 표시 이름이다.
이름을 붙여 한 번 등록하고 여러 Agent가 재사용한다.
저장할 때와 호스트·비밀번호를 바꿀 때 연결 테스트(2.2)를 실행한다. Agent에 Tool을 붙일 때는 등록한 설정을 고른다.

| 필드            | 타입    | 필수   | 기본값     | 설명                                                           |
| --------------- | ------- | ------ | ---------- | -------------------------------------------------------------- |
| `smtp_host`     | string  | O      | -          | SMTP 서버 호스트 (호스트명 또는 IPv4)                          |
| `smtp_port`     | integer | X      | `587`      | 25, 465, 587, 2525 중 하나                                     |
| `smtp_security` | string  | X      | `starttls` | `starttls` 또는 `ssl`                                          |
| `smtp_auth`     | string  | X      | `login`    | `login`(계정 로그인) 또는 `none`(무인증 릴레이)                |
| `smtp_username` | string  | O      | -          | 보내는 사람 주소. `login`이면 로그인 계정. `local@domain` 형식 |
| `smtp_password` | string  | 조건부 | -          | `login`이면 필수, `none`이면 넣지 않음. 민감 항목              |
| `from_name`     | string  | X      | -          | 보내는 사람 표시 이름                                          |

**발신 주소.** 발신 주소를 따로 받지 않고 `smtp_username`을 From과 envelope 발신자로 쓴다. 계정과 다른 주소로는 보낼 수 없다.
`smtp_auth`가 `none`이어도 같다. 이때 `smtp_username`은 로그인에 쓰이지 않고 발신 주소로만 쓰인다.
그래서 계정이 메일 주소가 아닌 서비스(AWS SES, SendGrid)는 쓸 수 없다(7장). 결정 근거는 `design.md` 3.3에 있다.

### 3.2. 호출 항목

모델이 Tool을 호출할 때 입력한다.

| 필드          | 타입        | 필수 | 기본값 | 설명                                         |
| ------------- | ----------- | ---- | ------ | -------------------------------------------- |
| `to`          | string 배열 | O    | -      | 받는 사람, 1~20개                            |
| `cc`          | string 배열 | X    | `[]`   | 참조, 0~20개                                 |
| `bcc`         | string 배열 | X    | `[]`   | 숨은 참조, 0~20개. 메일 헤더에 나타나지 않음 |
| `subject`     | string      | O    | -      | 제목, 1~200자, 줄바꿈 불가                   |
| `body`        | string      | O    | -      | 본문, 1~100,000자                            |
| `body_format` | string      | X    | `text` | `text` 또는 `html`                           |

### 3.3. 설정 스키마

`smtp_password`에는 `format: password`와 `writeOnly: true`를 함께 단다. Pydantic `SecretStr`이 만드는 스키마와 같은 조합이다.
`format: password`는 스키마 기반 폼 렌더러가 입력란을 가리는 기준이고, `writeOnly`는 조회 응답에 값을 돌려주지 않는다는 표시다.
둘 다 검증 키워드가 아니라 주석이므로, 실제로 가리고 암호화하는 일은 플랫폼이 해야 한다(`follow-ups.md` F-01, F-03).

`smtp_password`의 조건부 필수는 `if`/`then`/`else`(JSON Schema draft 7 이상)로 표현한다.
플랫폼 검증기가 이 키워드를 지원하는지는 확인 필요다. 함수도 같은 규칙을 검사한다(3.6).

**설정 스키마.**

```json
{
  "type": "object",
  "properties": {
    "smtp_host": {
      "type": "string",
      "description": "SMTP 서버 호스트. 예: smtp.gmail.com",
      "minLength": 1,
      "maxLength": 253,
      "pattern": "^[A-Za-z0-9.-]+$"
    },
    "smtp_port": {
      "type": "integer",
      "description": "SMTP 서버 포트",
      "enum": [25, 465, 587, 2525],
      "default": 587
    },
    "smtp_security": {
      "type": "string",
      "description": "연결 보안 방식. starttls(보통 587), ssl(보통 465)",
      "enum": ["starttls", "ssl"],
      "default": "starttls"
    },
    "smtp_auth": {
      "type": "string",
      "description": "로그인 방식. login(계정 로그인), none(무인증 릴레이)",
      "enum": ["login", "none"],
      "default": "login"
    },
    "smtp_username": {
      "type": "string",
      "description": "보내는 사람 메일 주소. smtp_auth 가 login 이면 로그인 계정",
      "format": "email",
      "maxLength": 254
    },
    "smtp_password": {
      "type": "string",
      "description": "SMTP 로그인 비밀번호 또는 앱 비밀번호. smtp_auth 가 login 일 때만 입력",
      "minLength": 1,
      "maxLength": 1024,
      "format": "password",
      "writeOnly": true
    },
    "from_name": {
      "type": "string",
      "description": "보내는 사람 표시 이름",
      "minLength": 1,
      "maxLength": 100
    }
  },
  "required": ["smtp_host", "smtp_username"],
  "if": {
    "properties": { "smtp_auth": { "const": "none" } },
    "required": ["smtp_auth"]
  },
  "then": { "not": { "required": ["smtp_password"] } },
  "else": { "required": ["smtp_password"] },
  "additionalProperties": false
}
```

### 3.4. 호출 스키마

`additionalProperties: false`이므로 모델이 설정 항목(예: `smtp_host`)을 넘기면 규격 위반으로 거부된다.
플랫폼이 호출 항목을 이 스키마로 따로 검증할 때만 성립한다.

```json
{
  "type": "object",
  "properties": {
    "to": {
      "type": "array",
      "description": "받는 사람 메일 주소 목록",
      "items": { "type": "string", "format": "email", "maxLength": 254 },
      "minItems": 1,
      "maxItems": 20,
      "uniqueItems": true
    },
    "cc": {
      "type": "array",
      "description": "참조 메일 주소 목록",
      "items": { "type": "string", "format": "email", "maxLength": 254 },
      "maxItems": 20,
      "uniqueItems": true,
      "default": []
    },
    "bcc": {
      "type": "array",
      "description": "숨은 참조 메일 주소 목록. 다른 수신자에게 보이지 않음",
      "items": { "type": "string", "format": "email", "maxLength": 254 },
      "maxItems": 20,
      "uniqueItems": true,
      "default": []
    },
    "subject": {
      "type": "string",
      "description": "메일 제목. 줄바꿈 불가",
      "minLength": 1,
      "maxLength": 200
    },
    "body": {
      "type": "string",
      "description": "메일 본문",
      "minLength": 1,
      "maxLength": 100000
    },
    "body_format": {
      "type": "string",
      "description": "본문 형식",
      "enum": ["text", "html"],
      "default": "text"
    }
  },
  "required": ["to", "subject", "body"],
  "additionalProperties": false
}
```

### 3.5. 함수 시그니처와 등록 형식

함수는 두 스키마(설정, 호출)를 합친 값을 키워드 인자로 받는다. 매개변수 이름은 스키마의 속성 이름과 같다(가이드 5장).

```python
def send_mail(
    smtp_host: str,
    smtp_username: str,
    to: list[str],
    subject: str,
    body: str,
    smtp_port: int = 587,
    smtp_security: str = "starttls",
    smtp_auth: str = "login",
    smtp_password: str | None = None,
    from_name: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    body_format: str = "text",
) -> dict: ...
```

**현재 플랫폼.** `registry.tool_def`는 입력 규격을 `input_schema` 하나만 받는다(Governance `storage.md` §9.3).
Worker는 이 값을 그대로 모델에 전달하는 Tool 인자 규격(`args_schema`)으로 쓴다(API 문서 §8.1).
따라서 지금 등록하려면 두 스키마의 `properties`와 `required`를 합쳐야 하고, 그러면 설정 항목이 모델에 노출된다.
F-02가 끝날 때까지 `design.md` 3.4의 위험이 그대로 남는다.

**제안하는 전달 방식 (미구현).** 플랫폼은 샌드박스를 warm pool로 미리 띄우므로 설정값을 Pod 생성 시점에 넣을 수 없다.
Worker가 Tool 호출 시점에 설정 조회 API에서 값을 받아 자식 프로세스에 stdin으로 넘기고, 자식 프로세스가 이 값으로 함수를 호출하는 방식을 제안한다.
설정 조회 API는 아직 없다. Tool 코드는 전달 경로와 무관하게 키워드 인자만 받는다(`tool-설정-전달-조사.md` 3.4, 3.5).

### 3.6. 추가 검증 규칙

JSON Schema로 표현할 수 없는 규칙은 함수에서 검사한다. 위반하면 접속을 시도하지 않는다.
설정 항목 위반은 `CONFIG_ERROR`, 호출 항목 위반은 `INVALID_INPUT`으로 구분한다. 설정 항목 오류는 모델이 고칠 수 없기 때문이다.

**설정 항목 (`CONFIG_ERROR`).**

1. `smtp_username`은 `local@domain` 형식이고 ASCII만 쓴다. 발신 주소로 쓰기 때문이다.
2. `smtp_auth`가 `login`이면 `smtp_password`는 빈 값일 수 없고, `none`이면 `smtp_password`를 넣을 수 없다.
3. `from_name`에는 줄바꿈 문자를 쓸 수 없다.
4. `smtp_port`는 25, 465, 587, 2525 중 하나이고, `smtp_security`는 `starttls`, `ssl` 중 하나, `smtp_auth`는 `login`, `none` 중 하나다.
   스키마에도 있는 규칙이지만, 현재 플랫폼은 설정 스키마를 따로 검증하지 않으므로 함수에서도 검사한다(내부망 서비스 접속 방지).

**호출 항목 (`INVALID_INPUT`).**

1. 주소는 `local@domain` 형식이고 ASCII만 쓴다. 표시 이름(`홍길동 <kim@example.com>`)과 한글 주소는 받지 않는다.
2. `to`, `cc`, `bcc`를 합친 수신자는 20명 이하다.
3. 제목은 앞뒤 공백을 뺀 뒤 비어 있으면 안 되고, 줄바꿈 문자를 쓸 수 없다.
4. 제목이나 본문에 `smtp_password` 값이 들어 있으면 거부한다(`smtp_auth=login`일 때).

## 4. 출력

### 4.1. 출력 형식

| 필드             | 타입        | 설명                                                |
| ---------------- | ----------- | --------------------------------------------------- |
| `status`         | string      | 항상 `sent`. 실패는 예외로 전달 (5장)               |
| `message_id`     | string      | 발송한 메일의 `Message-ID`. 메일 서버 기록 추적용   |
| `accepted_count` | integer     | 서버가 접수한 수신자 수                             |
| `refused`        | object 배열 | 서버가 거부한 수신자. 항목은 `address`, `smtp_code` |

```json
{
  "status": "sent",
  "message_id": "<179195365473.77991.10873220603335278444@example.com>",
  "accepted_count": 2,
  "refused": [
    { "address": "old-user@example.com", "smtp_code": 550 }
  ]
}
```

### 4.2. 결과 해석

- 예외 없이 반환되면 메일 서버가 최소 1명 이상의 수신자를 접수한 것이다.
- 접수는 최종 배달을 뜻하지 않는다. 배달 실패(반송)는 나중에 발신 주소로 따로 도착한다.
  예를 들어 Gmail 은 없는 주소도 접수한 뒤 `550 5.1.1` 반송 메일을 보낸다(2026-10-14 실측).
  반송 메일의 `X-Original-Message-ID` 가 결과의 `message_id` 와 같으므로 이 값으로 원래 발송과 짝지을 수 있다.
  사내 릴레이는 외부로 중계할 때 envelope 발신자를 빈 값(`<>`)으로 바꾸므로, 반송이 발신 주소로 오지 않을 수 있다(확인 필요).
- `refused`가 비어 있지 않으면 그 주소에는 발송되지 않았다. 나머지 수신자에게는 이미 발송됐다.

## 5. 오류

### 5.1. 오류 코드

실패하면 함수가 예외를 던지고, 그 호출만 오류로 끝난다(가이드 9장). 예외 메시지는 `[코드] 설명` 형식이다.
메시지에는 비밀번호, 계정, 호스트, 서버 응답 원문을 넣지 않는다. 서버가 응답한 경우 SMTP 코드와 확장 상태 코드만 붙인다.

| 코드                | 발생 조건                                                                                  | Agent 대응                                   |
| ------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------- |
| `CONFIG_ERROR`      | 설정 항목 규칙 위반 (3.6)                                                                  | 다시 호출하지 않고 사용자에게 설정 확인 요청 |
| `INVALID_INPUT`     | 호출 항목 규칙 위반 (3.6)                                                                  | 입력을 고쳐 다시 호출                        |
| `CONNECTION_FAILED` | 서버 접속·TLS 실패, 인증서 검증 실패, 타임아웃, 로그인 중 연결 끊김 (발송 전)              | 잠시 뒤 1회 재시도, 반복되면 사용자에게 알림 |
| `AUTH_FAILED`       | 계정·비밀번호 불일치, 앱 비밀번호 필요, 서버가 로그인 미지원(무인증 릴레이에 `login` 설정) | 다시 호출하지 않고 사용자에게 설정 확인 요청 |
| `SEND_REJECTED`     | 서버가 발신자·수신자·메시지를 거부, 릴레이가 송신 IP 미허용                                | 다시 호출하지 않고 사용자에게 알림           |
| `DELIVERY_UNKNOWN`  | 발송 도중 연결 끊김·타임아웃                                                               | 다시 호출하지 않고 사용자에게 확인           |
| (플랫폼) 규격 위반  | 입력이 JSON Schema에 맞지 않음                                                             | 입력을 고쳐 다시 호출                        |
| (플랫폼) 시간 초과  | `timeout_seconds` 초과                                                                     | `DELIVERY_UNKNOWN`과 같이 처리               |

메시지 예시는 다음과 같다.

```text
[CONFIG_ERROR] smtp_username 은 메일 주소 형식이어야 합니다
[CONFIG_ERROR] smtp_password 를 설정해야 합니다
[CONFIG_ERROR] smtp_auth 가 none 이면 smtp_password 를 설정할 수 없습니다
[INVALID_INPUT] to[0] 주소 형식이 올바르지 않습니다
[INVALID_INPUT] 제목이 비어 있습니다
[INVALID_INPUT] 제목이나 본문에 smtp_password 값이 들어 있습니다
[CONNECTION_FAILED] 메일 서버에 연결하지 못했습니다
[CONNECTION_FAILED] 메일 서버가 STARTTLS 를 지원하지 않습니다
[AUTH_FAILED] 메일 서버 로그인에 실패했습니다 (SMTP 534 5.7.9)
[AUTH_FAILED] 메일 서버가 지원하는 로그인 방식이 없습니다
[SEND_REJECTED] 메일 서버가 발송을 거부했습니다 (SMTP 550 5.7.1)
[DELIVERY_UNKNOWN] 발송 결과를 확인하지 못했습니다. 이미 발송됐을 수 있습니다
```

### 5.2. 자주 보는 로그인·릴레이 오류

서비스 응답 형식은 실측으로 최종 확인한다(`follow-ups.md` F-04). Gmail `535 5.7.8`은 2026-10-14 실측으로 확인했다.
사내 릴레이의 `AUTH_FAILED`는 2026-10-19 실측이다. `530 5.7.0`과 `550 5.7.1`은 일반적인 응답이며 실측하지 않았다(서버마다 다름).
Gmail 은 이 응답 직후 연결을 끊지만, Tool 은 이를 연결 오류가 아니라 `AUTH_FAILED`로 보고한다(`design.md` 4.6).

| 응답 코드                     | 서비스      | 의미                                             | 조치                                     |
| ----------------------------- | ----------- | ------------------------------------------------ | ---------------------------------------- |
| `534 5.7.9`                   | Gmail       | 앱 비밀번호가 필요함                             | 계정 비밀번호 대신 앱 비밀번호 설정      |
| `535 5.7.8`                   | Gmail       | 계정 또는 비밀번호 불일치                        | 계정 주소와 앱 비밀번호 확인             |
| `535 5.7.139`                 | M365        | 테넌트 또는 사서함에서 SMTP AUTH 비활성화        | 관리자에게 SMTP AUTH 허용 요청           |
| `AUTH_FAILED` (코드 없음)     | 사내 릴레이 | 서버가 AUTH 미지원, `smtp_auth=login`으로 설정함 | `smtp_auth`를 `none`으로, 비밀번호 삭제  |
| `530 5.7.0` (`SEND_REJECTED`) | 일반        | 인증 필요 서버에 `smtp_auth=none`으로 보냄       | `smtp_auth`를 `login`으로, 비밀번호 설정 |
| `550 5.7.1` (`SEND_REJECTED`) | 일반        | 릴레이 거부. 송신 IP 미허용 등                   | 메일 관리자에게 송신 IP 허용 요청        |

## 6. 사용 예

아래 계정·비밀번호·주소는 모두 예시 값이다.

### 6.1. 설정 등록 예

운영자가 등록하는 설정 항목이다. 설정 등록 기능과 F-03이 구현되면, 저장할 때 연결 테스트가 실행되고 이후 화면에는 비밀번호가 가림값으로만 표시된다.

```json
{
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_security": "starttls",
  "smtp_username": "agent-sender@example.com",
  "smtp_password": "<앱 비밀번호>",
  "from_name": "AI Agent"
}
```

이 설정으로 보내는 메일의 From 은 `AI Agent <agent-sender@example.com>`이다. Agent에 Tool을 붙일 때는 이 설정을 고른다.

**사내 무인증 릴레이.** 호스트는 사내 접속 정보이므로 운영 설정에만 넣는다. 아래 `relay.example.com`은 자리 표시 값이다.

```json
{
  "smtp_host": "relay.example.com",
  "smtp_port": 25,
  "smtp_security": "starttls",
  "smtp_auth": "none",
  "smtp_username": "agent-sender@example.com",
  "from_name": "AI Agent"
}
```

`smtp_password`는 넣지 않는다. `smtp_username`은 발신 주소로만 쓰이며, 릴레이가 허용하는 사내 도메인 주소를 넣는다.

### 6.2. 텍스트 메일 호출

모델이 넘기는 호출 항목이다.

```json
{
  "to": ["kim@example.com"],
  "cc": ["team@example.com"],
  "subject": "10월 2주차 주간 보고",
  "body": "안녕하세요.\n10월 2주차 주간 보고를 공유드립니다.\n\n1. 진행 사항\n- 메일 발송 Tool 설계 완료"
}
```

결과는 다음과 같다.

```json
{
  "status": "sent",
  "message_id": "<179195365473.77991.10873220603335278444@example.com>",
  "accepted_count": 2,
  "refused": []
}
```

### 6.3. HTML 메일과 숨은 참조

```json
{
  "to": ["lee@example.com"],
  "bcc": ["archive@example.com"],
  "subject": "배포 완료 안내",
  "body": "<p>배포가 완료되었습니다.</p><ul><li>버전: 1.0.0</li></ul>",
  "body_format": "html"
}
```

`archive@example.com`은 envelope 수신자에만 들어가고 메일 헤더에는 나타나지 않는다.

### 6.4. 공백뿐인 제목

아래 호출은 스키마(`minLength: 1`)는 통과하지만 함수 검증에서 거부되고, 접속을 시도하지 않는다.

```json
{
  "to": ["kim@example.com"],
  "subject": "   ",
  "body": "자료를 공유드립니다."
}
```

```text
[INVALID_INPUT] 제목이 비어 있습니다
```

## 7. 메일 서비스별 설정값

| 서비스                  | `smtp_host`               | `smtp_port` | `smtp_security` | `smtp_password`                       |
| ----------------------- | ------------------------- | ----------- | --------------- | ------------------------------------- |
| Gmail, Google Workspace | `smtp.gmail.com`          | 587         | `starttls`      | 앱 비밀번호                           |
| Gmail, Google Workspace | `smtp.gmail.com`          | 465         | `ssl`           | 앱 비밀번호                           |
| 네이버웍스              | `smtp.worksmobile.com`    | 465         | `ssl`           | 외부 앱 비밀번호                      |
| 하이웍스                | `smtps.hiworks.com`       | 465         | `ssl`           | 계정 비밀번호 또는 메일 전용 비밀번호 |
| 다우오피스              | `outbound.daouoffice.com` | 465         | `ssl`           | 계정 비밀번호                         |
| Microsoft 365           | `smtp.office365.com`      | 587         | `starttls`      | 사서함 비밀번호                       |
| Mailgun                 | `smtp.mailgun.org`        | 587         | `starttls`      | 도메인 SMTP 비밀번호                  |
| 사내 무인증 릴레이      | `<사내 릴레이 호스트>`    | 25          | `starttls`      | 없음 (`smtp_auth`: `none`)            |

`smtp_username`에는 메일 주소 전체를 넣는다. 이 값이 발신 주소가 된다.
AWS SES(서비스가 발급한 SMTP 사용자 이름)와 SendGrid(고정 문자열 `apikey`)는 계정이 메일 주소가 아니어서 쓸 수 없다.
Mailgun은 SMTP 사용자 이름이 `이름@발신 도메인` 형식이면 쓸 수 있다(확인 필요).
국내 기업 메일의 계정 형식과 포트는 서비스 공식 안내로 최종 확인한다.

설정을 등록하기 전에 서비스별로 준비할 것이다. 배경과 출처는 `design.md` 2.4와 9장에 있다.

- Gmail: 2단계 인증을 켜고 앱 비밀번호(16자)를 만들어 `smtp_password`에 넣는다. 계정 비밀번호로는 로그인할 수 없다.
  발송할 때마다 추가 인증은 없다. Google 계정 비밀번호를 바꾸면 앱 비밀번호가 폐기되므로 다시 만든다.
  Workspace 관리자가 막았거나 고급 보호 프로그램 계정이면 앱 비밀번호 메뉴가 보이지 않을 수 있다.
- Microsoft 365: 관리자가 사서함의 SMTP AUTH를 허용해야 한다. 비밀번호 로그인은 2026년 12월 말 기본 비활성화될 예정이다.
- 국내 기업 메일: 관리자가 외부 SMTP 사용을 허용해야 한다. 하이웍스는 접속 국가 제한이 있다.
- Mailgun: 발신 도메인을 서비스에 먼저 검증한다.
- 사내 무인증 릴레이: `smtp_auth`를 `none`으로 두고 `smtp_password`는 넣지 않는다.
  메일 관리자에게 실행 환경(k8s 샌드박스)의 송신 IP 허용과 허용 발신 도메인을 확인한다.
  연결 테스트는 송신 IP 허용 여부를 확인하지 못하므로, 등록한 뒤 시험 발송을 한다(8.4).
- 공통: 발신 주소는 `smtp_username`으로 고정되므로 별칭 주소로는 보낼 수 없다. 메일 서비스도 계정 외 주소를 거부하거나(M365) 계정 주소로 바꾼다.
  Gmail 은 등록된 별칭이 아닌 주소를 오류 없이 로그인 계정 주소로 바꿔 보낸다(2026-10-14 실측).

## 8. 개발 환경

### 8.1. 로컬 Python 환경

프로젝트 폴더에 conda 환경을 만들어 두었다(`.conda/`, Python 3.11.17).
운영 이미지는 uv로 패키지를 관리한다(가이드 0장). 로컬 conda 환경은 개발·테스트 전용이다.

```sh
# 최초 생성 (완료됨)
conda create -p ./.conda python=3.11 -y

# 활성화
conda activate ./.conda
python --version   # Python 3.11.17
```

**폴더를 옮긴 경우.** conda 환경은 만든 위치의 경로가 내부에 고정된다. 폴더를 옮기면 OpenSSL 기본 CA 경로가 옛 위치를 가리켜,
실제 메일 서버 접속에서 `CONNECTION_FAILED`(원인 `SSLCertVerificationError: unable to get local issuer certificate`)가 난다.
2026-10-14 에 이 문제가 실제로 있었다. 지금 위치에서 환경을 다시 만들거나, 임시로 `SSL_CERT_FILE` 을 지정해 실행한다.
테스트(`pytest`)는 테스트 CA 를 따로 지정하므로 이 문제가 드러나지 않는다.

```sh
python -c "import ssl; print(ssl.get_default_verify_paths().openssl_cafile)"   # 지금 위치를 가리키는지 확인
SSL_CERT_FILE="$PWD/.conda/ssl/cacert.pem" python scripts/gmail_integration.py   # 임시 우회
```

개발 전용 의존성(pytest, aiosmtpd, trustme)은 `pyproject.toml`의 `dev` 그룹에 버전을 고정했다. Tool 모듈은 외부 패키지를 쓰지 않는다.

```sh
python -m pip install --group dev   # pip 25.1 이상
python -m pytest                     # 로컬 SMTP 서버로 전체 테스트 실행
```

### 8.2. 로컬 직접 호출

구현 후 Agent 없이 함수를 직접 호출해 확인할 수 있다. 설정 항목과 호출 항목을 함께 넘긴다.
TLS 가 필수이므로(로그인하면 AUTH 도), 로컬 가짜 SMTP 서버로 시험하려면 STARTTLS 를 지원하고 신뢰하는 인증서를 쓰는 서버가 필요하다.
그 구성은 `tests/conftest.py`의 `smtp_server` fixture(aiosmtpd, trustme)에 있으므로, 로컬 동작 확인은 pytest 로 한다.
실제 메일 서비스로 시험할 때는 발송 전용 계정을 쓰고, 비밀번호를 코드나 셸 기록에 남기지 않는다. 아래 예는 비밀번호를 보이지 않게 입력받는다.

```python
import getpass

from tools.send_mail import send_mail

print(send_mail(
    # 설정 항목
    smtp_host="smtp.gmail.com",
    smtp_username="agent-sender@example.com",
    smtp_password=getpass.getpass("앱 비밀번호: "),
    from_name="AI Agent",
    # 호출 항목
    to=["kim@example.com"],
    subject="테스트",
    body="본문",
))
```

### 8.3. Gmail 실서비스 시험

`scripts/gmail_integration.py`는 실제 Gmail SMTP 서버(`smtp.gmail.com`)로 이 Tool 을 시험하는 대화형 스크립트다(`follow-ups.md` F-04).
고른 항목에 따라 실제로 메일이 발송된다. pytest 자동 실행 대상이 아니다.

**준비.** Google 계정에서 2단계 인증을 켜고 앱 비밀번호(16자)를 만든다(7장). 받는 사람은 본인이 확인할 수 있는 주소를 쓴다.

```sh
python scripts/gmail_integration.py          # mail-tool 폴더에서 실행
```

보내는 Gmail 주소, 앱 비밀번호, 받는 사람, 표시 이름, 연결 방식(587 STARTTLS, 465 SSL), 시험 항목을 차례로 묻는다.
앱 비밀번호는 화면에 보이지 않게 입력받고, 명령행 인자로는 받지 않는다. 화면과 파일에 출력하지 않는다.
반복 실행할 때는 환경 변수 `GMAIL_TEST_ADDRESS`, `GMAIL_TEST_TO`, `GMAIL_APP_PASSWORD` 로 미리 줄 수 있다.
메일을 보내는 항목이 있으면 몇 통을 보내는지 보여 주고 확인을 받는다.

| 번호 | 시험 항목                                   | 기대 결과     | 직접 확인할 것                                   |
| ---- | ------------------------------------------- | ------------- | ------------------------------------------------ |
| 1    | 연결 테스트 `check()` (메일 없음)           | `ok`          | -                                                |
| 2    | 텍스트 메일 발송                            | `sent`        | 도착 여부, 한글 제목·본문                        |
| 3    | HTML 메일 (한글 제목, 표시 이름, 숨은 참조) | `sent`        | HTML 렌더링, 받는 사람 사본에 숨은 참조가 없는지 |
| 4    | 틀린 비밀번호로 로그인 (메일 없음)          | `AUTH_FAILED` | SMTP 코드가 5.2 표와 같은지                      |
| 5    | 없는 Gmail 주소로 발송                      | 관찰          | `sent` 면 발신 계정으로 반송 메일이 오는지       |

기본 실행 항목은 1, 2, 4 다. 결과는 5.2 표와 7장, `follow-ups.md` F-04 에 반영한다.
3번은 숨은 참조 주소를 따로 묻는다. 받는 사람·보낸 계정과 다른 주소를 넣어야 한다.
보낸 계정 쪽 사본(보낸편지함 등)에는 Gmail 이 숨은 참조를 보여 주는 것이 정상이므로, 누출 여부는 받는 사람 메일함의 사본으로 판정한다.
로컬 PC 에서 실행하면 데이터센터 IP 로그인 차단 여부는 확인되지 않는다. 이 항목은 k8s 샌드박스에서 실행해야 한다.

### 8.4. 사내 릴레이 시험

`scripts/relay_integration.py`는 사내 무인증 릴레이로 이 Tool 을 `smtp_auth=none`으로 시험하는 대화형 스크립트다(`follow-ups.md` F-04).
고른 항목에 따라 실제로 메일이 발송된다. pytest 자동 실행 대상이 아니다.

**준비.** 실행하는 PC·서버의 송신 IP 가 릴레이에 허용돼 있어야 한다. 받는 사람은 본인이 확인할 수 있는 주소를 쓴다.
릴레이 호스트는 사내 접속 정보이므로 코드·문서·커밋에 적지 않고, 실행할 때 입력하거나 환경 변수로 준다.

```sh
RELAY_TEST_HOST=<사내 릴레이 호스트> RELAY_TEST_FROM=<사내 주소> RELAY_TEST_TO=<받는 주소> python scripts/relay_integration.py
```

포트는 25, 보안 방식은 STARTTLS 로 고정했다(2026-10-19 실측 구성). 비밀번호는 받지 않는다.
메일을 보내는 항목이 있으면 몇 통을 보내는지 보여 주고 확인을 받는다.

| 번호 | 시험 항목                            | 기대 결과     | 직접 확인할 것              |
| ---- | ------------------------------------ | ------------- | --------------------------- |
| 1    | 연결 테스트 `check()` (메일 없음)    | `ok`          | -                           |
| 2    | 텍스트 메일 발송                     | `sent`        | 도착 여부, 한글 제목·본문   |
| 3    | HTML 메일 (한글 제목, 표시 이름)     | `sent`        | HTML 렌더링, 한글 표시 이름 |
| 4    | `smtp_auth=login` 오설정 (메일 없음) | `AUTH_FAILED` | 메시지가 5.2 표와 같은지    |

기본 실행 항목은 1, 2, 4 다. 결과는 `follow-ups.md` F-04 에 반영한다.
로컬 PC 에서 실행하면 k8s 샌드박스 송신 IP 의 허용 여부는 확인되지 않는다. 이 항목은 샌드박스에서 실행해야 한다.

## 9. 제약과 주의사항

### 9.1. 비밀번호 취급

- 사내 무인증 릴레이(`smtp_auth=none`)는 비밀번호가 없으므로 이 절이 해당하지 않는다.
- 비밀번호는 설정 등록 화면에서만 입력한다. 프롬프트나 사용자 대화에 넣지 않는다.
- 저장된 비밀번호는 다시 볼 수 없고 가림값으로만 표시된다. 바꿀 때는 새 값을 입력한다(F-03 적용 후).
- 발송 전용 계정을 만들어 쓴다. 개인 계정이나 관리자 계정의 비밀번호를 넣지 않는다.
- 서비스가 지원하면 앱 비밀번호를 쓴다. 계정 비밀번호와 별개로 폐기·재발급할 수 있다.
- Tool은 결과와 오류 메시지에 비밀번호·계정을 넣지 않는다.
- 호출 기록·트레이스의 마스킹은 후속 과제다(F-01). 그 전까지는 기록에 비밀번호가 남을 수 있으므로 기록 열람 권한을 제한한다.
- 노출이 의심되면 즉시 앱 비밀번호를 폐기하고 다시 발급한다.

### 9.2. 기능 제약

- 첨부 파일을 보낼 수 없다.
- 발송을 취소할 수 없다.
- Tool은 이전 호출을 기억하지 못한다. 같은 인자로 두 번 호출하면 두 번 발송된다.
- 결과는 메일 서버 접수까지만 보장한다. 최종 배달 여부는 알 수 없다.
- 수신 도메인과 발송량을 Tool에서 제한하지 않는다. 계정의 발송 한도(사내 릴레이는 릴레이 정책)가 실질적인 제한이다.
- OAuth2 로그인(XOAUTH2)과 비ASCII 주소(SMTPUTF8)는 지원하지 않는다.
- 발신 주소는 `smtp_username`으로 고정된다. 별칭 주소로 보낼 수 없고, 계정이 메일 주소가 아닌 서비스(AWS SES, SendGrid)는 쓸 수 없다.
- 사내 무인증 릴레이는 실행 환경의 송신 IP 가 허용돼 있어야 한다. 연결 테스트로는 이를 확인할 수 없다.
- Agent마다 표시 이름을 다르게 쓰려면 같은 계정으로 설정을 따로 등록한다.

## 10. 변경 이력

| 버전  | 날짜       | 내용                                                                                                            |
| ----- | ---------- | --------------------------------------------------------------------------------------------------------------- |
| 1.0.0 | 2026-10-14 | 초안 작성 (구현 전)                                                                                             |
| 1.0.0 | 2026-10-14 | Governance 기준으로 등록 정보와 플랫폼 현황 정정, `design.md`와 중복 정리 (구현 전)                             |
| 1.0.0 | 2026-10-14 | 1차 구현. 포트·보안 방식 함수 검증 추가(3.6), 로그인 중 끊김을 `CONNECTION_FAILED`로 분류(5.1)                  |
| 1.0.0 | 2026-10-15 | 발신자를 설정으로 이동, 발신 주소는 `smtp_username`, 로그인 필수·`none` 삭제 (2.2, 3.1, 3.6)                    |
| 1.0.0 | 2026-10-15 | 설정 묶음을 뜻하던 용어 "연결"을 "설정"으로 변경. 서버 접속 뜻의 "연결"은 유지                                  |
| 1.0.0 | 2026-10-19 | 사내 무인증 릴레이 지원(`smtp_auth`), `smtp_password` 조건부 필수, 시그니처 순서 변경 (2.2, 3.1, 3.3, 3.5, 3.6) |
| 1.0.0 | 2026-10-19 | 사내 릴레이 오류(5.2), 설정 예(6.1, 7장), 시험 스크립트(8.4) 추가                                               |
