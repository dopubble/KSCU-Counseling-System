"""상담 신청·취소 요청 등 운영 알림 메일."""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.core.mail import send_mail

from apps.accounts.models import User, UserRole

logger = logging.getLogger(__name__)


def _email_async_enabled() -> bool:
    return getattr(settings, "EMAIL_ASYNC", True)


def _staff_recipient_emails(*, counselor=None) -> list[str]:
    """담당 상담사 + 관리자 알림 메일 목록(중복 제거)."""
    emails: list[str] = []
    seen: set[str] = set()

    def add(addr: str | None) -> None:
        if not addr:
            return
        key = addr.strip().lower()
        if key and key not in seen:
            seen.add(key)
            emails.append(addr.strip())

    if counselor is not None:
        add(getattr(counselor, "email", None))

    for addr in getattr(settings, "STAFF_NOTIFY_EMAILS", []):
        add(addr)

    for admin in User.objects.filter(role=UserRole.ADMIN, is_active=True).only("email"):
        add(admin.email)

    return emails


def send_new_application_notification(application) -> bool:
    """내담자 상담 신청 접수 알림."""
    recipients = _staff_recipient_emails()
    if not recipients:
        logger.warning("상담 신청 알림 수신자가 없습니다. STAFF_NOTIFY_EMAILS 또는 관리자 계정을 확인하세요.")
        return False

    client = application.client
    ps = application.preferred_schedule or {}
    preferred = "—"
    if ps.get("preferred_date"):
        preferred = f"{ps.get('preferred_date')} {ps.get('preferred_time', '')}".strip()

    subject = "[KSCU 상담] 새 상담 신청이 접수되었습니다"
    message = (
        f"내담자: {client.name} ({client.email})\n"
        f"거주지역: {getattr(application, 'residence_region', '') or '—'}\n"
        f"상담 유형: {application.counseling_type}\n"
        f"희망 일시: {preferred}\n"
        f"신청일: {application.created_at:%Y-%m-%d %H:%M}\n\n"
        f"병원 진단명: {getattr(application, 'clinical_diagnosis', '') or '—'}\n"
        f"복용 약: {getattr(application, 'current_medication', '') or '—'}\n"
    )
    occupation = getattr(application, "occupation", "") or ""
    if occupation:
        message += f"직업: {occupation}\n"
    message += f"\n사유:\n{application.reason}\n"
    return _send(subject, message, recipients)


def send_early_termination_counselor_notification(case) -> bool:
    """당일 취소 누적 조기 종결 시 담당 상담사 알림."""
    counselor = case.counselor
    if counselor is None or not counselor.email:
        logger.warning(
            "조기 종결 알림: 담당 상담사 이메일 없음 (사례 %s)",
            case.case_number,
        )
        return False

    application = case.application
    closed_line = (
        f"종결일: {case.closed_at:%Y-%m-%d %H:%M}\n"
        if case.closed_at
        else "종결일: —\n"
    )
    subject = "[KSCU 상담] 상담 조기 종결 안내"
    message = (
        f"담당 상담사님, 아래 사례가 당일 취소 누적으로 조기 종결되었습니다.\n\n"
        f"사례번호: {case.case_number}\n"
        f"내담자: {case.client.name} ({case.client.email})\n"
        f"상담 유형: {application.counseling_type}\n"
        f"당일 취소 누적: {case.day_of_cancel_count}회\n"
        f"남은 회기: {case.remaining_sessions} / {case.total_sessions}\n"
        f"{closed_line}\n"
        "내담자에게 후속 안내가 필요할 수 있습니다.\n"
    )
    return _send(subject, message, [counselor.email])


def send_cancel_request_notification(appointment, *, cancel_reason: str) -> bool:
    """내담자 상담 취소 요청 알림."""
    case = appointment.case
    counselor = appointment.counselor
    recipients = _staff_recipient_emails(counselor=counselor)
    if not recipients:
        logger.warning("취소 요청 알림 수신자가 없습니다.")
        return False

    subject = "[KSCU 상담] 상담 취소 요청이 접수되었습니다"
    message = (
        f"내담자: {appointment.client.name} ({appointment.client.email})\n"
        f"사례번호: {case.case_number}\n"
        f"상담 유형: {case.application.counseling_type}\n"
        f"확정 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n"
        f"담당 상담사: {counselor.name if counselor else '—'}\n\n"
        f"취소 사유:\n{cancel_reason}\n\n"
        "상담 상세 페이지에서 취소 요청을 승인하거나 반려해 주세요.\n"
    )
    return _send(subject, message, recipients)


