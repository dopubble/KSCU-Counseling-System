"""계정 생성 시 이메일 중복을 한 규칙으로 막는다.

정규화는 회원가입 폼과 같다. 앞뒤 공백을 제거하고 도메인만 소문자로 만든다.
PostgreSQL 고유 제약은 대소문자를 구분하므로, 소문자로 맞춘 이메일 단위의
트랜잭션 잠금 뒤에 대소문자 무시 조회를 한다.
SQLite 에서는 이 잠금이 없다.
"""

import hashlib

from django.db import connection


class EmailAlreadyRegistered(Exception):
    """같은 이메일의 계정이 이미 있거나, 이메일 고유 제약이 걸린 경우."""

    def __init__(self, message="이미 가입된 이메일입니다."):
        super().__init__(message)


_EMAIL_UNIQUE_CONSTRAINTS = (
    "accounts_user_email_key",
    "accounts_user_email_lower_uniq",
)


def is_email_unique_violation(exc: BaseException) -> bool:
    """이메일 고유 제약 위반만 참이다. 예외 문구는 화면에 그대로 내보내지 않는다."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        diag = getattr(current, "diag", None)
        constraint = ""
        if diag is not None:
            constraint = getattr(diag, "constraint_name", None) or ""
        if constraint in _EMAIL_UNIQUE_CONSTRAINTS:
            return True
        text = " ".join(str(part) for part in getattr(current, "args", ()) if part)
        lowered = text.lower()
        if any(name in lowered for name in _EMAIL_UNIQUE_CONSTRAINTS):
            return True
        if (
            "unique constraint failed" in lowered
            and "accounts_user.email" in lowered
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def normalize_account_email(raw) -> str:
    from apps.accounts.models import User

    return User.objects.normalize_email((raw or "").strip())


def email_is_registered(email: str, *, exclude_pk=None) -> bool:
    from apps.accounts.models import User

    matches = User.objects.filter(email__iexact=email)
    if exclude_pk is not None:
        matches = matches.exclude(pk=exclude_pk)
    return matches.exists()


def lock_email_signup(email: str) -> None:
    """PostgreSQL 트랜잭션 안에서만 같은 이메일의 계정 생성을 직렬화한다."""
    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(email.casefold().encode("utf-8")).digest()

    def signed_int32(raw: bytes) -> int:
        value = int.from_bytes(raw, "big")
        if value >= 2**31:
            value -= 2**32
        return value

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [signed_int32(digest[:4]), signed_int32(digest[4:8])],
        )


def claim_new_account_email(user) -> None:
    """새 계정을 저장하기 전에 호출한다. 같은 트랜잭션 안에서 써야 한다."""
    user.email = normalize_account_email(user.email)
    lock_email_signup(user.email)
    if user.email and email_is_registered(user.email):
        raise EmailAlreadyRegistered()


def claim_changed_account_email(user) -> None:
    """이메일이 실제로 바뀔 때만 잠그고 검사한다. 자기 주소는 중복이 아니다."""
    from apps.accounts.models import User

    user.email = normalize_account_email(user.email)
    loaded = getattr(user, "_loaded_email", None)
    if loaded is not None and normalize_account_email(loaded) == user.email:
        return
    if loaded is None and user.pk:
        stored = User.objects.filter(pk=user.pk).values_list("email", flat=True).first()
        if stored is None or normalize_account_email(stored) == user.email:
            user._loaded_email = user.email
            return
    lock_email_signup(user.email)
    if email_is_registered(user.email, exclude_pk=user.pk):
        raise EmailAlreadyRegistered()
    user._loaded_email = user.email
