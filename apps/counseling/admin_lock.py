from django.core.exceptions import PermissionDenied

from .services import (
    RECORDS_LOCKED_MESSAGE,
    case_records_are_locked,
    records_lock_case_for_obj,
)


class RecordsSubmittedLockMixin:
    """최종 제출된 사례의 Django admin 변경·삭제를 차단합니다. 조회는 허용합니다."""

    def has_change_permission(self, request, obj=None):
        if case_records_are_locked(records_lock_case_for_obj(obj)):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if case_records_are_locked(records_lock_case_for_obj(obj)):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if case_records_are_locked(records_lock_case_for_obj(obj)):
            raise PermissionDenied(RECORDS_LOCKED_MESSAGE)
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        if case_records_are_locked(records_lock_case_for_obj(obj)):
            raise PermissionDenied(RECORDS_LOCKED_MESSAGE)
        super().delete_model(request, obj)
