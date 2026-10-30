"""전문상담사 수련확인서 PDF.

수련 숫자는 get_counselor_training_progress() 결과를 그대로 표시한다.
"""

from __future__ import annotations

import os
from io import BytesIO
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponse
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
    "※ 진행방식은 회기·세션 스냅샷을 우선합니다. "
    "스냅샷이 없는 과거 접수면접·상담일지는 사례의 현재 상담방식을, "
    "스냅샷이 없는 과거 수퍼비전은 비대면으로 참고합니다."
)
CERTIFICATE_ISSUER = "숭실사이버대학교 평생교육원"
FOOTER_LOGO_FILENAME = "training-certificate-footer-logo.png"
_EMPTY = "—"
_FOOTER_LOGO_WIDTH_RATIO = 0.45

_PAGE_WIDTH = A4[0]
_SIDE_MARGIN = 22 * mm
_TOP_MARGIN = 36 * mm
_BOTTOM_MARGIN = _TOP_MARGIN
_CONTENT_WIDTH = _PAGE_WIDTH - (2 * _SIDE_MARGIN)
_SPACE_AFTER_TITLE = 16
_SPACE_AFTER_IDENTITY = 20
_SPACE_AFTER_TABLE = 20
_SPACE_AFTER_CONFIRMATION = 28
_ISSUER_FONT_SIZE = 11


def format_certificate_date(day) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일"


def _certificate_time_rows(item_name: str, *, in_person: int, remote: int, unknown: int):
    """방식별 분이 있으면 행을 나눈다. 전부 0이면 0분 한 행."""
    from apps.counseling.training_progress import format_training_minutes

    rows: list[list[str]] = []
    if in_person:
        rows.append([item_name, format_training_minutes(in_person), "대면"])
    if remote:
        rows.append([item_name, format_training_minutes(remote), "비대면"])
    if unknown:
        rows.append([item_name, format_training_minutes(unknown), "방식 미확인"])
    if not rows:
        rows.append([item_name, format_training_minutes(0), _EMPTY])
    return rows


def build_training_certificate_context(
    counselor,
    progress: dict,
    issued_on,
    *,
    supervisor_label: str,
    intake_mode: str = "",
    journal_mode: str = "",
    supervision_time: str = "",
) -> dict:
    """확인서에 찍을 값. progress는 수련현황 함수의 반환 그대로다.

    intake_mode/journal_mode/supervision_time은 하위 호환용이며
    행 구성은 progress의 방식별 분을 우선한다.
    """
    profile = getattr(counselor, "counselor_profile", None)
    birth_date = profile.birth_date if profile is not None else None
    identity = [("성명", counselor.name or _EMPTY)]
    if birth_date:
        identity.append(("생년월일", format_certificate_date(birth_date)))
    identity.append(("수퍼바이저", supervisor_label or _EMPTY))
    rows: list[list[str]] = []
    rows.extend(
        _certificate_time_rows(
            "접수면접",
            in_person=progress.get("intake_in_person_minutes", 0),
            remote=progress.get("intake_remote_minutes", 0),
            unknown=progress.get("intake_unknown_minutes", 0),
        )
    )
    rows.extend(
        _certificate_time_rows(
            "상담자 경험",
            in_person=progress.get("counseling_in_person_minutes", 0),
            remote=progress.get("counseling_remote_minutes", 0),
            unknown=progress.get("counseling_unknown_minutes", 0),
        )
    )
    rows.extend(
        _certificate_time_rows(
            "수퍼비전",
            in_person=progress.get("supervision_in_person_minutes", 0),
            remote=progress.get("supervision_remote_minutes", 0),
            unknown=progress.get("supervision_unknown_minutes", 0),
        )
    )
    rows.append(
        [
            "사례보고서",
            f"{progress['case_report_submitted_count']}사례",
            "비대면",
        ]
    )
    return {
        "title": CERTIFICATE_TITLE,
        "identity": identity,
        "columns": ["수련항목", "수련시간", "진행방식"],
        "rows": rows,
        "progress": progress,
        "method_notice": CERTIFICATE_METHOD_NOTICE,
        "confirmation": CERTIFICATE_CONFIRMATION,
        "issued_on_label": format_certificate_date(issued_on) if issued_on else _EMPTY,
        "issuer": CERTIFICATE_ISSUER,
    }


