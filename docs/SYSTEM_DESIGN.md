# 숭실사이버대학교 평생교육원 시스템 설계서

| 항목 | 내용 |
|------|------|
| 문서 버전 | 1.0 |
| 작성일 | 2026-06-01 |
| 프로젝트명 | KSCU Counseling System |
| 기술스택 | Django · Bootstrap · PostgreSQL |

---

## 목차

1. [개요](#1-개요)
2. [시스템 목표 및 범위](#2-시스템-목표-및-범위)
3. [사용자 유형 및 권한](#3-사용자-유형-및-권한)
4. [기능 요구사항](#4-기능-요구사항)
5. [시스템 아키텍처](#5-시스템-아키텍처)
6. [기술 스택 상세](#6-기술-스택-상세)
7. [데이터베이스 설계](#7-데이터베이스-설계)
8. [API 설계](#8-api-설계)
9. [화면 설계 (UI/UX)](#9-화면-설계-uiux)
10. [핵심 업무 흐름](#10-핵심-업무-흐름)
11. [Zoom 연동 설계](#11-zoom-연동-설계)
12. [보안 설계](#12-보안-설계)
13. [통계 및 리포팅](#13-통계-및-리포팅)
14. [비기능 요구사항](#14-비기능-요구사항)
15. [프로젝트 구조](#15-프로젝트-구조)
16. [배포 및 운영](#16-배포-및-운영)
17. [개발 로드맵](#17-개발-로드맵)

---

## 1. 개요

### 1.1 배경

숭실사이버대학교 평생교육원은 내담자의 상담 신청부터 상담사 매칭, 예약, Zoom 화상 상담, 상담일지·사례관리, 종결보고서 작성까지 전 과정을 디지털로 관리하는 통합 플랫폼이 필요하다.

### 1.2 시스템 정의

본 시스템은 **웹 기반 상담 관리 플랫폼**으로, 관리자·상담사·내담자 세 유형의 사용자가 각자의 역할에 맞는 기능을 수행한다.

```
┌─────────────────────────────────────────────────────────────┐
│              KSCU 숭실사이버대학교 평생교육원 통합 플랫폼                      │
├──────────────┬──────────────┬──────────────────────────────┤
│   관리자      │   상담사      │         내담자                │
│  (Admin)     │ (Counselor)  │       (Client)               │
├──────────────┴──────────────┴──────────────────────────────┤
│  Django Backend  │  Bootstrap Frontend  │  PostgreSQL DB   │
│  Zoom API        │  File Storage        │  Email/SMS       │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 시스템 목표 및 범위

### 2.1 목표

| 목표 | 설명 |
|------|------|
| 접근성 | 내담자가 온라인으로 상담 신청·예약·동의서 제출 가능 |
| 효율성 | 상담사 매칭·일정 관리 자동화로 운영 부담 감소 |
| 기록 관리 | 상담일지·사례관리·종결보고서의 체계적 보관 |
| 원격 상담 | Zoom API 연동으로 비대면 상담 지원 |
| 데이터 기반 운영 | 통계 대시보드로 센터 운영 현황 파악 |

### 2.2 범위 (In Scope)

- 회원가입·로그인·권한 관리
- 상담 신청 및 동의서 업로드
- 상담사 매칭 및 가용 시간 관리
- 예약 및 Zoom 화상 상담
- 상담일지·사례관리·종결보고서
- 관리자 대시보드 및 통계

### 2.3 범위 외 (Out of Scope — 1차)

- 모바일 네이티브 앱
- AI 기반 상담사 추천
- 결제·수강료 연동
- 외부 EMR/EHR 시스템 연동

---

## 3. 사용자 유형 및 권한

### 3.1 역할 정의

| 역할 | 코드 | 설명 |
|------|------|------|
| 관리자 | `ADMIN` | 센터 전체 운영, 사용자·상담사 관리, 통계 |
| 상담사 | `COUNSELOR` | 일정 등록, 상담 수행, 일지·사례·종결보고서 작성 |
| 내담자 | `CLIENT` | 회원가입, 상담 신청, 예약, 동의서 제출 |

### 3.2 권한 매트릭스 (RBAC)

| 기능 | 관리자 | 상담사 | 내담자 |
|------|:------:|:------:|:------:|
| 회원가입/로그인 | ✓ | ✓ | ✓ |
| 사용자 관리 | ✓ | — | — |
| 상담사 등록/승인 | ✓ | — | — |
| 상담 신청 | — | — | ✓ |
| 동의서 업로드 | — | — | ✓ |
| 상담사 매칭 | ✓ | — | — |
| 가용 시간 등록 | — | ✓ | — |
| 예약 생성/변경/취소 | ✓ | ✓(본인) | ✓(본인) |
| Zoom 상담 입장 | ✓(모니터) | ✓ | ✓ |
| 상담일지 작성 | — | ✓(담당) | — |
| 사례관리 | ✓ | ✓(담당) | — |
| 종결보고서 | ✓ | ✓(담당) | — |
| 통계 조회 | ✓ | △(본인) | — |
| 시스템 설정 | ✓ | — | — |

> △: 제한적 조회 (본인 상담 건수 등)

### 3.3 사용자 상태

```
[가입] → PENDING(승인대기) → ACTIVE(활성) → INACTIVE(비활성) / SUSPENDED(정지)
```

- **상담사**: 관리자 승인 후 ACTIVE
- **내담자**: 이메일 인증 후 ACTIVE (또는 즉시 활성화 정책 선택)

---

## 4. 기능 요구사항

### 4.1 회원가입 (FR-001)

| ID | 요구사항 |
|----|----------|
| FR-001-01 | 내담자·상담사 역할 선택 가입 |
| FR-001-02 | 필수 정보: 이름, 이메일, 비밀번호, 휴대폰 |
| FR-001-03 | 상담사 추가 정보: 자격증, 전문분야, 경력 |
| FR-001-04 | 이메일 인증 (선택) |
| FR-001-05 | 개인정보 처리방침·이용약관 동의 |

### 4.2 로그인 (FR-002)

| ID | 요구사항 |
|----|----------|
| FR-002-01 | 이메일 + 비밀번호 로그인 |
| FR-002-02 | 역할별 대시보드 리다이렉트 |
| FR-002-03 | 세션 타임아웃 (30분 비활동) |
| FR-002-04 | 비밀번호 찾기/재설정 |
| FR-002-05 | 로그인 실패 5회 시 계정 잠금 (15분) |

### 4.3 상담 신청 (FR-003)

| ID | 요구사항 |
|----|----------|
| FR-003-01 | 상담 유형 선택 (개인/가족/진로/학업 등) |
| FR-003-02 | 상담 사유·희망 일정 입력 |
| FR-003-03 | 긴급도 표시 (일반/긴급) |
| FR-003-04 | 신청 상태: 접수 → 매칭대기 → 매칭완료 → 진행중 → 종결 |
| FR-003-05 | 신청 취소 (매칭 전) |

### 4.4 상담 동의서 업로드 (FR-004)

| ID | 요구사항 |
|----|----------|
| FR-004-01 | PDF/이미지 업로드 (최대 10MB) |
| FR-004-02 | 동의서 유형: 개인정보·상담·녹화(Zoom) 동의 |
| FR-004-03 | 전자서명 또는 서명 이미지 첨부 |
| FR-004-04 | 동의서 미제출 시 예약 불가 |
| FR-004-05 | 관리자·상담사 열람 (감사 로그 기록) |

### 4.5 상담사 매칭 (FR-005)

| ID | 요구사항 |
|----|----------|
| FR-005-01 | 관리자 수동 매칭 (1차) |
| FR-005-02 | 매칭 기준: 전문분야, 상담사 부하, 가용 시간 |
| FR-005-03 | 매칭 완료 시 내담자·상담사 알림 |
| FR-005-04 | 매칭 변경·재배정 |
| FR-005-05 | 매칭 이력 보관 |

### 4.6 상담 가능 시간 등록 (FR-006)

| ID | 요구사항 |
|----|----------|
| FR-006-01 | 주간 반복 일정 등록 (요일·시작·종료·슬롯) |
| FR-006-02 | 특정 날짜 휴무/예외 설정 |
| FR-006-03 | 슬롯 단위: 50분 상담 + 10분 휴식 |
| FR-006-04 | 예약된 슬롯 자동 차단 |
| FR-006-05 | 최소 24시간 전 변경 권장 알림 |

### 4.7 예약 (FR-007)

| ID | 요구사항 |
|----|----------|
| FR-007-01 | 매칭된 상담사의 가용 슬롯에서 예약 |
| FR-007-02 | 예약 확인·변경·취소 (정책: 24h 전) |
| FR-007-03 | 예약 확정 시 Zoom 미팅 자동 생성 |
| FR-007-04 | 이메일/SMS 리마인더 (24h, 1h 전) |
| FR-007-05 | 노쇼(No-show) 기록 |

### 4.8 Zoom 상담 (FR-008)

| ID | 요구사항 |
|----|----------|
| FR-008-01 | 예약 확정 시 Zoom Meeting 자동 생성 |
| FR-008-02 | 상담사=Host, 내담자=Participant |
| FR-008-03 | 예약 상세 페이지에서 입장 링크 제공 |
| FR-008-04 | 상담 시작/종료 시간 기록 |
| FR-008-05 | (선택) 클라우드 녹화 URL 저장 |

### 4.9 상담일지 (FR-009)

| ID | 요구사항 |
|----|----------|
| FR-009-01 | 회차별 일지 작성 (날짜, 내용, 관찰, 계획) |
| FR-009-02 | 템플릿: SOAP 또는 센터 표준 양식 |
| FR-009-03 | 임시저장·최종저장 |
| FR-009-04 | 수정 이력 (감사) |
| FR-009-05 | 내담자 비공개 (상담사·관리자만) |

### 4.10 사례관리 (FR-010)

| ID | 요구사항 |
|----|----------|
| FR-010-01 | 사례(Case) 단위로 상담 신청·일지·예약 통합 |
| FR-010-02 | 사례 상태: 개입중 / 휴지 / 종결 / 이관 |
| FR-010-03 | 사례 메모·태그·위험도 표시 |
| FR-010-04 | 사례 이관 (다른 상담사) |
| FR-010-05 | 사례 목록 필터·검색 |

### 4.11 종결보고서 (FR-011)

| ID | 요구사항 |
|----|----------|
| FR-011-01 | 사례 종결 시 보고서 작성 |
| FR-011-02 | 항목: 개입 요약, 성과, 추후 권고, 종결 사유 |
| FR-011-03 | PDF 내보내기 |
| FR-011-04 | 관리자 승인 워크플로 (선택) |
| FR-011-05 | 종결 후 사례 읽기 전용 |

### 4.12 통계 (FR-012)

| ID | 요구사항 |
|----|----------|
| FR-012-01 | 대시보드: 월별 신청·예약·종결 건수 |
| FR-012-02 | 상담사별 상담 시간·건수 |
| FR-012-03 | 상담 유형·긴급도 분포 |
| FR-012-04 | 노쇼율·취소율 |
| FR-012-05 | 기간별 Excel/CSV 내보내기 |

---

## 5. 시스템 아키텍처

### 5.1 3-Tier 아키텍처

```
                    ┌─────────────────┐
                    │   Client Browser │
                    │   (Bootstrap 5)  │
                    └────────┬────────┘
                             │ HTTPS
                    ┌────────▼────────┐
                    │  Nginx / Gunicorn│
                    │  Reverse Proxy   │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │           Django Application           │
        │  ┌─────────┐ ┌─────────┐ ┌──────────┐ │
        │  │  Views  │ │ Services│ │  Signals │ │
        │  │(Template│ │ (Business│ │ (Events) │ │
        │  │ + REST) │ │  Logic) │ │          │ │
        │  └────┬────┘ └────┬────┘ └────┬─────┘ │
        │       └───────────┼───────────┘       │
        │              ┌────▼────┐               │
        │              │  Models │               │
        │              │ (ORM)   │               │
        │              └────┬────┘               │
        └───────────────────┼───────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
   ┌────▼────┐      ┌───────▼───────┐   ┌──────▼──────┐
   │PostgreSQL│      │  File Storage │   │  Zoom API   │
   │   DB     │      │  (Media/S3)   │   │  (OAuth JWT)│
   └──────────┘      └───────────────┘   └─────────────┘
                            │
                    ┌───────▼───────┐
                    │ Celery + Redis│
                    │ (Async Tasks) │
                    └───────────────┘
```

### 5.2 Django 앱 모듈 구조

```
kscu_counseling/          # 프로젝트 설정
├── accounts/             # 사용자·인증·권한
├── counseling/           # 상담 신청·매칭·사례
├── scheduling/           # 가용시간·예약
├── documents/            # 동의서·종결보고서
├── sessions_app/         # 상담일지·Zoom 연동
├── reports/              # 통계·대시보드
└── notifications/        # 이메일·SMS 알림
```

### 5.3 요청 처리 흐름

```
Browser → URL Router → Middleware (Auth, CSRF, Audit)
       → View → Service Layer → Model/ORM → PostgreSQL
       → Template Response (HTML) 또는 JsonResponse (AJAX)
```

---

## 6. 기술 스택 상세

### 6.1 Backend — Django 5.x

| 구성요소 | 선택 | 용도 |
|----------|------|------|
| Django | 5.x | 웹 프레임워크 |
| Django REST Framework | 3.x | AJAX/API (선택) |
| django-allauth | — | 소셜 로그인 (선택) |
| django-crispy-forms | — | Bootstrap 폼 |
| Celery | 5.x | 비동기 작업 (알림, Zoom) |
| Redis | 7.x | Celery 브로커·캐시 |
| Pillow | — | 이미지 처리 |
| ReportLab / WeasyPrint | — | PDF 생성 |

### 6.2 Frontend — Bootstrap 5

| 구성요소 | 용도 |
|----------|------|
| Bootstrap 5.3 | 반응형 UI |
| Bootstrap Icons | 아이콘 |
| FullCalendar.js | 예약 캘린더 |
| Chart.js | 통계 차트 |
| HTMX (선택) | 부분 페이지 갱신 |

**렌더링 방식**: Django Template + Bootstrap (SSR). 관리자는 Django Admin 보조 활용.

### 6.3 Database — PostgreSQL 16

- JSONB: 상담사 메타데이터, 통계 캐시
- Full-text search: 사례·일지 검색
- 트랜잭션: 예약 슬롯 동시성 제어

### 6.4 외부 연동

| 서비스 | 용도 |
|--------|------|
| Zoom API (Server-to-Server OAuth) | 미팅 생성·관리 |
| SMTP (SendGrid/SES) | 이메일 알림 |
| (선택) SMS Gateway | 문자 리마인더 |
| (선택) AWS S3 | 파일 저장 |

---

## 7. 데이터베이스 설계

### 7.1 ERD (개념)

```
┌──────────┐     ┌──────────────┐     ┌─────────────┐
│   User   │────<│ CounselorProfile│   │ ClientProfile│
└────┬─────┘     └──────────────┘     └──────┬──────┘
     │                                        │
     │         ┌──────────────┐               │
     └────────>│ CounselingApp │<─────────────┘
               └──────┬───────┘
                      │
          ┌───────────┼───────────┐
          │           │           │
    ┌─────▼─────┐ ┌───▼───┐ ┌────▼────┐
    │   Case    │ │ Match │ │ Consent │
    └─────┬─────┘ └───────┘ └─────────┘
          │
    ┌─────┼─────────────────┐
    │     │                 │
┌───▼───┐ │  ┌──────────┐  ┌─▼──────────┐
│Journal│ │  │Appointment│  │ClosureReport│
└───────┘ │  └─────┬────┘  └────────────┘
          │        │
          │   ┌────▼────┐
          │   │ZoomMeeting│
          │   └─────────┘
    ┌─────▼─────┐
    │Availability│
    └───────────┘
```

### 7.2 주요 테이블

#### users (Django AbstractUser 확장)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| email | VARCHAR(255) UNIQUE | 로그인 ID |
| password | VARCHAR | 해시 |
| role | ENUM | ADMIN, COUNSELOR, CLIENT |
| status | ENUM | PENDING, ACTIVE, INACTIVE, SUSPENDED |
| name | VARCHAR(100) | |
| phone | VARCHAR(20) | |
| created_at | TIMESTAMP | |
| last_login | TIMESTAMP | |

#### counselor_profiles

| 컬럼 | 타입 | 설명 |
|------|------|------|
| user_id | FK → users | |
| license_number | VARCHAR | 자격증 번호 |
| specialties | JSONB | 전문분야 배열 |
| bio | TEXT | 소개 |
| max_cases | INT | 최대 동시 사례 수 |
| is_approved | BOOLEAN | 관리자 승인 |

#### client_profiles

| 컬럼 | 타입 | 설명 |
|------|------|------|
| user_id | FK → users | |
| birth_date | DATE | (선택) |
| gender | VARCHAR | (선택) |
| emergency_contact | JSONB | 비상연락처 |

#### counseling_applications

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| client_id | FK → users | |
| counseling_type | VARCHAR | 상담 유형 |
| reason | TEXT | 상담 사유 |
| urgency | ENUM | NORMAL, URGENT |
| preferred_schedule | JSONB | 희망 일정 |
| status | ENUM | RECEIVED, WAITING_MATCH, MATCHED, IN_PROGRESS, CLOSED, CANCELLED |
| created_at | TIMESTAMP | |

#### cases

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| application_id | FK | |
| client_id | FK | |
| counselor_id | FK | |
| case_number | VARCHAR UNIQUE | 사례번호 (자동) |
| status | ENUM | ACTIVE, ON_HOLD, CLOSED, TRANSFERRED |
| risk_level | ENUM | LOW, MEDIUM, HIGH |
| tags | JSONB | |
| opened_at | TIMESTAMP | |
| closed_at | TIMESTAMP | |

#### counselor_availabilities

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| counselor_id | FK | |
| day_of_week | INT | 0=월 … 6=일 |
| start_time | TIME | |
| end_time | TIME | |
| slot_duration | INT | 분 (기본 50) |
| is_active | BOOLEAN | |

#### availability_exceptions

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| counselor_id | FK | |
| date | DATE | |
| is_available | BOOLEAN | false=휴무 |
| note | VARCHAR | |

#### appointments

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| case_id | FK | |
| counselor_id | FK | |
| client_id | FK | |
| scheduled_at | TIMESTAMP | |
| duration_minutes | INT | |
| status | ENUM | SCHEDULED, CONFIRMED, COMPLETED, CANCELLED, NO_SHOW |
| zoom_meeting_id | FK | |
| cancelled_at | TIMESTAMP | |
| cancel_reason | TEXT | |

#### consent_documents

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| client_id | FK | |
| application_id | FK | |
| doc_type | ENUM | PRIVACY, COUNSELING, RECORDING |
| file | VARCHAR | 파일 경로 |
| signed_at | TIMESTAMP | |
| verified_by | FK (nullable) | |

#### counseling_journals

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| case_id | FK | |
| appointment_id | FK (nullable) | |
| counselor_id | FK | |
| session_number | INT | 회차 |
| subjective | TEXT | S |
| objective | TEXT | O |
| assessment | TEXT | A |
| plan | TEXT | P |
| is_draft | BOOLEAN | |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

#### closure_reports

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| case_id | FK UNIQUE | |
| counselor_id | FK | |
| summary | TEXT | |
| outcomes | TEXT | |
| recommendations | TEXT | |
| closure_reason | VARCHAR | |
| approved_by | FK (nullable) | |
| approved_at | TIMESTAMP | |
| pdf_file | VARCHAR | |

#### zoom_meetings

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| appointment_id | FK UNIQUE | |
| zoom_meeting_id | VARCHAR | Zoom ID |
| join_url | VARCHAR | |
| start_url | VARCHAR | Host URL |
| password | VARCHAR | |
| recording_url | VARCHAR | (nullable) |

#### audit_logs

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL PK | |
| user_id | FK | |
| action | VARCHAR | |
| target_type | VARCHAR | |
| target_id | UUID | |
| ip_address | INET | |
| created_at | TIMESTAMP | |

### 7.3 인덱스 전략

```sql
CREATE INDEX idx_appointments_scheduled ON appointments(counselor_id, scheduled_at);
CREATE INDEX idx_cases_counselor_status ON cases(counselor_id, status);
CREATE INDEX idx_applications_status ON counseling_applications(status, created_at);
CREATE INDEX idx_journals_case ON counseling_journals(case_id, session_number);
```

### 7.4 예약 동시성

- `SELECT ... FOR UPDATE`로 슬롯 잠금
- `(counselor_id, scheduled_at)` UNIQUE 제약으로 이중 예약 방지

---

## 8. API 설계

### 8.1 URL 구조 (Django URLconf)

```
/                           # 랜딩
/accounts/
  signup/                   # 회원가입
  login/                    # 로그인
  logout/
  password/reset/

/client/                    # 내담자 영역
  dashboard/
  applications/             # 상담 신청 CRUD
  applications/<id>/consent/  # 동의서 업로드
  appointments/             # 예약 조회·생성
  appointments/<id>/zoom/   # Zoom 입장

/counselor/                 # 상담사 영역
  dashboard/
  availability/             # 가용 시간
  cases/                    # 사례 목록
  cases/<id>/journals/      # 상담일지
  appointments/

/admin-panel/               # 관리자 (Django Admin 외 커스텀)
  dashboard/
  users/
  counselors/approve/
  applications/
  matching/<app_id>/
  cases/
  reports/
  statistics/
  settings/

/api/v1/                    # REST (AJAX·향후 앱)
  appointments/available-slots/
  statistics/summary/
```

### 8.2 주요 API (REST — AJAX용)

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/v1/appointments/available-slots/?counselor_id=&date=` | 가용 슬롯 |
| POST | `/api/v1/appointments/` | 예약 생성 |
| PATCH | `/api/v1/appointments/{id}/` | 예약 변경 |
| POST | `/api/v1/matching/` | 매칭 (관리자) |
| GET | `/api/v1/statistics/summary/?from=&to=` | 통계 요약 |

### 8.3 응답 형식

```json
{
  "success": true,
  "data": { ... },
  "message": "예약이 확정되었습니다.",
  "errors": null
}
```

---

## 9. 화면 설계 (UI/UX)

### 9.1 공통

- Bootstrap 5 Navbar + Sidebar (역할별 메뉴)
- 반응형 (모바일 768px 이하: 햄버거 메뉴)
- 접근성: WCAG 2.1 AA 목표
- 색상: 센터 CI 반영 (Primary: #003366 등)

### 9.2 화면 목록

#### 공통
| ID | 화면명 | URL |
|----|--------|-----|
| SC-001 | 랜딩/소개 | `/` |
| SC-002 | 로그인 | `/accounts/login/` |
| SC-003 | 회원가입 | `/accounts/signup/` |

#### 내담자
| ID | 화면명 | URL |
|----|--------|-----|
| SC-C01 | 대시보드 | `/client/dashboard/` |
| SC-C02 | 상담 신청 | `/client/applications/new/` |
| SC-C03 | 신청 목록/상세 | `/client/applications/` |
| SC-C04 | 동의서 업로드 | `/client/applications/<id>/consent/` |
| SC-C05 | 예약하기 | `/client/appointments/book/` |
| SC-C06 | 예약 목록 | `/client/appointments/` |
| SC-C07 | Zoom 입장 | `/client/appointments/<id>/zoom/` |

#### 상담사
| ID | 화면명 | URL |
|----|--------|-----|
| SC-K01 | 대시보드 | `/counselor/dashboard/` |
| SC-K02 | 가용 시간 설정 | `/counselor/availability/` |
| SC-K03 | 예약 캘린더 | `/counselor/appointments/` |
| SC-K04 | 사례 목록 | `/counselor/cases/` |
| SC-K05 | 사례 상세 | `/counselor/cases/<id>/` |
| SC-K06 | 상담일지 작성 | `/counselor/cases/<id>/journals/new/` |
| SC-K07 | 종결보고서 | `/counselor/cases/<id>/closure/` |

#### 관리자
| ID | 화면명 | URL |
|----|--------|-----|
| SC-A01 | 대시보드 (KPI) | `/admin-panel/dashboard/` |
| SC-A02 | 사용자 관리 | `/admin-panel/users/` |
| SC-A03 | 상담사 승인 | `/admin-panel/counselors/approve/` |
| SC-A04 | 상담 신청 관리 | `/admin-panel/applications/` |
| SC-A05 | 상담사 매칭 | `/admin-panel/matching/<id>/` |
| SC-A06 | 전체 사례 | `/admin-panel/cases/` |
| SC-A07 | 통계 | `/admin-panel/statistics/` |
| SC-A08 | 시스템 설정 | `/admin-panel/settings/` |
| SC-A09 | 감사 로그 | `/admin-panel/audit-logs/` |

### 9.3 관리자 대시보드 와이어프레임

```
┌────────────────────────────────────────────────────────────┐
│  KSCU 숭실사이버대학교 평생교육원 관리자                    [알림] [프로필] │
├──────────┬─────────────────────────────────────────────────┤
│ 대시보드  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐            │
│ 사용자   │  │신청 12│ │예약 8│ │진행 5│ │종결 3│            │
│ 상담사   │  └──────┘ └──────┘ └──────┘ └──────┘            │
│ 신청관리  │                                                 │
│ 매칭     │  [월별 상담 추이 Chart]  [유형별 Pie Chart]       │
│ 사례     │                                                 │
│ 통계     │  ┌─────────────────────────────────────────┐   │
│ 설정     │  │ 매칭 대기 목록          │ 오늘 예약      │   │
│          │  │ · 홍길동 - 긴급         │ 10:00 김OO    │   │
│          │  │ · 이영희 - 일반         │ 14:00 박OO    │   │
│          │  └─────────────────────────────────────────┘   │
└──────────┴─────────────────────────────────────────────────┘
```

---

## 10. 핵심 업무 흐름

### 10.1 End-to-End 상담 프로세스

```
[내담자] 회원가입
    ↓
[내담자] 상담 신청
    ↓
[내담자] 동의서 업로드 (3종)
    ↓
[관리자] 상담사 매칭
    ↓
[시스템] Case 생성, 알림 발송
    ↓
[내담자] 상담사 가용 시간에서 예약
    ↓
[시스템] Zoom Meeting 생성, 확인 메일
    ↓
[상담사/내담자] Zoom 상담 진행
    ↓
[상담사] 상담일지 작성
    ↓
(반복: 추가 예약 → 상담 → 일지)
    ↓
[상담사] 종결보고서 작성
    ↓
[관리자] (선택) 승인
    ↓
[시스템] Case CLOSED, 통계 반영
```

### 10.2 상태 전이 — 상담 신청

```
RECEIVED → WAITING_MATCH → MATCHED → IN_PROGRESS → CLOSED
    ↓           ↓              ↓
CANCELLED   CANCELLED      CANCELLED (정책)
```

### 10.3 상태 전이 — 예약

```
SCHEDULED → CONFIRMED → COMPLETED
    ↓           ↓
CANCELLED   NO_SHOW
```

---

## 11. Zoom 연동 설계

### 11.1 인증

- **Server-to-Server OAuth** (Account-level app)
- 환경변수: `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`

### 11.2 미팅 생성 플로우

```
Appointment CONFIRMED
    → Celery Task: create_zoom_meeting
    → POST /v2/users/me/meetings
        {
          "topic": "KSCU 상담 - {case_number}",
          "type": 2,
          "start_time": "...",
          "duration": 50,
          "settings": {
            "waiting_room": true,
            "join_before_host": false
          }
        }
    → ZoomMeeting 레코드 저장
    → 알림 발송 (join_url)
```

### 11.3 입장 URL (운영)

- **내담자·상담사·이메일·알림**: `join_url` (`/j/`)만 사용
- **상담사 호스트 권한**: `join_url` 입장 후 **Claim Host** + `ZOOM_HOST_KEY`(또는 회기별 `counselor_host_key`)
- **`start_url` (`/s/`)**: Zoom API 보관용. 기관 Zoom 계정 로그인 전용이라 상담사 UI에 연결하지 않음
- 회의 설정: `join_before_host: true`, `waiting_room: false`

### 11.4 (선택) Webhook

- `meeting.started` / `meeting.ended` → 실제 상담 시간 기록
- `recording.completed` → recording_url 저장

---

## 12. 보안 설계

### 12.1 인증·인가

| 항목 | 구현 |
|------|------|
| 비밀번호 | PBKDF2 (Django default), 최소 8자 |
| 세션 | Django Session, HttpOnly·Secure·SameSite |
| CSRF | Django CSRF middleware |
| RBAC | `@login_required` + `@role_required` decorator |
| 관리자 | 별도 URL prefix, IP 화이트리스트 (선택) |

### 12.2 개인정보 보호

- 상담일지·동의서: 역할 기반 접근, URL 직접 접근 차단
- 파일 저장: `media/consents/{uuid}/` — UUID 파일명
- 감사 로그: 민감 데이터 열람·다운로드 기록
- 데이터 보존: 종결 후 N년 (정책 설정)
- 마스킹: 목록 화면 내담자 연락처 부분 마스킹

### 12.3 파일 업로드

- 허용: PDF, JPG, PNG
- 크기: 10MB
- MIME 검증 + 확장자 화이트리스트
- 바이러스 스캔 (ClamAV, 선택)

### 12.4 OWASP 대응

- SQL Injection: ORM 사용
- XSS: Template auto-escape
- Rate Limiting: django-ratelimit (로그인, API)

---

## 13. 통계 및 리포팅

### 13.1 대시보드 KPI

| 지표 | 계산 |
|------|------|
| 월간 신청 건수 | `counseling_applications` COUNT by month |
| 매칭 대기 | status = WAITING_MATCH |
| 활성 사례 | cases.status = ACTIVE |
| 월간 상담 시간 | SUM(appointments.duration) WHERE COMPLETED |
| 노쇼율 | NO_SHOW / (COMPLETED + NO_SHOW) |
| 상담사별 부하 | ACTIVE cases per counselor |

### 13.2 리포트

- 기간별 Excel/CSV export
- 종결보고서 PDF 일괄 다운로드 (관리자)
- Chart.js: Line (추이), Bar (상담사별), Pie (유형별)

### 13.3 집계 방식

- 실시간: PostgreSQL aggregate 쿼리
- (확장) 일별 Celery beat → `statistics_daily` 테이블 사전 집계

---

## 14. 비기능 요구사항

| 항목 | 목표 |
|------|------|
| 가용성 | 99.5% (업무시간) |
| 응답 시간 | 페이지 < 2초 (P95) |
| 동시 사용자 | 100명 |
| 데이터 백업 | 일 1회 PostgreSQL dump, 30일 보관 |
| 브라우저 | Chrome, Edge, Safari 최신 2버전 |
| 로그 | Django logging + 파일/log aggregator |
| i18n | 한국어 (기본), 영어 (선택) |

---

## 15. 프로젝트 구조

```
KSCU-Counseling-System/
├── docs/
│   └── SYSTEM_DESIGN.md
├── kscu_counseling/              # Django project
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   └── wsgi.py
├── apps/
│   ├── accounts/
│   ├── counseling/
│   ├── scheduling/
│   ├── documents/
│   ├── sessions_app/
│   ├── reports/
│   └── notifications/
├── templates/
│   ├── base.html
│   ├── accounts/
│   ├── client/
│   ├── counselor/
│   └── admin_panel/
├── static/
│   ├── css/
│   ├── js/
│   └── img/
├── media/                        # 업로드 파일 (gitignore)
├── requirements/
│   ├── base.txt
│   ├── dev.txt
│   └── prod.txt
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── .env.example
├── manage.py
└── README.md
```

---

## 16. 배포 및 운영

### 16.1 환경

| 환경 | 용도 |
|------|------|
| development | 로컬 Docker Compose |
| staging | QA·UAT |
| production | 운영 서버 |

### 16.2 Docker Compose (개발)

```yaml
services:
  web:
    build: .
    command: gunicorn kscu_counseling.wsgi:application
    volumes: [.:/app, media:/app/media]
    ports: ["8000:8000"]
    depends_on: [db, redis]
  db:
    image: postgres:16
  redis:
    image: redis:7
  celery:
    build: .
    command: celery -A kscu_counseling worker -l info
```

### 16.3 CI/CD (권장)

```
Git Push → GitHub Actions
  → Lint (flake8, black)
  → Test (pytest)
  → Build Docker Image
  → Deploy (staging → manual prod)
```

### 16.4 모니터링

- Health check: `/health/`
- Sentry: 에러 추적
- Uptime: 외부 ping

---

## 17. 개발 로드맵

### Phase 1 — 기반 (4주)

- [ ] Django 프로젝트 셋업, PostgreSQL
- [ ] User/Role 모델, 회원가입·로그인
- [ ] Bootstrap 레이아웃, 역할별 대시보드 shell
- [ ] 관리자 사용자·상담사 승인

### Phase 2 — 상담 코어 (4주)

- [ ] 상담 신청 CRUD
- [ ] 동의서 업로드
- [ ] 상담사 매칭, Case 생성
- [ ] 가용 시간 등록

### Phase 3 — 예약·Zoom (3주)

- [ ] 예약 슬롯·캘린더
- [ ] Zoom API 연동
- [ ] 이메일 알림 (Celery)

### Phase 4 — 기록·보고 (3주)

- [ ] 상담일지 (SOAP)
- [ ] 사례관리 UI
- [ ] 종결보고서 + PDF

### Phase 5 — 통계·마무리 (2주)

- [ ] 관리자 통계 대시보드
- [ ] 감사 로그
- [ ] UAT, 보안 점검, 배포

**총 예상: 16주**

---

## 부록 A — 용어 정의

| 용어 | 정의 |
|------|------|
| 내담자 | 상담을 받는 사용자 (Client) |
| 상담사 | 상담을 제공하는 전문가 (Counselor) |
| 사례(Case) | 한 내담자의 상담 과정 전체 단위 |
| 매칭 | 내담자와 상담사를 연결하는 행위 |
| 종결 | 상담 과정 완료 및 보고서 작성 |

## 부록 B — 환경변수

```env
SECRET_KEY=
DEBUG=False
DATABASE_URL=postgres://user:pass@db:5432/kscu_counseling
REDIS_URL=redis://redis:6379/0
ZOOM_ACCOUNT_ID=
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
EMAIL_HOST=
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
ALLOWED_HOSTS=
MEDIA_ROOT=/app/media
```

---

*본 문서는 구현 착수 전 기준 설계서이

, 운영 정책·법적 요건 확정 시 개정한다.*
