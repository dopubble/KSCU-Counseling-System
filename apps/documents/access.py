from apps.accounts.models import UserRole
from apps.documents.models import ConsentDocument


def user_can_access_consent(user, consent: ConsentDocument) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.role == UserRole.ADMIN:
        return True
    case = getattr(consent.application, "case", None)
    if not case:
        return False
    if user.role == UserRole.COUNSELOR and case.counselor_id == user.pk:
        return True
    return False


def user_can_replace_consent(user) -> bool:
    """Django Admin에서 필수 서류 파일을 교체할 수 있는 관리자만 True."""
    if not user or not user.is_authenticated or not user.is_staff:
        return False
    return bool(user.is_superuser or getattr(user, "role", None) == UserRole.ADMIN)
