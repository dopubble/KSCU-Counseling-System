"""전문상담사 수련확인서 PDF.

발급 context는 build_counselor_training_certificate_context() 한 번으로 만들고
그 결과를 PDF와 승인 hash가 함께 쓴다.
이 모듈의 build_training_certificate_context()는 넘겨받은 progress를 그대로 그린다.
"""

from __future__ import annotations

import os
from io import BytesIO
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponse
from PIL import Image, ImageFont
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

from apps.sessions_app.pdf import _register_korean_font, get_noto_font_path

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
# 로고 잉크 하단과 용지 하단 사이. 프레임 여백(36mm)보다 아래이며 20~25mm 안이다.
_FOOTER_LOGO_INK_BOTTOM = 22.5 * mm
# 수련확인증 글자 잉크 상단과 용지 상단 사이. 하단 로고 잉크 여백과 같게 둔다.
_TITLE_INK_TOP = 22.6 * mm
_TITLE_FONT_SIZE = 22
_CONTENT_WIDTH = _PAGE_WIDTH - (2 * _SIDE_MARGIN)
_SPACE_AFTER_TITLE = 16
_SPACE_AFTER_IDENTITY = 20
_SPACE_AFTER_TABLE = 20
_SPACE_AFTER_CONFIRMATION = 28
_ISSUER_FONT_SIZE = 11
_IDENTITY_RULE_WEIGHT = 0.3
_IDENTITY_RULE_COLOR = colors.HexColor("#e2e8f0")
_IDENTITY_VALUE_LEFT_PADDING = 4 * mm


def format_certificate_date(day) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일"


def _certificate_time_rows(
    item_name: str,
    *,
    in_person: int,
    remote: int,
    unknown: int,
    remark: str = "",
):
    """방식별 분이 있으면 행을 나눈다. 전부 0이면 0분 한 행.

    같은 수련항목의 이어지는 행은 첫·비고 열을 비워 PDF에서 rowspan(SPAN)한다.
    """
    from apps.counseling.training_progress import format_training_minutes

    parts: list[tuple[int, str]] = []
    if in_person:
        parts.append((in_person, "대면"))
    if remote:
        parts.append((remote, "비대면"))
    if unknown:
        parts.append((unknown, "방식 미확인"))
    if not parts:
        return [[item_name, format_training_minutes(0), _EMPTY, remark]]
    rows: list[list[str]] = []
    for index, (minutes, method) in enumerate(parts):
        label = item_name if index == 0 else ""
        note = remark if index == 0 else ""
        rows.append([label, format_training_minutes(minutes), method, note])
    return rows


def item_column_span_commands(
    rows: list[list[str]],
    *,
    header_rows: int = 1,
    remark_column: int = 3,
):
    """동일 수련항목의 빈 첫 칸 연속 행에 대해 ReportLab SPAN 명령을 만든다.

    rows는 PDF body 행(헤더 제외). 반환 좌표는 헤더를 포함한 table 행 번호다.
    비고 column이 있으면 같은 행 범위로 함께 SPAN한다.
    """
    commands: list[tuple] = []
    index = 0
    while index < len(rows):
        if not (rows[index][0] or "").strip():
            index += 1
            continue
        end = index + 1
        while end < len(rows) and not (rows[end][0] or "").strip():
            end += 1
        if end - index > 1:
            start_row = header_rows + index
            end_row = header_rows + end - 1
            commands.append(("SPAN", (0, start_row), (0, end_row)))
            if remark_column is not None and len(rows[index]) > remark_column:
                commands.append(
                    ("SPAN", (remark_column, start_row), (remark_column, end_row))
                )
        index = end
    return commands


