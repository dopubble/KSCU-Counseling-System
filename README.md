# KSCU Counseling System

숭실사이버대학교 평생교육원 **전문상담센터** 통합 관리 플랫폼

## 기술 스택

- **Backend**: Django 5.x
- **Frontend**: Bootstrap 5
- **Database**: PostgreSQL 16
- **Task Queue**: Celery + Redis

## 프로젝트 구조

```
KSCU-Counseling-System/
├── apps/
│   ├── accounts/        # 사용자·인증·권한
│   ├── counseling/      # 상담 신청·매칭·사례
│   ├── scheduling/      # 가용시간·예약
│   ├── documents/       # 동의서·종결보고서
│   ├── sessions_app/    # 상담일지·Zoom
│   ├── reports/         # 통계·관리자 대시보드
│   └── notifications/   # 이메일 알림 (Celery)
├── kscu_counseling/     # Django 프로젝트 설정
├── templates/           # Bootstrap 템플릿
├── static/              # CSS/JS
├── docker/              # Docker Compose
└── docs/                # 설계서
```

## 빠른 시작

### 1. 환경 설정

```bash
cp .env.example .env
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements/dev.txt
```

### 2-A. SQLite (로컬 간편 실행)

`.env`에서 `DATABASE_URL`을 비워두면 SQLite를 사용합니다.

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### 2-B. PostgreSQL (Docker)

```bash
cd docker
docker compose up -d db redis
cd ..
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### 3. 전체 Docker 실행

```bash
cd docker
docker compose up --build
```

http://localhost:8000 에서 확인

## 주요 URL

| URL | 설명 |
|-----|------|
| `/` | 랜딩 페이지 |
| `/accounts/login/` | 로그인 |
| `/accounts/signup/` | 회원가입 |
| `/client/dashboard/` | 내담자 대시보드 |
| `/counselor/dashboard/` | 상담사 대시보드 |
| `/admin-panel/dashboard/` | 관리자 대시보드 |
| `/admin/` | Django Admin |
| `/health/` | 헬스체크 |

## 사용자 역할

| 역할 | 코드 | 설명 |
|------|------|------|
| 관리자 | ADMIN | 센터 운영, 매칭, 통계 |
| 상담사 | COUNSELOR | 일정, 상담, 일지 (승인 필요) |
| 내담자 | CLIENT | 신청, 예약, 동의서 |

## Celery (비동기 작업)

```bash
celery -A kscu_counseling worker -l info
```

## 설계 문서

상세 설계는 [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) 참고
