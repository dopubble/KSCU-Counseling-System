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

from .models import CounselingJournal, InitialCounselingRecord, TerminationCounselingRecord

PDF_PASSWORD_NOTICE = (
    "다운로드 시 입력한 암호로 PDF가 암호화됩니다. 파일을 열 때 동일한 암호를 입력하세요."
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
        raise ImproperlyConfigured("PDF 암호화를 위해 user_password가 필요합니다.")

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
    client_name = summary.get("client_name") or client.name

    meta_rows = [
        ["사례번호", case.case_number, "회기", f"{journal.session_number}회기"],
        ["내담자", client_name, "학번", student_id or "—"],
        ["성별", summary.get("gender") or "—", "생년월일", summary.get("birth_date") or "—"],
        ["직업", summary.get("occupation") or "—", "연락처", summary.get("phone") or "—"],
        ["이메일", summary.get("email") or "—", "상담 구분", journal.session_category or "—"],
        ["상담 일시", _fmt_datetime(journal.session_datetime), "담당 상담사", counselor_name],
        ["작성일", _fmt_datetime(journal.created_at), "", ""],
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
        _para("숭실사이버대학교 평생교육원", subtitle_style),
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


def build_initial_record_pdf(
    record: InitialCounselingRecord,
    *,
    client_summary: dict | None = None,
    user_password: str | None = None,
) -> bytes:
    """초기상담 기록지 PDF 바이트 반환."""
    if not user_password or not str(user_password).strip():
        raise ImproperlyConfigured("PDF 암호화를 위해 user_password가 필요합니다.")

    user_password = str(user_password).strip()
    font = _register_korean_font()
    case = record.case
    summary = client_summary or {}

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"초기상담 기록지 {case.case_number}",
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
    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4a5568"),
    )

    counselor_name = record.counselor.name if record.counselor_id else "—"

    client_rows = [
        [
            _para("이름", table_header_style),
            _para(summary.get("client_name") or case.client.name, body_style),
            _para("성별", table_header_style),
            _para(summary.get("gender") or "—", body_style),
        ],
        [
            _para("생년월일", table_header_style),
            _para(summary.get("birth_date") or "—", body_style),
            _para("직업", table_header_style),
            _para(summary.get("occupation") or "—", body_style),
        ],
        [
            _para("연락처", table_header_style),
            _para(summary.get("phone") or "—", body_style),
            _para("이메일", table_header_style),
            _para(summary.get("email") or "—", body_style),
        ],
    ]
    client_table = Table(client_rows, colWidths=[28 * mm, 57 * mm, 28 * mm, 57 * mm])
    client_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), font, 9),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf2f7")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#edf2f7")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    meta_table = Table(
        [
            ["사례번호", case.case_number, "담당 상담사", counselor_name],
            ["작성일", _fmt_datetime(record.updated_at), "상담 시작 일시", _fmt_datetime(record.session_start_datetime)],
        ],
        colWidths=[28 * mm, 57 * mm, 28 * mm, 57 * mm],
    )
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
        _para("숭실사이버대학교 | 평생교육원", subtitle_style),
        _para("초기상담 기록지", title_style),
        _para("본 문서는 상담 기록으로 대외비이며 무단 배포를 금합니다.", subtitle_style),
        Spacer(1, 4 * mm),
        _para("내담자 기본정보", section_style),
        client_table,
        Spacer(1, 4 * mm),
        meta_table,
        Spacer(1, 6 * mm),
        _para("상담 기본정보", section_style),
    ]

    sections = [
        (
            "1. 제시된 문제들, 중심 주제, 패턴, 현재 주의를 요하는 내담자의 상태를 요약하면?",
            record.presented_problems_summary,
        ),
        (
            "2. 현재와 과거의 기능: 현재의 문제들이 자신의 행동이나 대인관계에 영향을 미치는 방식은?",
            record.functioning_impact,
        ),
        (
            "3. 내담자의 관계적 역사: 관련된 개인적, 가족적, 공동체적·문화적 역사는?",
            record.relational_history,
        ),
        (
            "4. 내담자의 임상적 역사: 관련된 신체적, 상담·치료적, 정신의학적 역사는?",
            record.clinical_history,
        ),
        (
            "5. 신학적 평가: 종교성, 소속된 종교단체, 종교적 신념 및 행위 등 신학적 진단을 한다면?",
            record.theological_evaluation,
        ),
        (
            "6. 임상적 전략: 현재의 진단 및 최초의 임상적 개입과 차후 임상 계획은 (단기 및 장기)?",
            record.clinical_strategy,
        ),
        ("7. 기타", record.other_notes),
    ]
    for heading, content in sections:
        story.append(_para(heading, section_style))
        story.append(_para(content, body_style))

    doc.build(story)
    return buffer.getvalue()


