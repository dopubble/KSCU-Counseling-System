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

from apps.accounts.models import User, UserRole, UserStatus  # noqa: E402
from apps.counseling.models import Case, CaseStatus, CounselingMethod  # noqa: E402

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

    info.update(
        {
            "client_email": client.email,
            "client_case_pk": str(case.pk),
            "remote_case_pk": str(remote_case.pk),
            "counselor_email": counselor.email,
            "counselor_case_pk": str(case.pk),
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

            context.clear_cookies()
            login(page, base_url, "manual-remote@kscu.local")
            capture_shot(
                page,
                f"{base_url}/client/case/{info['remote_case_pk']}/",
                OUT / "client-zoom.png",
            )
            print("OK client-zoom.png")

            context.clear_cookies()
            login(page, base_url, info["counselor_email"])
            counselor_shots = [
                ("counselor-dashboard.png", f"{base_url}/counseling/counselor/"),
                (
                    "counselor-case-detail.png",
                    f"{base_url}/counseling/counselor/case/{info['counselor_case_pk']}/",
                ),
                ("counselor-availability.png", f"{base_url}/scheduling/availability/"),
            ]
            for name, url in counselor_shots:
                capture_shot(page, url, OUT / name)
                print("OK", name)

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
