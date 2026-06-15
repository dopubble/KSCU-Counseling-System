"""내담자 개인정보 마스킹 (동기 상담일지 공유용)."""

from __future__ import annotations


def mask_client_name(name: str | None) -> str:
    """이름 가운데 글자 마스킹. 두 글자면 뒷글자 마스킹."""
    text = (name or "").strip()
    if not text:
        return "—"
    if len(text) == 1:
        return "*"
    if len(text) == 2:
        return text[0] + "*"
    return text[0] + "*" * (len(text) - 2) + text[-1]


def mask_phone(_phone: str | None) -> str:
    return "***-****-****"


def mask_email(_email: str | None) -> str:
    return "**"


def mask_client_summary_fields(summary: dict) -> dict:
    """API/템플릿/PDF용 client_summary 사본에 마스킹 적용."""
    masked = dict(summary)
    masked["client_name"] = mask_client_name(summary.get("client_name"))
    masked["phone"] = mask_phone(summary.get("phone"))
    masked["email"] = mask_email(summary.get("email"))
    return masked
