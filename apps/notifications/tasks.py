from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings


@shared_task
def send_email_notification(subject, message, recipient_list):
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=False,
    )


@shared_task
def send_appointment_reminder(appointment_id):
    """Send reminder email for upcoming appointment."""
    from apps.scheduling.models import Appointment

    try:
        appointment = Appointment.objects.select_related("client", "counselor").get(
            id=appointment_id
        )
    except Appointment.DoesNotExist:
        return

    subject = f"[KSCU 상담센터] 상담 예약 안내 - {appointment.scheduled_at:%Y-%m-%d %H:%M}"
    message = (
        f"안녕하세요, {appointment.client.name}님.\n\n"
        f"상담 예약이 {appointment.scheduled_at:%Y-%m-%d %H:%M}에 확정되었습니다.\n"
        f"담당 상담사: {appointment.counselor.name}\n\n"
        f"숭실사이버대학교 평생교육원"
    )
    send_email_notification.delay(subject, message, [appointment.client.email])


@shared_task
def send_matching_notification(case_id):
    """Notify client and counselor after matching."""
    from apps.counseling.models import Case

    try:
        case = Case.objects.select_related("client", "counselor").get(id=case_id)
    except Case.DoesNotExist:
        return

    if case.counselor:
        subject = "[KSCU 상담센터] 상담사 매칭 완료"
        message = (
            f"안녕하세요, {case.client.name}님.\n\n"
            f"담당 상담사 {case.counselor.name}님이 배정되었습니다.\n"
            f"사례번호: {case.case_number}\n\n"
            f"예약 메뉴에서 상담 일정을 선택해 주세요."
        )
        send_email_notification.delay(subject, message, [case.client.email])
