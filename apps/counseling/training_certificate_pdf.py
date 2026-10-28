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
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
    BaseDocTemplate,
    Frame,
    Image,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from apps.sessions_app.pdf import _register_korean_font

CERTIFICATE_TITLE = "수련확인증"
CERTIFICATE_CONFIRMATION = "위와 같이 전문상담사 수련과정의 수련 내역을 확인합니다."
CERTIFICATE_METHOD_NOTICE = (
    "※ 접수면접 및 상담자 경험의 진행방식은 현재 사례에 등록된 상담방식을 기준으로 표시합니다."
)
CERTIFICATE_ISSUER = "숭실사이버대학교 평생교육원"
_EMPTY = "—"

_PAGE_WIDTH = A4[0]
_SIDE_MARGIN = 22 * mm
_TOP_MARGIN = 36 * mm
_BOTTOM_MARGIN = 22 * mm
_CONTENT_WIDTH = _PAGE_WIDTH - (2 * _SIDE_MARGIN)
_SPACE_AFTER_TITLE = 16
_SPACE_AFTER_IDENTITY = 20
_SPACE_AFTER_TABLE = 14
_SPACE_AFTER_NOTICE = 20
_SPACE_AFTER_CONFIRMATION = 28
_SPACE_AFTER_DATE = 24
_ISSUER_GAP = 3 * mm
_ISSUER_FONT_SIZE = 11


def format_certificate_date(day) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일"


def build_training_certificate_context(
    counselor,
    progress: dict,
    issued_on,
    *,
    supervisor_label: str,
    intake_mode: str,
    journal_mode: str,
    supervision_time: str,
) -> dict:
    """확인서에 찍을 값. progress는 수련현황 함수의 반환 그대로다."""
    profile = getattr(counselor, "counselor_profile", None)
    birth_date = profile.birth_date if profile is not None else None
    identity = [("성명", counselor.name or _EMPTY)]
    if birth_date:
        identity.append(("생년월일", format_certificate_date(birth_date)))
    identity.append(("수퍼바이저", supervisor_label or _EMPTY))
    rows = [
        ["접수면접", progress["intake_completed_label"], intake_mode],
        ["상담자 경험", progress["counseling_completed_label"], journal_mode],
        ["수퍼비전", supervision_time, "비대면"],
        [
            "사례보고서",
            f"{progress['case_report_submitted_count']}사례",
            "비대면",
        ],
    ]
    return {
        "title": CERTIFICATE_TITLE,
        "identity": identity,
        "columns": ["수련항목", "수련시간", "진행방식"],
        "rows": rows,
        "progress": progress,
        "method_notice": CERTIFICATE_METHOD_NOTICE,
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
        max_width = 14 * mm
        max_height = 5.8 * mm
        scale = min(max_width / width_px, max_height / height_px)
        flowable = Image(path, width=width_px * scale, height=height_px * scale)
        flowable.hAlign = "CENTER"
        return flowable
    except Exception:
        return None


def build_training_certificate_pdf(context: dict) -> bytes:
    font = _register_korean_font()
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_SIDE_MARGIN,
        rightMargin=_SIDE_MARGIN,
        topMargin=_TOP_MARGIN,
        bottomMargin=_BOTTOM_MARGIN,
        title=context["title"],
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        id="certificate",
    )
    doc.addPageTemplates([PageTemplate(id="certificate", frames=[frame])])
    title_style = ParagraphStyle(
        "CertificateTitle",
        fontName=font,
        fontSize=22,
        leading=28,
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
    notice_style = ParagraphStyle(
        "CertificateNotice",
        fontName=font,
        fontSize=10,
        leading=14,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#4a5568"),
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

    story = [Paragraph(context["title"], title_style)]
    story.append(Spacer(1, _SPACE_AFTER_TITLE))

    label_width = 30 * mm
    identity_rows = [
        [Paragraph(label, label_style), Paragraph(value, value_style)]
        for label, value in context["identity"]
    ]
    identity = Table(
        identity_rows,
        colWidths=[label_width, _CONTENT_WIDTH - label_width],
    )
    identity.hAlign = "CENTER"
    identity.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, -1), 1 * mm),
                ("RIGHTPADDING", (0, 0), (0, -1), 4 * mm),
                ("LEFTPADDING", (1, 0), (1, -1), 4 * mm),
                ("RIGHTPADDING", (1, 0), (1, -1), 1 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
            ]
        )
    )
    story.extend([identity, Spacer(1, _SPACE_AFTER_IDENTITY)])

    header = [Paragraph(column, cell_style) for column in context["columns"]]
    body = [
        [Paragraph(str(cell), cell_style) for cell in row]
        for row in context["rows"]
    ]
    item_width = _CONTENT_WIDTH * 0.30
    time_width = _CONTENT_WIDTH * 0.30
    table = Table(
        [header, *body],
        colWidths=[item_width, time_width, _CONTENT_WIDTH - item_width - time_width],
    )
    table.hAlign = "CENTER"
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
            Spacer(1, _SPACE_AFTER_TABLE),
            Paragraph(context["method_notice"], notice_style),
            Spacer(1, _SPACE_AFTER_NOTICE),
            Paragraph(context["confirmation"], center_style),
            Spacer(1, _SPACE_AFTER_CONFIRMATION),
            Paragraph(context["issued_on_label"], center_style),
            Spacer(1, _SPACE_AFTER_DATE),
            _IssuerMark(
                context["issuer"],
                font,
                _ISSUER_FONT_SIZE,
                page_width=_PAGE_WIDTH,
                left_margin=_SIDE_MARGIN,
                logo=_logo_flowable(),
                gap=_ISSUER_GAP,
            ),
        ]
    )
    doc.build(story)
    return buffer.getvalue()


