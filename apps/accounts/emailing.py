"""계정 관련 이메일 발송."""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_find_id_email(*, user) -> bool:
    """아이디(이메일) 안내 메일 발송. 실패 시 False."""
    subject = "[숭실사이버대학교 평생교육원] 로그인 아이디 안내"
    message = (
        f"안녕하세요, {user.name}님.\n\n"
        f"요청하신 로그인 아이디(이메일)는 아래와 같습니다.\n\n"
        f"  {user.email}\n\n"
        "로그인 페이지에서 위 이메일과 비밀번호로 로그인해 주세요.\n"
        "비밀번호를 잊으셨다면 '비밀번호 찾기'를 이용해 주세요.\n"
    )
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("아이디 찾기 이메일 발송 실패: user_id=%s", user.pk)
        return False