def build_training_certificate_context(
    counselor,
    progress: dict,
    issued_on,
    *,
    supervisor_label: str,
    counseling_cases_5plus_count: int = 0,
    intake_mode: str = "",
    journal_mode: str = "",
    supervision_time: str = "",
) -> dict:
    """확인서에 찍을 값. progress는 수련현황 함수의 반환 그대로다.

    intake_mode/journal_mode/supervision_time은 하위 호환용이며
    행 구성은 progress의 방식별 분을 우선한다.
    """
    from apps.counseling.training_certificate import counseling_cases_5plus_label

    profile = getattr(counselor, "counselor_profile", None)
    birth_date = profile.birth_date if profile is not None else None
    identity = [("성명", counselor.name or _EMPTY)]
    if birth_date:
        identity.append(("생년월일", format_certificate_date(birth_date)))
    identity.append(("수퍼바이저", "\n".join(supervisor_display_lines(supervisor_label))))
    from apps.counseling.training_certificate import (
        own_case_supervision_post_count,
        own_case_supervision_time_label,
    )

    counseling_remark = counseling_cases_5plus_label(counseling_cases_5plus_count)
    own_case_posts = own_case_supervision_post_count(counselor)
    supervision_remark = own_case_supervision_time_label(own_case_posts)
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
            remark=counseling_remark,
        )
    )
    rows.extend(
        _certificate_time_rows(
            "수퍼비전",
            in_person=progress.get("supervision_in_person_minutes", 0),
            remote=progress.get("supervision_remote_minutes", 0),
            unknown=progress.get("supervision_unknown_minutes", 0),
            remark=supervision_remark,
        )
    )
    rows.append(
        [
            "사례보고서",
            f"{progress['case_report_submitted_count']}사례",
            "비대면",
            "",
        ]
    )
    return {
        "title": CERTIFICATE_TITLE,
        "identity": identity,
        "columns": ["수련항목", "수련시간", "진행방식", "비고"],
        "rows": rows,
        "progress": progress,
        "counseling_cases_5plus_count": counseling_cases_5plus_count,
        "own_case_supervision_posts": own_case_posts,
        "method_notice": CERTIFICATE_METHOD_NOTICE,
        "confirmation": CERTIFICATE_CONFIRMATION,
        "issued_on": issued_on,
        "issued_on_label": format_certificate_date(issued_on) if issued_on else _EMPTY,
        "supervisor_label": supervisor_label or _EMPTY,
        "supervisor_lines": supervisor_display_lines(supervisor_label),
        "issuer": CERTIFICATE_ISSUER,
    }


def supervisor_display_lines(label: str) -> list[str]:
    """기존 수퍼바이저 표기를 기본정보의 줄 단위로 나눈다. 순서는 유지한다."""
    text = (label or "").strip()
    if not text or text == _EMPTY:
        return [_EMPTY]
    lines: list[str] = []
    for part in text.split(", "):
        name = part.strip()
        if not name or name == _EMPTY:
            continue
        if not name.endswith("교수"):
            name = f"{name} 교수"
        lines.append(f"{name} (인)")
    return lines or [_EMPTY]


def footer_logo_size(content_width, pixel_width, pixel_height, ratio=_FOOTER_LOGO_WIDTH_RATIO):
    """원본 비율을 유지한 하단 로고 크기. 가로는 본문 폭의 ratio를 넘지 않는다."""
    if not pixel_width or not pixel_height:
        return 0, 0
    width = content_width * ratio
    return width, width * (pixel_height / pixel_width)


def footer_logo_origin(page_width, image_width):
    """이미지 왼쪽 시작 좌표. 중심은 페이지 중심과 같다."""
    return (page_width - image_width) / 2


def footer_logo_ink_bottom_inset(path, height):
    """이미지 박스 하단부터 실제 잉크까지의 투명 여백."""
    if not path or not height:
        return 0
    image = Image.open(path).convert("RGBA")
    width_px, height_px = image.size
    if width_px <= 0 or height_px <= 0:
        return 0
    pixels = image.load()
    content_bottom = None
    for y in range(height_px - 1, -1, -1):
        for x in range(width_px):
            red, green, blue, alpha = pixels[x, y]
            if alpha > 16 and (red < 245 or green < 245 or blue < 245):
                content_bottom = y
                break
        if content_bottom is not None:
            break
    if content_bottom is None:
        return 0
    transparent_below = height_px - 1 - content_bottom
    return height * transparent_below / height_px


def footer_logo_draw_y(ink_inset):
    """프레임 하단에 놓인 로고를 용지 하단 기준으로 내린다.

    본문 프레임과 발급일 위치는 그대로 두고, 그리기 좌표만 옮긴다.
    """
    return _FOOTER_LOGO_INK_BOTTOM - _BOTTOM_MARGIN - ink_inset