def issuer_group_origin(page_width, logo_width, text_width, gap):
    """로고+간격+기관명 그룹의 페이지 왼쪽 시작 좌표."""
    logo_width = logo_width or 0
    gap = gap if logo_width else 0
    group_width = logo_width + gap + text_width
    return group_width, (page_width - group_width) / 2


def issuer_mark_offsets(box_height, logo_height, ascent, descent):
    """로고와 글자의 시각적 중심이 같은 높이가 되는 좌표."""
    logo_y = (box_height - logo_height) / 2 if logo_height else 0
    baseline = box_height / 2 - (ascent + descent) / 2
    return logo_y, baseline


def _text_metrics(font_name, font_size):
    face = pdfmetrics.getFont(font_name).face
    ascent = getattr(face, "ascent", 0) or 800
    descent = getattr(face, "descent", 0) or -200
    return ascent * font_size / 1000.0, descent * font_size / 1000.0


class _IssuerMark(Flowable):
    """로고와 기관명을 페이지 가로 중심 기준으로 한 그룹으로 그린다."""

    def __init__(self, text, font, font_size, *, page_width, left_margin, logo, gap):
        super().__init__()
        self.text = text
        self.font = font
        self.font_size = font_size
        self.page_width = page_width
        self.left_margin = left_margin
        self.logo = logo
        self.gap = gap
        self._ascent = font_size * 0.8
        self._descent = -font_size * 0.2

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        self._ascent, self._descent = _text_metrics(self.font, self.font_size)
        text_height = self._ascent - self._descent
        logo_height = self.logo.drawHeight if self.logo is not None else 0
        self.height = max(text_height, logo_height)
        return availWidth, self.height

    def draw(self):
        logo_width = self.logo.drawWidth if self.logo is not None else 0
        logo_height = self.logo.drawHeight if self.logo is not None else 0
        text_width = stringWidth(self.text, self.font, self.font_size)
        _, start_x = issuer_group_origin(
            self.page_width,
            logo_width,
            text_width,
            self.gap,
        )
        x = start_x - self.left_margin
        logo_y, baseline = issuer_mark_offsets(
            self.height,
            logo_height,
            self._ascent,
            self._descent,
        )
        if self.logo is not None:
            self.canv.saveState()
            self.canv.translate(x, logo_y)
            self.logo.canv = self.canv
            self.logo.draw()
            self.canv.restoreState()
            x += logo_width + self.gap
        self.canv.setFillColor(colors.black)
        self.canv.setFont(self.font, self.font_size)
        self.canv.drawString(x, baseline, self.text)


def training_certificate_response(counselor) -> HttpResponse:
    from apps.counseling.training_certificate import (
        intake_delivery_mode_label,
        journal_delivery_mode_label,
        supervisor_label_for_counselor,
        supervision_attended_time_label,
    )
    from apps.counseling.training_progress import get_counselor_training_progress

    progress = get_counselor_training_progress(counselor)
    context = build_training_certificate_context(
        counselor,
        progress,
        timezone.localdate(),
        supervisor_label=supervisor_label_for_counselor(counselor),
        intake_mode=intake_delivery_mode_label(counselor),
        journal_mode=journal_delivery_mode_label(counselor),
        supervision_time=supervision_attended_time_label(counselor),
    )
    pdf_bytes = build_training_certificate_pdf(context)
    filename = f"수련확인증_{counselor.name or 'counselor'}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        "inline; filename=\"training-certificate.pdf\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    response["Content-Length"] = len(pdf_bytes)
    return response