def send_appointment_request_notification(appointment) -> bool:
    """내담자 예약 신청 — 담당 상담사(가입 이메일) 알림."""
    case = appointment.case
    counselor = appointment.counselor
    if counselor is None or not counselor.email:
        logger.warning(
            "예약 요청 알림: 담당 상담사 이메일 없음 (appointment=%s)",
            appointment.pk,
        )
        return False

    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "회기 미지정"
    )
    request_note = (appointment.request_message or "").strip()
    subject = "[KSCU 상담] 새 예약 요청이 접수되었습니다"
    message = (
        f"{counselor.name}님, 안녕하세요.\n\n"
        f"내담자 {appointment.client.name}님이 예약을 요청했습니다.\n"
        f"사례번호: {case.case_number}\n"
        f"회기: {session_label}\n"
        f"희망 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n"
        f"상담 시간: {appointment.duration_minutes}분\n"
    )
    if request_note:
        message += f"\n요청 내용:\n{request_note}\n"
    message += "\n상담 상세 페이지에서 예약 확정 또는 반려를 처리해 주세요.\n"
    return _send(subject, message, [counselor.email])


def send_appointment_confirmation_notification(appointment) -> bool:
    """상담사 예약 확정 — 내담자(가입 이메일) 알림."""
    client = appointment.client
    if not client.email:
        logger.warning(
            "예약 확정 알림: 내담자 이메일 없음 (appointment=%s)",
            appointment.pk,
        )
        return False

    case = appointment.case
    counselor = appointment.counselor
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    subject = "[KSCU 상담] 상담 예약 확정 안내"
    message = (
        f"{client.name}님, 안녕하세요.\n\n"
        f"{session_label} 예약이 확정되었습니다.\n"
        f"사례번호: {case.case_number}\n"
        f"담당 상담사: {counselor.name if counselor else '—'}\n"
        f"일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n"
        f"상담 시간: {appointment.duration_minutes}분\n"
    )
    zoom_url = (case.zoom_meeting_url or "").strip()
    if zoom_url:
        message += f"\nZoom 참여 링크:\n{zoom_url}\n"
    message += "\n상담 상세 페이지에서 일정과 Zoom 정보를 확인하실 수 있습니다.\n"
    return _send(subject, message, [client.email])


def send_appointment_pending_update_notification(appointment) -> bool:
    """상담사가 확정 전 희망 시간 수정 — 내담자(가입 이메일) 알림."""
    client = appointment.client
    if not client.email:
        return False

    case = appointment.case
    counselor = appointment.counselor
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    subject = "[KSCU 상담] 예약 희망 시간 변경 안내"
    message = (
        f"{client.name}님, 안녕하세요.\n\n"
        f"담당 상담사 {counselor.name if counselor else '—'}님이 "
        f"{session_label} 예약 검토를 위해 희망 시간을 조정했습니다.\n"
        f"사례번호: {case.case_number}\n"
        f"조정 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n"
        f"상담 시간: {appointment.duration_minutes}분\n\n"
        "확정 전 단계이므로 상담 상세 페이지에서 일정을 확인해 주세요.\n"
    )
    return _send(subject, message, [client.email])


def send_cancel_approval_notification(appointment) -> bool:
    """취소 요청 승인 — 내담자 알림."""
    client = appointment.client
    if not client.email:
        return False

    case = appointment.case
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    subject = "[KSCU 상담] 예약 취소 확정 안내"
    message = (
        f"{client.name}님, 안녕하세요.\n\n"
        f"{session_label} 예약 취소 요청이 승인되어 예약이 취소되었습니다.\n"
        f"사례번호: {case.case_number}\n"
        f"상담 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n\n"
        "상담 상세 페이지에서 확인하실 수 있습니다.\n"
    )
    return _send(subject, message, [client.email])


def send_counselor_direct_cancel_notification(
    appointment,
    *,
    cancel_reason: str,
) -> bool:
    """상담사 직접 취소 — 내담자 알림."""
    client = appointment.client
    if not client.email:
        return False

    case = appointment.case
    counselor = appointment.counselor
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    subject = "[KSCU 상담] 상담 예약 취소 안내"
    message = (
        f"{client.name}님, 안녕하세요.\n\n"
        f"담당 상담사 {counselor.name if counselor else '—'}님이 "
        f"{session_label} 예약을 취소했습니다.\n"
        f"사례번호: {case.case_number}\n"
        f"상담 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n\n"
        f"취소 사유:\n{cancel_reason.strip()}\n\n"
        "다른 시간으로 다시 예약하시려면 상담 상세 페이지를 이용해 주세요.\n"
    )
    return _send(subject, message, [client.email])


