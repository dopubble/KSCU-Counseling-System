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
