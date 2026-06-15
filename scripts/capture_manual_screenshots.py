"""Capture platform screenshots for the user manual (local dev server)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# manage.py 와 동일하게 .env 로드
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False, encoding="utf-8")
except ImportError:
    pass

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kscu_counseling.settings.development")

import django  # noqa: E402

django.setup()

from datetime import time as dt_time, timedelta

from apps.accounts.models import User, UserRole, UserStatus  # noqa: E402
from apps.counseling.models import Case, CaseStatus, CounselingMethod  # noqa: E402
from apps.scheduling.models import Appointment, AppointmentStatus, CounselorAvailability  # noqa: E402
from django.utils import timezone  # noqa: E402

OUT = BASE_DIR / "static" / "manual" / "screenshots"
DEFAULT_PORT = int(os.environ.get("MANUAL_SCREENSHOT_PORT", "9876"))
PASSWORD = "ManualSnap2026!"


def wait_for_server(host: str, port: int, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.25)
    raise TimeoutError(f"Server not ready at {host}:{port}")


def start_server(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_for_server("127.0.0.1", port)
    return proc


def ensure_users() -> dict:
    from apps.accounts.models import CounselorProfile, ClientProfile
    from apps.counseling.models import (
        ApplicationStatus,
        ChatMessage,
        CounselingApplication,
    )
    from apps.counseling.services import assign_counselor

    info: dict = {}
    pwd = PASSWORD

    client, _ = User.objects.update_or_create(
        email="manual-client@kscu.local",
        defaults={
            "name": "매뉴얼내담자",
            "role": UserRole.CLIENT,
            "status": UserStatus.ACTIVE,
            "phone": "010-1234-5678",
        },
    )
    client.set_password(pwd)
    client.save()
    ClientProfile.objects.update_or_create(
        user=client,
        defaults={
            "birth_date": "1995-05-15",
            "is_kcu_student": True,
            "student_id": "20251234",
            "department": "상담심리학과",
        },
    )

    counselor, _ = User.objects.update_or_create(
        email="manual-counselor@kscu.local",
        defaults={
            "name": "매뉴얼상담사",
            "role": UserRole.COUNSELOR,
            "status": UserStatus.ACTIVE,
            "phone": "010-9876-5432",
        },
    )
    counselor.set_password(pwd)
    counselor.save()
    profile, _ = CounselorProfile.objects.get_or_create(user=counselor)
    profile.is_approved = True
    profile.cohort = profile.cohort or 100
    profile.save()

    admin, _ = User.objects.update_or_create(
        email="manual-admin@kscu.local",
        defaults={
            "name": "매뉴얼관리자",
            "role": UserRole.ADMIN,
            "status": UserStatus.ACTIVE,
            "is_staff": True,
        },
    )
    admin.set_password(pwd)
    admin.save()

    pending_counselor, _ = User.objects.update_or_create(
        email="manual-pending@kscu.local",
        defaults={
            "name": "승인대기상담사",
            "role": UserRole.COUNSELOR,
            "status": UserStatus.PENDING,
        },
    )
    pending_counselor.set_password(pwd)
    pending_counselor.save()
    CounselorProfile.objects.get_or_create(user=pending_counselor)

    app, _ = CounselingApplication.objects.get_or_create(
        client=client,
        status=ApplicationStatus.IN_PROGRESS,
        defaults={
            "counseling_types": ["개인상담"],
            "reason": "매뉴얼 데모용 상담 신청",
            "preferred_schedule": {
                "preferred_date": "2026-06-15",
                "preferred_time": "14:00",
            },
        },
    )

    try:
        case = app.case
    except Case.DoesNotExist:
        case = None
    if case is None:
        case = assign_counselor(app, counselor, total_sessions=8)
    case.counseling_method = CounselingMethod.IN_PERSON
    case.save(update_fields=["counseling_method"])

    remote_client, _ = User.objects.update_or_create(
        email="manual-remote@kscu.local",
        defaults={
            "name": "비대면내담자",
            "role": UserRole.CLIENT,
            "status": UserStatus.ACTIVE,
        },
    )
    remote_client.set_password(pwd)
    remote_client.save()
    ClientProfile.objects.get_or_create(user=remote_client)
    remote_app, _ = CounselingApplication.objects.get_or_create(
        client=remote_client,
        status=ApplicationStatus.IN_PROGRESS,
        defaults={
            "counseling_types": ["개인상담"],
            "reason": "비대면 Zoom 데모",
            "preferred_schedule": {},
        },
    )
    try:
        remote_case = remote_app.case
    except Case.DoesNotExist:
        remote_case = assign_counselor(remote_app, counselor, total_sessions=8)
    remote_case.counseling_method = CounselingMethod.REMOTE
    remote_case.zoom_meeting_url = "https://zoom.us/j/demo-meeting"
    remote_case.save(update_fields=["counseling_method", "zoom_meeting_url"])

    CounselorAvailability.objects.filter(counselor=counselor).delete()
    for day in range(5):
        CounselorAvailability.objects.create(
            counselor=counselor,
            is_recurring=True,
            day_of_week=day,
            start_time=dt_time(10, 0),
            end_time=dt_time(18, 0),
            is_available=True,
        )
    for day in (5, 6):
        CounselorAvailability.objects.create(
            counselor=counselor,
            is_recurring=True,
            day_of_week=day,
            start_time=dt_time(10, 0),
            end_time=dt_time(18, 0),
            is_available=False,
        )

    now = timezone.now()
    zoom_at = now.replace(hour=14, minute=0, second=0, microsecond=0)
    if zoom_at <= now:
        zoom_at += timedelta(days=1)
    Appointment.objects.filter(case=remote_case, session_number=2).delete()
    Appointment.objects.create(
        case=remote_case,
        counselor=counselor,
        client=remote_client,
        scheduled_at=zoom_at,
        session_number=2,
        status=AppointmentStatus.CONFIRMED,
        confirmed_at=timezone.now(),
    )

    pending_at = now.replace(hour=15, minute=0, second=0, microsecond=0)
    if pending_at <= now:
        pending_at += timedelta(days=1)
    while pending_at.weekday() >= 5:
        pending_at += timedelta(days=1)
    Appointment.objects.filter(
        case=case,
        session_number=2,
        status=AppointmentStatus.PENDING,
    ).delete()
    pending_apt = Appointment.objects.create(
        case=case,
        counselor=counselor,
        client=client,
        scheduled_at=pending_at,
        session_number=2,
        status=AppointmentStatus.PENDING,
        request_message="매뉴얼 데모 예약 요청",
    )

    ChatMessage.objects.filter(case=case).delete()
    ChatMessage.objects.create(
        case=case,
        sender=client,
        recipient=counselor,
        body="안녕하세요, 다음 회기 일정 문의드립니다.",
        is_read=False,
    )
    ChatMessage.objects.create(
        case=case,
        sender=counselor,
        recipient=client,
        body="네, 확인 후 안내드리겠습니다.",
        is_read=True,
    )

    info.update(
        {
            "client_email": client.email,
            "client_case_pk": str(case.pk),
            "remote_case_pk": str(remote_case.pk),
            "counselor_email": counselor.email,
            "counselor_case_pk": str(case.pk),
            "pending_appointment_pk": str(pending_apt.pk),
            "admin_email": admin.email,
            "pending_email": pending_counselor.email,
        }
    )
    return info


def login(page, base_url: str, email: str, password: str = PASSWORD) -> None:
    page.goto(f"{base_url}/accounts/login/", wait_until="networkidle")
    page.fill('input[name="username"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    if "/accounts/login" in page.url:
        raise RuntimeError(f"Login failed for {email}")


def strip_debug_toolbar(page) -> None:
    page.evaluate(
        """() => {
            const tb = document.getElementById('djDebug');
            if (tb) tb.remove();
        }"""
    )


def capture_shot(page, url: str, path: Path, *, full_page: bool = True) -> None:
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(600)
    strip_debug_toolbar(page)
    page.screenshot(path=str(path), full_page=full_page)


def capture_element(page, selector: str, path: Path) -> None:
    locator = page.locator(selector).first
    locator.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    locator.screenshot(path=str(path))


def capture_viewport(page, path: Path) -> None:
    page.wait_for_timeout(400)
    strip_debug_toolbar(page)
    page.screenshot(path=str(path), full_page=False)


def capture_booking_calendar(page, base_url: str, case_pk: str, path: Path) -> None:
    page.goto(f"{base_url}/client/case/{case_pk}/", wait_until="networkidle")
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    booking_btn = page.locator('.client-session-schedule-change-btn:has-text("상담일정 예약")').first
    booking_btn.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    booking_btn.click()
    page.wait_for_selector("#sessionScheduleChangeModal.show", timeout=5000)
    page.wait_for_timeout(400)
    page.locator("#sessionSchedulePreferredDatetime").click()
    page.wait_for_selector(".flatpickr-calendar.open", timeout=5000)
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.screenshot(path=str(path), full_page=False)


def capture_file_attachment(page, base_url: str, case_pk: str, path: Path) -> None:
    """회기별 자료 첨부 — 가로 전체 뷰포트, 불필요한 세로 스크롤 없음."""
    page.goto(f"{base_url}/client/case/{case_pk}/", wait_until="networkidle")
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const row = document.querySelector('.case-detail-row');
            const attachBtn = document.querySelector(
                'button[data-bs-target="#sessionMaterialUploadModal"]'
            );
            if (!row) return;
            let top = row.offsetTop - 80;
            if (attachBtn) {
                const card = attachBtn.closest('.client-session-card');
                if (card) {
                    const cardTop = card.getBoundingClientRect().top + window.scrollY;
                    top = Math.min(top, cardTop - 100);
                }
            }
            window.scrollTo(0, Math.max(0, top));
        }"""
    )
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            document
                .querySelectorAll('button[data-bs-target="#sessionMaterialUploadModal"]')
                .forEach(function (btn) {
                    btn.style.setProperty('outline', '3px solid #dc2626', 'important');
                    btn.style.setProperty('outline-offset', '3px', 'important');
                    btn.style.setProperty(
                        'box-shadow',
                        '0 0 0 5px rgba(220, 38, 38, 0.35)',
                        'important'
                    );
                    btn.style.position = 'relative';
                    btn.style.zIndex = '5';
                });
        }"""
    )
    page.wait_for_timeout(200)
    page.screenshot(path=str(path), full_page=False)


def capture_zoom_entry(page, base_url: str, case_pk: str, path: Path) -> None:
    """Zoom 회의 바로가기 — 가로 전체 뷰포트 + 빨간 강조."""
    page.goto(f"{base_url}/client/case/{case_pk}/", wait_until="networkidle")
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const row = document.querySelector('.case-detail-row');
            const zoomLink = document.querySelector(
                'article.client-session-card a[href*="zoom"]'
            ) || document.querySelector(
                'article.client-session-card a:has(.bi-camera-video)'
            );
            if (!row) return;
            let top = row.offsetTop - 80;
            if (zoomLink) {
                const card = zoomLink.closest('.client-session-card');
                if (card) {
                    const cardTop = card.getBoundingClientRect().top + window.scrollY;
                    top = Math.min(top, cardTop - 100);
                }
            }
            window.scrollTo(0, Math.max(0, top));
        }"""
    )
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const links = document.querySelectorAll(
                'article.client-session-card a[href*="zoom"], ' +
                'article.client-session-card footer a.client-dashboard-action-btn'
            );
            links.forEach(function (el) {
                if (!el.textContent.includes('Zoom')) return;
                el.style.setProperty('outline', '3px solid #dc2626', 'important');
                el.style.setProperty('outline-offset', '3px', 'important');
                el.style.setProperty(
                    'box-shadow',
                    '0 0 0 5px rgba(220, 38, 38, 0.35)',
                    'important'
                );
                el.style.position = 'relative';
                el.style.zIndex = '5';
            });
        }"""
    )
    page.wait_for_timeout(200)
    page.screenshot(path=str(path), full_page=False)


def capture_counselor_case_detail(page, base_url: str, case_pk: str, path: Path) -> None:
    """상담사 사례 상세 — 게시글·상담일지·초기기록·과제·기수과제 번호 강조."""
    page.goto(f"{base_url}/counseling/counselor/case/{case_pk}/", wait_until="networkidle")
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const chat = document.getElementById('caseChatRoot');
            if (chat) chat.style.display = 'none';
            const pending = document.querySelector('.counselor-pending-requests-card');
            if (pending) pending.style.visibility = 'hidden';

            const row = document.querySelector('.case-detail-row')
                || document.querySelector('.client-portal-stack');
            window.scrollTo(0, row ? Math.max(0, row.offsetTop - 72) : 0);
        }"""
    )
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const targets = [
                { sel: 'button[data-bs-target="#boardPostCreateModal"]', num: 1 },
                { sel: '#session-1 .client-session-card-footer a[href*="journal"]', num: 2 },
                { sel: '#session-1 .client-session-card-footer a[href*="initial-record"]', num: 3 },
                {
                    sel: '#session-1 button[data-bs-target="#counselorAssignmentUploadModal"]',
                    num: 4,
                },
                { sel: '#session-1 .cohort-assignments-open-btn', num: 5 },
            ];

            function highlight(el, num) {
                const rect = el.getBoundingClientRect();
                if (rect.width < 2 || rect.height < 2) return;
                const pad = 6;

                const frame = document.createElement('div');
                frame.className = 'manual-feature-highlight-frame';
                Object.assign(frame.style, {
                    position: 'fixed',
                    left: (rect.left - pad) + 'px',
                    top: (rect.top - pad) + 'px',
                    width: (rect.width + pad * 2) + 'px',
                    height: (rect.height + pad * 2) + 'px',
                    border: '3px solid #dc2626',
                    borderRadius: '8px',
                    boxShadow: '0 0 0 5px rgba(220, 38, 38, 0.28)',
                    zIndex: '10000',
                    pointerEvents: 'none',
                });
                document.body.appendChild(frame);

                const badge = document.createElement('div');
                badge.className = 'manual-feature-highlight-badge';
                badge.textContent = String(num);
                Object.assign(badge.style, {
                    position: 'fixed',
                    left: (rect.left - pad - 2) + 'px',
                    top: (rect.top - pad - 14) + 'px',
                    minWidth: '26px',
                    height: '26px',
                    padding: '0 6px',
                    borderRadius: '9999px',
                    background: '#dc2626',
                    color: '#fff',
                    fontWeight: '800',
                    fontSize: '14px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    zIndex: '10001',
                    fontFamily: 'Pretendard, Apple SD Gothic Neo, Malgun Gothic, sans-serif',
                    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.28)',
                    lineHeight: '1',
                });
                document.body.appendChild(badge);
            }

            for (const { sel, num } of targets) {
                const el = document.querySelector(sel);
                if (el) highlight(el, num);
            }
        }"""
    )
    page.wait_for_timeout(300)
    page.screenshot(path=str(path), full_page=False)


def capture_counselor_appointment_confirm(
    page, base_url: str, case_pk: str, path: Path
) -> None:
    """사례 상세 — 왼쪽 대기 중인 예약 신청 · 노란색 [예약 확인 및 확정] 강조."""
    page.goto(f"{base_url}/counseling/counselor/case/{case_pk}/", wait_until="networkidle")
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const card = document.querySelector('.counselor-pending-requests-card');
            if (card) {
                const top = card.getBoundingClientRect().top + window.scrollY - 72;
                window.scrollTo(0, Math.max(0, top));
            }
        }"""
    )
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const btn = document.querySelector('.counselor-pending-request-btn');
            if (!btn) return;

            const rect = btn.getBoundingClientRect();
            const pad = 10;

            const frame = document.createElement('div');
            frame.id = 'manual-pending-confirm-highlight-frame';
            Object.assign(frame.style, {
                position: 'fixed',
                left: (rect.left - pad) + 'px',
                top: (rect.top - pad) + 'px',
                width: (rect.width + pad * 2) + 'px',
                height: (rect.height + pad * 2) + 'px',
                border: '4px solid #dc2626',
                borderRadius: '12px',
                boxShadow:
                    '0 0 0 8px rgba(220, 38, 38, 0.35), 0 0 28px rgba(220, 38, 38, 0.65)',
                zIndex: '10001',
                pointerEvents: 'none',
            });
            document.body.appendChild(frame);

            const label = document.createElement('div');
            label.id = 'manual-pending-confirm-highlight-label';
            label.innerHTML =
                '<span style="font-size:16px;font-weight:800;display:block;">✅ 예약 확인 및 확정</span>' +
                '<span style="font-size:12px;font-weight:600;opacity:0.95;">여기를 클릭</span>';
            Object.assign(label.style, {
                position: 'fixed',
                left: (rect.right + 20) + 'px',
                top: (rect.top + rect.height / 2 - 28) + 'px',
                background: '#dc2626',
                color: '#fff',
                padding: '10px 16px',
                borderRadius: '12px',
                zIndex: '10002',
                boxShadow: '0 12px 32px rgba(0, 0, 0, 0.28)',
                fontFamily: 'Pretendard, Apple SD Gothic Neo, Malgun Gothic, sans-serif',
                lineHeight: '1.35',
                pointerEvents: 'none',
                whiteSpace: 'nowrap',
            });

            const arrow = document.createElement('div');
            Object.assign(arrow.style, {
                position: 'absolute',
                left: '-11px',
                top: '50%',
                transform: 'translateY(-50%)',
                width: '0',
                height: '0',
                borderTop: '10px solid transparent',
                borderBottom: '10px solid transparent',
                borderRight: '12px solid #dc2626',
            });
            label.appendChild(arrow);
            document.body.appendChild(label);
        }"""
    )
    page.wait_for_timeout(300)
    page.screenshot(path=str(path), full_page=False)


def capture_counselor_appointment_manage(
    page, base_url: str, appointment_pk: str, path: Path
) -> None:
    """예약 처리 화면 — [예약 확정] 버튼 강조."""
    page.goto(
        f"{base_url}/counseling/counselor/appointments/{appointment_pk}/manage/",
        wait_until="networkidle",
    )
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            document.querySelectorAll('.alert-warning').forEach((el) => el.remove());

            const btn = document.querySelector('.appointment-manage-confirm-btn');
            if (!btn) return;
            btn.removeAttribute('disabled');
            btn.style.setProperty('opacity', '1', 'important');

            const rect = btn.getBoundingClientRect();
            const pad = 10;

            const frame = document.createElement('div');
            frame.id = 'manual-confirm-highlight-frame';
            Object.assign(frame.style, {
                position: 'fixed',
                left: (rect.left - pad) + 'px',
                top: (rect.top - pad) + 'px',
                width: (rect.width + pad * 2) + 'px',
                height: (rect.height + pad * 2) + 'px',
                border: '4px solid #dc2626',
                borderRadius: '12px',
                boxShadow:
                    '0 0 0 8px rgba(220, 38, 38, 0.35), 0 0 28px rgba(220, 38, 38, 0.65)',
                zIndex: '10001',
                pointerEvents: 'none',
            });
            document.body.appendChild(frame);

            const label = document.createElement('div');
            label.id = 'manual-confirm-highlight-label';
            label.innerHTML =
                '<span style="font-size:16px;font-weight:800;display:block;">✅ 예약 확정</span>' +
                '<span style="font-size:12px;font-weight:600;opacity:0.95;">여기를 클릭</span>';
            Object.assign(label.style, {
                position: 'fixed',
                left: (rect.right + 20) + 'px',
                top: (rect.top + rect.height / 2 - 28) + 'px',
                background: '#dc2626',
                color: '#fff',
                padding: '10px 16px',
                borderRadius: '12px',
                zIndex: '10002',
                boxShadow: '0 12px 32px rgba(0, 0, 0, 0.28)',
                fontFamily: 'Pretendard, Apple SD Gothic Neo, Malgun Gothic, sans-serif',
                lineHeight: '1.35',
                pointerEvents: 'none',
                whiteSpace: 'nowrap',
            });

            const arrow = document.createElement('div');
            Object.assign(arrow.style, {
                position: 'absolute',
                left: '-11px',
                top: '50%',
                transform: 'translateY(-50%)',
                width: '0',
                height: '0',
                borderTop: '10px solid transparent',
                borderBottom: '10px solid transparent',
                borderRight: '12px solid #dc2626',
            });
            label.appendChild(arrow);
            document.body.appendChild(label);
        }"""
    )
    page.wait_for_timeout(300)
    page.screenshot(path=str(path), full_page=False)