def send_cancel_rejection_notification(appointment, *, reason: str) -> bool:
    """취소 요청 반려 — 내담자 알림 (예약 유지)."""
    client = appointment.client
    if not client.email:
        return False

    case = appointment.case
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    subject = "[KSCU 상담] 예약 취소 요청 반려 안내"
    message = (
        f"{client.name}님, 안녕하세요.\n\n"
        f"{session_label} 예약 취소 요청이 반려되어 기존 예약이 유지됩니다.\n"
        f"사례번호: {case.case_number}\n"
        f"상담 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n\n"
        f"반려 사유:\n{reason}\n\n"
        "상담 상세 페이지에서 일정을 확인해 주세요.\n"
    )
    return _send(subject, message, [client.email])


def send_schedule_change_request_notification(schedule_request) -> bool:
    """내담자 확정 회기 일정 변경 요청 — 담당 상담사 알림."""
    appointment = schedule_request.appointment
    case = schedule_request.case
    counselor = case.counselor
    if counselor is None or not counselor.email:
        return False

    session_label = f"{schedule_request.session_number}회기"
    preferred = schedule_request.preferred_datetime
    subject = "[KSCU 상담] 일정 변경 요청이 접수되었습니다"
    message = (
        f"내담자: {schedule_request.client.name} ({schedule_request.client.email})\n"
        f"사례번호: {case.case_number}\n"
        f"회기: {session_label}\n"
    )
    if appointment and appointment.scheduled_at:
        message += f"현재 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n"
    if preferred:
        message += f"변경 희망: {preferred:%Y-%m-%d %H:%M}\n"
    message += (
        f"\n요청 내용:\n{(schedule_request.message or '').strip() or '—'}\n\n"
        "상담 상세 페이지에서 일정 변경을 승인하거나 반려해 주세요.\n"
    )
    return _send(subject, message, [counselor.email])


def send_schedule_change_approval_notification(
    appointment,
    *,
    old_scheduled_at,
    new_scheduled_at,
) -> bool:
    """일정 변경 승인 — 내담자 알림."""
    client = appointment.client
    if not client.email:
        return False

    case = appointment.case
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    subject = "[KSCU 상담] 일정 변경 확정 안내"
    message = (
        f"{client.name}님, 안녕하세요.\n\n"
        f"{session_label} 일정 변경 요청이 승인되었습니다.\n"
        f"사례번호: {case.case_number}\n"
        f"기존 일시: {old_scheduled_at:%Y-%m-%d %H:%M}\n"
        f"변경 일시: {new_scheduled_at:%Y-%m-%d %H:%M}\n\n"
        "상담 상세 페이지에서 확인하실 수 있습니다.\n"
    )
    return _send(subject, message, [client.email])


def send_schedule_change_rejection_notification(
    appointment,
    *,
    preferred_datetime,
    reason: str,
) -> bool:
    """일정 변경 반려 — 내담자 알림 (기존 일정 유지)."""
    client = appointment.client
    if not client.email:
        return False

    case = appointment.case
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    subject = "[KSCU 상담] 일정 변경 요청 반려 안내"
    message = (
        f"{client.name}님, 안녕하세요.\n\n"
        f"{session_label} 일정 변경 요청이 반려되어 기존 일정이 유지됩니다.\n"
        f"사례번호: {case.case_number}\n"
        f"현재 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n"
    )
    if preferred_datetime:
        message += f"요청하신 변경 일시: {preferred_datetime:%Y-%m-%d %H:%M}\n"
    message += (
        f"\n반려 사유:\n{reason}\n\n"
        "상담 상세 페이지에서 일정을 확인해 주세요.\n"
    )
    return _send(subject, message, [client.email])


def send_appointment_rejection_notification(appointment, *, reason: str) -> bool:
    """예약 반려 — 내담자 알림."""
    client = appointment.client
    if not client.email:
        return False

    case = appointment.case
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    subject = "[KSCU 상담] 예약 요청 반려 안내"
    message = (
        f"{client.name}님, 안녕하세요.\n\n"
        f"{session_label} 예약 요청이 반려되었습니다.\n"
        f"사례번호: {case.case_number}\n"
        f"요청 일시: {appointment.scheduled_at:%Y-%m-%d %H:%M}\n\n"
        f"반려 사유:\n{reason}\n\n"
        "상담 상세 페이지에서 다시 예약을 요청하실 수 있습니다.\n"
    )
    return _send(subject, message, [client.email])


def _send(subject: str, message: str, recipients: list[str]) -> bool:
    """운영 알림 — 기본 비동기(백그라운드)로 HTTP 응답을 막지 않음."""
    if not recipients:
        return False
    if _email_async_enabled():
        threading.Thread(
            target=_send_sync,
            args=(subject, message, list(recipients)),
            daemon=True,
        ).start()
        return True
    return _send_sync(subject, message, recipients)


def _send_sync(subject: str, message: str, recipients: list[str]) -> bool:
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("운영 알림 메일 발송 실패: subject=%s", subject)
        return False