def footer_logo_size(content_width, pixel_width, pixel_height, ratio=_FOOTER_LOGO_WIDTH_RATIO):
    """원본 비율을 유지한 하단 로고 크기. 가로는 본문 폭의 ratio를 넘지 않는다."""
    if not pixel_width or not pixel_height:
        return 0, 0
    width = content_width * ratio
    return width, width * (pixel_height / pixel_width)


def footer_logo_origin(page_width, image_width):
    """이미지 왼쪽 시작 좌표. 중심은 페이지 중심과 같다."""
    return (page_width - image_width) / 2


def _footer_logo_spec():
    path = os.path.join(settings.BASE_DIR, "static", "images", FOOTER_LOGO_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        image = ImageReader(path)
        width_px, height_px = image.getSize()
        width, height = footer_logo_size(_CONTENT_WIDTH, width_px, height_px)
        if width <= 0 or height <= 0:
            return None
        return path, width, height
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
    issuer = _FooterMark(
        context["issuer"],
        font,
        _ISSUER_FONT_SIZE,
        page_width=_PAGE_WIDTH,
        left_margin=_SIDE_MARGIN,
        logo=_footer_logo_spec(),
    )
    _, issuer_height = issuer.wrap(_CONTENT_WIDTH, doc.height)
    story.extend(
        [
            table,
            Spacer(1, _SPACE_AFTER_TABLE),
            Paragraph(context["confirmation"], center_style),
            Spacer(1, _SPACE_AFTER_CONFIRMATION),
            Paragraph(context["issued_on_label"], center_style),
            _FillRemaining(issuer_height),
            issuer,
        ]
    )
    doc.build(story)
    return buffer.getvalue()


class _FillRemaining(Flowable):
    """뒤에 올 요소 높이만 남기고, 그 위를 비워 페이지 하단에 붙인다."""

    def __init__(self, reserve):
        super().__init__()
        self.reserve = reserve
        self.height = 0

    def wrap(self, availWidth, availHeight):
        self.height = max(availHeight - self.reserve, 0)
        return availWidth, self.height

    def draw(self):
        return


def _text_metrics(font_name, font_size):
    face = pdfmetrics.getFont(font_name).face
    ascent = getattr(face, "ascent", 0) or 800
    descent = getattr(face, "descent", 0) or -200
    return ascent * font_size / 1000.0, descent * font_size / 1000.0


class _FooterMark(Flowable):
    """완성형 하단 로고. 이미지가 없으면 기관명 텍스트만 중앙에 그린다."""

    def __init__(self, text, font, font_size, *, page_width, left_margin, logo):
        super().__init__()
        self.text = text
        self.font = font
        self.font_size = font_size
        self.page_width = page_width
        self.left_margin = left_margin
        self.logo = logo
        self._ascent = font_size * 0.8
        self._descent = -font_size * 0.2

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        if self.logo is not None:
            self.height = self.logo[2]
        else:
            self._ascent, self._descent = _text_metrics(self.font, self.font_size)
            self.height = self._ascent - self._descent
        return availWidth, self.height

    def draw(self):
        if self.logo is not None:
            path, width, height = self.logo
            x = footer_logo_origin(self.page_width, width) - self.left_margin
            self.canv.drawImage(
                path,
                x,
                0,
                width=width,
                height=height,
                mask="auto",
                preserveAspectRatio=True,
            )
            return
        text_width = stringWidth(self.text, self.font, self.font_size)
        x = footer_logo_origin(self.page_width, text_width) - self.left_margin
        self.canv.setFillColor(colors.black)
        self.canv.setFont(self.font, self.font_size)
        self.canv.drawString(x, -self._descent, self.text)


def training_certificate_response(counselor) -> HttpResponse:
    from apps.counseling.training_certificate import (
        intake_delivery_mode_label,
        journal_delivery_mode_label,
        certificate_date_for_counselor,
        supervisor_label_for_counselor,
        supervision_attended_time_label,
    )
    from apps.counseling.training_progress import get_counselor_training_progress

    progress = get_counselor_training_progress(counselor)
    context = build_training_certificate_context(
        counselor,
        progress,
        certificate_date_for_counselor(counselor),
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