def capture_counselor_chat(page, base_url: str, case_pk: str, path: Path) -> None:
    """상담사 사례 상세 — 1:1 채팅 플로팅 버튼·채팅창 강조."""
    page.goto(f"{base_url}/counseling/counselor/case/{case_pk}/", wait_until="networkidle")
    page.wait_for_timeout(500)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const pending = document.querySelector('.counselor-pending-requests-card');
            if (pending) pending.style.visibility = 'hidden';
            const row = document.querySelector('.case-detail-row')
                || document.querySelector('.client-portal-stack');
            window.scrollTo(0, row ? Math.max(0, row.offsetTop - 72) : 0);
        }"""
    )
    page.wait_for_timeout(400)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const btn = document.getElementById('caseChatToggleBtn');
            if (btn) btn.click();
        }"""
    )
    page.wait_for_selector("#caseChatPanel:not(.d-none)", timeout=5000)
    page.wait_for_timeout(900)
    strip_debug_toolbar(page)
    page.evaluate(
        """() => {
            const root = document.getElementById('caseChatRoot');
            const btn = document.getElementById('caseChatToggleBtn');
            const wrap = document.querySelector('.case-chat-toggle-wrap');
            const input = document.getElementById('caseChatInput');
            if (!root || !btn) return;

            root.style.zIndex = '10050';

            const rootRect = root.getBoundingClientRect();
            const pad = 14;
            const spot = document.createElement('div');
            spot.id = 'manual-chat-spotlight';
            Object.assign(spot.style, {
                position: 'fixed',
                left: (rootRect.left - pad) + 'px',
                top: (rootRect.top - pad) + 'px',
                width: (rootRect.width + pad * 2) + 'px',
                height: (rootRect.height + pad * 2) + 'px',
                borderRadius: '18px',
                border: '5px solid #fbbf24',
                boxShadow:
                    '0 0 0 9999px rgba(15, 23, 42, 0.62),'
                    + '0 0 0 8px rgba(220, 38, 38, 0.55),'
                    + '0 0 40px rgba(251, 191, 36, 0.75)',
                zIndex: '10040',
                pointerEvents: 'none',
            });
            document.body.appendChild(spot);

            btn.style.setProperty('transform', 'scale(1.18)', 'important');
            btn.style.setProperty('transform-origin', 'center center', 'important');
            btn.style.setProperty('box-shadow', '0 0 0 6px #fff, 0 0 24px rgba(220,38,38,0.9)', 'important');

            if (wrap) {
                const btnRect = wrap.getBoundingClientRect();
                [10, 24, 38].forEach((ringPad, i) => {
                    const ring = document.createElement('div');
                    Object.assign(ring.style, {
                        position: 'fixed',
                        left: (btnRect.left - ringPad) + 'px',
                        top: (btnRect.top - ringPad) + 'px',
                        width: (btnRect.width + ringPad * 2) + 'px',
                        height: (btnRect.height + ringPad * 2) + 'px',
                        borderRadius: '9999px',
                        border: (4 - i) + 'px solid rgba(220, 38, 38, ' + (0.85 - i * 0.2) + ')',
                        zIndex: '10041',
                        pointerEvents: 'none',
                    });
                    document.body.appendChild(ring);
                });
            }

            const label = document.createElement('div');
            label.id = 'manual-chat-callout';
            label.innerHTML =
                '<span style="font-size:19px;font-weight:800;display:block;line-height:1.3;">'
                + '💬 1:1 채팅</span>'
                + '<span style="font-size:13px;font-weight:600;opacity:0.95;">'
                + '오른쪽 하단 · 클릭하여 열기</span>';
            const btnRect = (wrap || btn).getBoundingClientRect();
            Object.assign(label.style, {
                position: 'fixed',
                right: (window.innerWidth - btnRect.left + 28) + 'px',
                bottom: (window.innerHeight - btnRect.bottom - 4) + 'px',
                background: 'linear-gradient(135deg, #dc2626 0%, #991b1b 100%)',
                color: '#fff',
                padding: '14px 20px',
                borderRadius: '14px',
                zIndex: '10055',
                boxShadow: '0 16px 48px rgba(220, 38, 38, 0.55)',
                fontFamily: 'Pretendard, Apple SD Gothic Neo, Malgun Gothic, sans-serif',
                lineHeight: '1.35',
                pointerEvents: 'none',
                whiteSpace: 'nowrap',
            });
            const arrow = document.createElement('div');
            Object.assign(arrow.style, {
                position: 'absolute',
                right: '-12px',
                bottom: '18px',
                width: '0',
                height: '0',
                borderTop: '11px solid transparent',
                borderBottom: '11px solid transparent',
                borderLeft: '14px solid #991b1b',
            });
            label.appendChild(arrow);
            document.body.appendChild(label);

            if (input) {
                const inputRect = input.getBoundingClientRect();
                const inputFrame = document.createElement('div');
                Object.assign(inputFrame.style, {
                    position: 'fixed',
                    left: (inputRect.left - 6) + 'px',
                    top: (inputRect.top - 6) + 'px',
                    width: (inputRect.width + 12) + 'px',
                    height: (inputRect.height + 12) + 'px',
                    border: '3px solid #2563eb',
                    borderRadius: '8px',
                    boxShadow: '0 0 0 4px rgba(37, 99, 235, 0.25)',
                    zIndex: '10056',
                    pointerEvents: 'none',
                });
                document.body.appendChild(inputFrame);

                const inputLabel = document.createElement('div');
                inputLabel.textContent = '메시지 입력';
                Object.assign(inputLabel.style, {
                    position: 'fixed',
                    left: inputRect.left + 'px',
                    top: (inputRect.top - 28) + 'px',
                    background: '#2563eb',
                    color: '#fff',
                    fontSize: '12px',
                    fontWeight: '700',
                    padding: '4px 10px',
                    borderRadius: '6px',
                    zIndex: '10057',
                    fontFamily: 'Pretendard, sans-serif',
                    pointerEvents: 'none',
                });
                document.body.appendChild(inputLabel);
            }
        }"""
    )
    page.wait_for_timeout(300)
    page.screenshot(path=str(path), full_page=False)