def title_ink_inset(font_path, font_size, text):
    """문단 박스 상단에서 실제 글자 잉크 상단까지의 거리.

    ReportLab은 기준선을 글자 크기만큼 박스 상단 아래에 둔다.
    한글 잉크는 그 글자 크기보다 낮으므로 그 차이를 잰다.
    """
    if not font_path or not font_size or not text:
        return 0
    sample = 1000
    image_font = ImageFont.truetype(font_path, sample)
    _left, top, _right, _bottom = image_font.getbbox(text, anchor="ls")
    ink_above_baseline = max(-top, 0) * font_size / sample
    return font_size - ink_above_baseline


def title_draw_rise(font_path, font_size, text):
    """제목만 위로 올려 글자 잉크 상단을 용지 상단에서 _TITLE_INK_TOP에 둔다.

    흐름 높이는 그대로라 성명 이하 본문은 움직이지 않는다.
    """
    return _TOP_MARGIN + title_ink_inset(font_path, font_size, text) - _TITLE_INK_TOP


class _RaisedFlowable(Flowable):
    """내용은 rise만큼 위로 그리고, 자리 높이는 유지한다."""

    def __init__(self, inner, rise):
        super().__init__()
        self.inner = inner
        self.rise = rise

    def wrap(self, availWidth, availHeight):
        width, height = self.inner.wrap(availWidth, availHeight)
        self.width = width
        self.height = height
        return width, height

    def draw(self):
        self.inner.drawOn(self.canv, 0, self.rise)


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


def _pdf_identity_rows(context: dict) -> list[tuple[str, str]]:
    """기본정보 행. 수퍼바이저 한 명은 성명·생년월일과 같은 한 행이다.

    라벨은 첫 수퍼바이저 행에만 둔다. 줄바꿈 문자로 간격을 만들지 않는다.
    """
    rows = [
        (label, value)
        for label, value in context.get("identity") or []
        if label != "수퍼바이저"
    ]
    lines = context.get("supervisor_lines")
    if not lines:
        label = context.get("supervisor_label")
        if not label:
            for item_label, value in context.get("identity") or []:
                if item_label == "수퍼바이저":
                    label = value.replace(" (인)", "").replace("\n", ", ")
                    break
        lines = supervisor_display_lines(label or "")
    for index, line in enumerate(lines):
        rows.append(("수퍼바이저" if index == 0 else "", line))
    return rows


def _supervisor_value_rule_rows(row_count: int, supervisor_start: int | None) -> tuple[int, ...]:
    """마지막 수퍼바이저를 제외한 수퍼바이저 행. 이 행의 선은 값 영역에만 둔다."""
    if supervisor_start is None:
        return ()
    return tuple(range(supervisor_start, row_count - 1))


