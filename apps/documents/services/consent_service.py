from django.db import transaction

from apps.documents.models import (
    COUNSELOR_REQUIRED_DOC_TYPES,
    DOC_TYPE_FILENAME_LABEL,
    ConsentDocument,
)


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
    if not created and consent.file:
        consent.file.delete(save=False)

    consent.client = case.client
    consent.uploaded_by = uploaded_by
    consent.file = file_obj
    consent.save()
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

    consent.file.delete(save=True)
    return True
