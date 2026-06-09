"""CSV 기반 User / CounselorProfile / ClientProfile 일괄 등록."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO, Iterable, TextIO

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import ClientProfile, CounselorProfile, User, UserRole, UserStatus

# CSV 헤더 — 한글·영문 모두 허용 (소문자 정규화 후 매칭)
HEADER_ALIASES: dict[str, str] = {
    "email": "email",
    "이메일": "email",
    "e-mail": "email",
    "name": "name",
    "이름": "name",
    "phone": "phone",
    "연락처": "phone",
    "휴대폰": "phone",
    "전화": "phone",
    "password": "password",
    "비밀번호": "password",
    "초기비밀번호": "password",
    "initial_password": "password",
    "department": "department",
    "소속학과": "department",
    "학과": "department",
    "student_id": "student_id",
    "학번": "student_id",
    "birth_date": "birth_date",
    "생년월일": "birth_date",
    "gender": "gender",
    "성별": "gender",
    "role": "role",
    "역할": "role",
    "구분": "role",
    "is_kcu_student": "is_kcu_student",
    "숭실사이버대학교학생": "is_kcu_student",
    "kcu_student": "is_kcu_student",
}

ROLE_ALIASES = {
    "counselor": UserRole.COUNSELOR,
    "상담사": UserRole.COUNSELOR,
    "counsellor": UserRole.COUNSELOR,
    "client": UserRole.CLIENT,
    "내담자": UserRole.CLIENT,
    "student": UserRole.CLIENT,
    "학생": UserRole.CLIENT,
}


@dataclass
class ImportRow:
    line_no: int
    email: str
    name: str
    phone: str = ""
    password: str = ""
    department: str = ""
    student_id: str = ""
    birth_date: date | None = None
    gender: str = ""
    role: str = UserRole.CLIENT
    is_kcu_student: bool | None = None


@dataclass
class RowResult:
    line_no: int
    email: str
    action: str  # created | updated | skipped | error
    message: str = ""


@dataclass
class ImportSummary:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[RowResult] = field(default_factory=list)


def _normalize_header(value: str) -> str:
    key = value.strip().lower().replace(" ", "")
    return HEADER_ALIASES.get(key, key)


def _parse_bool(value: str) -> bool | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    if text in ("y", "yes", "true", "1", "예", "o", "ok"):
        return True
    if text in ("n", "no", "false", "0", "아니오", "아니요"):
        return False
    raise ValueError(f"예/아니오 형식이 아닙니다: {value!r}")


def _parse_gender(value: str) -> str:
    """성별 — 남/여 등 흔한 표기를 통일 (비어 있으면 공란)."""
    text = (value or "").strip()
    if not text:
        return ""
    key = text.lower().replace(" ", "")
    aliases = {
        "m": "남",
        "male": "남",
        "man": "남",
        "남": "남",
        "남성": "남",
        "f": "여",
        "female": "여",
        "woman": "여",
        "여": "여",
        "여성": "여",
    }
    return aliases.get(key, text[:10])


def _parse_birth_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"생년월일 형식 오류 (YYYY-MM-DD): {value!r}")


def _parse_role(value: str, default_role: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return default_role
    role = ROLE_ALIASES.get(text)
    if role:
        return role
    raise ValueError(f"역할 값 오류 (counselor/client 또는 상담사/내담자): {value!r}")


def _decode_csv_bytes(raw: bytes) -> str:
    """CSV 바이트 → 텍스트. Windows Excel(CP949) · UTF-8 BOM 모두 지원."""
    if not raw:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "CSV 인코딩을 읽을 수 없습니다. "
        "Excel에서 'CSV UTF-8(쉼표로 분리)(*.csv)'로 저장하거나, "
        "메모장에서 UTF-8로 저장한 뒤 다시 시도해 주세요."
    )


def read_csv_rows(
    source: str | Path | TextIO | BinaryIO,
    *,
    default_role: str = UserRole.CLIENT,
) -> list[ImportRow]:
    """CSV → ImportRow 목록. UTF-8·CP949(Windows Excel) 지원."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        text = _decode_csv_bytes(path.read_bytes())
        reader = csv.DictReader(io.StringIO(text))
    elif hasattr(source, "read"):
        if isinstance(source, (io.TextIOWrapper, io.StringIO)):
            reader = csv.DictReader(source)
        else:
            raw = source.read()
            if isinstance(raw, bytes):
                text = _decode_csv_bytes(raw)
            else:
                text = raw
            reader = csv.DictReader(io.StringIO(text))
    else:
        raise TypeError("source must be path or file-like object")

    if not reader.fieldnames:
        raise ValueError("CSV 헤더가 없습니다.")

    normalized_field_map = {_normalize_header(h): h for h in reader.fieldnames if h}

    def cell(row: dict[str, str], canonical: str) -> str:
        original_key = normalized_field_map.get(canonical)
        if not original_key:
            return ""
        return (row.get(original_key) or "").strip()

    rows: list[ImportRow] = []
    for line_no, raw_row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in raw_row.values()):
            continue

        email = cell(raw_row, "email")
        name = cell(raw_row, "name")
        if not email or not name:
            raise ValueError(
                f"{line_no}행: 이메일과 이름은 필수입니다 (email={email!r}, name={name!r})."
            )

        password = cell(raw_row, "password")
        if not password:
            raise ValueError(f"{line_no}행: 초기 비밀번호(password)가 비어 있습니다.")

        try:
            rows.append(
                ImportRow(
                    line_no=line_no,
                    email=User.objects.normalize_email(email),
                    name=name,
                    phone=cell(raw_row, "phone"),
                    password=password,
                    department=cell(raw_row, "department"),
                    student_id=cell(raw_row, "student_id"),
                    birth_date=_parse_birth_date(cell(raw_row, "birth_date")),
                    gender=_parse_gender(cell(raw_row, "gender")),
                    role=_parse_role(cell(raw_row, "role"), default_role),
                    is_kcu_student=_parse_bool(cell(raw_row, "is_kcu_student")),
                )
            )
        except ValueError as exc:
            raise ValueError(f"{line_no}행: {exc}") from exc

    return rows


