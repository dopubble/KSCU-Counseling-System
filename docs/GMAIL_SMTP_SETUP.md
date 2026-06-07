# Gmail SMTP 설정 가이드

상담 신청·취소 요청 알림 메일은 Django `send_mail`과 `.env`의 SMTP 설정을 사용합니다.

## 1. Google 앱 비밀번호 발급

1. Google 계정에서 **2단계 인증**을 켭니다.
2. [Google 계정 → 보안 → 앱 비밀번호](https://myaccount.google.com/apppasswords)에서 메일용 앱 비밀번호를 생성합니다.
3. 생성된 **16자리 비밀번호**를 복사합니다. (공백 포함 표기 가능)

## 2. 설정 방법 (택 1)

### 방법 A — `local_email.py` (로컬 테스트에 편함)

프로젝트 루트(`manage.py`와 같은 폴더)의 `local_email.py`에서 `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`를 수정합니다.  
`development` 설정이 자동으로 이 파일을 불러옵니다. (`local_email.py.example` 참고)

### 방법 B — `.env` 설정

프로젝트 루트의 `.env` 파일에 다음을 추가합니다.

```env
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your.account@gmail.com
EMAIL_HOST_PASSWORD=your-16-char-app-password
DEFAULT_FROM_EMAIL=your.account@gmail.com

# 취소·신청 알림을 받을 운영 메일(쉼표 구분, 선택)
STAFF_NOTIFY_EMAILS=admin@example.com
```

- `EMAIL_HOST_PASSWORD`에는 **로그인 비밀번호가 아닌 앱 비밀번호**를 넣습니다.
- `.env`는 Git에 커밋하지 마세요.

## 3. 동작 확인

```powershell
python manage.py check_dotenv
python manage.py runserver
```

개발 환경에서 `EMAIL_HOST` / `EMAIL_HOST_USER`가 비어 있으면 메일은 **콘솔**에만 출력됩니다.

## 4. 발송 대상

| 이벤트 | 수신자 |
|--------|--------|
| 상담 신청 완료 | `STAFF_NOTIFY_EMAILS` + 활성 **관리자** 계정 이메일 |
| 상담 취소 요청 | 위 목록 + 해당 건 **담당 상담사** 이메일 |

## 5. 문제 해결

- **SMTPAuthenticationError**: 앱 비밀번호·`EMAIL_HOST_USER` 확인
- **메일이 안 옴**: 스팸함 확인, `DEFAULT_FROM_EMAIL`을 Gmail 계정과 동일하게 설정
- **운영 서버**: Gmail 대신 SendGrid·AWS SES 등 전용 SMTP 권장