def capture() -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    info = ensure_users()
    port = DEFAULT_PORT
    base_url = f"http://127.0.0.1:{port}"
    print("Demo accounts:", {k: v for k, v in info.items() if "email" in k})
    print("Starting server at", base_url)

    server = start_server(port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                device_scale_factor=2,
            )
            page = context.new_page()

            public_shots = [
                ("client-signup.png", f"{base_url}/accounts/signup/"),
                ("client-login.png", f"{base_url}/accounts/login/"),
                ("client-pending.png", f"{base_url}/accounts/pending/"),
            ]
            for name, url in public_shots:
                capture_shot(page, url, OUT / name)
                print("OK", name)

            login(page, base_url, info["client_email"])
            page.goto(f"{base_url}/client/dashboard/", wait_until="networkidle")
            page.wait_for_timeout(500)
            strip_debug_toolbar(page)
            capture_element(page, "#active-cases", OUT / "client-dashboard-booking.png")
            print("OK client-dashboard-booking.png")

            capture_booking_calendar(
                page,
                base_url,
                info["client_case_pk"],
                OUT / "client-booking-calendar.png",
            )
            print("OK client-booking-calendar.png")

            client_shots = [
                ("client-dashboard.png", f"{base_url}/client/dashboard/"),
                ("client-apply.png", f"{base_url}/counseling/apply/"),
                (
                    "client-case-detail.png",
                    f"{base_url}/client/case/{info['client_case_pk']}/",
                ),
            ]
            for name, url in client_shots:
                capture_shot(page, url, OUT / name)
                print("OK", name)

            page.goto(
                f"{base_url}/client/case/{info['client_case_pk']}/",
                wait_until="networkidle",
            )
            page.wait_for_timeout(400)
            strip_debug_toolbar(page)
            page.evaluate(
                """() => {
                    const btn = document.getElementById('caseChatToggleBtn');
                    if (btn) btn.click();
                }"""
            )
            page.wait_for_timeout(700)
            strip_debug_toolbar(page)
            page.screenshot(path=str(OUT / "client-chat.png"), full_page=False)
            print("OK client-chat.png")

            capture_file_attachment(
                page,
                base_url,
                info["client_case_pk"],
                OUT / "client-file-attachment.png",
            )
            print("OK client-file-attachment.png")

            context.clear_cookies()
            login(page, base_url, "manual-remote@kscu.local")
            capture_zoom_entry(
                page,
                base_url,
                info["remote_case_pk"],
                OUT / "client-zoom.png",
            )
            print("OK client-zoom.png")

            context.clear_cookies()
            login(page, base_url, info["counselor_email"])
            capture_shot(page, f"{base_url}/counseling/counselor/", OUT / "counselor-dashboard.png")
            print("OK counselor-dashboard.png")

            capture_counselor_case_detail(
                page,
                base_url,
                info["counselor_case_pk"],
                OUT / "counselor-case-detail.png",
            )
            print("OK counselor-case-detail.png")

            capture_shot(page, f"{base_url}/scheduling/availability/", OUT / "counselor-availability.png")
            print("OK counselor-availability.png")

            capture_counselor_appointment_confirm(
                page,
                base_url,
                info["counselor_case_pk"],
                OUT / "counselor-appointment-confirm.png",
            )
            print("OK counselor-appointment-confirm.png")

            capture_counselor_appointment_manage(
                page,
                base_url,
                info["pending_appointment_pk"],
                OUT / "counselor-appointment-manage.png",
            )
            print("OK counselor-appointment-manage.png")

            capture_counselor_chat(
                page,
                base_url,
                info["counselor_case_pk"],
                OUT / "counselor-chat.png",
            )
            print("OK counselor-chat.png")

            context.clear_cookies()
            login(page, base_url, info["admin_email"])
            admin_shots = [
                ("admin-dashboard.png", f"{base_url}/admin-panel/dashboard/"),
                (
                    "admin-matching.png",
                    f"{base_url}/admin-panel/matching/?filter=waiting",
                ),
                (
                    "admin-counseling.png",
                    f"{base_url}/admin-panel/counseling-management/?tab=waiting",
                ),
            ]
            for name, url in admin_shots:
                capture_shot(page, url, OUT / name)
                print("OK", name)

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    capture()
