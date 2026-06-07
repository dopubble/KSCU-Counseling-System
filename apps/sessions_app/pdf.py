"""상담일지 PDF 생성 (ReportLab + Noto Sans KR)"""

from __future__ import annotations

import os
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import CounselingJournal

PDF_PASSWORD_NOTICE = (
    "다운로드된 PDF 파일의 암호는 로그인 계정 이메일 주소입니다. (예: 0000@naver.com)"
)

_FONT_NAME = "NotoSansKR"
_FONT_REGISTERED = False


def get_noto_font_path() -> str:
    """프로젝트 static/fonts/NotoSansKR-Regular.ttf 절대 경로"""
    return os.path.abspath(
        os.path.join(
            settings.BASE_DIR,
            "static",
            "fonts",
            "NotoSansKR-Regular.ttf",
        )
    )


def _register_korean_font() -> str:
    global _FONT_REGISTERED
    if _FONT_REGISTERED and _FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return _FONT_NAME

    font_path = get_noto_font_path()
    if not os.path.isfile(font_path):
        raise ImproperlyConfigured(
            f"한글 PDF 폰트가 없습니다: {font_path}\n"
            "static/fonts/NotoSansKR-Regular.ttf 파일을 추가하거나 "
            "scripts/install_noto_font.ps1 을 실행해 주세요."
        )

    pdfmetrics.registerFont(TTFont(_FONT_NAME, font_path))
    _FONT_REGISTERED = True
    return _FONT_NAME


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    safe = escape(text or "—").replace("\n", "<br/>")
    return Paragraph(safe, style)


def _fmt_datetime(dt) -> str:
    if not dt:
        return "—"
    from django.utils import timezone

    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.strftime("%Y-%m-%d %H:%M")


def encryptWithPassword(
    user_password: str,
    owner_password: str | None = None,
) -> StandardEncryption:
    """ReportLab PDF 열람 비밀번호 설정 (SimpleDocTemplate.encrypt용)"""
    return StandardEncryption(
        userPassword=user_password,
        ownerPassword=owner_password or user_password,
        canPrint=1,
        canModify=0,
        canCopy=0,
        canAnnotate=0,
        strength=128,
    )


def build_journal_pdf(
    journal: CounselingJournal,
    *,
    client_summary: dict | None = None,
    user_password: str | None = None,
) -> bytes:
    """상담일지 PDF 바이트 반환 (user_password: PDF 열람 비밀번호, 필수)"""
    if not user_password or not str(user_password).strip():
        raise ImproperlyConfigured("PDF 암호화를 위해 user_password(이메일)가 필요합니다.")

    user_password = str(user_password).strip()
    font = _register_korean_font()
    case = journal.case
    client = case.client
    summary = client_summary or {}

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"상담일지 {case.case_number} {journal.session_number}회기",
        encrypt=encryptWithPassword(user_password),
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontName=font,
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=6,
        textColor=colors.HexColor("#1a365d"),
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.grey,
        spaceAfter=14,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName=font,
        fontSize=10,
        leading=15,
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontName=font,
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=6,
        textColor=colors.HexColor("#2c5282"),
    )

    student_id = summary.get("student_id") or "—"
    counselor_name = journal.counselor.name if journal.counselor_id else "—"

    meta_rows = [
        ["사례번호", case.case_number, "회기", f"{journal.session_number}회기"],
        ["내담자", client.name, "학번", student_id or "—"],
        ["상담 구분", journal.session_category or "—", "상담 일시", _fmt_datetime(journal.session_datetime)],
        ["담당 상담사", counselor_name, "작성일", _fmt_datetime(journal.created_at)],
    ]
    meta_table = Table(meta_rows, colWidths=[28 * mm, 57 * mm, 28 * mm, 57 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), font, 9),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf2f7")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#edf2f7")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#4a5568")),
                ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#4a5568")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    story = [
        _para("숭실사이버대학교 평생교육원 전문상담센터", subtitle_style),
        _para("상담일지", title_style),
        _para("본 문서는 상담 기록으로 대외비이며 무단 배포를 금합니다.", subtitle_style),
        Spacer(1, 4 * mm),
        meta_table,
        Spacer(1, 6 * mm),
    ]

    sections = [
        ("상담 내용", journal.subjective),
        ("상담자 관찰", journal.objective),
        ("임상적 평가", journal.assessment),
        ("향후 계획", journal.plan),
    ]
    for heading, content in sections:
        if heading == "향후 계획" and not (content or "").strip():
            continue
        story.append(_para(heading, section_style))
        story.append(_para(content, body_style))

    doc.build(story)
    return buffer.getvalue()


def journal_pdf_filename(journal: CounselingJournal) -> str:
    case_number = journal.case.case_number.replace("/", "-")
    return f"상담일지_{case_number}_{journal.session_number}회기.pdf"
