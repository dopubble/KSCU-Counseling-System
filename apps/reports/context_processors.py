from apps.accounts.models import UserRole
from apps.counseling.services import count_cancel_pending_appointments


def admin_pending_alerts(request):
    """관리자 화면 상단 알림용."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    if request.user.role != UserRole.ADMIN:
        return {}
    count = count_cancel_pending_appointments()
    return {
        "admin_cancel_pending_count": count,
        "admin_show_cancel_pending_alert": count > 0,
    }
