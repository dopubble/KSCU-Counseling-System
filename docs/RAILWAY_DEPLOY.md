# Railway 비공개 배포 가이드 — KSCU Counseling System

GitHub **Private** 저장소를 Railway에 연결해 Django 앱을 운영하는 절차입니다.

---

## 사전 준비

- [Railway](https://railway.app) 계정
- GitHub에 올린 `KSCU-Counseling-System` 저장소 (Private 가능)
- Zoom Server-to-Server OAuth 앱, Gmail SMTP(앱 비밀번호)

---

## 1. Railway 프로젝트 생성

1. Railway 대시보드 → **New Project**
2. **Deploy from GitHub repo** 선택
3. GitHub 연동 후 **KSCU-Counseling-System** (Private repo) 선택
4. 프로젝트 이름 예: `kscu-counseling`

---

## 2. PostgreSQL 추가

1. 프로젝트 캔버스 → **+ Create** → **Database** → **PostgreSQL**
2. 생성된 Postgres 서비스 → **Variables** 탭에서 `DATABASE_URL` 확인
3. **Web 서비스**와 같은 프로젝트에 두면 Railway가 **Service Variable Reference**로 연결 가능

**Web 서비스 Variables에 연결:**

| Variable | 값 |
|----------|-----|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |

(Postgres 서비스 이름이 `Postgres`가 아니면 해당 이름으로 변경)

---

## 3. Redis 추가 (Celery)

1. **+ Create** → **Database** → **Redis**
2. Web / Celery Worker Variables:

| Variable | 값 |
|----------|-----|
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |

(Redis 서비스 이름에 맞게 수정)

---

## 4. Web 서비스 설정 (Django + Gunicorn)

GitHub 연결 시 자동 생성된 **Web 서vice**를 선택합니다.

### Build / Deploy

저장소 루트의 [`railway.toml`](../railway.toml), [`nixpacks.toml`](../nixpacks.toml)이 적용됩니다.

Nixpacks가 **루트 `requirements.txt`**(운영 의존성)를 자동 설치합니다.  
`requirements/dev.txt`는 로컬 개발 전용이며 Railway 빌드에 쓰이지 않습니다.

| 항목 | 값 |
|------|-----|
| **Build Command** | `python manage.py collectstatic --noinput` |
| **Pre-deploy Command** | `python manage.py migrate --noinput` |
| **Start Command** | `gunicorn kscu_counseling.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120` |

대시보드에서 `railway.toml`을 쓰지 않을 경우 위 명령을 **Settings → Deploy**에 직접 입력하세요.

### Public URL

1. Web 서비스 → **Settings** → **Networking** → **Generate Domain**
2. 예: `kscu-counseling-production.up.railway.app`
3. Railway가 `RAILWAY_PUBLIC_DOMAIN`, `PORT`를 자동 주입합니다.

---

## 5. Celery Worker 서비스 추가

1. 같은 프로젝트 → **+ Create** → **Empty Service**
2. 이름: `celery-worker`
3. **Settings** → **Source** → **Connect Repo** → 동일 GitHub repo / branch
4. **Settings** → **Deploy**:
   - **Build Command**: `pip install -r requirements/prod.txt`
   - **Start Command**: `celery -A kscu_counseling worker -l info`
   - Pre-deploy / collectstatic **불필요** (Worker는 정적 파일 미사용)
5. **Variables** — Web 서비스와 **동일한** 환경 변수 복사:
   - `DJANGO_SETTINGS_MODULE`
   - `SECRET_KEY`
   - `DATABASE_URL` → `${{Postgres.DATABASE_URL}}`
   - `REDIS_URL` → `${{Redis.REDIS_URL}}`
   - 이메일·Zoom 변수 (Worker가 알림 task를 실행할 경우)

> Worker는 **Public Domain 불필요** (Networking 설정 안 해도 됨)

---

## 6. 필수 환경 변수 (Web + Celery 공통)

Railway → **Web 서비스** → **Variables** (Raw Editor 권장)

### Django / Railway

| Variable | 필수 | 설명 | 예시 |
|----------|------|------|------|
| `DJANGO_SETTINGS_MODULE` | ✅ | 운영 설정 | `kscu_counseling.settings.production` |
| `SECRET_KEY` | ✅ | 50자 이상 랜덤 | `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DEBUG` | ✅ | 운영 | `False` |
| `ALLOWED_HOSTS` | ✅ | 쉼표 구분 | `kscu-counseling-production.up.railway.app,.up.railway.app` |
| `CSRF_TRUSTED_ORIGINS` | ✅ | HTTPS Origin | `https://kscu-counseling-production.up.railway.app` |
| `DATABASE_URL` | ✅ | Postgres | `${{Postgres.DATABASE_URL}}` |
| `REDIS_URL` | ✅ | Celery | `${{Redis.REDIS_URL}}` |
| `PORT` | — | Railway 자동 | (설정하지 않음) |
| `RAILWAY_PUBLIC_DOMAIN` | — | Railway 자동 | (설정하지 않음) |

`ALLOWED_HOSTS`에 `.up.railway.app`을 넣으면 Railway가 부여하는 서브도메인 변경에 대응하기 쉽습니다.

`CSRF_TRUSTED_ORIGINS`는 **반드시 `https://` 포함**. 커스텀 도메인 연결 시 함께 추가:

```env
CSRF_TRUSTED_ORIGINS=https://your-app.up.railway.app,https://counseling.kcu.ac.kr
```

> **로그인·폼 제출 시 `403 CSRF 검증 실패`**  
> 1. `DJANGO_SETTINGS_MODULE` = `kscu_counseling.settings.production` (development 아님)  
> 2. `DEBUG` = `False` (또는 Variables에서 삭제 — production 설정이 강제로 False)  
> 3. `CSRF_TRUSTED_ORIGINS` = `https://실제접속도메인.up.railway.app` (`CSRF_TRUSTED_ORIGINS=` 접두사 붙이지 않기)  
> 4. 배포 후 **시크릿 창**에서 재시도(예전 CSRF 쿠키 제거)  
> 5. Deploy Logs에서 `[kscu] CSRF_TRUSTED_ORIGINS=...` 에 현재 URL origin이 포함되는지 확인  
>
> 홈(/)은 GET이라 CSRF 없이 열리지만, 로그인 POST는 Origin 검증이 필요합니다.  
> 이미 로그인된 세션이 남아 있으면 홈에서는 로그인 상태로 보일 수 있습니다.

### Zoom

| Variable | 필수 |
|----------|------|
| `ZOOM_ACCOUNT_ID` | 예약 확정·일정 변경 시 |
| `ZOOM_CLIENT_ID` | |
| `ZOOM_CLIENT_SECRET` | |
| `ZOOM_HOST_KEY` | 상담사 Claim Host 안내용 6자리 (Zoom Profile → Host Key 와 동일) |
| `ZOOM_LICENSED_USERS` | Licensed 사용자 이메일 2개 (쉼표 구분). 동시간대 비대면 회의 host 1/2 자동 배정 |

계정 교체 후 기존 확정 비대면 예약 Zoom 링크 재생성:

```bash
# 1) Zoom 키·Licensed 이메일이 같은 계정인지 확인 (로컬에서 돌릴 때 ZOOM_* 도 함께 설정)
python manage.py verify_zoom_setup

# 2) 재생성
python manage.py recreate_zoom_meetings --apply
```

로컬 PC에서 운영 DB에 붙일 때는 `DATABASE_URL`만 넣으면 안 됩니다. Railway Variables의 `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`을 **같은 PowerShell 세션**에 `$env:ZOOM_*`로 함께 설정하세요. 로컬 `.env`의 예전 Zoom 키가 쓰이면 `User does not exist` 오류가 납니다.

(Railway Shell 또는 Public DATABASE_URL 연결 PC에서 실행)

### Gmail SMTP

| Variable | 필수 |
|----------|------|
| `EMAIL_HOST` | `smtp.gmail.com` |
| `EMAIL_PORT` | `587` |
| `EMAIL_USE_TLS` | `True` |
| `EMAIL_HOST_USER` | Gmail 주소 |
| `EMAIL_HOST_PASSWORD` | Google **앱 비밀번호** 16자 |
| `DEFAULT_FROM_EMAIL` | 발신 주소 |
| `STAFF_NOTIFY_EMAILS` | `admin@example.com,counselor@example.com` |

> **상담 신청 제출 시 오래 로딩 후 500 오류**  
> `EMAIL_HOST_USER`만 설정하고 비밀번호가 없거나 SMTP가 Railway에서 막히면, 메일 발송 대기 중 Gunicorn이 타임아웃(120초)될 수 있습니다.  
> - `EMAIL_HOST_USER`와 `EMAIL_HOST_PASSWORD`(앱 비밀번호) **둘 다** 설정하거나, 테스트 중에는 **둘 다 비워 두세요**(신청 저장은 되고 알림만 콘솔 로그).  
> - 운영에서는 `EMAIL_TIMEOUT`(기본 10초)로 SMTP 대기 시간이 제한됩니다.

상세: [GMAIL_SMTP_SETUP.md](./GMAIL_SMTP_SETUP.md)

---

## 7. `.env.example` 형식 (Railway Raw Editor 붙여넣기 템플릿)

```env
DJANGO_SETTINGS_MODULE=kscu_counseling.settings.production
SECRET_KEY=여기에-랜덤-SECRET-KEY
DEBUG=False
ALLOWED_HOSTS=your-service.up.railway.app,.up.railway.app
CSRF_TRUSTED_ORIGINS=https://your-service.up.railway.app

DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}

ZOOM_ACCOUNT_ID=
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
ZOOM_HOST_KEY=
ZOOM_LICENSED_USERS=sscukscu@gmail.com,sedulife@mail.kcu.ac

EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=
STAFF_NOTIFY_EMAILS=
```

`${{Postgres.DATABASE_URL}}` 문법은 Railway Variable Reference입니다. Raw Editor에서 그대로 사용 가능합니다.

---

## 8. 배포 후 확인

1. **Deploy** 로그에서 `migrate`, `collectstatic`, Gunicorn 기동 성공 확인
2. 브라우저: `https://<your-domain>/health/` → 200
3. `/accounts/login/`, `/admin/` 접속
4. 터미널(로컬) 또는 Railway **Run Command**:

   ```bash
   python manage.py createsuperuser
   ```

5. Celery Worker 로그에 `celery@... ready` 확인
6. 테스트: 상담 신청 → 이메일 알림 큐 처리

---

## 9. 주의 사항

| 항목 | 내용 |
|------|------|
| **media/** | Railway 디스크는 **재배포 시 유실**됩니다. 과제·첨부 파일 유지 방법: (1) Railway Volume 마운트 + `MEDIA_ROOT=/data/media` (2) S3 호환 스토리지(`AWS_STORAGE_BUCKET_NAME` 등). Volume/S3 적용 **이후** 과제를 다시 제출해야 합니다. |
| **Private repo** | GitHub Private + Railway 연동은 유료 플랜 필요할 수 있음 |
| **HTTPS** | `production.py`에 `SECURE_PROXY_SSL_HEADER` 설정됨 — HTTP로 직접 접속하지 말 것 |
| **비밀값** | `.env`는 GitHub에 올리지 않음. Railway Variables만 사용 |

### 9.1 과제·첨부 파일 영구 저장 (Railway Volume)

1. Railway 대시보드 → **Web 서비스** → **Volumes** → **Add Volume**
2. Mount Path: `/data/media`
3. **Variables**에 `MEDIA_ROOT=/data/media` 추가
4. 재배포 후 상담사가 **과제를 다시 제출** (이전 업로드 파일은 복구되지 않음)
5. 이후 ZIP·개별 다운로드가 정상 동작

S3(R2 등) 사용 시: `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` 설정 (선택: `AWS_S3_ENDPOINT_URL`, `AWS_S3_REGION_NAME`)

---

## 10. 서비스 구성 요약

```
Railway Project: kscu-counseling
├── Postgres          → DATABASE_URL
├── Redis             → REDIS_URL
├── Web (Gunicorn)    → Public URL, railway.toml
└── celery-worker     → celery -A kscu_counseling worker -l info
```

---

## 관련 파일

| 파일 | 용도 |
|------|------|
| `kscu_counseling/settings/production.py` | HTTPS·CSRF·WhiteNoise |
| `railway.toml` | Web 빌드/배포 명령 |
| `scripts/railway_web.sh` | Web Start Command 참고 |
| `scripts/railway_celery.sh` | Celery Start Command 참고 |
| `requirements/prod.txt` | gunicorn, whitenoise |
