"""전문상담사 수련확인서 PDF.

수련 숫자는 get_counselor_training_progress() 결과를 그대로 표시한다.
"""

from __future__ import annotations

import os
from io import BytesIO
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.sessions_app.pdf import _register_korean_font

CERTIFICATE_TITLE = "전문상담사 수련확인서"
CERTIFICATE_CONFIRMATION = "위와 같이 전문상담사 수련과정의 수련 내역을 확인합니다."
CERTIFICATE_ISSUER = "숭실사이버대학교 평생교육원"
_EMPTY = "—"


def format_certificate_date(day) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일"


def build_training_certificate_context(counselor, progress: dict, issued_on) -> dict:
    """확인서에 찍을 값. progress는 수련현황 함수의 반환 그대로다."""
    profile = getattr(counselor, "counselor_profile", None)
    cohort = profile.cohort if profile is not None else None
    birth_date = profile.birth_date if profile is not None else None
    identity = [
        ("성명", counselor.name or _EMPTY),
        ("기수", f"{cohort}기" if cohort else _EMPTY),
    ]
    if birth_date:
        identity.append(("생년월일", format_certificate_date(birth_date)))
    rows = [
        [
            "접수면접",
            f"{progress['intake_required_count']}건",
            f"{progress['intake_completed_count']}건",
            progress["intake_completed_label"],
        ],
        [
            "상담자 경험",
            f"{progress['counseling_assigned_count']}회기",
            f"{progress['journal_completed_count']}회기",
            progress["counseling_completed_label"],
        ],
        [
            "종결기록지",
            f"{progress['termination_required_count']}건",
            f"{progress['termination_completed_count']}건",
            _EMPTY,
        ],
        [
            "수퍼비전",
            f"{progress['supervision_required_count']}회",
            (
                f"{progress['supervision_attended_count']}회 출석 "
                f"({progress['supervision_attendance_percent']}%)"
            ),
            _EMPTY,
        ],
        [
            "사례보고서",
            f"{progress['case_report_required_count']}건",
            f"{progress['case_report_submitted_count']}건",
            _EMPTY,
        ],
        [
            "공개사례발표회",
            _EMPTY,
            f"{progress['public_presentation_participation_count']}회 참여",
            _EMPTY,
        ],
    ]
    return {
        "title": CERTIFICATE_TITLE,
        "identity": identity,
        "columns": ["수련항목", "기준", "완료/참여", "인정시간"],
        "rows": rows,
        "progress": progress,
        "confirmation": CERTIFICATE_CONFIRMATION,
        "issued_on_label": format_certificate_date(issued_on),
        "issuer": CERTIFICATE_ISSUER,
    }


def _logo_flowable():
    path = os.path.join(settings.BASE_DIR, "static", "images", "ssu-logo.png")
    if not os.path.isfile(path):
        return None
    try:
        image = ImageReader(path)
        width_px, height_px = image.getSize()
        if not width_px or not height_px:
            return None
        max_width = 36 * mm
        max_height = 14 * mm
        scale = min(max_width / width_px, max_height / height_px)
        flowable = Image(path, width=width_px * scale, height=height_px * scale)
        flowable.hAlign = "CENTER"
        return flowable
    except Exception:
        return None


def build_training_certificate_pdf(context: dict) -> bytes:
    font = _register_korean_font()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=context["title"],
    )
    title_style = ParagraphStyle(
        "CertificateTitle",
        fontName=font,
        fontSize=18,
        leading=24,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#1a365d"),
        spaceAfter=0,
    )
    center_style = ParagraphStyle(
        "CertificateCenter",
        fontName=font,
        fontSize=11,
        leading=16,
        alignment=TA_CENTER,
    )
    cell_style = ParagraphStyle(
        "CertificateCell",
        fontName=font,
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
    )
    label_style = ParagraphStyle(
        "CertificateLabel",
        fontName=font,
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#4a5568"),
    )
    value_style = ParagraphStyle(
        "CertificateValue",
        fontName=font,
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
    )

    story = []
    logo = _logo_flowable()
    if logo is not None:
        story.extend([logo, Spacer(1, 4 * mm)])
    else:
        story.append(Paragraph(context["issuer"], center_style))
        story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(context["title"], title_style))
    story.append(Spacer(1, 8 * mm))

    identity_rows = [
        [Paragraph(label, label_style), Paragraph(value, value_style)]
        for label, value in context["identity"]
    ]
    identity = Table(identity_rows, colWidths=[28 * mm, 146 * mm])
    identity.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
            ]
        )
    )
    story.extend([identity, Spacer(1, 8 * mm)])

    header = [Paragraph(column, cell_style) for column in context["columns"]]
    body = [
        [Paragraph(str(cell), cell_style) for cell in row]
        for row in context["rows"]
    ]
    table = Table(
        [header, *body],
        colWidths=[38 * mm, 36 * mm, 64 * mm, 36 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), font),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1a365d")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend(
        [
            table,
            Spacer(1, 10 * mm),
            Paragraph(context["confirmation"], center_style),
            Spacer(1, 12 * mm),
            Paragraph(context["issued_on_label"], center_style),
            Spacer(1, 3 * mm),
            Paragraph(context["issuer"], center_style),
        ]
    )
    doc.build(story)
    return buffer.getvalue()


def training_certificate_response(counselor) -> HttpResponse:
    from apps.counseling.training_progress import get_counselor_training_progress

    progress = get_counselor_training_progress(counselor)
    context = build_training_certificate_context(
        counselor,
        progress,
        timezone.localdate(),
    )
    pdf_bytes = build_training_certificate_pdf(context)
    filename = f"수련확인서_{counselor.name or 'counselor'}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        "inline; filename=\"training-certificate.pdf\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    response["Content-Length"] = len(pdf_bytes)
    return response