def initial_record_pdf_filename(record: InitialCounselingRecord) -> str:
    case_number = record.case.case_number.replace("/", "-")
    return f"초기상담기록지_{case_number}.pdf"


def build_termination_record_pdf(
    record: TerminationCounselingRecord,
    *,
    client_summary: dict | None = None,
    user_password: str | None = None,
) -> bytes:
    """종결기록지 PDF 바이트 반환."""
    if not user_password or not str(user_password).strip():
        raise ImproperlyConfigured("PDF 암호화를 위해 user_password가 필요합니다.")

    user_password = str(user_password).strip()
    font = _register_korean_font()
    case = record.case
    summary = client_summary or {}

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"종결기록지 {case.case_number}",
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
    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4a5568"),
    )

    counselor_name = record.counselor.name if record.counselor_id else "—"

    client_rows = [
        [
            _para("이름", table_header_style),
            _para(summary.get("client_name") or case.client.name, body_style),
            _para("성별", table_header_style),
            _para(summary.get("gender") or "—", body_style),
        ],
        [
            _para("생년월일", table_header_style),
            _para(summary.get("birth_date") or "—", body_style),
            _para("직업", table_header_style),
            _para(summary.get("occupation") or "—", body_style),
        ],
        [
            _para("연락처", table_header_style),
            _para(summary.get("phone") or "—", body_style),
            _para("이메일", table_header_style),
            _para(summary.get("email") or "—", body_style),
        ],
    ]
    client_table = Table(client_rows, colWidths=[28 * mm, 57 * mm, 28 * mm, 57 * mm])
    client_table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), font, 9),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf2f7")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#edf2f7")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    meta_table = Table(
        [
            ["사례번호", case.case_number, "담당 상담사", counselor_name],
            ["작성일", _fmt_datetime(record.updated_at), "총 회기", f"{case.total_sessions}회"],
        ],
        colWidths=[28 * mm, 57 * mm, 28 * mm, 57 * mm],
    )
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
        _para("숭실사이버대학교 | 평생교육원", subtitle_style),
        _para("종결기록지", title_style),
        _para("본 문서는 상담 기록으로 대외비이며 무단 배포를 금합니다.", subtitle_style),
        Spacer(1, 4 * mm),
        _para("내담자 기본정보", section_style),
        client_table,
        Spacer(1, 4 * mm),
        meta_table,
        Spacer(1, 6 * mm),
        _para("상담 기본정보", section_style),
    ]

    sections = [
        ("1. 상담 진행 일시", record.counseling_period),
        ("2. 상담받은 주요주제", record.main_topics),
        ("3. 종결(중단) 사유", record.termination_reason),
        ("4. 내담자에 대한 상담자 소견", record.counselor_opinion),
        ("5. 종결 후 계획 또는 후속조치", record.post_termination_plan),
        ("6. 기타", record.other_notes),
    ]
    for heading, content in sections:
        story.append(_para(heading, section_style))
        story.append(_para(content, body_style))

    doc.build(story)
    return buffer.getvalue()


def termination_record_pdf_filename(record: TerminationCounselingRecord) -> str:
    case_number = record.case.case_number.replace("/", "-")
    return f"종결기록지_{case_number}.pdf"
