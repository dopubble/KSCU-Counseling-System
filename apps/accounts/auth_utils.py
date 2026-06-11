"""로그인/회원가입 후 안전한 리다이렉트(next) 처리."""

from functools import wraps

from django.utils.http import url_has_allowed_host_and_scheme


def skip_session_save(view_func):
    """
    폴링 등 읽기 전용 API에서 세션 저장을 건너뜁니다.
    동시 요청 시 DB 세션 락 경합을 줄입니다.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        finally:
            request.session.modified = False

    return wrapper


def get_safe_next_url(request, *, default: str | None = None) -> str | None:
    """GET/POST의 next가 허용된 호스트일 때만 반환."""
    candidate = request.POST.get("next") or request.GET.get("next")
    if not candidate:
        return default
    if url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default