def _identity_table_style(row_count: int, supervisor_start: int | None) -> TableStyle:
    """성명·생년월일과 같은 padding과 전체 폭 구분선.

    수퍼바이저 중간 행은 여기서 전체 폭 선을 긋지 않는다.
    그 선은 값 영역 시작 x부터 별도로 그린다.
    """
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, -1), 1 * mm),
        ("RIGHTPADDING", (0, 0), (0, -1), 4 * mm),
        ("LEFTPADDING", (1, 0), (1, -1), _IDENTITY_VALUE_LEFT_PADDING),
        ("RIGHTPADDING", (1, 0), (1, -1), 1 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_index in range(row_count):
        if row_index in _supervisor_value_rule_rows(row_count, supervisor_start):
            continue
        commands.append(
            (
                "LINEBELOW",
                (0, row_index),
                (-1, row_index),
                _IDENTITY_RULE_WEIGHT,
                _IDENTITY_RULE_COLOR,
            )
        )
    return TableStyle(commands)


def _identity_value_rule_segments(table) -> list[tuple[float, float, float, float]]:
    """수퍼바이저 중간 구분선. x는 값 칸 글자 시작, 끝은 표 오른쪽 끝이다."""
    rows = tuple(getattr(table, "_value_rule_rows", ()) or ())
    positions = getattr(table, "_colpositions", None)
    if not rows or not positions:
        return []
    left_padding = table._cellStyles[rows[0]][1].leftPadding
    x0 = positions[1] + left_padding
    x1 = positions[-1]
    return [
        (x0, table._rowpositions[row_index + 1], x1, table._rowpositions[row_index + 1])
        for row_index in rows
    ]


def _draw_identity_value_rules(table) -> None:
    segments = _identity_value_rule_segments(table)
    if not segments:
        return
    weight, color = table._value_rule_stroke
    canvas = table.canv
    canvas.saveState()
    canvas.setStrokeColor(color)
    canvas.setLineWidth(weight)
    for x0, y0, x1, y1 in segments:
        canvas.line(x0, y0, x1, y1)
    canvas.restoreState()


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
        fontSize=_TITLE_FONT_SIZE,
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
    # 수퍼비전 비고만 한 줄에 들어간다. 열 폭과 다른 칸의 글자 크기는 그대로다.
    supervision_remark_style = ParagraphStyle(
        "CertificateSupervisionRemark",
        parent=cell_style,
        fontSize=8,
        leading=11,
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

    story = [
        _RaisedFlowable(
            Paragraph(context["title"], title_style),
            title_draw_rise(get_noto_font_path(), _TITLE_FONT_SIZE, context["title"]),
        )
    ]
    story.append(Spacer(1, _SPACE_AFTER_TITLE))

    label_width = 30 * mm
    pdf_identity = _pdf_identity_rows(context)
    identity_rows = [
        [Paragraph(label, label_style), Paragraph(value, value_style)]
        for label, value in pdf_identity
    ]
    identity = Table(
        identity_rows,
        colWidths=[label_width, _CONTENT_WIDTH - label_width],
    )
    identity.hAlign = "CENTER"
    supervisor_start = next(
        (index for index, (label, _value) in enumerate(pdf_identity) if label == "수퍼바이저"),
        None,
    )
    identity.setStyle(_identity_table_style(len(pdf_identity), supervisor_start))
    identity._value_rule_rows = _supervisor_value_rule_rows(len(pdf_identity), supervisor_start)
    identity._value_rule_stroke = (_IDENTITY_RULE_WEIGHT, _IDENTITY_RULE_COLOR)
    draw_identity = identity.draw

    def draw_identity_with_value_rules():
        draw_identity()
        _draw_identity_value_rules(identity)

    identity.draw = draw_identity_with_value_rules
    story.extend([identity, Spacer(1, _SPACE_AFTER_IDENTITY)])

    header = [Paragraph(column, cell_style) for column in context["columns"]]
    body_rows = context["rows"]

    def _body_cell(cell):
        text = str(cell)
        style = (
            supervision_remark_style
            if text.startswith("내 사례로 슈퍼비전 받은시간")
            else cell_style
        )
        return Paragraph(text, style)

    body = [[_body_cell(cell) for cell in row] for row in body_rows]
    # 비고는 "5회기 이상 지속된 사례 총 00건 포함" 한 줄 + 여백만 두고 배분.
    item_width = _CONTENT_WIDTH * 0.28
    time_width = _CONTENT_WIDTH * 0.17
    method_width = _CONTENT_WIDTH * 0.24
    remark_width = _CONTENT_WIDTH - item_width - time_width - method_width
    table = Table(
        [header, *body],
        colWidths=[item_width, time_width, method_width, remark_width],
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
                *item_column_span_commands(body_rows),
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
        self._ink_inset = 0

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        if self.logo is not None:
            path, _width, height = self.logo
            self.height = height
            self._ink_inset = footer_logo_ink_bottom_inset(path, height)
        else:
            self._ascent, self._descent = _text_metrics(self.font, self.font_size)
            self.height = self._ascent - self._descent
            self._ink_inset = 0
        return availWidth, self.height

    def draw(self):
        if self.logo is not None:
            path, width, height = self.logo
            x = footer_logo_origin(self.page_width, width) - self.left_margin
            self.canv.drawImage(
                path,
                x,
                footer_logo_draw_y(self._ink_inset),
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


def training_certificate_response(counselor, context=None) -> HttpResponse:
    """context를 넘기면 그 값으로 PDF를 만든다. 승인 검사는 view가 한다."""
    if context is None:
        from apps.counseling.training_certificate import (
            build_counselor_training_certificate_context,
        )

        context = build_counselor_training_certificate_context(counselor)
    pdf_bytes = build_training_certificate_pdf(context)
    filename = f"수련확인증_{counselor.name or 'counselor'}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        "inline; filename=\"training-certificate.pdf\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    response["Content-Length"] = len(pdf_bytes)
    return response
