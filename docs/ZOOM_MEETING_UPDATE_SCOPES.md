# Zoom 회의 일정 변경(UPDATE) Scope 설정 가이드

플랫폼에서 상담 일정을 변경할 때 Zoom 회의 시간도 함께 바뀌려면, Server-to-Server OAuth 앱에 **회의 수정(Update Meeting)** 권한이 있어야 합니다.

에러 예시:

```text
Invalid access token, does not contain scopes:
[meeting:update:meeting:admin, meeting:update:meeting]
```

---

## 1. Zoom Marketplace 접속

1. [Zoom App Marketplace](https://marketplace.zoom.us/) 에 **Zoom 계정 관리자**로 로그인합니다.
2. 우측 상단 **Develop** → **Build App** (또는 **Manage** → 기존 앱 선택)으로 이동합니다.
3. KSCU 상담 시스템에서 사용하는 **`ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` / `ZOOM_ACCOUNT_ID`**에 연결된 **Server-to-Server OAuth** 앱을 선택합니다.  
   (새로 만들 경우: **Server-to-Server OAuth** 유형 선택 → 앱 이름 입력 → 생성)

---

## 2. Scope(권한) 추가

1. 앱 설정 화면 왼쪽 메뉴에서 **Scopes** (또는 **Add Scopes**) 를 클릭합니다.
2. 검색창에 `meeting:update` 를 입력합니다.
3. 아래 Scope를 **모두** 체크합니다.

   | Scope | 용도 |
   |-------|------|
   | `meeting:update:meeting:admin` | 계정 내 회의 일시·설정 수정 (권장) |
   | `meeting:update:meeting` | 회의 일시·설정 수정 |

4. 이미 없다면 **조회** Scope도 함께 확인합니다 (동기화 스크립트·일정 비교용).

   | Scope | 용도 |
   |-------|------|
   | `meeting:read:meeting:admin` | 회의 상세 조회 |
   | `meeting:read:meeting` | 회의 상세 조회 |

5. 화면 하단 **Done** 또는 **Save** 를 눌러 저장합니다.

> **참고:** Zoom은 2024년 이후 **Granular Scopes** 를 사용합니다. 예전 `meeting:write` 한 줄만으로는 PATCH `/meetings/{id}` 가 거절될 수 있습니다.

---

## 3. 앱 활성화(Activate)

1. 왼쪽 메뉴 **Activation** (또는 **App Credentials** 상단 배너)으로 이동합니다.
2. Scope 변경 후 **Activate** / **Continue** 로 앱을 **Activated** 상태로 유지합니다.
3. **App Credentials** 탭에서 아래 값이 Railway(또는 `.env`)와 일치하는지 확인합니다.
   - **Account ID** → `ZOOM_ACCOUNT_ID`
   - **Client ID** → `ZOOM_CLIENT_ID`
   - **Client Secret** → `ZOOM_CLIENT_SECRET`

Scope를 추가·변경한 뒤에는 **Client Secret을 다시 복사할 필요는 없지만**, Railway 환경 변수가 옛 값이 아닌지 한 번 확인하세요.

---

## 4. 계정에 앱 연결 (Account-level)

Server-to-Server OAuth 앱은 **앱을 만든 Zoom 계정(또는 동일 조직)** 에 자동 연결됩니다.

- 상담 Zoom 회의가 **다른 Zoom 계정**에서 생성된 경우, 해당 계정 관리자도 동일 앱을 설치·승인해야 PATCH가 성공합니다.
- KSCU는 `ZOOM_LICENSED_USERS`에 등록된 Licensed 사용자 계정으로 회의를 생성하므로, **앱이 그 계정이 속한 Zoom Organization에 Activated** 되어 있어야 합니다.

---

## 5. 배포 후 토큰 캐시 초기화

앱은 재시작 시 새 Scope가 반영된 토큰을 받습니다.

1. Railway에서 서비스 **Redeploy** (또는 `python manage.py`를 실행하는 프로세스 재시작).
2. (선택) Django shell에서 한 번 일정 변경을 테스트하거나, 아래 동기화 명령의 `--dry-run`으로 조회 API가 되는지 확인합니다.

---

## 6. 권한 적용 확인 (간단 테스트)

Railway Shell 또는 로컬에서:

```bash
# 1) 불일치 목록만 조회 (Zoom PATCH 호출 없음)
python manage.py sync_zoom_meeting_times --dry-run

# 2) 불일치 건을 DB(KST) 기준으로 Zoom에 반영
python manage.py sync_zoom_meeting_times
```

`--dry-run`에서 `조회 실패` 없이 Zoom 시간이 출력되면 `meeting:read:*` Scope는 정상입니다.  
실제 실행에서 `OK`가 나오면 `meeting:update:*` Scope도 정상입니다.

---

## 7. Scope 추가 후 일괄 동기화 (1회성)

권한 수정 **직후**, DB와 Zoom이 어긋난 확정 비대면 예약을 한 번에 맞춥니다.

```bash
# 목록만 확인
python manage.py sync_zoom_meeting_times --dry-run

# Zoom API로 일괄 PATCH (에러 나도 다음 건 계속)
python manage.py sync_zoom_meeting_times
```

운영(Railway) 예:

```bash
railway run python manage.py sync_zoom_meeting_times --dry-run
railway run python manage.py sync_zoom_meeting_times
```

---

## 문제 해결

| 증상 | 조치 |
|------|------|
| `Invalid access token, does not contain scopes` | Scope 추가 후 Activate, 서버 재시작 |
| `Zoom 회의 조회에 실패` | `meeting:read:meeting:admin` 추가 |
| DB는 17:00인데 Zoom은 16:00 | Scope 수정 후 `sync_zoom_meeting_times` 실행 |
| 특정 meeting_id만 실패 | Zoom에서 회의가 삭제됐거나 다른 계정 소유 — 해당 Case 수동 확인 |

공식 문서: [Granular scopes](https://developers.zoom.us/docs/integrations/oauth-scopes-granular/) · [Update a meeting API](https://developers.zoom.us/docs/api/rest/reference/zoom-api/methods/#operation/meetingUpdate)
