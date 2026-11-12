"""비로그인 상담 신청과 함께 내담자 계정을 만든다.

역할과 상태는 이 모듈에서만 정한다. 요청 값으로 관리자·상담사 권한을
만들 수 없다. 비밀번호는 User.objects.create_user 가 해시한다.
"""

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction

try:
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - redis 는 운영 의존성
    RedisError = ConnectionError

logger = logging.getLogger(__name__)
_CACHE_OUTAGE = (RedisError, ConnectionError, TimeoutError, OSError)

from apps.accounts.email_guard import (
    EmailAlreadyRegistered,
    email_is_registered,
    is_email_unique_violation,
    lock_email_signup,
    normalize_account_email,
)
from apps.accounts.models import ClientProfile, User, UserRole, UserStatus

EMAIL_AVAILABLE = "사용 가능한 이메일 주소입니다."
EMAIL_TAKEN = "이미 가입된 메일주소입니다. 로그인 후 상담신청을 하시기 바랍니다."
EMAIL_INVALID = "올바른 이메일 주소를 입력해 주세요."
EMAIL_UNCONFIRMED = "이메일 중복확인을 완료해 주세요."
EMAIL_CHECK_LIMITED = "잠시 후 다시 확인해 주세요."
APPLY_SUBMIT_LIMITED = "요청이 많습니다. 잠시 후 다시 시도해 주세요."
APPLY_FAILED = "상담 신청을 접수하지 못했습니다. 잠시 후 다시 시도해 주세요."


def email_rate_identity(email: str) -> str:
    """캐시 키에는 이메일 원문 대신 해시만 넣는다."""
    normalized = normalize_account_email(email)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()


def client_ip(request) -> str:
    """프록시가 넘긴 주소는 신뢰하지 않는다. REMOTE_ADDR 만 사용한다."""
    return (request.META.get("REMOTE_ADDR") or "unknown")[:64]


def rate_limit_exceeded(bucket: str, ident: str) -> bool:
    """캐시로 횟수를 센다.

    키가 없으면 add 로 1을 만들고, 있으면 incr 한다.
    만료 직후 incr 이 실패해도 set 으로 기존 값을 덮어쓰지 않는다.
    REDIS_URL 이 없으면 프로세스 메모리라 워커마다 따로 센다.
    캐시에 연결하지 못하면 신청 화면이 멈추지 않도록 제한을 건너뛴다.
    """
    try:
        return _rate_limit_exceeded(bucket, ident)
    except _CACHE_OUTAGE:
        logger.warning("request limit cache unavailable")
        return False


def _rate_limit_exceeded(bucket: str, ident: str) -> bool:
    limits = {
        "email-ip": ("APPLY_GUEST_EMAIL_CHECK_IP_LIMIT", 300),
        "email-address": ("APPLY_GUEST_EMAIL_CHECK_ADDRESS_LIMIT", 10),
        "submit-ip": ("APPLY_GUEST_SUBMIT_IP_LIMIT", 200),
        "submit-address": ("APPLY_GUEST_SUBMIT_ADDRESS_LIMIT", 20),
        "signup-ip": ("SIGNUP_IP_LIMIT", 200),
        "signup-address": ("SIGNUP_ADDRESS_LIMIT", 20),
    }
    windows = {
        "email-ip": "APPLY_GUEST_EMAIL_CHECK_WINDOW",
        "email-address": "APPLY_GUEST_EMAIL_CHECK_WINDOW",
        "submit-ip": "APPLY_GUEST_SUBMIT_WINDOW",
        "submit-address": "APPLY_GUEST_SUBMIT_WINDOW",
        "signup-ip": "SIGNUP_WINDOW",
        "signup-address": "SIGNUP_WINDOW",
    }
    setting_name, default_limit = limits[bucket]
    limit = int(getattr(settings, setting_name, default_limit))
    window = int(getattr(settings, windows[bucket], 600))
    if limit <= 0:
        return False
    key = f"apply-guest:{bucket}:{ident}"
    return _next_count(key, window) > limit


def _next_count(key: str, window: int) -> int:
    try:
        return cache.incr(key)
    except ValueError:
        if cache.add(key, 1, timeout=window):
            return 1
        try:
            return cache.incr(key)
        except ValueError:
            cache.add(key, 1, timeout=window)
            return 1


def guest_request_is_limited(kind: str, request, email: str = "") -> bool:
    buckets = {
        "email": ("email-ip", "email-address"),
        "signup": ("signup-ip", "signup-address"),
    }
    ip_bucket, address_bucket = buckets.get(kind, ("submit-ip", "submit-address"))
    if rate_limit_exceeded(ip_bucket, client_ip(request)):
        return True
    identity = email_rate_identity(email)
    if not identity:
        return False
    return rate_limit_exceeded(address_bucket, identity)


def create_client_account(account: dict) -> User:
    """CLIENT·ACTIVE 계정과 ClientProfile 을 만든다. 호출은 atomic 안에서."""
    email = account["email"]
    lock_email_signup(email)
    if email_is_registered(email):
        raise EmailAlreadyRegistered()
    is_kcu = bool(account.get("is_kcu_student"))
    department = (account.get("department") or "").strip() if is_kcu else ""
    student_id = (account.get("student_id") or "").strip() if is_kcu else ""
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=account["password"],
                name=account["name"],
                phone=account.get("phone") or "",
                role=UserRole.CLIENT,
                status=UserStatus.ACTIVE,
                is_staff=False,
                is_superuser=False,
            )
    except IntegrityError as exc:
        if is_email_unique_violation(exc):
            raise EmailAlreadyRegistered() from None
        raise
    if user.role != UserRole.CLIENT or user.status != UserStatus.ACTIVE:
        raise EmailAlreadyRegistered()
    if user.is_staff or user.is_superuser:
        raise EmailAlreadyRegistered()
    ClientProfile.objects.create(
        user=user,
        birth_date=account["birth_date"],
        is_kcu_student=is_kcu,
        student_id=student_id,
        department=department,
    )
    return user
