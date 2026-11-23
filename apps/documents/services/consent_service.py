import os
import uuid

from django.db import models, transaction
from django.utils import timezone

from apps.documents.models import (
    COUNSELOR_REQUIRED_DOC_TYPES,
    DOC_TYPE_FILENAME_LABEL,
    ConsentDocType,
    ConsentDocument,
    ConsentDocumentReplacement,
)


def _consent_storage_path(*, consent, application_id, file_obj) -> str:
    uploaded_name = getattr(file_obj, "name", "") or "file"
    ext = (uploaded_name.rsplit(".", 1)[-1] if "." in uploaded_name else "bin").lower()
    basename = consent.build_storage_basename(ext)
    name_root, ext_part = os.path.splitext(basename)
    timestamp = int(timezone.now().timestamp())
    unique = uuid.uuid4().hex[:8]
    return f"consents/{application_id}/{name_root}_{timestamp}_{unique}{ext_part}"


def consent_history_retains_file(name: str) -> bool:
    """관리자 교체 이력에 남은 경로는 상담사 재업로드·삭제 때도 저장소에서 지우지 않는다."""
    if not name:
        return False
    return ConsentDocumentReplacement.objects.filter(
        models.Q(previous_file_name=name) | models.Q(new_file_name=name)
    ).exists()


def _clear_consent_file(consent, *, save: bool) -> None:
    name = consent.file.name if consent.file else ""
    if not name:
        return
    if consent_history_retains_file(name):
        consent.file.name = ""
        if save:
            consent.save(update_fields=["file", "updated_at"])
        return
    consent.file.delete(save=save)


def get_consents_for_application(application) -> dict[str, ConsentDocument | None]:
    existing = {
        doc.doc_type: doc
        for doc in ConsentDocument.objects.filter(
            application=application,
            doc_type__in=COUNSELOR_REQUIRED_DOC_TYPES,
        )
    }
    return {doc_type: existing.get(doc_type) for doc_type in COUNSELOR_REQUIRED_DOC_TYPES}


def build_consent_rows(application) -> list[dict]:
    slots = get_consents_for_application(application)
    return [
        {
            "doc_type": doc_type,
            "label": DOC_TYPE_FILENAME_LABEL[doc_type],
            "doc": slots[doc_type],
        }
        for doc_type in COUNSELOR_REQUIRED_DOC_TYPES
    ]


@transaction.atomic
def upsert_counselor_consent(*, case, doc_type: str, file_obj, uploaded_by):
    doc_type = doc_type.upper()
    if doc_type not in COUNSELOR_REQUIRED_DOC_TYPES:
        raise ValueError(f"unsupported doc_type: {doc_type}")

    application = case.application
    consent, created = ConsentDocument.objects.get_or_create(
        application=application,
        doc_type=doc_type,
        defaults={
            "client": case.client,
            "uploaded_by": uploaded_by,
        },
    )
    if consent.file.name:
        _clear_consent_file(consent, save=False)
        consent.file.name = ""

    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    storage_path = _consent_storage_path(
        consent=consent,
        application_id=application.id,
        file_obj=file_obj,
    )
    consent.client = case.client
    consent.uploaded_by = uploaded_by
    saved_name = consent.file.storage.save(storage_path, file_obj)
    consent.file.name = saved_name
    consent.save()

    # 접수면접지 최초 제출 시 Case에 진행방식 스냅샷을 남긴다. 이후 Case.counseling_method
    # 변경이나 재업로드로 덮어쓰지 않는다. ConsentDocument는 파일 저장용으로 유지한다.
    if doc_type == ConsentDocType.INTAKE and not (case.intake_counseling_method or "").strip():
        method = (case.counseling_method or "").strip()
        if method:
            type(case).objects.filter(pk=case.pk).update(intake_counseling_method=method)
            case.intake_counseling_method = method

    return consent


@transaction.atomic
def delete_counselor_consent(*, case, doc_type: str) -> bool:
    """상담사 필수 동의서 파일 삭제 — 스토리지 파일과 DB file 필드만 초기화."""
    doc_type = doc_type.upper()
    if doc_type not in COUNSELOR_REQUIRED_DOC_TYPES:
        raise ValueError(f"unsupported doc_type: {doc_type}")

    consent = ConsentDocument.objects.filter(
        application=case.application,
        doc_type=doc_type,
    ).first()
    if not consent or not consent.file:
        return False

    _clear_consent_file(consent, save=True)
    return True


def replace_consent_file_for_admin(*, case, doc_type: str, file_obj, replaced_by, reason: str):
    """관리자가 필수 서류 파일을 새 경로에 저장하고 이전 파일은 남긴다."""
    doc_type = (doc_type or "").upper()
    if doc_type not in COUNSELOR_REQUIRED_DOC_TYPES:
        raise ValueError(f"unsupported doc_type: {doc_type}")
    if case is None or getattr(case, "application_id", None) is None:
        raise ValueError("case required")

    reason_text = (reason or "").strip()
    if len(reason_text) < 2:
        raise ValueError("reason required")

    application = case.application
    saved_name = ""
    storage = None
    try:
        consent, _created = ConsentDocument.objects.get_or_create(
            application=application,
            doc_type=doc_type,
            defaults={
                "client": case.client,
                "uploaded_by": replaced_by,
            },
        )
        previous_name = consent.file.name if consent.file else ""
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        storage_path = _consent_storage_path(
            consent=consent,
            application_id=application.id,
            file_obj=file_obj,
        )
        storage = consent.file.storage
        saved_name = storage.save(storage_path, file_obj)
        if not saved_name or not storage.exists(saved_name):
            raise RuntimeError("consent replace save missing")
        if previous_name and saved_name == previous_name:
            raise RuntimeError("consent replace reused path")

        with transaction.atomic():
            locked = ConsentDocument.objects.select_for_update().get(pk=consent.pk)
            locked.client = case.client
            locked.file.name = saved_name
            locked.save(update_fields=["client", "file", "updated_at"])
            history = ConsentDocumentReplacement.objects.create(
                consent=locked,
                case=case,
                case_number=case.case_number,
                doc_type=doc_type,
                previous_file_name=previous_name,
                new_file_name=saved_name,
                reason=reason_text,
                replaced_by=replaced_by,
            )
        return locked, history
    except Exception:
        if saved_name and storage is not None:
            try:
                if storage.exists(saved_name):
                    storage.delete(saved_name)
            except Exception:
                pass
        raise