def _resolve_is_kcu_student(row: ImportRow) -> bool:
    if row.is_kcu_student is not None:
        return row.is_kcu_student
    return bool(row.department.strip())


@transaction.atomic
def _upsert_row(
    row: ImportRow,
    *,
    update_existing: bool,
    approve_counselors: bool,
    validate_passwords: bool,
) -> RowResult:
    existing = User.objects.filter(email__iexact=row.email).first()

    if existing and not update_existing:
        return RowResult(
            line_no=row.line_no,
            email=row.email,
            action="skipped",
            message="이미 등록된 이메일 ( --update-existing 으로 갱신 가능)",
        )

    if validate_passwords:
        candidate = existing or User(email=row.email, name=row.name)
        try:
            validate_password(row.password, candidate)
        except ValidationError as exc:
            return RowResult(
                line_no=row.line_no,
                email=row.email,
                action="error",
                message="; ".join(exc.messages),
            )

    action = "updated" if existing else "created"

    if existing:
        user = existing
        user.name = row.name
        user.phone = row.phone
        user.role = row.role
        if row.role == UserRole.CLIENT:
            user.status = UserStatus.ACTIVE
        elif row.role == UserRole.COUNSELOR and user.status == UserStatus.PENDING:
            user.status = UserStatus.ACTIVE
        user.set_password(row.password)
        user.save()
    else:
        status = UserStatus.ACTIVE if row.role == UserRole.CLIENT else UserStatus.PENDING
        if row.role == UserRole.COUNSELOR and approve_counselors:
            status = UserStatus.ACTIVE
        user = User.objects.create_user(
            email=row.email,
            password=row.password,
            name=row.name,
            phone=row.phone,
            role=row.role,
            status=status,
        )

    if row.role == UserRole.COUNSELOR:
        profile, _ = CounselorProfile.objects.get_or_create(user=user)
        profile.birth_date = row.birth_date
        profile.gender = row.gender
        if approve_counselors:
            profile.is_approved = True
        profile.save(
            update_fields=[
                "birth_date",
                "gender",
                "is_approved",
                "updated_at",
            ]
        )
    elif row.role == UserRole.CLIENT:
        CounselorProfile.objects.filter(user=user).delete()
        is_kcu = _resolve_is_kcu_student(row)
        profile, _ = ClientProfile.objects.get_or_create(user=user)
        profile.student_id = row.student_id
        profile.birth_date = row.birth_date
        profile.gender = row.gender
        profile.is_kcu_student = is_kcu
        profile.department = row.department if is_kcu else ""
        profile.save(
            update_fields=[
                "student_id",
                "birth_date",
                "gender",
                "is_kcu_student",
                "department",
                "updated_at",
            ]
        )

    return RowResult(line_no=row.line_no, email=row.email, action=action)


def import_user_rows(
    rows: Iterable[ImportRow],
    *,
    update_existing: bool = False,
    approve_counselors: bool = True,
    validate_passwords: bool = False,
    dry_run: bool = False,
) -> ImportSummary:
    summary = ImportSummary()
    for row in rows:
        if dry_run:
            exists = User.objects.filter(email__iexact=row.email).exists()
            if exists and not update_existing:
                summary.skipped += 1
                action = "skipped"
                message = "dry-run: 기존 계정"
            elif exists:
                summary.updated += 1
                action = "updated"
                message = "dry-run"
            else:
                summary.created += 1
                action = "created"
                message = "dry-run"
            summary.results.append(
                RowResult(row.line_no, row.email, action, message)
            )
            continue

        try:
            result = _upsert_row(
                row,
                update_existing=update_existing,
                approve_counselors=approve_counselors,
                validate_passwords=validate_passwords,
            )
        except Exception as exc:  # noqa: BLE001 — 행 단위 오류 수집
            result = RowResult(
                line_no=row.line_no,
                email=row.email,
                action="error",
                message=str(exc),
            )

        summary.results.append(result)
        if result.action == "created":
            summary.created += 1
        elif result.action == "updated":
            summary.updated += 1
        elif result.action == "skipped":
            summary.skipped += 1
        else:
            summary.errors += 1

    return summary
